"""
Options Expiration & VIX Settlement Fetcher
=============================================
- 月次OpEx: 第3金曜
- 四半期OpEx (Quad Witch): 3/6/9/12月の第3金曜
- VIX最終決済: OpEx前日(水曜)の寄付
- 0DTE参考: 月曜・水曜・金曜のSPX/SPYオプション満期
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


def fetch_opex_events(
    start: date,
    end: date,
    exceptions_csv: Optional[Path] = None,
) -> list[Event]:
    events = []
    exceptions = _load_opex_exceptions(exceptions_csv)

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

            # VIX最終決済（OpExの前日水曜の寄付 9:30 ET → 実際はAM settlement 8:30頃）
            vix_date = previous_wednesday(opex_date)
            if vix_date != opex_date and start <= vix_date <= end:
                events.append(Event(
                    name_short=make_summary(Importance.MEDIUM, "VIX最終決済"),
                    name_full="VIX Final Settlement (AM settlement)",
                    dt_utc=et_to_utc(vix_date, time(8, 30)),
                    category="opex",
                    importance=2,
                    details={
                        "note": "SOQ (Special Opening Quotation) で決済値算出",
                        "source": "CBOE",
                    },
                    uid_hint=f"VIX_SETTLE:{vix_date.isoformat()}",
                ))

        # 次月
        if month == 12:
            d = date(year + 1, 1, 1)
        else:
            d = date(year, month + 1, 1)

    return events
