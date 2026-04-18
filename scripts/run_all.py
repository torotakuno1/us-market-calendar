#!/usr/bin/env python3
"""
US Market Calendar — Main Orchestrator
========================================
GitHub Actions から呼び出され、全ICSを再生成。
Usage:
    python scripts/run_all.py [--months N] [--no-earnings]
"""

import argparse
import sys
import os
from datetime import date, timedelta
from pathlib import Path

# scripts/ ディレクトリをパスに追加
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from ics_builder import build_ics_files
from utils import Event


def main():
    parser = argparse.ArgumentParser(description="Generate US Market Calendar ICS files")
    parser.add_argument("--months", type=int, default=3, help="生成する月数（デフォルト3ヶ月先まで）")
    parser.add_argument("--no-earnings", action="store_true", help="決算データをスキップ（API不通時）")
    parser.add_argument("--output", type=str, default="docs", help="出力ディレクトリ")
    args = parser.parse_args()

    today = date.today()
    # 今月1日から N ヶ月先の末日まで
    start = today.replace(day=1)
    end_month = today.month + args.months
    end_year = today.year + (end_month - 1) // 12
    end_month = ((end_month - 1) % 12) + 1
    end = date(end_year, end_month, 1) - timedelta(days=1)

    print(f"=== US Market Calendar Generator ===")
    print(f"Period: {start} → {end}")
    print()

    all_events: list[Event] = []

    # ── 1. 経済指標 ──
    print("[1/5] Economic Data Releases...")
    from fetchers.econ_data import fetch_econ_data
    overrides_csv = SCRIPT_DIR.parent / "data" / "econ_overrides.csv"
    econ = fetch_econ_data(start, end, overrides_csv if overrides_csv.exists() else None)
    all_events.extend(econ)
    print(f"  → {len(econ)} events")

    # ── 2. Fed ──
    print("[2/5] Fed Events...")
    from fetchers.fed import fetch_fed_events
    fed = fetch_fed_events(start, end)
    all_events.extend(fed)
    print(f"  → {len(fed)} events")

    # ── 3. Treasury Auctions ──
    print("[3/5] Treasury Auctions...")
    from fetchers.treasury import fetch_treasury_auctions
    auctions = fetch_treasury_auctions(start, end)
    all_events.extend(auctions)
    print(f"  → {len(auctions)} events")

    # ── 4. OpEx / VIX ──
    print("[4/5] OpEx & VIX Settlement...")
    from fetchers.opex import fetch_opex_events
    opex_exc = SCRIPT_DIR.parent / "data" / "opex_exceptions.csv"
    vix_exc = SCRIPT_DIR.parent / "data" / "vix_exceptions.csv"
    opex = fetch_opex_events(
        start,
        end,
        opex_exc if opex_exc.exists() else None,
        vix_exc if vix_exc.exists() else None,
    )
    all_events.extend(opex)
    print(f"  → {len(opex)} events")

    # ── 5. Earnings ──
    if not args.no_earnings:
        print("[5/5] Major US Earnings...")
        from fetchers.earnings import fetch_earnings
        earn = fetch_earnings(start, end)
        all_events.extend(earn)
        print(f"  → {len(earn)} events")
    else:
        print("[5/5] Earnings SKIPPED")

    # ── ソート ──
    all_events.sort(key=lambda e: (e.dt_utc, -e.importance))

    # ── ICS生成 ──
    print()
    print(f"Total: {len(all_events)} events")
    print("Generating ICS files...")
    output_dir = SCRIPT_DIR.parent / args.output
    results = build_ics_files(all_events, output_dir)

    print()
    print("=== Done ===")
    for cat, path in results.items():
        size = path.stat().st_size
        print(f"  {path.name}: {size:,} bytes")

    return 0


if __name__ == "__main__":
    sys.exit(main())
