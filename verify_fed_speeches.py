#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_fed_speeches.py — V5.0.2 動作検証ツール（バグ修正版）
============================================================
us_fed.ics および us_market_all.ics から議長・副議長発言イベントを
抽出し、取得件数・ソース内訳・期間カバレッジを報告する。

v5.0.2 修正:
  - 判定対象に Jefferson (副議長) 追加
  - 日本語姓 (パウエル / ウォーシュ) を認識
  - SUMMARY / DESCRIPTION 両方をチェック

Usage:
    python verify_fed_speeches.py
    python verify_fed_speeches.py --ics docs/us_fed.ics
    python verify_fed_speeches.py --strict   # 0件時に exit 1
    python verify_fed_speeches.py --verbose  # 全イベントの SUMMARY を表示
"""
import argparse
import re
import sys
from pathlib import Path
from collections import Counter
from datetime import datetime


# 判定キーワード
# (key, [match patterns]) — いずれかにマッチすれば該当
SPEAKER_KEYWORDS: list[tuple[str, list[str]]] = [
    ("powell",    ["powell", "パウエル"]),
    ("warsh",     ["warsh", "ウォーシュ"]),
    ("jefferson", ["jefferson"]),  # 副議長、日本語表記なし
]


def parse_ics(path: Path) -> list[dict]:
    if not path.exists():
        print(f"[ERROR] file not found: {path}")
        return []
    text = path.read_text(encoding="utf-8", errors="replace")

    events = []
    for block in text.split("BEGIN:VEVENT"):
        if "END:VEVENT" not in block:
            continue
        ev = {}
        # 複数行 DESCRIPTION 等を結合するため、折り畳み行（先頭スペース）を処理
        lines = []
        for line in block.splitlines():
            if line.startswith(" ") and lines:
                # ICS の折り畳み行 (RFC 5545): 前行に連結
                lines[-1] += line[1:]
            else:
                lines.append(line)

        for line in lines:
            if line.startswith("SUMMARY"):
                ev["summary"] = line.split(":", 1)[-1].strip()
            elif line.startswith("DTSTART"):
                m = re.search(r"(\d{8}T\d{6})", line)
                if m:
                    ev["dtstart"] = m.group(1)
            elif line.startswith("DESCRIPTION"):
                ev["description"] = line.split(":", 1)[-1].strip()
            elif line.startswith("UID"):
                ev["uid"] = line.split(":", 1)[-1].strip()
            elif line.startswith("CATEGORIES"):
                ev["categories"] = line.split(":", 1)[-1].strip()

        if ev.get("summary"):
            events.append(ev)
    return events


def classify(ev: dict) -> str:
    """SUMMARY/DESCRIPTION から発言者を特定。
    戻り値:
      - fomc_press:{name}    : FOMC 記者会見
      - chair_speech:{name}  : 議長の任意講演・証言
      - vc_speech:{name}     : 副議長の任意講演
      - other                : それ以外
    """
    summary = ev.get("summary", "")
    desc = ev.get("description", "")
    uid = ev.get("uid", "")
    combined = (summary + " " + desc + " " + uid).lower()

    for speaker_key, patterns in SPEAKER_KEYWORDS:
        for pat in patterns:
            if pat.lower() in combined:
                # FOMC 記者会見判定
                if ("記者会見" in summary) or ("press conference" in combined):
                    return f"fomc_press:{speaker_key}"
                # 議長 vs 副議長
                if speaker_key == "jefferson":
                    return f"vc_speech:{speaker_key}"
                else:
                    return f"chair_speech:{speaker_key}"
    return "other"


def fmt_dt(dtstart: str) -> str:
    try:
        d = datetime.strptime(dtstart, "%Y%m%dT%H%M%S")
        return d.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return dtstart or "??"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ics", default="docs/us_fed.ics",
                    help="対象ICSファイル（デフォルト docs/us_fed.ics）")
    ap.add_argument("--strict", action="store_true",
                    help="議長・副議長発言0件時に exit 1")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="全イベントの SUMMARY を表示")
    args = ap.parse_args()

    ics_path = Path(args.ics)
    events = parse_ics(ics_path)
    print(f"[info] {ics_path}: {len(events)} events total")

    counter: Counter = Counter()
    categorized = {"chair_speech": [], "vc_speech": [], "fomc_press": [], "other": []}

    for ev in events:
        k = classify(ev)
        counter[k] += 1
        prefix = k.split(":")[0] if ":" in k else k
        if prefix in categorized:
            categorized[prefix].append(ev)

    # 分類結果
    print("\n=== 分類結果 ===")
    for k in sorted(counter.keys(), key=lambda x: (-counter[x], x)):
        v = counter[k]
        print(f"  {k}: {v}")

    # 詳細表示: FOMC記者会見
    if categorized["fomc_press"]:
        print(f"\n=== FOMC 記者会見 ({len(categorized['fomc_press'])}件) ===")
        for ev in sorted(categorized["fomc_press"], key=lambda e: e.get("dtstart", "")):
            print(f"  {fmt_dt(ev.get('dtstart', ''))}  {ev['summary']}")

    # 詳細表示: 議長任意発言
    print(f"\n=== 議長（Powell/Warsh）発言 ({len(categorized['chair_speech'])}件) ===")
    if categorized["chair_speech"]:
        for ev in sorted(categorized["chair_speech"], key=lambda e: e.get("dtstart", "")):
            print(f"  {fmt_dt(ev.get('dtstart', ''))}  {ev['summary']}")
    else:
        print("  (該当なし)")

    # 詳細表示: 副議長発言
    print(f"\n=== 副議長（Jefferson）発言 ({len(categorized['vc_speech'])}件) ===")
    if categorized["vc_speech"]:
        for ev in sorted(categorized["vc_speech"], key=lambda e: e.get("dtstart", "")):
            print(f"  {fmt_dt(ev.get('dtstart', ''))}  {ev['summary']}")
    else:
        print("  (該当なし)")

    # verbose: 全イベント
    if args.verbose:
        print(f"\n=== 全イベント ({len(events)}件) ===")
        for ev in sorted(events, key=lambda e: e.get("dtstart", "")):
            cat = classify(ev)
            print(f"  [{cat:30}] {fmt_dt(ev.get('dtstart', ''))}  {ev['summary']}")

    # サマリと終了コード
    total_chair_vc = len(categorized["chair_speech"]) + len(categorized["vc_speech"])
    print(f"\n=== サマリ ===")
    print(f"  議長・副議長発言 合計: {total_chair_vc}件")
    print(f"  FOMC 記者会見:         {len(categorized['fomc_press'])}件")

    if args.strict and total_chair_vc == 0:
        print("\n[FAIL] no chair/vc speech events found (strict mode)")
        sys.exit(1)
    elif total_chair_vc == 0:
        print("\n[WARN] no chair/vc speech events found")
        print("       Playwright が動かなかった可能性、または Fed サイトに該当イベント未登録")
    else:
        print(f"\n[SUCCESS] {total_chair_vc} chair/vc speech events confirmed")


if __name__ == "__main__":
    main()
