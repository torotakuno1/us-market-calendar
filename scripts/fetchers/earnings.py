"""
Major US Earnings Fetcher
==========================
S&P500上位 + セクター代表の決算日を取得。
データソース: yfinance（無料）
"""

import json
from datetime import date, datetime, time, timedelta
from typing import Optional

from config import MAJOR_EARNINGS_TICKERS, Importance, make_summary
from utils import Event, et_to_utc, ET, UTC

# セクター分類（iPhone表示で簡潔にグルーピング）
SECTOR_MAP = {
    # Mag7
    "AAPL": "Tech", "MSFT": "Tech", "GOOGL": "Tech", "AMZN": "Cons",
    "META": "Tech", "NVDA": "Semi", "TSLA": "Auto",
    # 半導体
    "TSM": "Semi", "AVGO": "Semi", "AMD": "Semi", "INTC": "Semi",
    "QCOM": "Semi", "TXN": "Semi", "ASML": "Semi", "MU": "Semi",
    # 金融
    "JPM": "Fin", "BAC": "Fin", "GS": "Fin", "MS": "Fin",
    "WFC": "Fin", "C": "Fin", "BLK": "Fin", "SCHW": "Fin",
    # ヘルスケア
    "UNH": "HC", "JNJ": "HC", "LLY": "HC", "PFE": "HC",
    "ABBV": "HC", "MRK": "HC", "TMO": "HC",
    # 消費
    "WMT": "Cons", "COST": "Cons", "HD": "Cons", "MCD": "Cons",
    "NKE": "Cons", "SBUX": "Cons", "TGT": "Cons", "PG": "Cons",
    # エネルギー
    "XOM": "Ene", "CVX": "Ene", "SLB": "Ene", "COP": "Ene", "FCX": "Mat",
    # 工業
    "CAT": "Ind", "BA": "Ind", "GE": "Ind", "UPS": "Ind",
    "HON": "Ind", "RTX": "Ind", "DE": "Ind", "LMT": "Ind",
    # 通信/メディア
    "DIS": "Media", "NFLX": "Media", "CMCSA": "Media", "T": "Tel", "VZ": "Tel",
    # その他
    "V": "Fin", "MA": "Fin", "PYPL": "Fin", "CRM": "Tech",
    "ORCL": "Tech", "ADBE": "Tech", "NOW": "Tech",
    "COIN": "Fin", "SQ": "Fin", "ABNB": "Cons", "UBER": "Cons",
}

# 超大型＝★★★、大型＝★★、それ以外＝★
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


def fetch_earnings(start: date, end: date) -> list[Event]:
    """主要米国企業の決算日を取得。"""
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
            # calendar property gives next earnings date
            cal = tk.calendar
            if cal is None or cal.empty:
                continue

            # yfinance returns earnings date as index or column
            if hasattr(cal, "iloc"):
                # DataFrame format
                if "Earnings Date" in cal.columns:
                    earn_dates = cal["Earnings Date"].tolist()
                elif len(cal) > 0:
                    earn_dates = [cal.iloc[0, 0]] if cal.shape[1] > 0 else []
                else:
                    continue
            else:
                continue

            for ed in earn_dates:
                if isinstance(ed, str):
                    ed = datetime.fromisoformat(ed).date()
                elif isinstance(ed, datetime):
                    ed = ed.date()
                elif not isinstance(ed, date):
                    continue

                if start <= ed <= end:
                    imp = _importance(ticker)
                    sector = SECTOR_MAP.get(ticker, "")
                    # BMO (Before Market Open) / AMC (After Market Close)
                    # yfinance doesn't reliably give this, default to BMO
                    earn_time = time(7, 0)  # pre-market

                    dt_utc = et_to_utc(ed, earn_time)
                    summary = make_summary(imp, f"{ticker} 決算")

                    events.append(Event(
                        name_short=summary,
                        name_full=f"{ticker} Earnings Release ({sector})",
                        dt_utc=dt_utc,
                        category="earnings",
                        importance=int(imp),
                        all_day=True,  # 正確なBMO/AMC不明のため終日表示
                        details={
                            "ticker": ticker,
                            "sector": sector,
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
    """
    Financial Modeling Prep API（代替データソース）。
    環境変数 FMP_API_KEY が必要。
    """
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

            eps_est = item.get("epsEstimated")
            rev_est = item.get("revenueEstimated")

            suffix_parts = []
            if eps_est:
                suffix_parts.append(f"EPS予{eps_est}")

            summary = make_summary(imp, f"{ticker} 決算",
                                   " ".join(suffix_parts) if suffix_parts else "")

            events.append(Event(
                name_short=summary,
                name_full=f"{ticker} Earnings Release ({sector})",
                dt_utc=et_to_utc(ed, time(7, 0)),
                category="earnings",
                importance=int(imp),
                all_day=True,
                details={
                    "ticker": ticker,
                    "sector": sector,
                    "eps_estimate": str(eps_est) if eps_est else "",
                    "revenue_estimate": str(rev_est) if rev_est else "",
                    "source": "FMP API",
                },
                uid_hint=f"EARN:{ticker}:{ed.isoformat()}",
            ))

    except Exception as e:
        print(f"  [earnings/fmp] error: {e}")

    print(f"  [earnings/fmp] {len(events)} earnings from FMP")
    return events
