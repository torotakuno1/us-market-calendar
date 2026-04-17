"""
Fed Events Fetcher
===================
- FOMC 金利決定 / 記者会見 / 議事録（静的）
- ベージュブック（静的）
- 鉱工業生産 G.17（v10.2 で削除済み、PFEI側で一元管理）
- Fed議長発言（v5: fed_speeches.py に分離、Playwright + HTMLアーカイブ）
"""

from datetime import date, time

from config import (
    FOMC_DATES, BEIGE_BOOK_DATES,
    Importance, make_summary,
)
from utils import Event, et_to_utc


def fetch_fed_events(start: date, end: date) -> list[Event]:
    events = []

    # ── FOMC 金利決定 / 記者会見 / 議事録 ──
    for fomc in FOMC_DATES:
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

            dt_press = et_to_utc(decision_date, time(14, 30))
            # v5: 議長名を動的に（Powell or Warsh 後任）
            # 2026-05-15 以降は Warsh 想定
            press_chair = "Warsh" if decision_date >= date(2026, 5, 15) else "Powell"
            chair_jp = "ウォーシュ" if press_chair == "Warsh" else "パウエル"
            events.append(Event(
                name_short=make_summary(Importance.HIGH, f"{chair_jp}記者会見"),
                name_full=f"FOMC Press Conference (Chair {press_chair})",
                dt_utc=dt_press,
                category="fed",
                importance=3,
                details={"source": "federalreserve.gov", "chair": press_chair},
                uid_hint=f"FOMC_PRESS:{decision_date.isoformat()}",
            ))

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

    # ── ベージュブック ──
    for d_str in BEIGE_BOOK_DATES:
        d = date.fromisoformat(d_str)
        if start <= d <= end:
            dt_utc = et_to_utc(d, time(14, 0))
            events.append(Event(
                name_short=make_summary(Importance.MEDIUM, "ベージュブック"),
                name_full="Beige Book (Summary of Commentary on Current Economic Conditions)",
                dt_utc=dt_utc,
                category="fed",
                importance=2,
                details={
                    "note": "地区連銀経済報告 — FOMC前の景気判断材料",
                    "source": "federalreserve.gov",
                },
                uid_hint=f"BEIGE:{d.isoformat()}",
            ))

    # ── 鉱工業生産 G.17 ──
    # v10.2 で削除済み（PFEI 側で econ_data.py から取得）

    # ── Fed議長発言（v5: Playwright + 静的アーカイブ） ──
    try:
        from fetchers.fed_speeches import fetch_fed_chair_speeches
        speeches = fetch_fed_chair_speeches(start, end)
        events.extend(speeches)
    except Exception as ex:
        print(f"  [fed] chair speeches module failed: {ex}")

    return events
