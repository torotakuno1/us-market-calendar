"""
ICS Builder — iPhone最適化
===========================
- SUMMARY を25文字以内に制限
- 重要度★を先頭に
- DESCRIPTION にフル情報
- カテゴリ別に独立VCALENDARを生成
"""

import hashlib
from datetime import datetime, timedelta
from pathlib import Path

from icalendar import Calendar, Event as IcsEvent, Alarm
import pytz

from utils import Event, JST, UTC
from config import CALENDARS, stars, Importance


def _uid(event: Event) -> str:
    """Deterministic UID — 同一イベントは同じUIDで上書き更新。"""
    seed = f"{event.category}:{event.uid_hint or event.name_short}:{event.dt_utc.isoformat()}"
    h = hashlib.md5(seed.encode()).hexdigest()[:16]
    return f"{h}@us-market-cal"


def _build_description(event: Event) -> str:
    """DESCRIPTION フィールド（iPhoneで詳細タップ時に表示）。"""
    lines = [event.name_full]

    d = event.details
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

    if d.get("source"):
        lines.append(f"出典: {d['source']}")
    if d.get("note"):
        lines.append(d["note"])

    return "\n".join(lines)


def _make_ics_event(event: Event) -> IcsEvent:
    """Event → icalendar.Event 変換。"""
    e = IcsEvent()
    e.add("uid", _uid(event))
    e.add("summary", event.name_short)
    e.add("description", _build_description(event))
    e.add("categories", [event.category])
    e.add("dtstamp", datetime.now(UTC))

    if event.all_day:
        e.add("dtstart", event.dt_utc.date())
        e.add("dtend", event.dt_utc.date() + timedelta(days=1))
    else:
        e.add("dtstart", event.dt_utc)
        e.add("dtend", event.dt_utc + timedelta(minutes=30))

    # ★★★ イベントには発表30分前にアラーム
    if event.importance >= Importance.HIGH and not event.all_day:
        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", f"まもなく: {event.name_short}")
        alarm.add("trigger", timedelta(minutes=-30))
        e.add_component(alarm)

    # ★★ イベントには15分前
    elif event.importance >= Importance.MEDIUM and not event.all_day:
        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", event.name_short)
        alarm.add("trigger", timedelta(minutes=-15))
        e.add_component(alarm)

    return e


def build_ics_files(events: list[Event], output_dir: str | Path) -> dict[str, Path]:
    """
    イベントリストからカテゴリ別ICSファイルを生成。
    戻り値: {category: output_path}
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # カテゴリ別に仕分け
    by_cat: dict[str, list[Event]] = {}
    for ev in events:
        by_cat.setdefault(ev.category, []).append(ev)

    result = {}
    for cat, cal_info in CALENDARS.items():
        cal = Calendar()
        cal.add("prodid", "-//US-Market-Calendar//EN")
        cal.add("version", "2.0")
        cal.add("calscale", "GREGORIAN")
        cal.add("x-wr-calname", cal_info["name"])
        cal.add("x-wr-timezone", "Asia/Tokyo")
        # iPhone用: 更新間隔を短く
        cal.add("x-published-ttl", "PT6H")
        cal.add("refresh-interval;value=duration", "PT6H")

        for ev in by_cat.get(cat, []):
            cal.add_component(_make_ics_event(ev))

        out_path = output_dir / cal_info["file"]
        with open(out_path, "wb") as f:
            f.write(cal.to_ical())
        result[cat] = out_path
        print(f"  [{cat}] {len(by_cat.get(cat, []))} events → {out_path.name}")

    # 全部入り（1ファイルで購読したい人向け）
    cal_all = Calendar()
    cal_all.add("prodid", "-//US-Market-Calendar//EN")
    cal_all.add("version", "2.0")
    cal_all.add("calscale", "GREGORIAN")
    cal_all.add("x-wr-calname", "🇺🇸 US Market (All)")
    cal_all.add("x-wr-timezone", "Asia/Tokyo")
    cal_all.add("x-published-ttl", "PT6H")

    for ev in events:
        cal_all.add_component(_make_ics_event(ev))

    all_path = output_dir / "us_market_all.ics"
    with open(all_path, "wb") as f:
        f.write(cal_all.to_ical())
    print(f"  [ALL] {len(events)} events → {all_path.name}")
    result["all"] = all_path

    return result
