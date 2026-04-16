"""
ICS Builder — iPhone最適化 v2
===============================
- X-APPLE-CALENDAR-COLOR でiPhone初期色指定
- VTIMEZONE (America/New_York) 明記
- TRANSP:TRANSPARENT（予定ブロックしない）
- イベント5分枠（省スペース）
- 更新間隔1時間
- ★★★は1時間前+15分前の2段アラーム
- 決算にEPS予想・BMO/AMC表示
"""

import hashlib
from datetime import datetime, timedelta
from pathlib import Path

from icalendar import Calendar, Event as IcsEvent, Alarm, Timezone, TimezoneStandard, TimezoneDaylight
import pytz

from utils import Event, JST, UTC
from config import CALENDARS, stars, Importance


# ── VTIMEZONE (America/New_York) ──────────────────────
def _make_vtimezone():
    """America/New_York の VTIMEZONE コンポーネント。DST対応。"""
    tz = Timezone()
    tz.add("tzid", "America/New_York")

    # EST (Standard)
    std = TimezoneStandard()
    std.add("dtstart", datetime(1970, 11, 1, 2, 0, 0))
    std.add("rrule", {"freq": "yearly", "bymonth": 11, "byday": "1SU"})
    std.add("tzoffsetfrom", timedelta(hours=-4))
    std.add("tzoffsetto", timedelta(hours=-5))
    std.add("tzname", "EST")
    tz.add_component(std)

    # EDT (Daylight)
    dlt = TimezoneDaylight()
    dlt.add("dtstart", datetime(1970, 3, 8, 2, 0, 0))
    dlt.add("rrule", {"freq": "yearly", "bymonth": 3, "byday": "2SU"})
    dlt.add("tzoffsetfrom", timedelta(hours=-5))
    dlt.add("tzoffsetto", timedelta(hours=-4))
    dlt.add("tzname", "EDT")
    tz.add_component(dlt)

    return tz


def _uid(event: Event) -> str:
    """Deterministic UID — 同一イベントは同じUIDで上書き更新。"""
    seed = f"{event.category}:{event.uid_hint or event.name_short}:{event.dt_utc.isoformat()}"
    h = hashlib.md5(seed.encode()).hexdigest()[:16]
    return f"{h}@us-market-cal"


def _build_description(event: Event) -> str:
    """DESCRIPTION — iPhoneで詳細タップ時に表示。"""
    lines = [event.name_full]

    d = event.details

    # 決算: BMO/AMC + EPS予想
    if d.get("timing") and d["timing"] != "未定":
        lines.append(f"発表: {d['timing']}")
    if d.get("eps_estimate") and d["eps_estimate"] not in ("", "None"):
        lines.append(f"EPS予想: {d['eps_estimate']}")

    # 経済指標: 予想/前回/結果
    if d.get("forecast"):
        lines.append(f"予想: {d['forecast']}")
    if d.get("previous"):
        lines.append(f"前回: {d['previous']}")
    if d.get("actual"):
        lines.append(f"結果: {d['actual']}")

    # 発表時刻（ET / JST 併記）
    if not event.all_day:
        et = event.dt_et
        jst = event.dt_jst
        lines.append(f"ET {et.strftime('%H:%M')} / JST {jst.strftime('%H:%M')}")

    # ソース
    if d.get("source"):
        lines.append(f"出典: {d['source']}")
    if d.get("note") and d["note"]:
        lines.append(d["note"])
    if d.get("speaker"):
        lines.append(f"発言者: {d['speaker']}")

    return "\n".join(lines)


def _make_ics_event(event: Event) -> IcsEvent:
    """Event → icalendar.Event 変換（iPhone最適化）。"""
    e = IcsEvent()
    e.add("uid", _uid(event))
    e.add("summary", event.name_short)
    e.add("description", _build_description(event))
    e.add("categories", [event.category])
    e.add("dtstamp", datetime.now(UTC))

    # TRANSP:TRANSPARENT — 他の予定をブロックしない
    e.add("transp", "TRANSPARENT")

    if event.all_day:
        e.add("dtstart", event.dt_utc.date())
        e.add("dtend", event.dt_utc.date() + timedelta(days=1))
    else:
        e.add("dtstart", event.dt_utc)
        # 5分枠（日表示で省スペース）
        e.add("dtend", event.dt_utc + timedelta(minutes=5))

    # ── アラーム設定 ──
    if event.importance >= Importance.HIGH and not event.all_day:
        # ★★★: 1時間前 + 15分前の2段アラーム
        alarm1 = Alarm()
        alarm1.add("action", "DISPLAY")
        alarm1.add("description", f"1時間後: {event.name_short}")
        alarm1.add("trigger", timedelta(hours=-1))
        e.add_component(alarm1)

        alarm2 = Alarm()
        alarm2.add("action", "DISPLAY")
        alarm2.add("description", f"まもなく: {event.name_short}")
        alarm2.add("trigger", timedelta(minutes=-15))
        e.add_component(alarm2)

    elif event.importance >= Importance.MEDIUM and not event.all_day:
        # ★★: 15分前
        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", event.name_short)
        alarm.add("trigger", timedelta(minutes=-15))
        e.add_component(alarm)

    # ★: アラームなし

    return e


def _create_calendar(name: str, color: str) -> Calendar:
    """カレンダーオブジェクト作成（共通設定）。"""
    cal = Calendar()
    cal.add("prodid", "-//US-Market-Calendar//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", name)
    cal.add("x-wr-timezone", "America/New_York")

    # iPhone初期色指定
    cal.add("x-apple-calendar-color", color)

    # 更新間隔: 1時間
    cal.add("x-published-ttl", "PT1H")
    cal.add("refresh-interval;value=duration", "PT1H")

    # VTIMEZONE 追加
    cal.add_component(_make_vtimezone())

    return cal


def build_ics_files(events: list[Event], output_dir: str | Path) -> dict[str, Path]:
    """
    イベントリストからカテゴリ別ICSファイルを生成。
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    by_cat: dict[str, list[Event]] = {}
    for ev in events:
        by_cat.setdefault(ev.category, []).append(ev)

    result = {}
    for cat, cal_info in CALENDARS.items():
        cal = _create_calendar(cal_info["name"], cal_info["color"])

        for ev in by_cat.get(cat, []):
            cal.add_component(_make_ics_event(ev))

        out_path = output_dir / cal_info["file"]
        with open(out_path, "wb") as f:
            f.write(cal.to_ical())
        result[cat] = out_path
        print(f"  [{cat}] {len(by_cat.get(cat, []))} events -> {out_path.name}")

    # 全部入り
    cal_all = _create_calendar("🇺🇸 US Market (All)", "#5856D6")
    for ev in events:
        cal_all.add_component(_make_ics_event(ev))

    all_path = output_dir / "us_market_all.ics"
    with open(all_path, "wb") as f:
        f.write(cal_all.to_ical())
    print(f"  [ALL] {len(events)} events -> {all_path.name}")
    result["all"] = all_path

    return result
