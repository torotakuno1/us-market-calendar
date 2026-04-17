#!/usr/bin/env python3
"""決算サプライズ通知 メインスクリプト

実行タイミング: 07:00 JST (GitHub Actions cron '52 21 * * *')
処理内容:
  1. watchlist.csv 読込
  2. チェック対象日付を算出 (前日ET ~ 当日ET)
  3. Finnhub から決算結果取得
  4. epsActual 判定 → サプライズ計算
  5. HTML整形 → Telegram送信
  6. ログ保存
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

# パス解決
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent

# サプライズ固有モジュールを先に import (message_builder 名前衝突回避)
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from surprise_checker import check_surprise
from message_builder import build_surprise_message

# 既存 earnings_preview のモジュールを import (名前衝突しない finnhub_client, telegram_sender)
PREVIEW_DIR = REPO_ROOT / "scripts" / "earnings_preview"
if str(PREVIEW_DIR) not in sys.path:
    sys.path.append(str(PREVIEW_DIR))  # append で後方に追加 (衝突防止)

from finnhub_client import FinnhubClient
from telegram_sender import send_telegram, send_error_notification

# scripts/ 直下の共有モジュール
SCRIPTS_COMMON_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_COMMON_DIR) not in sys.path:
    sys.path.append(str(SCRIPTS_COMMON_DIR))
from position_merger import merge_positions

# タイムゾーン
ET = ZoneInfo("America/New_York")
JST = ZoneInfo("Asia/Tokyo")

# パス
WATCHLIST_PATH = REPO_ROOT / "data" / "earnings_watchlist.csv"
PREVIEW_LOG_PATH = REPO_ROOT / "docs" / "earnings_preview_log.json"
LOG_DIR = REPO_ROOT / "docs"
LOG_PATH = LOG_DIR / "earnings_surprise_log.json"

# Finnhub レートリミット対策
PER_SYMBOL_SLEEP_SEC = 0.25


def load_watchlist(csv_path: Path) -> dict:
    """watchlist CSV を読み込み {ticker: {tier, sector, ...}} を返す"""
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
            }
    logging.info(f"Loaded {len(watchlist)} tickers from watchlist")
    return watchlist


def load_preview_log() -> dict:
    """プレビューログから {symbol: implied_move_pct} を読み込む
    最新エントリからシンボルごとの implied_move を取得"""
    result = {}
    if not PREVIEW_LOG_PATH.exists():
        logging.info("No preview log found, skipping implied move comparison")
        return result

    try:
        with open(PREVIEW_LOG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or []

        # 全エントリからシンボル → implied_move_pct を構築 (新しいものが上書き)
        for entry in data:
            for sym_info in entry.get("symbols", []):
                sym = sym_info.get("symbol")
                imp = sym_info.get("implied_move_pct")
                if sym and imp is not None:
                    result[sym] = imp

        logging.info(f"Loaded implied_move data for {len(result)} symbols from preview log")
    except Exception as ex:
        logging.warning(f"Failed to load preview log: {ex}")

    return result


def write_log(date_str: str, results: list, pending: list):
    """実行ログを JSON で保存 (最新30件のみ保持)"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        existing = []
        if LOG_PATH.exists():
            try:
                with open(LOG_PATH, "r", encoding="utf-8") as f:
                    existing = json.load(f) or []
            except (json.JSONDecodeError, OSError):
                existing = []

        # 同日のエントリは上書き
        existing = [e for e in existing if e.get("date") != date_str]

        entry = {
            "date": date_str,
            "executed_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
            "results_count": len(results),
            "pending_count": len(pending),
            "results": [
                {
                    "symbol": r.get("symbol"),
                    "tier": r.get("tier"),
                    "eps_verdict": (r.get("surprise") or {}).get("eps_verdict"),
                    "eps_surprise_pct": (r.get("surprise") or {}).get("eps_surprise_pct"),
                    "price_change_pct": (r.get("surprise") or {}).get("price_change_pct"),
                    "vs_implied": (r.get("surprise") or {}).get("vs_implied"),
                }
                for r in results
            ],
        }
        existing.append(entry)
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

    # 1. 環境変数 (プレビューと同じ Secrets)
    finnhub_key = os.environ.get("FINNHUB_API_KEY")
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    test_mode = os.environ.get("TEST_MODE", "0") == "1"

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
        # 2. watchlist 読込 + ポジション自動マージ
        watchlist = load_watchlist(WATCHLIST_PATH)
        merge_positions(watchlist)

        # 3. チェック対象の日付範囲を算出
        #    07:00 JST ≈ 17:00-18:00 ET 前日
        #    チェック対象:
        #      - 前日ET の AMC + BMO (既発表のはず)
        #      - 当日ET の BMO (07:00 JST = 18:00 ET前日 なので当日BMOはまだだが、
        #        test_mode 用に含める)
        now_et = datetime.now(ET)
        yesterday_et = (now_et - timedelta(days=1)).date()
        today_et = now_et.date()

        if test_mode:
            # テストモード: 過去7日分をスキャン
            from_date = (now_et - timedelta(days=7)).date()
            to_date = today_et
            logging.info(f"=== TEST MODE: scanning {from_date} to {to_date} ===")
        else:
            from_date = yesterday_et
            to_date = today_et

        from_str = from_date.strftime("%Y-%m-%d")
        to_str = to_date.strftime("%Y-%m-%d")
        yesterday_str = yesterday_et.strftime("%Y-%m-%d")
        logging.info(
            f"Checking earnings results: {from_str} to {to_str} "
            f"| Now JST: {datetime.now(JST).isoformat(timespec='seconds')}"
        )

        # 4. Finnhub 決算カレンダー取得
        client = FinnhubClient(finnhub_key)
        all_earnings = client.earnings_calendar(
            from_date=from_str,
            to_date=to_str,
        )
        logging.info(f"Finnhub returned {len(all_earnings)} total entries for {from_str}..{to_str}")

        # 5. watchlist フィルタ
        watchlist_tickers = set(watchlist.keys())
        matched = [
            e for e in all_earnings
            if (e.get("symbol") or "").upper() in watchlist_tickers
        ]
        logging.info(f"Matched {len(matched)} watchlist tickers")

        if not matched:
            logging.info("No watchlist earnings in range. Silent exit.")
            write_log(yesterday_str, [], [])
            return

        # 6. epsActual の有無で分類
        results_raw = []   # 結果判明
        pending_raw = []   # 未判明
        for e in matched:
            if e.get("epsActual") is not None:
                results_raw.append(e)
            else:
                pending_raw.append(e)

        logging.info(f"Results: {len(results_raw)} confirmed, {len(pending_raw)} pending")

        if not results_raw and not pending_raw:
            logging.info("Nothing to report. Silent exit.")
            return

        # 結果なし (全部 pending) かつ test_mode でない → サイレント終了
        # (Finnhub 反映遅延の可能性。pending だけ送っても情報価値低い)
        if not results_raw and not test_mode:
            logging.info("All earnings still pending (Finnhub lag?). Silent exit.")
            write_log(yesterday_str, [], pending_raw)
            return

        # 7. プレビューログから織込変動を読込
        implied_map = load_preview_log()

        # 8. 各銘柄の追加情報取得 + サプライズ計算
        results = []
        for entry in results_raw:
            symbol = (entry.get("symbol") or "").upper()
            tier_info = watchlist.get(symbol, {"tier": 1, "sector": ""})
            try:
                quote = client.quote(symbol)
                implied_pct = implied_map.get(symbol)

                surprise = check_surprise(entry, quote, implied_pct)

                results.append({
                    "symbol": symbol,
                    "company_name": symbol,  # profile は省略 (API コール節約)
                    "tier": tier_info["tier"],
                    "hour": entry.get("hour", ""),
                    "date": entry.get("date"),
                    "surprise": surprise,
                })
                logging.info(
                    f"Checked {symbol}: EPS {surprise.get('eps_verdict')} "
                    f"({surprise.get('eps_surprise_pct')}%), "
                    f"price {surprise.get('price_change_pct')}%"
                )
            except Exception as ex:
                logging.error(f"Failed to check {symbol}: {ex}")
                results.append({
                    "symbol": symbol,
                    "tier": tier_info["tier"],
                    "error": str(ex),
                })
            sleep(PER_SYMBOL_SLEEP_SEC)

        # pending リストも整形用に tier 情報を付与
        pending = []
        for entry in pending_raw:
            symbol = (entry.get("symbol") or "").upper()
            tier_info = watchlist.get(symbol, {"tier": 1})
            pending.append({
                "symbol": symbol,
                "tier": tier_info["tier"],
                "hour": entry.get("hour", ""),
            })

        # 9. メッセージ整形
        messages = build_surprise_message(results, pending, yesterday_str)
        if not messages:
            logging.info("No messages to send")
            return

        # 10. Telegram 送信
        send_telegram(messages, tg_token, tg_chat_id)

        # 11. ログ保存
        write_log(yesterday_str, results, pending)

        logging.info(f"Done: sent {len(messages)} message(s), {len(results)} result(s)")

    except Exception as ex:
        err_trace = traceback.format_exc()
        logging.error(f"Fatal error: {ex}\n{err_trace}")
        try:
            send_error_notification(
                f"サプライズ通知エラー: {type(ex).__name__}: {str(ex)[:500]}",
                tg_token,
                tg_chat_id,
            )
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
