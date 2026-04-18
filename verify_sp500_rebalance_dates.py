#!/usr/bin/env python3
"""
verify_sp500_rebalance_dates.py — S&P 500 四半期リバランス発表イベントの検証 (v8.3.1)
"""
import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

# 期待値テーブル（2026年）
EXPECTED_ANNOUNCEMENTS = {
    "2026-03-13": ("Q1", "2026-03-20"),
    "2026-06-12": ("Q2", "2026-06-19"),
    "2026-09-11": ("Q3", "2026-09-18"),
    "2026-12-11": ("Q4", "2026-12-18"),
}


def parse_ics_events(ics_path: Path) -> list[dict]:
    if not ics_path.exists():
        raise FileNotFoundError(f"ICS file not found: {ics_path}")
    content = ics_path.read_text(encoding="utf-8")
    content = re.sub(r"\r?\n[ \t]", "", content)
    events = []
    for block in content.split("BEGIN:VEVENT")[1:]:
        block = block.split("END:VEVENT")[0]
        if "S&P" not in block and "SP500" not in block and "リバランス発表" not in block:
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


def check_russell_removal(ics_path: Path) -> bool:
    """Russell イベントが完全に削除されているか確認"""
    content = ics_path.read_text(encoding="utf-8")
    has_russell = "Russell" in content
    if has_russell:
        print("  [NG] Russell イベントが残存している")
        for i, line in enumerate(content.split("\n"), 1):
            if "Russell" in line:
                print(f"    Line {i}: {line.strip()}")
                break
    else:
        print("  [OK] Russell イベントは完全削除されている")
    return not has_russell


def verify(ics_path: Path) -> int:
    print("━" * 60)
    print(f"S&P 500 リバランス発表検証: {ics_path}")
    print("━" * 60)

    try:
        events = parse_ics_events(ics_path)
    except FileNotFoundError as ex:
        print(f"[ERROR] {ex}")
        return 2

    print(f"ICS内の S&P リバランス発表イベント: {len(events)} 件\n")
    for e in events:
        wd = ["月","火","水","木","金","土","日"][e["date"].weekday()]
        print(f"  {e['date']} ({wd})  {e['summary']}")
    print()

    # Russell 削除確認
    print("━" * 60)
    print("Russell イベント削除確認")
    print("━" * 60)
    russell_ok = check_russell_removal(ics_path)
    print()

    # 期待値突合
    actual_by_date = {str(e["date"]): e["summary"] for e in events}
    ok = miss = skip = 0
    print("━" * 60)
    print("S&P リバランス発表 期待値チェック")
    print("━" * 60)
    for d_str, (quarter, effective) in EXPECTED_ANNOUNCEMENTS.items():
        summary = actual_by_date.get(d_str)
        ex_date = date.fromisoformat(d_str)
        today = date.today()
        if summary is None:
            if ex_date < today.replace(day=1):
                print(f"  [skip] {d_str} {quarter}（過去月、生成対象外）")
                skip += 1
                continue
            print(f"  [MISS] {d_str} {quarter} — ICSに見つからない")
            miss += 1
        elif quarter in summary and ("S&P" in summary or "リバランス発表" in summary):
            wd = ["月","火","水","木","金","土","日"][ex_date.weekday()]
            print(f"  [OK]   {d_str} ({wd}) {summary}  — effective {effective}")
            ok += 1
        else:
            print(f"  [NG]   {d_str} 期待='{quarter} S&P リバランス発表' 実測='{summary}'")
            miss += 1

    print()
    print(f"S&P: OK {ok} / MISS {miss} / skip {skip}")

    # 重点確認
    print("\n" + "━" * 60)
    print("重点確認")
    print("━" * 60)

    # 次の S&P 発表日（2026-06-12）
    next_ann = actual_by_date.get("2026-06-12")
    if next_ann and "Q2" in next_ann:
        print(f"  [OK] 2026-06-12 金  {next_ann}")
    else:
        print(f"  [NG] 2026-06-12 金 (Q2発表) が正しくない: {next_ann}")

    total_fail = miss + (0 if russell_ok else 1)
    return 0 if total_fail == 0 else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ics", default="docs/us_opex.ics")
    args = parser.parse_args()
    return verify(Path(args.ics))


if __name__ == "__main__":
    sys.exit(main())
