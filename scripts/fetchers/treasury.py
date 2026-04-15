"""
Treasury Auction Fetcher
=========================
TreasuryDirect API → 入札スケジュール取得。
フォールバック: ルールベース推定。
"""

import json
from datetime import date, datetime, time, timedelta
from typing import Optional

import requests

from config import Importance, make_summary
from utils import Event, et_to_utc, UTC


TREASURY_API = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query"

# iPhone表示用 tenor → 略称
TENOR_SHORT = {
    "4-Week":  "4W Bill",
    "8-Week":  "8W Bill",
    "13-Week": "13W Bill",
    "17-Week": "17W Bill",
    "26-Week": "26W Bill",
    "52-Week": "52W Bill",
    "2-Year":  "2Y入札",
    "3-Year":  "3Y入札",
    "5-Year":  "5Y入札",
    "7-Year":  "7Y入札",
    "10-Year": "10Y入札",
    "20-Year": "20Y入札",
    "30-Year": "30Y入札",
    "2-Year FRN": "2Y FRN",
    "5-Year TIPS": "5Y TIPS",
    "10-Year TIPS": "10Y TIPS",
    "30-Year TIPS": "30Y TIPS",
}

TENOR_IMPORTANCE = {
    "2-Year": Importance.MEDIUM,
    "5-Year": Importance.MEDIUM,
    "7-Year": Importance.LOW,
    "10-Year": Importance.MEDIUM,
    "20-Year": Importance.LOW,
    "30-Year": Importance.MEDIUM,
}

# Bill は重要度低 → iPhone表示ではスキップ可能
SKIP_BILLS = True  # True = T-Bill入札をカレンダーに含めない


def fetch_treasury_auctions(start: date, end: date) -> list[Event]:
    """TreasuryDirect API から入札スケジュールを取得。"""
    events = []

    try:
        params = {
            "fields": "security_type,security_term,auction_date,issue_date,offering_amt,cusip",
            "filter": f"auction_date:gte:{start.isoformat()},auction_date:lte:{end.isoformat()}",
            "sort": "auction_date",
            "page[size]": 500,
        }
        resp = requests.get(TREASURY_API, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except Exception as e:
        print(f"  [auction] API error: {e} — using fallback")
        return _fallback_auctions(start, end)

    for row in data:
        term = row.get("security_term", "")
        sec_type = row.get("security_type", "")
        auction_date_str = row.get("auction_date", "")

        if not auction_date_str:
            continue

        # T-Billスキップ
        if SKIP_BILLS and sec_type == "Bill":
            continue

        auction_date = date.fromisoformat(auction_date_str)
        short_name = TENOR_SHORT.get(term, f"{term} {sec_type}")
        importance = TENOR_IMPORTANCE.get(term, Importance.LOW)

        # 入札は通常 13:00 ET（Note/Bond）
        auction_time = time(13, 0) if sec_type != "Bill" else time(11, 30)
        dt_utc = et_to_utc(auction_date, auction_time)

        offering = row.get("offering_amt", "")
        offering_str = f"${float(offering)/1e9:.0f}B" if offering else ""
        suffix = offering_str if offering_str else ""

        summary = make_summary(importance, short_name, suffix)

        events.append(Event(
            name_short=summary,
            name_full=f"US Treasury Auction: {term} {sec_type}",
            dt_utc=dt_utc,
            category="auction",
            importance=int(importance),
            details={
                "offering": offering_str,
                "cusip": row.get("cusip", ""),
                "source": "TreasuryDirect",
            },
            uid_hint=f"AUCTION:{term}:{auction_date.isoformat()}",
        ))

    print(f"  [auction] {len(events)} auctions from API")
    return events


def _fallback_auctions(start: date, end: date) -> list[Event]:
    """API不通時の最小フォールバック — 主要入札のみ。"""
    # Note/Bond は月に数回。正確な日程なしでは空を返す方が安全。
    print("  [auction] fallback: no events (API required for accurate dates)")
    return []
