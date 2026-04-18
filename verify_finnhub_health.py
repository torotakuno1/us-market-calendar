#!/usr/bin/env python3
"""
verify_finnhub_health.py — Finnhub API 生死確認 (v8.3.3)

Usage:
    python verify_finnhub_health.py
"""
import os
import sys
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))


def check_env_vars():
    print("━" * 60)
    print("環境変数チェック")
    print("━" * 60)

    keys = ["FINNHUB_API_KEY", "FMP_API_KEY", "FRED_API_KEY"]
    all_set = True
    for k in keys:
        val = os.environ.get(k, "")
        if val:
            masked = val[:4] + "*" * (len(val) - 4) if len(val) > 4 else "****"
            print(f"  [OK]  {k} = {masked}")
        else:
            print(f"  [NG]  {k} = (未設定)")
            if k == "FINNHUB_API_KEY":
                all_set = False
    print()
    return all_set


def test_finnhub_direct():
    print("━" * 60)
    print("Finnhub API 直接疎通テスト")
    print("━" * 60)

    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        print("  [SKIP] FINNHUB_API_KEY 未設定のためスキップ")
        print()
        return False

    try:
        import requests
        url = "https://finnhub.io/api/v1/calendar/earnings"
        params = {
            "from": date.today().isoformat(),
            "to": (date.today() + timedelta(days=60)).isoformat(),
            "symbol": "AAPL",
            "token": api_key,
        }
        r = requests.get(url, params=params, timeout=10)
        print(f"  HTTP Status: {r.status_code}")

        if r.status_code == 200:
            data = r.json()
            earnings = data.get("earningsCalendar", [])
            print(f"  [OK]  AAPL の決算 {len(earnings)} 件取得")
            for e in earnings[:3]:
                print(f"        {e.get('date')} {e.get('symbol')} EPS予想={e.get('epsEstimate')}")
            return True
        elif r.status_code == 401:
            print(f"  [NG]  401 Unauthorized — API キーが無効")
            print(f"        Response: {r.text[:200]}")
            return False
        elif r.status_code == 429:
            print(f"  [NG]  429 Rate Limit — 無料枠超過")
            return False
        else:
            print(f"  [NG]  予期しないステータス: {r.status_code}")
            print(f"        Response: {r.text[:200]}")
            return False
    except Exception as e:
        print(f"  [NG]  例外: {type(e).__name__}: {e}")
        return False
    finally:
        print()


def test_earnings_fetcher():
    print("━" * 60)
    print("earnings.py fetcher 経由テスト")
    print("━" * 60)

    try:
        from fetchers.earnings import fetch_earnings
        start = date.today()
        end = date.today() + timedelta(days=30)

        print(f"  期間: {start} → {end}")
        print(f"  fetch_earnings() 実行中...")
        events = fetch_earnings(start, end)

        print(f"  [取得結果] {len(events)} events")
        if events:
            for ev in events[:3]:
                src = ev.details.get("source", "?") if hasattr(ev, "details") else "?"
                print(f"        {ev.dt_utc.date()} {ev.name_short} (source: {src})")

            sources = {}
            for ev in events:
                src = ev.details.get("source", "unknown") if hasattr(ev, "details") else "unknown"
                sources[src] = sources.get(src, 0) + 1
            print(f"  [ソース別] {sources}")
        return len(events) > 0
    except Exception as e:
        import traceback
        print(f"  [NG]  例外: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False
    finally:
        print()


def main():
    env_ok = check_env_vars()
    direct_ok = test_finnhub_direct()
    fetcher_ok = test_earnings_fetcher()

    print("━" * 60)
    print("総合判定")
    print("━" * 60)
    print(f"  環境変数:     {'OK' if env_ok else 'NG (FINNHUB_API_KEY 未設定)'}")
    print(f"  直接 API:     {'OK' if direct_ok else 'NG / SKIP'}")
    print(f"  fetcher 経由: {'OK' if fetcher_ok else 'NG'}")

    if not env_ok:
        print()
        print("  → ローカル環境変数に FINNHUB_API_KEY を設定する必要あり")
        print("    CMD 例:  set FINNHUB_API_KEY=xxxxxxxx")
        print("    永続化:  システム環境変数に登録、または .env ファイル運用")
    elif env_ok and not direct_ok:
        print()
        print("  → API キー自体が無効/失効の可能性。Finnhub ダッシュボードで再発行検討")
    elif direct_ok and not fetcher_ok:
        print()
        print("  → 直接は動くが fetcher で失敗 = earnings.py のロジック問題")

    return 0 if (env_ok and direct_ok and fetcher_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
