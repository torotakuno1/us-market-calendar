#!/usr/bin/env python3
"""
US Market Calendar ICS 検証スクリプト
=======================================
GitHub Pages から us_data.ics をDLし、主要指標の日付を抽出して表示。
FRED修正が正しく反映されたかを即座に確認できる。

使い方:
    python verify_ics_dates.py
"""

import sys
from datetime import datetime

try:
    import urllib.request
except ImportError:
    print("urllib が使えません")
    sys.exit(1)

ICS_URL = "https://torotakuno1.github.io/us-market-calendar/us_data.ics"

# 検証したいキーワード（日本語 SUMMARY に含まれる文字列）
TARGETS = [
    ("雇用統計",       "NFP",          "毎月第1金曜"),
    ("ADP雇用",        "ADP",          "NFP 2日前（水曜）"),
    ("CPI",            "CPI",          "毎月10-15日頃"),
    ("PPI",            "PPI",          "CPIの翌日前後"),
    ("JOLTS",          "JOLTS",        "毎月上旬"),
    ("住宅着工",       "Housing Starts","毎月15-20日頃"),
    ("中古住宅",       "Existing Home","毎月中下旬"),
    ("新築住宅",       "New Home",     "毎月下旬"),
    ("NY連銀",         "Empire State", "毎月15日"),
    ("フィラデルフィア", "Philly Fed",   "毎月第3木曜"),
    ("ミシガン",       "UMich",        "月2回（速報+確報）"),
    ("PCE",            "Core PCE",     "月末金曜"),
    ("小売",           "Retail",       "毎月15-17日"),
    ("輸入物価",       "Import Price", "毎月中旬"),
    ("ケースシラー",   "Case-Shiller", "毎月下旬"),
]


def fetch_ics() -> str:
    """ICS ファイルをDL"""
    print(f"Fetching {ICS_URL} ...")
    req = urllib.request.Request(
        ICS_URL,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def parse_events(ics_text: str) -> list[dict]:
    """ICS から VEVENT を抽出"""
    events = []
    current = None
    for line in ics_text.splitlines():
        line = line.rstrip("\r")
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            if current:
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            key, _, val = line.partition(":")
            key = key.split(";")[0]  # "DTSTART;TZID=..." → "DTSTART"
            current[key] = val
    return events


def format_date(dtstart: str) -> str:
    """ICS の DTSTART を読みやすい日付に"""
    try:
        # 20260506T083000Z or 20260506T083000
        dt_str = dtstart.rstrip("Z")
        if "T" in dt_str:
            dt = datetime.strptime(dt_str, "%Y%m%dT%H%M%S")
        else:
            dt = datetime.strptime(dt_str, "%Y%m%d")
        return dt.strftime("%Y-%m-%d (%a) %H:%M UTC")
    except Exception:
        return dtstart


def main():
    try:
        ics_text = fetch_ics()
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    events = parse_events(ics_text)
    print(f"Parsed {len(events)} events\n")
    print("=" * 80)
    print(f"{'指標':<14} {'期待ルール':<24} 発表日")
    print("=" * 80)

    for jp_keyword, label, rule in TARGETS:
        matches = [
            e for e in events
            if jp_keyword in e.get("SUMMARY", "")
        ]
        if not matches:
            print(f"{label:<14} {rule:<24} (該当なし)")
            continue

        for m in matches[:4]:  # 最大4件表示
            summary = m.get("SUMMARY", "")
            dt_str = format_date(m.get("DTSTART", ""))
            print(f"{label:<14} {rule:<24} {dt_str}  | {summary}")

        if len(matches) > 4:
            print(f"{'':<40} ... (+{len(matches) - 4} more)")
        print()


if __name__ == "__main__":
    main()
