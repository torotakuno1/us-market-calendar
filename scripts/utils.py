"""
Shared data models and utility functions.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional
import calendar

import pytz

ET = pytz.timezone("US/Eastern")
JST = pytz.timezone("Asia/Tokyo")
UTC = pytz.UTC


@dataclass
class Event:
    """1 calendar event."""
    name_short: str          # iPhone SUMMARY (≤25 chars target)
    name_full: str           # DESCRIPTION 用フル名称
    dt_utc: datetime         # UTC datetime
    category: str            # data | fed | auction | opex | earnings
    importance: int          # 1-3
    all_day: bool = False
    details: dict = field(default_factory=dict)
    uid_hint: str = ""       # UID生成用ヒント

    @property
    def dt_jst(self) -> datetime:
        return self.dt_utc.astimezone(JST)

    @property
    def dt_et(self) -> datetime:
        return self.dt_utc.astimezone(ET)


# ── 日付算出ユーティリティ ──────────────────────────────

def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """月のN番目の特定曜日を返す (n=1 で第1)。weekday: 0=Mon...6=Sun."""
    c = calendar.monthcalendar(year, month)
    days = [week[weekday] for week in c if week[weekday] != 0]
    return date(year, month, days[n - 1])


def last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """月の最終の特定曜日。"""
    c = calendar.monthcalendar(year, month)
    days = [week[weekday] for week in c if week[weekday] != 0]
    return date(year, month, days[-1])


def nth_business_day(year: int, month: int, n: int) -> date:
    """月のN番目の営業日（土日除外、祝日未考慮）。"""
    d = date(year, month, 1)
    count = 0
    while True:
        if d.weekday() < 5:  # Mon-Fri
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)
        # 月をまたぐ場合は最終営業日を返す
        if d.month != month:
            return d - timedelta(days=1)


def first_friday(year: int, month: int) -> date:
    return nth_weekday(year, month, 4, 1)  # Friday = 4


def last_friday(year: int, month: int) -> date:
    return last_weekday_of_month(year, month, 4)


def third_friday(year: int, month: int) -> date:
    return nth_weekday(year, month, 4, 3)


def calendar_day_adjusted(year: int, month: int, day: int) -> date:
    """
    暦日ベースのN日。土日なら直近の営業日に調整。
    Sat→前日Fri, Sun→翌日Mon。月末超過はクランプ。
    """
    import calendar as cal_mod
    last_day = cal_mod.monthrange(year, month)[1]
    day = min(day, last_day)
    d = date(year, month, day)
    if d.weekday() == 5:   # Saturday
        d -= timedelta(days=1)
    elif d.weekday() == 6:  # Sunday
        d += timedelta(days=1)
    return d


def previous_wednesday(d: date) -> date:
    """指定日の直前の水曜日（当日が水曜なら当日）。"""
    offset = (d.weekday() - 2) % 7
    return d - timedelta(days=offset)


def et_to_utc(d: date, t: time) -> datetime:
    """東部時間の日付+時刻 → UTC datetime。"""
    naive = datetime.combine(d, t)
    localized = ET.localize(naive)
    return localized.astimezone(UTC)


def every_weekday_in_range(start: date, end: date, weekday: int) -> list[date]:
    """期間内の毎週特定曜日のリスト。"""
    dates = []
    d = start
    # 最初の該当曜日まで進める
    while d.weekday() != weekday:
        d += timedelta(days=1)
    while d <= end:
        dates.append(d)
        d += timedelta(days=7)
    return dates
