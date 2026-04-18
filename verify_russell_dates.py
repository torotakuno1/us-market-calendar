#!/usr/bin/env python3
"""
verify_russell_dates.py — Russell Reconstitution 2026 イベント検証 (v8.3)
"""
import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

# 期待値テーブル（2026年、一次情報確認済）
EXPECTED = {
    "2026-04-30": ("Russell Rank Day",  "★★", "4月Rank Day（木）"),
    "2026-05-22": ("Russell 暫定リスト",  "★",  "Preliminary List 初回"),
    "2026-06-26": ("Russell リバランス",  "★★★", "6月リバランス（第4金曜）"),
    "2026-10-30": ("Russell Rank Day",  "★★", "10月Rank Day（金）"),
    "2026-12-11": ("Russell リバランス",  "★★★", "12月リバランス（第2金曜、新設）"),
}


def parse_ics_events(ics_path: Path) -> list[dict]:
    if not ics_path.exists():
        raise FileNotFoundError(f"ICS file not found: {ics_path}")
    content = ics_path.read_text(encoding="utf-8")
    content = re.sub(r"\r?\n[ \t]", "", content)
    events = []
    for block in content.split("BEGIN:VEVENT")[1:]:
        block = block.split("END:VEVENT")[0]
        if "Russell" not in block:
            continue
        dt_match = re.search(r"DTSTART(?:;[^:]*)?:(\d{8})", block)
        sum_match = re.search(r"SUMMARY:([^\r\n]+)", block)
        if not dt_match or not sum_match:
            continue
        d = datetime.strptime(dt_match.group(1), "%Y%m%d").date()
        summary = sum_match.group(1).strip()
        events.append({"date": d, "summary": summary})
    events.sort(key=lambda e: e["date"])
    return events


def verify(ics_path: Path) -> int:
    print("━" * 60)
    print(f"Russell Reconstitution 検証: {ics_path}")
    print("━" * 60)

    try:
        events = parse_ics_events(ics_path)
    except FileNotFoundError as ex:
        print(f"[ERROR] {ex}")
        return 2

    print(f"ICS内の Russell イベント: {len(events)} 件\n")
    for e in events:
        wd = ["月","火","水","木","金","土","日"][e["date"].weekday()]
        print(f"  {e['date']} ({wd})  {e['summary']}")
    print()

    # 期待値突合
    actual_by_date = {str(e["date"]): e["summary"] for e in events}
    ok = miss = skip = 0
    print("期待値チェック:")
    for d_str, (expected_kw, stars, note) in EXPECTED.items():
        summary = actual_by_date.get(d_str)
        ex_date = date.fromisoformat(d_str)
        today = date.today()

        if summary is None:
            if ex_date < today.replace(day=1):
                print(f"  [skip] {d_str} {expected_kw}（過去月、生成対象外）")
                skip += 1
                continue
            print(f"  [MISS] {d_str} {expected_kw} — ICSに見つからない")
            miss += 1
        elif expected_kw in summary and stars in summary:
            print(f"  [OK]   {d_str} {summary}  — {note}")
            ok += 1
        else:
            print(f"  [NG]   {d_str} 期待='{stars} {expected_kw}' 実測='{summary}'")
            miss += 1

    print()
    print(f"OK: {ok} / MISS: {miss} / skip: {skip}")

    # 重点確認
    print("\n" + "━" * 60)
    print("重点確認")
    print("━" * 60)

    # Rank Day 4/30（直近12日後）
    rd_apr = actual_by_date.get("2026-04-30")
    if rd_apr and "★★" in rd_apr and "Rank Day" in rd_apr:
        print(f"  [OK] 2026-04-30 木  {rd_apr}  — 直近Rank Day")
    else:
        print(f"  [NG] 2026-04-30 木 が正しくない: {rd_apr}")

    # 6月リバランス
    rec_jun = actual_by_date.get("2026-06-26")
    if rec_jun and "★★★" in rec_jun and "リバランス" in rec_jun:
        print(f"  [OK] 2026-06-26 金  {rec_jun}  — 6月リバランス")
    else:
        print(f"  [NG] 2026-06-26 金 が正しくない: {rec_jun}")

    # 12月リバランス（新設）
    rec_dec = actual_by_date.get("2026-12-11")
    if rec_dec and "★★★" in rec_dec and "リバランス" in rec_dec:
        print(f"  [OK] 2026-12-11 金  {rec_dec}  — 12月リバランス（新設）")
    else:
        print(f"  [NG] 2026-12-11 金 が正しくない: {rec_dec}")

    return 0 if miss == 0 else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ics", default="docs/us_opex.ics")
    args = parser.parse_args()
    return verify(Path(args.ics))


if __name__ == "__main__":
    sys.exit(main())
