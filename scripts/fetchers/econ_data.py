"""
Economic Data Release Fetcher
==============================
ルールベースで発表日を算出し、CSV上書きを適用。
"""

import csv
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from config import INDICATORS, IndicatorDef, Importance, make_summary
from utils import (
    Event, et_to_utc,
    nth_weekday, last_weekday_of_month, nth_business_day,
    first_friday, last_friday, every_weekday_in_range,
    calendar_day_adjusted,
)


def _resolve_date(ind: IndicatorDef, year: int, month: int) -> Optional[date]:
    """ルール文字列から発表日を算出。"""
    rule = ind.rule

    if rule == "manual":
        return None  # CSV or FOMC handler

    if rule in ("every_thursday", "every_wednesday"):
        return None  # 週次は別途ハンドリング

    if rule == "first_friday":
        return first_friday(year, month)

    if rule == "last_friday":
        return last_friday(year, month)

    # ── 特殊 bday ルール（汎用より先に判定）──
    if rule == "bday:2_next":
        # 翌月の第2営業日（当月データの翌月発表: JOLTS等）
        next_month = month + 1 if month < 12 else 1
        next_year = year if month < 12 else year + 1
        return nth_business_day(next_year, next_month, 2)

    if rule == "bday:-2_before_nfp":
        # NFP(第1金曜)の2日前 = 水曜 (ADP)
        nfp = first_friday(year, month)
        return nfp - timedelta(days=2)

    # ── 汎用 bday:N ──
    if rule.startswith("bday:"):
        n = int(rule.split(":")[1])
        return nth_business_day(year, month, n)

    # ── 暦日ベース cday:N（土日→直近営業日に調整）──
    if rule.startswith("cday:"):
        n = int(rule.split(":")[1])
        return calendar_day_adjusted(year, month, n)

    if rule.startswith("weekday:"):
        # weekday:DOW:N  (DOW=0..6, N=occurrence)
        parts = rule.split(":")
        dow, n = int(parts[1]), int(parts[2])
        return nth_weekday(year, month, dow, n)

    return None


def _load_overrides(csv_path: Path) -> dict[str, dict]:
    """
    CSV override: key,YYYY-MM-DD,HH:MM(ET),note
    戻り値: { "KEY:YYYY-MM" : {"date": date, "time": time_or_none, "note": str} }
    """
    overrides = {}
    if not csv_path.exists():
        return overrides
    with open(csv_path) as f:
        for row in csv.reader(f):
            if not row or row[0].startswith("#"):
                continue
            key = row[0].strip()
            d = date.fromisoformat(row[1].strip())
            month_key = f"{key}:{d.year}-{d.month:02d}"
            overrides[month_key] = {
                "date": d,
                "note": row[3].strip() if len(row) > 3 else "",
            }
    return overrides


def fetch_econ_data(
    start: date,
    end: date,
    overrides_csv: Optional[Path] = None,
) -> list[Event]:
    """指定期間の米国経済指標イベントを生成。"""
    events = []
    overrides = _load_overrides(overrides_csv) if overrides_csv else {}

    # 月次イベント
    d = date(start.year, start.month, 1)
    while d <= end:
        year, month = d.year, d.month

        for ind in INDICATORS:
            if ind.rule in ("every_thursday", "every_wednesday"):
                continue  # 週次は別処理
            if ind.category != "data":
                continue  # fed等は別fetcher

            # Override チェック
            override_key = f"{ind.key}:{year}-{month:02d}"
            if override_key in overrides:
                release_date = overrides[override_key]["date"]
                extra_note = overrides[override_key].get("note", "")
            else:
                release_date = _resolve_date(ind, year, month)
                extra_note = ""

            if release_date is None:
                continue
            if release_date < start or release_date > end:
                continue

            dt_utc = et_to_utc(release_date, ind.release_time_et)
            summary = make_summary(ind.importance, ind.name_short)

            ev = Event(
                name_short=summary,
                name_full=f"{ind.name_full} ({ind.name_short})",
                dt_utc=dt_utc,
                category="data",
                importance=int(ind.importance),
                details={
                    "source": "Rule-based schedule",
                    "note": extra_note,
                },
                uid_hint=f"{ind.key}:{release_date.isoformat()}",
            )
            events.append(ev)

        # 次月
        if month == 12:
            d = date(year + 1, 1, 1)
        else:
            d = date(year, month + 1, 1)

    # ── 週次イベント ──
    for ind in INDICATORS:
        if ind.rule == "every_thursday":
            dow = 3  # Thursday
        elif ind.rule == "every_wednesday":
            dow = 2  # Wednesday
        else:
            continue

        for release_date in every_weekday_in_range(start, end, dow):
            dt_utc = et_to_utc(release_date, ind.release_time_et)
            summary = make_summary(ind.importance, ind.name_short)

            ev = Event(
                name_short=summary,
                name_full=f"{ind.name_full} ({ind.name_short})",
                dt_utc=dt_utc,
                category="data",
                importance=int(ind.importance),
                details={"source": "Weekly release"},
                uid_hint=f"{ind.key}:{release_date.isoformat()}",
            )
            events.append(ev)

    return events
