#!/usr/bin/env python3
"""決算プレビュー通知 メインスクリプト

実行タイミング: 21:00 JST (GitHub Actions cron '52 11 * * *')
処理内容:
  1. watchlist.csv 読込
  2. Finnhub から翌日(ET基準)の決算カレンダー取得
  3. watchlist フィルタ
  4. 各銘柄の詳細収集 (quote, profile, 過去決算, implied_move)
  5. HTML整形 → Telegram送信
  6. 実行ログを docs/earnings_preview_log.json に保存
"""
import os
import sys
import csv
import json
import logging
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo

# 自ディレクトリを sys.path に追加 (他モジュールの絶対import用)
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from finnhub_client import FinnhubClient
from implied_move import calculate_implied_move
from past_earnings import get_past_earnings_stats
from message_builder import build_telegram_message
from telegram_sender import send_telegram, send_error_notification

# タイムゾーン
ET = ZoneInfo("America/New_York")
JST = ZoneInfo("Asia/Tokyo")

# リポジトリルート基準のパス解決 (scripts/earnings_preview/main.py → repo root)
REPO_ROOT = SCRIPT_DIR.parent.parent
WATCHLIST_PATH = REPO_ROOT / "data" / "earnings_watchlist.csv"
LOG_DIR = REPO_ROOT / "docs"
LOG_PATH = LOG_DIR / "earnings_preview_log.json"

# Finnhub レートリミット対策 (無料枠 60 calls/min = 1秒1コール弱)
# 1銘柄あたり3コール消費するので0.25秒スリープで十分
PER_SYMBOL_SLEEP_SEC = 0.25


