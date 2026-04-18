"""
Options Expiration & VIX Settlement Fetcher
=============================================
- 月次OpEx: 第3金曜
- 四半期OpEx (Quad Witch): 3/6/9/12月の第3金曜
- VIX最終決済: 原則OpEx前日(水曜)、祝日等で不規則の場合は CSV 参照
- 0DTE参考: 月曜・水曜・金曜のSPX/SPYオプション満期

v8.1 (2026-04-18): VIX決済日のCSV例外対応追加
"""

import csv
from datetime import date, time
from pathlib import Path
from typing import Optional

from config import Importance, make_summary
from utils import Event, et_to_utc, third_friday, previous_wednesday


QUAD_WITCH_MONTHS = {3, 6, 9, 12}


def _load_opex_exceptions(csv_path: Optional[Path]) -> dict[str, date]:
    """CSV: month(YYYY-MM),actual_expiration_date(YYYY-MM-DD)"""
    exc = {}
    if csv_path and csv_path.exists():
        with open(csv_path) as f:
            for row in csv.reader(f):
                if not row or row[0].startswith("#"):
                    continue
                exc[row[0].strip()] = date.fromisoformat(row[1].strip())
    return exc


def _load_vix_exceptions(csv_path: Optional[Path]) -> dict[str, tuple[date, str]]:
    """
    VIX最終決済日の明示テーブル。
    CSV: month(YYYY-MM),settlement_date(YYYY-MM-DD),note
    戻り値: { "YYYY-MM": (date, note_str) }

    v8.1: このCSVがある月は previous_wednesday 算出を上書きする。
    """
    exc: dict[str, tuple[date, str]] = {}
    if csv_path and csv_path.exists():
        with open(csv_path, encoding="utf-8") as f:
            for row in csv.reader(f):
                if not row or row[0].startswith("#"):
                    continue
                if len(row) < 2:
                    continue
                month_key = row[0].strip()
                d = date.fromisoformat(row[1].strip())
                note = row[2].strip() if len(row) >= 3 else ""
                exc[month_key] = (d, note)
    return exc


def fetch_opex_events(
    start: date,
    end: date,
    exceptions_csv: Optional[Path] = None,
    vix_exceptions_csv: Optional[Path] = None,  # ← 追加
) -> list[Event]:
    events = []
    exceptions = _load_opex_exceptions(exceptions_csv)
    vix_exceptions = _load_vix_exceptions(vix_exceptions_csv)

    d = date(start.year, start.month, 1)
    while d <= end:
        year, month = d.year, d.month
        month_key = f"{year}-{month:02d}"

        # OpEx日
        if month_key in exceptions:
            opex_date = exceptions[month_key]
        else:
            opex_date = third_friday(year, month)

        if start <= opex_date <= end:
            is_quad = month in QUAD_WITCH_MONTHS
            label = "Quad Witch" if is_quad else "月次OpEx"
            imp = Importance.HIGH if is_quad else Importance.MEDIUM

            events.append(Event(
                name_short=make_summary(imp, label),
                name_full=f"Monthly Options Expiration {'(Quad Witching)' if is_quad else ''}",
                dt_utc=et_to_utc(opex_date, time(16, 0)),
                category="opex",
                importance=int(imp),
                all_day=True,
                details={
                    "note": "Quad Witch: 株式先物/オプション + 指数先物/オプション 同時満期" if is_quad else "",
                    "source": "Calculated (3rd Friday rule)",
                },
                uid_hint=f"OPEX:{opex_date.isoformat()}",
            ))

            # VIX最終決済
            # 優先順位: (1) vix_exceptions.csv → (2) previous_wednesday フォールバック
            vix_source: str
            vix_extra_note: str = ""
            if month_key in vix_exceptions:
                vix_date, vix_extra_note = vix_exceptions[month_key]
                vix_source = "CBOE (vix_exceptions.csv)"
            else:
                vix_date = previous_wednesday(opex_date)
                vix_source = "CBOE (fallback: previous_wednesday)"

            if vix_date != opex_date and start <= vix_date <= end:
                detail_note = "SOQ (Special Opening Quotation) で決済値算出"
                if vix_extra_note:
                    detail_note = f"{detail_note} | {vix_extra_note}"

                events.append(Event(
                    name_short=make_summary(Importance.MEDIUM, "VIX最終決済"),
                    name_full="VIX Final Settlement (AM settlement)",
                    dt_utc=et_to_utc(vix_date, time(8, 30)),
                    category="opex",
                    importance=2,
                    details={
                        "note": detail_note,
                        "source": vix_source,
                    },
                    uid_hint=f"VIX_SETTLE:{vix_date.isoformat()}",
                ))

        # 次月
        if month == 12:
            d = date(year + 1, 1, 1)
        else:
            d = date(year, month + 1, 1)

    return events
