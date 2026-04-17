"""
Economic Data Release Fetcher
==============================
優先順: CSV上書き > BLS iCal > FRED API > ルールベース推定
"""

import csv
import os
from datetime import date, timedelta, time
from pathlib import Path
from typing import Optional

import requests

from config import (
    INDICATORS, IndicatorDef, Importance, make_summary,
    FRED_RELEASE_IDS, FRED_MONTH_FILTERS,
)
try:
    from fetchers.omb_pfei import fetch_pfei_dates
except ImportError:
    try:
        from omb_pfei import fetch_pfei_dates
    except ImportError:
        fetch_pfei_dates = None
from utils import (
    Event, et_to_utc,
    nth_weekday, last_weekday_of_month, nth_business_day,
    first_friday, last_friday, every_weekday_in_range,
    calendar_day_adjusted,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FRED API 経由で公式リリース日を取得
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fetch_fred_dates(start: date, end: date) -> dict[str, list[date]]:
    """
    FRED API から各リリースの公式発表日を取得。
    戻り値: { indicator_key: [date, date, ...] }
    """
    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        print("  [fred] FRED_API_KEY not set — skipping FRED")
        return {}

    result: dict[str, list[date]] = {}
    seen_releases: dict[int, list[date]] = {}  # release_id → dates キャッシュ

    for ind_key, release_id in FRED_RELEASE_IDS.items():
        if release_id in seen_releases:
            result[ind_key] = seen_releases[release_id]
            continue

        try:
            url = "https://api.stlouisfed.org/fred/release/dates"
            params = {
                "release_id": release_id,
                "api_key": api_key,
                "file_type": "json",
                "include_release_dates_with_no_data": "true",
                "realtime_start": start.isoformat(),
                "realtime_end": end.isoformat(),
                "sort_order": "asc",
            }
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            dates = []
            for item in data.get("release_dates", []):
                d = date.fromisoformat(item["date"])
                if start <= d <= end:
                    dates.append(d)

            seen_releases[release_id] = dates
            result[ind_key] = dates

        except Exception as e:
            print(f"  [fred] release {release_id} ({ind_key}): {e}")
            seen_releases[release_id] = []
            result[ind_key] = []

    found = sum(1 for v in result.values() if v)
    print(f"  [fred] {found}/{len(FRED_RELEASE_IDS)} releases with dates")
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BLS iCal フィード解析
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BLS_ICAL_URL = "https://www.bls.gov/schedule/news_release/bls.ics"

# BLS iCal SUMMARY → config key マッピング
BLS_SUMMARY_MAP = {
    "employment situation": "NFP",
    "consumer price index": "CPI",
    "producer price index": "PPI",
    "real earnings": None,  # skip
    "employer costs": None,
    "job openings": "JOLTS",
    "import": "IMPORT_PX",
    "productivity": None,
}


def _fetch_bls_ical(start: date, end: date) -> dict[str, list[date]]:
    """
    BLS公式iCalフィードから発表日を取得。
    戻り値: { indicator_key: [date, ...] }
    """
    result: dict[str, list[date]] = {}

    try:
        resp = requests.get(BLS_ICAL_URL, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (compatible; US-Market-Calendar/1.0; +https://github.com/torotakuno1/us-market-calendar)",
            "Accept": "text/calendar, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        resp.raise_for_status()

        from icalendar import Calendar
        cal = Calendar.from_ical(resp.content)

        for comp in cal.walk():
            if comp.name != "VEVENT":
                continue

            summary = str(comp.get("summary", "")).lower()
            dtstart = comp.get("dtstart")
            if not dtstart:
                continue

            dt = dtstart.dt
            if hasattr(dt, "date"):
                d = dt.date()
            elif isinstance(dt, date):
                d = dt
            else:
                continue

            if not (start <= d <= end):
                continue

            # BLS SUMMARY → config key
            matched_key = None
            for pattern, key in BLS_SUMMARY_MAP.items():
                if pattern in summary:
                    matched_key = key
                    break

            if matched_key:
                result.setdefault(matched_key, []).append(d)

    except Exception as e:
        if "403" in str(e):
            print(f"  [bls_ical] skipped (403 from Akamai; using FRED instead)")
        else:
            print(f"  [bls_ical] error: {e}")

    print(f"  [bls_ical] {sum(len(v) for v in result.values())} events from BLS iCal")
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ルールベース推定（フォールバック）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _resolve_date(ind: IndicatorDef, year: int, month: int) -> Optional[date]:
    """ルール文字列から発表日を算出。"""
    rule = ind.rule

    if rule == "manual":
        return None
    if rule in ("every_thursday", "every_wednesday"):
        return None
    if rule == "first_friday":
        return first_friday(year, month)
    if rule == "last_friday":
        return last_friday(year, month)

    if rule == "bday:2_next":
        next_month = month + 1 if month < 12 else 1
        next_year = year if month < 12 else year + 1
        return nth_business_day(next_year, next_month, 2)

    if rule == "bday:-2_before_nfp":
        nfp = first_friday(year, month)
        return nfp - timedelta(days=2)

    if rule.startswith("bday:"):
        n = int(rule.split(":")[1])
        return nth_business_day(year, month, n)

    if rule.startswith("cday:"):
        n = int(rule.split(":")[1])
        return calendar_day_adjusted(year, month, n)

    if rule.startswith("weekday:"):
        parts = rule.split(":")
        dow, n = int(parts[1]), int(parts[2])
        return nth_weekday(year, month, dow, n)

    return None


def _load_overrides(csv_path: Path) -> dict[str, dict]:
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# メイン関数
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_econ_data(
    start: date,
    end: date,
    overrides_csv: Optional[Path] = None,
) -> list[Event]:
    """
    優先順位:
      1. CSV上書き（手動補正）
      2. OMB PFEI PDF（米国政府年次公式日程 — v10で追加）
      3. BLS iCal（PFEI未掲載のBLS指標のフォールバック）
      4. FRED API（PFEI未掲載で FRED 登録ありの指標）
      5. ルールベース推定（最終フォールバック）
    """
    events = []
    overrides = _load_overrides(overrides_csv) if overrides_csv else {}

    # API・iCal・PDFから公式日程を取得
    fred_dates = _fetch_fred_dates(start, end)
    bls_dates = _fetch_bls_ical(start, end)

    # v10: OMB PFEI PDFを最優先の公式ソースとして取り込む
    pfei_dates: dict[str, list[date]] = {}
    if fetch_pfei_dates is not None:
        pfei_pdf_path = Path(__file__).resolve().parents[2] / "data" / f"pfei_{start.year}.pdf"
        try:
            pfei_dates = fetch_pfei_dates(
                year=start.year,
                local_fallback=pfei_pdf_path if pfei_pdf_path.exists() else None,
            )
            if pfei_dates:
                print(f"  [pfei] {len(pfei_dates)} indicators loaded from OMB PFEI")
        except Exception as e:
            print(f"  [pfei] error: {e} — skipping PFEI layer")

        # 年またぎの場合は翌年分も取得
        if start.year != end.year:
            for y in range(start.year + 1, end.year + 1):
                pfei_pdf_y = Path(__file__).resolve().parents[2] / "data" / f"pfei_{y}.pdf"
                try:
                    additional = fetch_pfei_dates(
                        year=y,
                        local_fallback=pfei_pdf_y if pfei_pdf_y.exists() else None,
                    )
                    for k, v in additional.items():
                        pfei_dates.setdefault(k, []).extend(v)
                except Exception as e:
                    print(f"  [pfei] {y} error: {e}")

    # ── 月次イベント ──
    d = date(start.year, start.month, 1)
    while d <= end:
        year, month = d.year, d.month

        for ind in INDICATORS:
            if ind.rule in ("every_thursday", "every_wednesday"):
                continue
            if ind.category != "data":
                continue

            override_key = f"{ind.key}:{year}-{month:02d}"

            # 1. CSV上書き
            if override_key in overrides:
                release_date = overrides[override_key]["date"]
                source = "CSV override"
                extra_note = overrides[override_key].get("note", "")

            # 2. OMB PFEI PDF (v10で追加 — 米国政府公式の年次スケジュール)
            elif ind.key in pfei_dates and pfei_dates[ind.key]:
                month_matches = [
                    dd for dd in pfei_dates[ind.key]
                    if dd.year == year and dd.month == month
                ]
                if month_matches:
                    release_date = month_matches[0]
                    source = "OMB PFEI"
                    extra_note = ""
                else:
                    release_date = None
                    source = ""
                    extra_note = ""

            # 3. BLS iCal（月に1つマッチするものを探す）
            elif ind.key in bls_dates:
                month_matches = [
                    dd for dd in bls_dates[ind.key]
                    if dd.year == year and dd.month == month
                ]
                if month_matches:
                    release_date = month_matches[0]
                    source = "BLS official iCal"
                    extra_note = ""
                else:
                    release_date = None
                    source = ""
                    extra_note = ""

            # 3. FRED API
            elif ind.key in fred_dates and fred_dates[ind.key]:
                allowed_months = FRED_MONTH_FILTERS.get(ind.key)
                if allowed_months is not None and month not in allowed_months:
                    # GDP等、四半期ごとに特定月のみ発表される指標はスキップ
                    release_date = None
                    source = ""
                    extra_note = ""
                else:
                    month_matches = [
                        dd for dd in fred_dates[ind.key]
                        if dd.year == year and dd.month == month
                    ]
                    if month_matches:
                        # 今日以降の将来日程を優先（過去の改定リリース等を除外）
                        today_d = date.today()
                        future_matches = [dd for dd in month_matches if dd >= today_d]
                        if future_matches:
                            release_date = future_matches[0]
                        else:
                            # 全て過去ならその月の最終日（通常はメインリリース）を採用
                            release_date = month_matches[-1]
                        source = "FRED API"
                        extra_note = ""
                    else:
                        release_date = None
                        source = ""
                        extra_note = ""

            # 4. ルールベース
            else:
                release_date = _resolve_date(ind, year, month)
                source = "Rule-based estimate"
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
                    "source": source,
                    "note": extra_note,
                },
                uid_hint=f"{ind.key}:{release_date.isoformat()}",
            )
            events.append(ev)

        if month == 12:
            d = date(year + 1, 1, 1)
        else:
            d = date(year, month + 1, 1)

    # ── 週次イベント（FRED/BLS不要、固定スケジュール）──
    for ind in INDICATORS:
        if ind.rule == "every_thursday":
            dow = 3
        elif ind.rule == "every_wednesday":
            dow = 2
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