def load_watchlist(csv_path: Path) -> dict:
    """watchlist CSV を読み込み、ticker -> {tier, sector, ...} のdictにして返す"""
    if not csv_path.exists():
        raise FileNotFoundError(f"watchlist not found: {csv_path}")

    watchlist = {}
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = (row.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            try:
                tier = int(row.get("tier", "1"))
            except ValueError:
                tier = 1
            watchlist[ticker] = {
                "tier": tier,
                "sector": (row.get("sector") or "").strip(),
                "subsector": (row.get("subsector") or "").strip(),
                "notes": (row.get("notes") or "").strip(),
            }
    logging.info(f"Loaded {len(watchlist)} tickers from watchlist")
    return watchlist


def build_preview(client: FinnhubClient, symbol: str, entry: dict, tier_info: dict) -> dict:
    """1銘柄の詳細情報を収集"""
    # 現在株価
    quote = client.quote(symbol)
    current_price = quote.get("c")
    day_change_pct = quote.get("dp")

    # 企業プロファイル
    try:
        profile = client.profile(symbol)
    except Exception as ex:
        logging.warning(f"{symbol} profile fetch failed: {ex}")
        profile = {}

    # 過去4四半期実績
    past_stats = get_past_earnings_stats(client, symbol)

    # オプション織り込み変動率 (yfinance)
    implied = None
    if current_price and current_price > 0:
        implied = calculate_implied_move(
            symbol=symbol,
            stock_price=current_price,
            earnings_date=entry["date"],
        )

    return {
        "symbol": symbol,
        "company_name": profile.get("name") or symbol,
        "tier": tier_info["tier"],
        "sector": tier_info["sector"],
        "hour": entry.get("hour", ""),
        "eps_estimate": entry.get("epsEstimate"),
        "revenue_estimate": entry.get("revenueEstimate"),
        "current_price": current_price,
        "day_change_pct": day_change_pct,
        "market_cap": profile.get("marketCapitalization"),
        "past_stats": past_stats,
        "implied_move": implied,
    }


def write_log(target_date: str, previews: list):
    """実行ログを JSON で追記保存 (最新30件のみ保持)"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        # 既存ログ読込
        existing = []
        if LOG_PATH.exists():
            try:
                with open(LOG_PATH, "r", encoding="utf-8") as f:
                    existing = json.load(f) or []
            except (json.JSONDecodeError, OSError):
                existing = []

        # 当該日付の既存エントリは上書き
        existing = [e for e in existing if e.get("target_date") != target_date]

        # 軽量化: 必要情報のみ保存
        entry = {
            "target_date": target_date,
            "executed_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
            "symbol_count": len(previews),
            "symbols": [
                {
                    "symbol": p.get("symbol"),
                    "tier": p.get("tier"),
                    "hour": p.get("hour"),
                    "has_error": "error" in p,
                    "implied_move_pct": (p.get("implied_move") or {}).get("implied_move_pct"),
                }
                for p in previews
            ],
        }
        existing.append(entry)

        # 最新30件のみ保持
        existing = existing[-30:]

        with open(LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        logging.info(f"Log saved: {LOG_PATH}")
    except Exception as ex:
        logging.error(f"Log save failed: {ex}")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # 1. 環境変数確認
    finnhub_key = os.environ.get("FINNHUB_API_KEY")
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    missing = [
        name for name, val in [
            ("FINNHUB_API_KEY", finnhub_key),
            ("TELEGRAM_BOT_TOKEN", tg_token),
            ("TELEGRAM_CHAT_ID", tg_chat_id),
        ] if not val
    ]
    if missing:
        logging.error(f"Missing env vars: {missing}")
        sys.exit(1)

    try:
        # 2. watchlist 読込
        watchlist = load_watchlist(WATCHLIST_PATH)

        # 3. 翌日(ET基準)の日付算出
        #    21:00 JST ≈ 07:00-08:00 ET (冬時間/夏時間)
        #    米市場寄り前に「翌日」のBMO/AMC両方を通知
        now_et = datetime.now(ET)
        target_date = (now_et + timedelta(days=1)).date()
        target_str = target_date.strftime("%Y-%m-%d")
        logging.info(f"Target date (ET+1): {target_str} / Now JST: {datetime.now(JST).isoformat(timespec='seconds')}")

        # 4. Finnhub 決算カレンダー取得
        client = FinnhubClient(finnhub_key)
        all_earnings = client.earnings_calendar(
            from_date=target_str,
            to_date=target_str,
        )
        logging.info(f"Finnhub returned {len(all_earnings)} total earnings entries for {target_str}")

        # 5. watchlist フィルタ
        watchlist_tickers = set(watchlist.keys())
        tomorrow_earnings = [
            e for e in all_earnings
            if (e.get("symbol") or "").upper() in watchlist_tickers
        ]
        logging.info(f"Filtered to {len(tomorrow_earnings)} watchlist tickers")

        if not tomorrow_earnings:
            logging.info(f"No watchlist earnings on {target_str}. Silent exit.")
            write_log(target_str, [])
            return

        # 6. ソート: tier降順 → BMO/AMC → symbol
        hour_order = {"bmo": 0, "amc": 1, "dmh": 2, "": 3}
        tomorrow_earnings.sort(key=lambda e: (
            -watchlist[(e.get("symbol") or "").upper()]["tier"],
            hour_order.get(e.get("hour", ""), 3),
            (e.get("symbol") or "").upper(),
        ))

        # 7. 各銘柄の詳細データ収集 (順次 + スリープ)
        previews = []
        for entry in tomorrow_earnings:
            symbol = (entry.get("symbol") or "").upper()
            if not symbol:
                continue
            tier_info = watchlist[symbol]
            try:
                detail = build_preview(client, symbol, entry, tier_info)
                previews.append(detail)
                logging.info(f"Built preview for {symbol} (tier={tier_info['tier']})")
            except Exception as ex:
                logging.error(f"Failed to build {symbol}: {ex}")
                previews.append({
                    "symbol": symbol,
                    "tier": tier_info["tier"],
                    "hour": entry.get("hour", ""),
                    "error": str(ex),
                })
            sleep(PER_SYMBOL_SLEEP_SEC)

        # 8. メッセージ整形
        messages = build_telegram_message(previews, target_str)
        if not messages:
            logging.info("No messages to send")
            return

        # 9. Telegram 送信
        send_telegram(messages, tg_token, tg_chat_id)

        # 10. ログ保存
        write_log(target_str, previews)

        logging.info(f"Done: sent {len(messages)} message(s), {len(previews)} symbol(s)")

    except Exception as ex:
        err_trace = traceback.format_exc()
        logging.error(f"Fatal error: {ex}\n{err_trace}")
        # エラー通知 (送信失敗しても握りつぶす)
        try:
            send_error_notification(
                f"{type(ex).__name__}: {str(ex)[:500]}",
                tg_token,
                tg_chat_id,
            )
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
