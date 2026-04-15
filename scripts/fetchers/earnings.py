"""
Major US Earnings Fetcher
==========================
S&P500上位 + セクター代表の決算日を取得。
BMO(寄前) / AMC(引後) を判定して表示。
"""

import json
from datetime import date, datetime, time, timedelta
from typing import Optional

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

# 既知のBMO/AMCパターン（フォールバック用）
# BMO = Before Market Open (寄前), AMC = After Market Close (引後)
KNOWN_BMO = {
    "JPM", "BAC", "GS", "MS", "WFC", "C", "BLK", "SCHW",  # 銀行は基本寄前
    "UNH", "JNJ", "PG", "WMT", "CAT", "BA", "HON", "RTX",
    "LMT", "GE", "UPS", "DE", "MCD", "VZ", "T",
    "PFE", "MRK", "ABBV", "TMO", "LLY",
}
KNOWN_AMC = {
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA",  # ビッグテック引後
    "NVDA", "AMD", "INTC", "QCOM", "NFLX", "CRM",
    "ADBE", "NOW", "ORCL", "COIN", "SQ", "ABNB", "UBER",
    "PYPL", "V", "MA", "DIS",
}


def _importance(ticker: str) -> Importance:
    if ticker in TOP_TIER:
        return Importance.HIGH
    if ticker in MID_TIER:
        return Importance.MEDIUM
    return Importance.LOW


def _detect_bmo_amc(ticker: str, earn_datetime=None) -> str:
    """
    BMO/AMC を判定。
    戻り値: "寄前" or "引後" or ""
    """
    # 1. タイムスタンプから判定（yfinance が時刻情報を持っている場合）
    if earn_datetime is not None and hasattr(earn_datetime, 'hour'):
        hour = earn_datetime.hour
        if 0 <= hour <= 9:  # 早朝〜9時台 → 寄前
            return "寄前"
        elif hour >= 16:  # 16時以降 → 引後
            return "引後"
        elif 10 <= hour <= 12:  # 午前中 → 寄前寄り
            return "寄前"

    # 2. 既知パターンからフォールバック
    if ticker in KNOWN_BMO:
        return "寄前"
    if ticker in KNOWN_AMC:
        return "引後"

    return ""


def fetch_earnings(start: date, end: date) -> list[Event]:
    """主要米国企業の決算日を取得（BMO/AMC判定付き）。"""
    events = []

    try:
        import yfinance as yf
    except ImportError:
        print("  [earnings] yfinance not installed — skipping")
        return events

    print(f"  [earnings] fetching {len(MAJOR_EARNINGS_TICKERS)} tickers...")

    for ticker in MAJOR_EARNINGS_TICKERS:
        try:
            tk = yf.Ticker(ticker)
            cal = tk.calendar
            if cal is None or (hasattr(cal, 'empty') and cal.empty) or (isinstance(cal, dict) and not cal):
                continue

            # yfinance returns dict or DataFrame depending on version
            earn_dates = []
            raw_datetimes = []  # 時刻情報保持用

            if isinstance(cal, dict):
                raw = cal.get("Earnings Date") or cal.get("earnings_date") or cal.get("Earnings Dates")
                if raw is None:
                    for v in cal.values():
                        if isinstance(v, (list, tuple)) and len(v) > 0:
                            raw = v
                            break
                if raw is not None:
                    if isinstance(raw, (list, tuple)):
                        raw_datetimes = list(raw)
                    else:
                        raw_datetimes = [raw]
            elif hasattr(cal, "iloc"):
                if "Earnings Date" in cal.columns:
                    raw_datetimes = cal["Earnings Date"].tolist()
                elif len(cal) > 0:
                    raw_datetimes = [cal.iloc[0, 0]] if cal.shape[1] > 0 else []

            if not raw_datetimes:
                continue

            for raw_dt in raw_datetimes:
                try:
                    earn_dt_raw = raw_dt  # 元のオブジェクト保持
                    if isinstance(raw_dt, str):
                        ed = datetime.fromisoformat(raw_dt.replace("Z", "")).date()
                    elif hasattr(raw_dt, 'date') and callable(raw_dt.date):
                        ed = raw_dt.date()
                    elif isinstance(raw_dt, date):
                        ed = raw_dt
                        earn_dt_raw = None
                    else:
                        continue
                except Exception:
                    continue

                if start <= ed <= end:
                    imp = _importance(ticker)
                    sector = SECTOR_MAP.get(ticker, "")

                    # BMO/AMC判定
                    bmo_amc = _detect_bmo_amc(ticker, earn_dt_raw)
                    bmo_amc_tag = f" {bmo_amc}" if bmo_amc else ""

                    # 寄前→終日表示だが早朝マーク、引後→引け後マーク
                    summary = make_summary(imp, f"{ticker}{bmo_amc_tag}")

                    events.append(Event(
                        name_short=summary,
                        name_full=f"{ticker} Earnings Release ({sector}) [{bmo_amc or 'TBD'}]",
                        dt_utc=et_to_utc(ed, time(7, 0)),
                        category="earnings",
                        importance=int(imp),
                        all_day=True,
                        details={
                            "ticker": ticker,
                            "sector": sector,
                            "timing": bmo_amc or "未定",
                            "source": "yfinance",
                        },
                        uid_hint=f"EARN:{ticker}:{ed.isoformat()}",
                    ))

        except Exception as e:
            print(f"  [earnings] {ticker}: {e}")
            continue

    print(f"  [earnings] {len(events)} earnings events found")
    return events


def fetch_earnings_from_fmp(start: date, end: date, api_key: str = "") -> list[Event]:
    """Financial Modeling Prep API（BMO/AMC情報付き）。"""
    import requests

    if not api_key:
        import os
        api_key = os.environ.get("FMP_API_KEY", "")
    if not api_key:
        print("  [earnings/fmp] no API key — skipping")
        return []

    events = []
    tickers_set = set(MAJOR_EARNINGS_TICKERS)
    url = "https://financialmodelingprep.com/api/v3/earning_calendar"

    try:
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

            # FMP provides "amc" / "bmo" / "dmh" (during market hours)
            fmp_time = item.get("time", "")
            if fmp_time == "bmo":
                bmo_amc = "寄前"
            elif fmp_time == "amc":
                bmo_amc = "引後"
            else:
                bmo_amc = _detect_bmo_amc(ticker)  # フォールバック

            bmo_amc_tag = f" {bmo_amc}" if bmo_amc else ""
            summary = make_summary(imp, f"{ticker}{bmo_amc_tag}")

            events.append(Event(
                name_short=summary,
                name_full=f"{ticker} Earnings Release ({sector}) [{bmo_amc or 'TBD'}]",
                dt_utc=et_to_utc(ed, time(7, 0)),
                category="earnings",
                importance=int(imp),
                all_day=True,
                details={
                    "ticker": ticker,
                    "sector": sector,
                    "timing": bmo_amc or "未定",
                    "source": "FMP API",
                },
                uid_hint=f"EARN:{ticker}:{ed.isoformat()}",
            ))

    except Exception as e:
        print(f"  [earnings/fmp] error: {e}")

    print(f"  [earnings/fmp] {len(events)} earnings from FMP")
    return events
