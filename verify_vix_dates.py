#!/usr/bin/env python3
"""
verify_vix_dates.py — VIX最終決済日の検証ツール (v8.1)
docs/us_opex.ics と期待値テーブルを突合。
"""
import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

EXPECTED_VIX_SETTLEMENT = {
    "2026-01": date(2026, 1, 21),
    "2026-02": date(2026, 2, 18),
    "2026-03": date(2026, 3, 18),
    "2026-04": date(2026, 4, 15),
    "2026-05": date(2026, 5, 19),  # ★不規則火曜
    "2026-06": date(2026, 6, 17),
    "2026-07": date(2026, 7, 22),
    "2026-08": date(2026, 8, 19),
    "2026-09": date(2026, 9, 16),
    "2026-10": date(2026, 10, 21),
    "2026-11": date(2026, 11, 18),
    "2026-12": date(2026, 12, 16),
}


def parse_ics_vix_events(ics_path: Path) -> list[dict]:
    if not ics_path.exists():
        raise FileNotFoundError(f"ICS file not found: {ics_path}")
    content = ics_path.read_text(encoding="utf-8")
    content = re.sub(r"\r?\n[ \t]", "", content)

    events = []
    for block in content.split("BEGIN:VEVENT")[1:]:
        block = block.split("END:VEVENT")[0]
        if "VIX最終決済" not in block and "VIX Final Settlement" not in block:
            continue
        dt_match = re.search(r"DTSTART(?:;[^:]*)?:(\d{8})", block)
        if not dt_match:
            continue
        dt = datetime.strptime(dt_match.group(1), "%Y%m%d").date()
        uid_match = re.search(r"UID:([^\r\n]+)", block)
        uid = uid_match.group(1).strip() if uid_match else "?"
        events.append({"date": dt, "uid": uid})
    events.sort(key=lambda e: e["date"])
    return events


def verify(ics_path: Path, year_filter=None) -> int:
    print("━" * 60)
    print(f"VIX決済日検証: {ics_path}")
    print("━" * 60)
    try:
        events = parse_ics_vix_events(ics_path)
    except FileNotFoundError as ex:
        print(f"[ERROR] {ex}")
        return 2

    print(f"ICS内のVIX決済イベント: {len(events)} 件\n")

    filtered = [e for e in events if year_filter is None or e["date"].year == year_filter]
    actual_by_month = {f"{e['date'].year}-{e['date'].month:02d}": e["date"] for e in filtered}
    expected = {k: v for k, v in EXPECTED_VIX_SETTLEMENT.items()
                if year_filter is None or int(k[:4]) == year_filter}

    ok = miss = missing = 0
    print(f"{'月':<10}{'実測':<16}{'期待':<14}{'判定'}")
    print("-" * 60)
    for mk in sorted(expected.keys()):
        exp = expected[mk]
        act = actual_by_month.get(mk)
        if act is None:
            print(f"{mk:<10}{'(欠落)':<16}{str(exp):<14}[MISS]")
            missing += 1
        elif act == exp:
            wd = ["月","火","水","木","金","土","日"][act.weekday()]
            mark = "★" if mk == "2026-05" else " "
            print(f"{mk:<10}{str(act)+f'({wd})':<16}{str(exp):<14}[OK] {mark}")
            ok += 1
        else:
            a_wd = ["月","火","水","木","金","土","日"][act.weekday()]
            e_wd = ["月","火","水","木","金","土","日"][exp.weekday()]
            print(f"{mk:<10}{str(act)+f'({a_wd})':<16}{str(exp)+f'({e_wd})':<14}[NG]")
            miss += 1
    print("-" * 60)
    print(f"OK: {ok} / NG: {miss} / 欠落: {missing}\n")

    # 重点チェック
    if year_filter is None or year_filter == 2026:
        print("━" * 60)
        print("重点検証: 2026-05 不規則決済（Juneteenth起因）")
        print("━" * 60)
        act_may = actual_by_month.get("2026-05")
        if act_may == date(2026, 5, 19):
            print(f"  [OK] 2026-05 VIX決済 = {act_may} (火曜)  ★v8.1 成功")
        elif act_may == date(2026, 5, 13):
            print(f"  [NG] 2026-05 VIX決済 = {act_may} (水曜)  ← パッチ未適用")
        else:
            print(f"  [NG] 2026-05 VIX決済 = {act_may}")

    return 0 if (miss == 0 and missing == 0) else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ics", default="docs/us_opex.ics")
    parser.add_argument("--year", type=int, default=None)
    args = parser.parse_args()
    return verify(Path(args.ics), args.year)


if __name__ == "__main__":
    sys.exit(main())
