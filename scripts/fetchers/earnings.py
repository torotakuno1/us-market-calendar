"""
Major US Earnings Fetcher
==========================
戦略: Finnhub(ティッカー個別) + yfinance(補完) → マージ
- Finnhub: BMO/AMC正確。ただし未確定の決算は返らない
- yfinance: 日付は多く返るがBMO/AMCなし → 既知パターンで補完
- 両方取れた場合はFinnhubを優先
"""

import os
import time as time_mod
from datetime import date, datetime, time, timedelta
from typing import Optional

import requests

from config import MAJOR_EARNINGS_TICKERS, Importance, make_summary
from utils import Event, et_to_utc, ET, UTC

# ── セクター分類 ──
SECTOR_MAP = {
    "AAPL": "Tech", "MSFT": "Tech", "GOOGL": "Tech", "AMZN": "Cons",
    "META": "Tech", "NVDA": "Semi", "TSLA": "Auto",
    "TSM": "Semi", "AVGO": "Semi", "AMD": "Semi", "INTC": "Semi",
    "QCOM": "Semi", "TXN": "Semi", "ASML": "Semi", "MU": "Semi",
    "JPM": "Fin", "BAC": "Fin", "GS": "Fin", "MS": "Fin",
    "WFC": "Fin", "C": "Fin", "BLK": "Fin", "SCHW": "Fin",
    "UNH": "HC", "JNJ": "HC", "LLY": "HC", "PFE": "HC",
    "ABBV": "HC", "MRK": "HC", "TMO": "HC",
    "WMT": "Cons", "COST": "Cons", "HD": "Cons", "MCD": "Cons",
    "NKE": "Cons", "SBUX": "Cons", "TGT": "Cons", "PG": "Cons",
    "XOM": "Ene", "CVX": "Ene", "SLB": "Ene", "COP": "Ene", "FCX": "Mat",
    "CAT": "Ind", "BA": "Ind", "GE": "Ind", "UPS": "Ind",
    "HON": "Ind", "RTX": "Ind", "DE": "Ind", "LMT": "Ind",
    "DIS": "Media", "NFLX": "Media", "CMCSA": "Media", "T": "Tel", "VZ": "Tel",
    "V": "Fin", "MA": "Fin", "PYPL": "Fin", "CRM": "Tech",
    "ORCL": "Tech", "ADBE": "Tech", "NOW": "Tech",
    "COIN": "Fin", "XYZ": "Fin", "ABNB": "Cons", "UBER": "Cons",
}

TOP_TIER = {"AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM"}
MID_TIER = {
    "BAC", "GS", "MS", "WFC", "TSM", "AVGO", "AMD",
    "UNH", "JNJ", "LLY", "WMT", "XOM", "NFLX",
    "CRM", "V", "MA", "CAT", "BA",
}

# yfinance用 既知BMO/AMCパターン
KNOWN_BMO = {
    "JPM", "BAC", "GS", "MS", "WFC", "C", "BLK", "SCHW",
    "UNH", "JNJ", "PG", "WMT", "CAT", "BA", "HON", "RTX",
    "LMT", "GE", "UPS", "DE", "MCD", "VZ", "T",
    "PFE", "MRK", "ABBV", "TMO", "LLY",
}
KNOWN_AMC = {
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA",
    "NVDA", "AMD", "INTC", "QCOM", "NFLX", "CRM",
    "ADBE", "NOW", "ORCL", "COIN", "XYZ", "ABNB", "UBER",
    "PYPL", "V", "MA", "DIS",
}


def _importance(ticker: str) -> Importance:
    if ticker in TOP_TIER:
        return Importance.HIGH
    if ticker in MID_TIER:
        return Importance.MEDIUM
    return Importance.LOW


def _timing_label(hour_str: str) -> str:
    h = hour_str.lower().strip()
    if h in ("bmo", "before market open", "pre"):
        return "寄前"
    elif h in ("amc", "after market close", "post"):
        return "引後"
    return ""


