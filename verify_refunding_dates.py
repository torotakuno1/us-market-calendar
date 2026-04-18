#!/usr/bin/env python3
"""
verify_refunding_dates.py — Quarterly Refunding イベントの検証 (v7.1)
docs/us_auction.ics に Refunding/Estimates が正しく入っているか確認。
"""
import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

# 期待値テーブル（config.py と一致している必要あり）
EXPECTED = {
    "2026-05-04": ("借入額見積り", "Financing Estimates 月曜15:00 ET"),
    "2026-05-06": ("四半期入札方針", "Refunding Announcement 水曜08:30 ET ★★★"),
    "2026-08-03": ("借入額見積り", ""),
    "2026-08-05": ("四半期入札方針", ""),
    "2026-11-02": ("借入額見積り", ""),
    "2026-11-04": ("四半期入札方針", ""),
}


def parse_ics_events(ics_path: Path) -> list[dict]:
    if not ics_path.exists():
        raise FileNotFoundError(f"ICS file not found: {ics_path}")
    content = ics_path.read_text(encoding="utf-8")
    content = re.sub(r"\r?\n[ \t]", "", content)
    events = []
    for block in content.split("BEGIN:VEVENT")[1:]:
        block = block.split("END:VEVENT")[0]
        if "借入額見積り" not in block and "四半期入札方針" not in block:
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
    print(f"Quarterly Refunding 検証: {ics_path}")
    print("━" * 60)
    try:
        events = parse_ics_events(ics_path)
    except FileNotFoundError as ex:
        print(f"[ERROR] {ex}")
        return 2

    print(f"ICS内の Refunding/Estimates イベント: {len(events)} 件\n")
    for e in events:
        wd = ["月","火","水","木","金","土","日"][e["date"].weekday()]
        print(f"  {e['date']} ({wd})  {e['summary']}")
    print()

    # 期待値突合
    actual_by_date = {str(e["date"]): e["summary"] for e in events}
    ok = miss = 0
    print("期待値チェック:")
    for d_str, (expected_kw, note) in EXPECTED.items():
        summary = actual_by_date.get(d_str)
        if summary is None:
            today = date.today()
            ex_date = date.fromisoformat(d_str)
            if ex_date < today.replace(day=1):
                print(f"  [skip] {d_str} {expected_kw}（過去月のため生成対象外）")
                continue
            print(f"  [MISS] {d_str} {expected_kw} — ICS に見つからない")
            miss += 1
        elif expected_kw in summary:
            note_str = f"  — {note}" if note else ""
            print(f"  [OK]   {d_str} {summary}{note_str}")
            ok += 1
        else:
            print(f"  [NG]   {d_str} 期待='{expected_kw}' 実測='{summary}'")
            miss += 1

    print()
    print(f"OK: {ok} / MISS: {miss}")

    # 重点確認
    print("\n" + "━" * 60)
    print("重点確認: 次回 Refunding (2026-05-06)")
    print("━" * 60)
    key_refunding = actual_by_date.get("2026-05-06")
    key_estimates = actual_by_date.get("2026-05-04")
    if key_refunding and "★★★" in key_refunding and "四半期入札方針" in key_refunding:
        print(f"  [OK] 2026-05-06 水  {key_refunding}")
    else:
        print(f"  [NG] 2026-05-06 水 が正しく生成されていない: {key_refunding}")
    if key_estimates and "★★" in key_estimates and "借入額見積り" in key_estimates:
        print(f"  [OK] 2026-05-04 月  {key_estimates}")
    else:
        print(f"  [NG] 2026-05-04 月 が正しく生成されていない: {key_estimates}")

    return 0 if miss == 0 else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ics", default="docs/us_auction.ics")
    args = parser.parse_args()
    return verify(Path(args.ics))


if __name__ == "__main__":
    sys.exit(main())
