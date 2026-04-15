"""
Fed Events Fetcher
===================
FOMC日程（静的） + Fed発言（将来的にAPI連携）
"""

from datetime import date, time

from config import FOMC_DATES, Importance, make_summary
from utils import Event, et_to_utc


def fetch_fed_events(start: date, end: date) -> list[Event]:
    events = []

    for fomc in FOMC_DATES:
        # ── FOMC金利決定 ──
        decision_date = date.fromisoformat(fomc["decision"])
        if start <= decision_date <= end:
            dt_utc = et_to_utc(decision_date, time(14, 0))
            events.append(Event(
                name_short=make_summary(Importance.HIGH, "FOMC 金利決定"),
                name_full="FOMC Interest Rate Decision + Statement + Dot Plot (if SEP meeting)",
                dt_utc=dt_utc,
                category="fed",
                importance=3,
                details={
                    "note": "声明文 14:00 ET / 記者会見 14:30 ET",
                    "source": "federalreserve.gov",
                },
                uid_hint=f"FOMC_DEC:{decision_date.isoformat()}",
            ))

            # パウエル記者会見（決定日 14:30 ET）
            dt_press = et_to_utc(decision_date, time(14, 30))
            events.append(Event(
                name_short=make_summary(Importance.HIGH, "パウエル記者会見"),
                name_full="FOMC Press Conference (Chair Powell)",
                dt_utc=dt_press,
                category="fed",
                importance=3,
                details={"source": "federalreserve.gov"},
                uid_hint=f"FOMC_PRESS:{decision_date.isoformat()}",
            ))

        # ── FOMC議事録 ──
        minutes_date = date.fromisoformat(fomc["minutes"])
        if start <= minutes_date <= end:
            dt_utc = et_to_utc(minutes_date, time(14, 0))
            events.append(Event(
                name_short=make_summary(Importance.MEDIUM, "FOMC議事録"),
                name_full=f"FOMC Minutes (from {fomc['decision']} meeting)",
                dt_utc=dt_utc,
                category="fed",
                importance=2,
                details={"source": "federalreserve.gov"},
                uid_hint=f"FOMC_MIN:{minutes_date.isoformat()}",
            ))

    return events
