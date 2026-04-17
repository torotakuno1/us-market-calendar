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

# scripts/ 直下の共有モジュール
SCRIPTS_DIR_COMMON = SCRIPT_DIR.parent
if str(SCRIPTS_DIR_COMMON) not in sys.path:
    sys.path.append(str(SCRIPTS_DIR_COMMON))
from position_merger import merge_positions

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


def run_test_ping(finnhub_key: str, tg_token: str, tg_chat_id: str):
    """疎通テスト: Finnhub API + Telegram 配信の両方を確認する短文を送信

    目的:
      - Bot Token の正当性確認
      - Chat ID の正当性確認
      - Finnhub API Key の有効性確認
      - watchlist CSV の読込確認
    トリガー: workflow_dispatch で input `test_ping=true` を指定した時のみ
    """
    logging.info("=== TEST PING MODE ===")

    # watchlist 読込確認 + ポジションマージ
    try:
        watchlist = load_watchlist(WATCHLIST_PATH)
        base_size = len(watchlist)
        pos_added = merge_positions(watchlist)
        watchlist_size = len(watchlist)
        watchlist_status = f"✅ {base_size}銘柄 + positions {pos_added}追加 = {watchlist_size}"
    except Exception as ex:
        watchlist_size = 0
        watchlist_status = f"❌ 読込失敗: {str(ex)[:80]}"
        logging.error(f"Watchlist load failed in test_ping: {ex}")

    # Finnhub 疎通確認 (軽量な /quote コール1回)
    finnhub_status = "❌ 未確認"
    try:
        client = FinnhubClient(finnhub_key)
        quote = client.quote("AAPL")
        aapl_price = quote.get("c")
        if aapl_price and aapl_price > 0:
            finnhub_status = f"✅ OK (AAPL ${aapl_price:.2f})"
        else:
            finnhub_status = f"⚠️ 応答異常 (c={aapl_price})"
    except Exception as ex:
        finnhub_status = f"❌ FAIL: {str(ex)[:80]}"
        logging.error(f"Finnhub test failed: {ex}")

    # 実行環境情報
    now_jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST")
    now_et = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    tomorrow_et = (datetime.now(ET) + timedelta(days=1)).strftime("%Y-%m-%d")
    # Chat ID 末尾4桁のみ表示 (全桁は漏洩防止)
    chat_id_suffix = str(tg_chat_id)[-4:] if tg_chat_id else "????"

    msg_lines = [
        "🔔 <b>疎通テスト</b>",
        "",
        f"<b>実行時刻</b>",
        f"  JST: {now_jst}",
        f"  ET:  {now_et}",
        "",
        f"<b>次回ターゲット日 (ET+1)</b>: {tomorrow_et}",
        "",
        f"<b>watchlist</b>: {watchlist_status}",
        f"<b>Finnhub API</b>: {finnhub_status}",
        f"<b>Telegram 配信</b>: ✅ 本メッセージ到達=OK",
        f"<b>Chat ID 末尾</b>: ...{chat_id_suffix}",
        "",
        "━━━━━━━━━━",
        "本番配信: 21:00 JST 毎日",
        "(翌日決算対象あり時のみ送信)",
    ]
    message = "\n".join(msg_lines)

    try:
        send_telegram([message], tg_token, tg_chat_id)
        logging.info("Test ping sent successfully")
    except Exception as ex:
        logging.error(f"Test ping send failed: {ex}")
        raise


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # 1. 環境変数確認
    finnhub_key = os.environ.get("FINNHUB_API_KEY")
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    test_ping_mode = os.environ.get("TEST_PING", "0") == "1"

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

    # 1.5 疎通テストモード (workflow_dispatch input 指定時のみ)
    if test_ping_mode:
        try:
            run_test_ping(finnhub_key, tg_token, tg_chat_id)
            return
        except Exception as ex:
            logging.error(f"Test ping fatal: {ex}")
            sys.exit(1)

    try:
        # 2. watchlist 読込 + ポジション自動マージ
        watchlist = load_watchlist(WATCHLIST_PATH)
        merge_positions(watchlist)

        # 3. ターゲット期間算出: 「これから24時間以内に発表される決算」
        #    21:00 JST ≈ 07:00-08:00 ET (冬時間/夏時間)
        #    = 当日ET 米市場寄り前時点
        #    通知対象:
        #      (a) 当日ET AMC   : 今夜 引け後発表 (例: NFLX, 発表まで約8h)
        #      (b) 翌日ET BMO   : 明朝 寄り前発表 (発表まで約20-23h)
        #      (c) 翌日ET 時刻未定: 翌日のどこかで発表 (安全側で含める)
        #    除外:
        #      - 当日ET BMO    : 既発表済 (21:00 JST = 08:00 ET で BMO の多くは発表済)
        #      - 翌日ET AMC    : 次回実行 (翌日 21:00 JST) で「今夜 AMC」として拾う
        now_et = datetime.now(ET)
        today_et = now_et.date()
        tomorrow_et = today_et + timedelta(days=1)
        today_str = today_et.strftime("%Y-%m-%d")
        tomorrow_str = tomorrow_et.strftime("%Y-%m-%d")
        logging.info(
            f"Window: today_ET={today_str} AMC + tomorrow_ET={tomorrow_str} BMO/TBD "
            f"| Now JST: {datetime.now(JST).isoformat(timespec='seconds')}"
        )

        # 4. Finnhub 決算カレンダー取得 (2日分)
        client = FinnhubClient(finnhub_key)
        all_earnings = client.earnings_calendar(
            from_date=today_str,
            to_date=tomorrow_str,
        )
        logging.info(f"Finnhub returned {len(all_earnings)} total earnings entries for {today_str}..{tomorrow_str}")

        # 5. watchlist フィルタ + セッション分類
        #    _session 値:
        #      "today_late"     : 今夜AMC  (今日ET引け後発表)
        #      "tomorrow_early" : 明朝BMO  (翌日ET寄り前発表)
        #      "tomorrow_tbd"   : 翌日時刻未定
        watchlist_tickers = set(watchlist.keys())
        upcoming = []
        for e in all_earnings:
            symbol = (e.get("symbol") or "").upper()
            if symbol not in watchlist_tickers:
                continue
            ed = e.get("date")
            eh = (e.get("hour") or "").lower()

            if ed == today_str and eh in ("amc", "dmh"):
                e["_session"] = "today_late"
                upcoming.append(e)
            elif ed == tomorrow_str and eh == "bmo":
                e["_session"] = "tomorrow_early"
                upcoming.append(e)
            elif ed == tomorrow_str and eh in ("", "dmh"):
                e["_session"] = "tomorrow_tbd"
                upcoming.append(e)
            # 当日ET BMO: 既発表 → スキップ
            # 翌日ET AMC: 次回実行で拾う → スキップ

        logging.info(f"Filtered to {len(upcoming)} upcoming watchlist earnings")
        if upcoming:
            session_counts = {}
            for e in upcoming:
                s = e.get("_session", "?")
                session_counts[s] = session_counts.get(s, 0) + 1
            logging.info(f"Session breakdown: {session_counts}")

        if not upcoming:
            logging.info(f"No watchlist earnings in 24h window. Silent exit.")
            write_log(today_str, [])
            return

        # 6. ソート: session順 → tier降順 → symbol
        session_order = {"today_late": 0, "tomorrow_early": 1, "tomorrow_tbd": 2}
        upcoming.sort(key=lambda e: (
            session_order.get(e.get("_session", "tomorrow_tbd"), 9),
            -watchlist[(e.get("symbol") or "").upper()]["tier"],
            (e.get("symbol") or "").upper(),
        ))

        # 7. 各銘柄の詳細データ収集 (順次 + スリープ)
        previews = []
        for entry in upcoming:
            symbol = (entry.get("symbol") or "").upper()
            if not symbol:
                continue
            tier_info = watchlist[symbol]
            try:
                detail = build_preview(client, symbol, entry, tier_info)
                detail["session"] = entry.get("_session", "tomorrow_tbd")
                detail["date"] = entry.get("date")
                previews.append(detail)
                logging.info(f"Built preview for {symbol} (tier={tier_info['tier']}, session={detail['session']})")
            except Exception as ex:
                logging.error(f"Failed to build {symbol}: {ex}")
                previews.append({
                    "symbol": symbol,
                    "tier": tier_info["tier"],
                    "hour": entry.get("hour", ""),
                    "session": entry.get("_session", "tomorrow_tbd"),
                    "date": entry.get("date"),
                    "error": str(ex),
                })
            sleep(PER_SYMBOL_SLEEP_SEC)

        # 8. メッセージ整形 (今日ET と 翌日ET の両日付を渡す)
        messages = build_telegram_message(previews, today_str, tomorrow_str)
        if not messages:
            logging.info("No messages to send")
            return

        # 9. Telegram 送信
        send_telegram(messages, tg_token, tg_chat_id)

        # 10. ログ保存 (当日ET日付をキーにする)
        write_log(today_str, previews)

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
