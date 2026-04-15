"""
Fed Events Fetcher
===================
- FOMC 金利決定 / 記者会見 / 議事録（静的）
- ベージュブック（静的）
- 鉱工業生産 G.17（静的スケジュール）
- Fed理事発言（FederalReserve.gov スクレイピング）
"""

from datetime import date, time, datetime

import requests
from bs4 import BeautifulSoup

from config import (
    FOMC_DATES, BEIGE_BOOK_DATES, G17_DATES_2026,
    FED_KEY_SPEAKERS, Importance, make_summary,
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
            events.append(Event(
                name_short=make_summary(Importance.HIGH, "パウエル記者会見"),
                name_full="FOMC Press Conference (Chair Powell)",
                dt_utc=dt_press,
                category="fed",
                importance=3,
                details={"source": "federalreserve.gov"},
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
    for d_str in G17_DATES_2026:
        d = date.fromisoformat(d_str)
        if start <= d <= end:
            dt_utc = et_to_utc(d, time(9, 15))
            events.append(Event(
                name_short=make_summary(Importance.MEDIUM, "鉱工業生産 G17"),
                name_full="Industrial Production and Capacity Utilization (G.17)",
                dt_utc=dt_utc,
                category="data",
                importance=2,
                details={"source": "federalreserve.gov"},
                uid_hint=f"G17:{d.isoformat()}",
            ))

    # ── Fed理事発言（スクレイピング）──
    speeches = _fetch_fed_speeches(start, end)
    events.extend(speeches)

    return events


def _fetch_fed_speeches(start: date, end: date) -> list[Event]:
    """FederalReserve.gov カレンダーから主要理事の発言予定を取得。"""
    events = []

    try:
        url = "https://www.federalreserve.gov/newsevents/calendar.htm"
        resp = requests.get(url, timeout=30, headers={
            "User-Agent": "US-Market-Calendar/1.0"
        })
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Fed calendar uses panels with dates and event descriptions
        panels = soup.select(".panel-default")
        for panel in panels:
            heading = panel.select_one(".panel-heading")
            body = panel.select_one(".panel-body")
            if not heading or not body:
                continue

            # Extract date from heading
            date_text = heading.get_text(strip=True)
            event_date = _parse_fed_date(date_text)
            if event_date is None:
                continue
            if not (start <= event_date <= end):
                continue

            # Look for speeches/testimonies
            items = body.select("li, p, div")
            for item in items:
                text = item.get_text(" ", strip=True)
                if not any(w in text.lower() for w in
                           ["speech", "testimony", "speaks", "remarks",
                            "participates", "discussion", "press conference"]):
                    continue

                # Key speaker filter
                speaker = None
                for name in FED_KEY_SPEAKERS:
                    if name.lower() in text.lower():
                        speaker = name
                        break
                if not speaker:
                    continue

                imp = Importance.HIGH if speaker == "Powell" else Importance.MEDIUM
                short_name = f"Fed {speaker}発言"
                dt_utc = et_to_utc(event_date, time(12, 0))

                events.append(Event(
                    name_short=make_summary(imp, short_name),
                    name_full=f"Fed Speech: {speaker}",
                    dt_utc=dt_utc,
                    category="fed",
                    importance=int(imp),
                    details={
                        "speaker": speaker,
                        "description": text[:200],
                        "source": "federalreserve.gov",
                    },
                    uid_hint=f"FED_SPEECH:{speaker}:{event_date.isoformat()}",
                ))

    except Exception as e:
        print(f"  [fed_speeches] scraping error: {e}")

    print(f"  [fed_speeches] {len(events)} speeches found")
    return events


def _parse_fed_date(text: str):
    """Fed calendar heading からdateを抽出。'April 15, 2026' 形式等。"""
    import re
    # "April 15-16, 2026" or "April 15, 2026"
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    text_lower = text.lower().strip()
    for mname, mnum in months.items():
        if mname in text_lower:
            nums = re.findall(r'\d+', text)
            if len(nums) >= 2:
                day = int(nums[0])
                year = int(nums[-1])
                if year < 100:
                    year += 2000
                if 2024 <= year <= 2030 and 1 <= day <= 31:
                    try:
                        return date(year, mnum, day)
                    except ValueError:
                        pass
    return None