def _guess_timing(ticker: str) -> str:
    if ticker in KNOWN_BMO:
        return "寄前"
    if ticker in KNOWN_AMC:
        return "引後"
    return ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Finnhub — ティッカー個別呼び出し
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fetch_finnhub_per_ticker(start: date, end: date) -> dict[str, list[dict]]:
    """
    Finnhubをティッカー毎に呼び出し。
    戻り値: { ticker: [{"date": date, "timing": "寄前"/"引後"/""}, ...] }
    """
    api_key = os.environ.get("FINNHUB_API_KEY", "")
    if not api_key:
        print("  [finnhub] SKIP: FINNHUB_API_KEY not set")
        return {}

    result = {}
    found_count = 0

    for i, ticker in enumerate(MAJOR_EARNINGS_TICKERS):
        try:
            url = "https://finnhub.io/api/v1/calendar/earnings"
            params = {
                "symbol": ticker,
                "from": start.isoformat(),
                "to": end.isoformat(),
                "token": api_key,
            }
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            entries = []
            for item in data.get("earningsCalendar", []):
                try:
                    ed = date.fromisoformat(item["date"])
                except (KeyError, ValueError):
                    continue
                if start <= ed <= end:
                    timing = _timing_label(item.get("hour", ""))
                    entries.append({"date": ed, "timing": timing})

            if entries:
                result[ticker] = entries
                found_count += 1

        except requests.HTTPError as e:
            status = e.response.status_code if (hasattr(e, "response") and e.response is not None) else "?"
            if status == 401:
                print(f"  [finnhub] FAIL: 401 Unauthorized — API キー無効の可能性、残りスキップ")
                break
            elif status == 429:
                print(f"  [finnhub] rate limited at {ticker}, sleeping 60s...")
                time_mod.sleep(60)
            else:
                print(f"  [finnhub] FAIL: HTTP {status} at {ticker}")
        except Exception as e:
            print(f"  [finnhub] WARN: {type(e).__name__} at {ticker}: {e}")

        # レート制限対策: 60回/分 → 1.1秒間隔
        if (i + 1) % 55 == 0:
            time_mod.sleep(5)
        else:
            time_mod.sleep(1.1)

    print(f"  [finnhub] {found_count}/{len(MAJOR_EARNINGS_TICKERS)} tickers with earnings")
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# yfinance — 不足分の補完
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fetch_yfinance_dates(start: date, end: date, skip_tickers: set) -> dict[str, list[dict]]:
    """
    Finnhubで取れなかったティッカーのみyfinanceで補完。
    戻り値: { ticker: [{"date": date, "timing": "寄前"/"引後"/""}, ...] }
    """
    try:
        import yfinance as yf
    except ImportError:
        print("  [yfinance] not installed — skipping")
        return {}

    tickers_to_fetch = [t for t in MAJOR_EARNINGS_TICKERS if t not in skip_tickers]
    if not tickers_to_fetch:
        print("  [yfinance] all tickers covered by Finnhub — skipping")
        return {}

    print(f"  [yfinance] fetching {len(tickers_to_fetch)} remaining tickers...")
    result = {}

    for ticker in tickers_to_fetch:
        try:
            tk = yf.Ticker(ticker)
            cal = tk.calendar
            if cal is None or (hasattr(cal, 'empty') and cal.empty) or (isinstance(cal, dict) and not cal):
                continue

            raw_dates = []
            if isinstance(cal, dict):
                raw = cal.get("Earnings Date") or cal.get("earnings_date") or cal.get("Earnings Dates")
                if raw is None:
                    for v in cal.values():
                        if isinstance(v, (list, tuple)) and len(v) > 0:
                            raw = v
                            break
                if raw is not None:
                    raw_dates = list(raw) if isinstance(raw, (list, tuple)) else [raw]
            elif hasattr(cal, "iloc"):
                if "Earnings Date" in cal.columns:
                    raw_dates = cal["Earnings Date"].tolist()

            entries = []
            for raw_dt in raw_dates:
                try:
                    if isinstance(raw_dt, str):
                        ed = datetime.fromisoformat(raw_dt.replace("Z", "")).date()
                    elif hasattr(raw_dt, 'date') and callable(raw_dt.date):
                        ed = raw_dt.date()
                    elif isinstance(raw_dt, date):
                        ed = raw_dt
                    else:
                        continue
                except Exception:
                    continue

                if start <= ed <= end:
                    timing = _guess_timing(ticker)
                    entries.append({"date": ed, "timing": timing})

            if entries:
                result[ticker] = entries

        except Exception:
            continue

    print(f"  [yfinance] {len(result)} tickers with earnings")
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 統合エントリポイント
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_earnings(start: date, end: date) -> list[Event]:
    """
    Finnhub(ティッカー個別) + yfinance(補完) → マージしてイベント生成。
    同一(ticker, date)はFinnhub優先。
    """

    # Step 1: Finnhub（BMO/AMC正確）
    finnhub_data = _fetch_finnhub_per_ticker(start, end)

    # Step 2: yfinance（Finnhubで取れなかった分を補完）
    missing_count = len([t for t in MAJOR_EARNINGS_TICKERS if t not in finnhub_data])
    if not finnhub_data:
        print(f"  [yfinance] fallback: Finnhub 0件のため全 {missing_count} ティッカーをyfinanceで取得")
    elif missing_count > 0:
        print(f"  [yfinance] supplement: {missing_count} ティッカーがFinnhub未取得のため補完")
    yf_data = _fetch_yfinance_dates(start, end, skip_tickers=set(finnhub_data.keys()))

    # Step 3: マージ
    merged: dict[str, list[dict]] = {}
    for ticker, entries in finnhub_data.items():
        merged[ticker] = entries
    for ticker, entries in yf_data.items():
        if ticker not in merged:
            merged[ticker] = entries

    # Step 4: Event生成
    events = []
    for ticker, entries in merged.items():
        for entry in entries:
            ed = entry["date"]
            timing = entry["timing"]
            imp = _importance(ticker)
            sector = SECTOR_MAP.get(ticker, "")

            timing_tag = f" {timing}" if timing else ""
            summary = make_summary(imp, f"{ticker}{timing_tag}")

            source = "Finnhub" if ticker in finnhub_data else "yfinance"

            events.append(Event(
                name_short=summary,
                name_full=f"{ticker} Earnings ({sector}) [{timing or 'TBD'}]",
                dt_utc=et_to_utc(ed, time(7, 0)),
                category="earnings",
                importance=int(imp),
                all_day=True,
                details={
                    "ticker": ticker,
                    "sector": sector,
                    "timing": timing or "未定",
                    "source": source,
                },
                uid_hint=f"EARN:{ticker}:{ed.isoformat()}",
            ))

    total_finnhub = sum(len(v) for v in finnhub_data.values())
    total_yf = sum(len(v) for v in yf_data.values())
    print(f"  [earnings] merged: {total_finnhub} from Finnhub + {total_yf} from yfinance = {len(events)} total")

    return events
