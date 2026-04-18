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

from config import Importance, make_summary, QUARTERLY_REFUNDING_DATES
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

    # ── Quarterly Refunding イベント（静的リスト） ──
    refunding_events = _build_refunding_events(start, end)
    events.extend(refunding_events)
    print(f"  [auction] {len(refunding_events)} refunding events (static list)")

    return events


def _fallback_auctions(start: date, end: date) -> list[Event]:
    """API不通時の最小フォールバック — 主要入札のみ。"""
    # Note/Bond は月に数回。正確な日程なしでは空を返す方が安全。
    print("  [auction] fallback: no events (API required for accurate dates)")
    return []


def _build_refunding_events(start: date, end: date) -> list[Event]:
    """
    Treasury Quarterly Refunding 発表イベントを生成する。

    各 Refunding サイクルで2イベント出力:
      1. Financing Estimates（月曜 15:00 ET、★★）— 借入額見積り
      2. Refunding Announcement（水曜 08:30 ET、★★★）— 入札スケジュール & Policy Statement

    出典: home.treasury.gov/policy-issues/financing-the-government/quarterly-refunding
    """
    events = []

    for entry in QUARTERLY_REFUNDING_DATES:
        estimates_str = entry.get("estimates", "")
        refunding_str = entry.get("refunding", "")

        # ── 1. Financing Estimates（月曜 15:00 ET、★★） ──
        if estimates_str:
            try:
                estimates_date = date.fromisoformat(estimates_str)
                if start <= estimates_date <= end:
                    dt_utc = et_to_utc(estimates_date, time(15, 0))
                    events.append(Event(
                        name_short=make_summary(Importance.MEDIUM, "借入額見積り"),
                        name_full="Treasury Financing Estimates (Quarterly Refunding先行)",
                        dt_utc=dt_utc,
                        category="auction",
                        importance=int(Importance.MEDIUM),
                        details={
                            "source": "home.treasury.gov/quarterly-refunding",
                            "note": "翌々水曜 Refunding 発表の2日前、借入規模を先行公開",
                        },
                        uid_hint=f"TREAS_ESTIMATES:{estimates_date.isoformat()}",
                    ))
            except ValueError:
                print(f"  [auction] invalid estimates date: {estimates_str}")

        # ── 2. Refunding Announcement（水曜 08:30 ET、★★★） ──
        if refunding_str:
            try:
                refunding_date = date.fromisoformat(refunding_str)
                if start <= refunding_date <= end:
                    dt_utc = et_to_utc(refunding_date, time(8, 30))
                    events.append(Event(
                        name_short=make_summary(Importance.HIGH, "四半期入札方針"),
                        name_full="Treasury Quarterly Refunding Announcement",
                        dt_utc=dt_utc,
                        category="auction",
                        importance=int(Importance.HIGH),
                        details={
                            "source": "home.treasury.gov/quarterly-refunding",
                            "note": "四半期の借入計画・入札方針・Buyback Schedule 等を一括公表。長期金利に直接影響する ★★★ イベント",
                        },
                        uid_hint=f"TREAS_REFUNDING:{refunding_date.isoformat()}",
                    ))
            except ValueError:
                print(f"  [auction] invalid refunding date: {refunding_str}")

    return events
