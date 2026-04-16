"""
Major US Earnings Fetcher
==========================
優先順: Finnhub API > FMP API > yfinance
BMO(寄前) / AMC(引後) を判定して表示。
"""

import os
from datetime import date, datetime, time, timedelta
from typing import Optional

import requests

from config import MAJOR_EARNINGS_TICKERS, Importance, make_summary
from utils import Event, et_to_utc, ET, UTC

# セクター分類
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
    "COIN": "Fin", "SQ": "Fin", "ABNB": "Cons", "UBER": "Cons",
}

TOP_TIER = {"AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM"}
MID_TIER = {
    "BAC", "GS", "MS", "WFC", "TSM", "AVGO", "AMD",
    "UNH", "JNJ", "LLY", "WMT", "XOM", "NFLX",
    "CRM", "V", "MA", "CAT", "BA",
}


def _importance(ticker: str) -> Importance:
    if ticker in TOP_TIER:
        return Importance.HIGH
    if ticker in MID_TIER:
        return Importance.MEDIUM
    return Importance.LOW


def _timing_label(hour_str: str) -> str:
    """Finnhub/FMP の hour フィールドを日本語ラベルに変換。"""
    h = hour_str.lower().strip()
    if h in ("bmo", "before market open", "pre"):
        return "寄前"
    elif h in ("amc", "after market close", "post"):
        return "引後"
    elif h in ("dmh", "during market hours"):
        return ""
    return ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Finnhub（最優先）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_earnings_finnhub(start: date, end: date) -> list[Event]:
    """Finnhub earnings calendar API。BMO/AMC情報付き。"""
    api_key = os.environ.get("FINNHUB_API_KEY", "")
    if not api_key:
        print("  [earnings/finnhub] FINNHUB_API_KEY not set — skipping")
        return []

    events = []
    tickers_set = set(MAJOR_EARNINGS_TICKERS)

    try:
        url = "https://finnhub.io/api/v1/calendar/earnings"
        params = {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "token": api_key,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("earningsCalendar", []):
            ticker = item.get("symbol", "")
            if ticker not in tickers_set:
                continue

            try:
                ed = date.fromisoformat(item["date"])
            except (KeyError, ValueError):
                continue

            if not (start <= ed <= end):
                continue

            imp = _importance(ticker)
            sector = SECTOR_MAP.get(ticker, "")

            # Finnhub provides "hour" field: "bmo", "amc", "dmh"
            hour_str = item.get("hour", "")
            timing = _timing_label(hour_str)
            timing_tag = f" {timing}" if timing else ""

            summary = make_summary(imp, f"{ticker}{timing_tag}")

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
                    "eps_estimate": str(item.get("epsEstimate", "")),
                    "source": "Finnhub",
                },
                uid_hint=f"EARN:{ticker}:{ed.isoformat()}",
            ))

    except Exception as e:
        print(f"  [earnings/finnhub] error: {e}")

    print(f"  [earnings/finnhub] {len(events)} earnings found")
    return events


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FMP API（2番目）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_earnings_fmp(start: date, end: date) -> list[Event]:
    """Financial Modeling Prep API。"""
    api_key = os.environ.get("FMP_API_KEY", "")
    if not api_key:
        print("  [earnings/fmp] FMP_API_KEY not set — skipping")
        return []

    events = []
    tickers_set = set(MAJOR_EARNINGS_TICKERS)

    try:
        url = "https://financialmodelingprep.com/api/v3/earning_calendar"
        params = {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "apikey": api_key,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for item in data:
            ticker = item.get("symbol", "")
            if ticker not in tickers_set:
                continue

            ed = date.fromisoformat(item["date"])
            imp = _importance(ticker)
            sector = SECTOR_MAP.get(ticker, "")

            fmp_time = item.get("time", "")
            timing = _timing_label(fmp_time)
            timing_tag = f" {timing}" if timing else ""

            summary = make_summary(imp, f"{ticker}{timing_tag}")

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
                    "source": "FMP API",
                },
                uid_hint=f"EARN:{ticker}:{ed.isoformat()}",
            ))

    except Exception as e:
        print(f"  [earnings/fmp] error: {e}")

    print(f"  [earnings/fmp] {len(events)} earnings found")
    return events


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# yfinance（最終フォールバック）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 既知のBMO/AMCパターン（yfinance用フォールバック）
KNOWN_BMO = {
    "JPM", "BAC", "GS", "MS", "WFC", "C", "BLK", "SCHW",
    "UNH", "JNJ", "PG", "WMT", "CAT", "BA", "HON", "RTX",
    "LMT", "GE", "UPS", "DE", "MCD", "VZ", "T",
    "PFE", "MRK", "ABBV", "TMO", "LLY",
}
KNOWN_AMC = {
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA",
    "NVDA", "AMD", "INTC", "QCOM", "NFLX", "CRM",
    "ADBE", "NOW", "ORCL", "COIN", "SQ", "ABNB", "UBER",
    "PYPL", "V", "MA", "DIS",
}


def fetch_earnings_yfinance(start: date, end: date) -> list[Event]:
    """yfinance（最終フォールバック）。"""
    events = []
    try:
        import yfinance as yf
    except ImportError:
        print("  [earnings/yfinance] yfinance not installed — skipping")
        return events

    print(f"  [earnings/yfinance] fetching {len(MAJOR_EARNINGS_TICKERS)} tickers...")

    for ticker in MAJOR_EARNINGS_TICKERS:
        try:
            tk = yf.Ticker(ticker)
            cal = tk.calendar
            if cal is None or (hasattr(cal, 'empty') and cal.empty) or (isinstance(cal, dict) and not cal):
                continue

            earn_dates = []
            if isinstance(cal, dict):
                raw = cal.get("Earnings Date") or cal.get("earnings_date") or cal.get("Earnings Dates")
                if raw is None:
                    for v in cal.values():
                        if isinstance(v, (list, tuple)) and len(v) > 0:
                            raw = v
                            break
                if raw is not None:
                    earn_dates = list(raw) if isinstance(raw, (list, tuple)) else [raw]
            elif hasattr(cal, "iloc"):
                if "Earnings Date" in cal.columns:
                    earn_dates = cal["Earnings Date"].tolist()

            for raw_dt in earn_dates:
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
                    imp = _importance(ticker)
                    sector = SECTOR_MAP.get(ticker, "")

                    if ticker in KNOWN_BMO:
                        timing = "寄前"
                    elif ticker in KNOWN_AMC:
                        timing = "引後"
                    else:
                        timing = ""

                    timing_tag = f" {timing}" if timing else ""
                    summary = make_summary(imp, f"{ticker}{timing_tag}")

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
                            "source": "yfinance",
                        },
                        uid_hint=f"EARN:{ticker}:{ed.isoformat()}",
                    ))

        except Exception as e:
            print(f"  [earnings/yfinance] {ticker}: {e}")

    print(f"  [earnings/yfinance] {len(events)} earnings found")
    return events


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 統合エントリポイント
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_earnings(start: date, end: date) -> list[Event]:
    """優先順: Finnhub > FMP > yfinance。最初に成功したソースを使う。"""

    # 1. Finnhub
    events = fetch_earnings_finnhub(start, end)
    if events:
        return events

    # 2. FMP
    events = fetch_earnings_fmp(start, end)
    if events:
        return events

    # 3. yfinance (最終手段)
    return fetch_earnings_yfinance(start, end)
