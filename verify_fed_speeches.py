#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_fed_speeches.py — V5 動作検証ツール
==========================================
us_fed.ics および us_market_all.ics から議長発言イベントを抽出し、
取得件数・ソース内訳・期間カバレッジを報告する。

Usage:
    python verify_fed_speeches.py
    python verify_fed_speeches.py --ics docs/us_fed.ics
    python verify_fed_speeches.py --strict  # 0件時に exit 1
"""
import argparse
import re
import sys
from pathlib import Path
from collections import Counter
from datetime import datetime


def parse_ics(path: Path) -> list[dict]:
    if not path.exists():
        print(f"[ERROR] file not found: {path}")
        return []
    text = path.read_text(encoding="utf-8", errors="replace")

    events = []
    # VEVENT ブロック単位で分割
    for block in text.split("BEGIN:VEVENT"):
        if "END:VEVENT" not in block:
            continue
        ev = {}
        for line in block.splitlines():
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
        if ev.get("summary"):
            events.append(ev)
    return events


def classify(ev: dict) -> str:
    """SUMMARY/DESCRIPTION から議長発言かを判定"""
    s = (ev.get("summary", "") + " " + ev.get("description", "")).lower()
    for chair in ["powell", "warsh"]:
        if chair in s:
            # FOMC記者会見は既存経路のため除外してスクレイパー結果のみカウント
            if "fomc" in s and ("記者会見" in s or "press" in s):
                return f"fomc_press:{chair}"
            return f"chair_speech:{chair}"
    return "other"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ics", default="docs/us_fed.ics",
                    help="対象ICSファイル（デフォルト docs/us_fed.ics）")
    ap.add_argument("--strict", action="store_true",
                    help="議長発言0件時に exit 1")
    args = ap.parse_args()

    ics_path = Path(args.ics)
    events = parse_ics(ics_path)
    print(f"[info] {ics_path}: {len(events)} events total")

    counter: Counter = Counter()
    chair_events = []
    for ev in events:
        k = classify(ev)
        counter[k] += 1
        if k.startswith("chair_speech:"):
            chair_events.append(ev)

    print("\n=== 分類結果 ===")
    for k, v in counter.most_common():
        print(f"  {k}: {v}")

    print(f"\n=== 議長発言イベント詳細 ({len(chair_events)}件) ===")
    for ev in sorted(chair_events, key=lambda e: e.get("dtstart", "")):
        dt = ev.get("dtstart", "????????T??????")
        try:
            d = datetime.strptime(dt, "%Y%m%dT%H%M%S")
            ds = d.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            ds = dt
        print(f"  {ds}  {ev['summary']}")

    if args.strict and len(chair_events) == 0:
        print("\n[FAIL] no chair speech events found (strict mode)")
        sys.exit(1)
    elif len(chair_events) == 0:
        print("\n[WARN] no chair speech events found — "
              "Playwright が動かなかった可能性")
    else:
        print(f"\n[SUCCESS] {len(chair_events)} chair speech events confirmed")


if __name__ == "__main__":
    main()
