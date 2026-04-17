#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_v5_0_1_patch.py — v5.0.1 Fed Speaker Scraper 修正
=========================================================

実施内容:
  1. fed_speeches.py の URL バグ修正
     - `/newsevents/{year}-{mname}.htm` (月名英語) → `/newsevents/{year}-{mm:02d}.htm` (数字)
     - 2026-april.htm は 404 のため取得できず Playwright が空を返していた

  2. CHAIR_CANDIDATES に Jefferson (副議長) 追加
     config.py:
       CHAIR_CANDIDATES: Powell, Warsh → Powell, Warsh, Jefferson
       SCRAPE_TARGET_SPEAKERS: {Powell:3, Warsh:3} → {Powell:3, Warsh:3, Jefferson:2}

  3. _make_event の重要度をスピーカー別に分岐
     fed_speeches.py:
       議長 (Powell/Warsh) = ★★★
       副議長 (Jefferson) = ★★

  4. SUMMARY の命名を役職別に分岐
     ★★★ パウエル講演 (議長)
     ★★★ ウォーシュ講演 (議長)
     ★★ Jefferson講演 (副議長)

Usage:
    python apply_v5_0_1_patch.py --dry-run
    python apply_v5_0_1_patch.py
    python apply_v5_0_1_patch.py --revert

前提: v5 パッチ適用済 (fed_speeches.py, config.py 変更済)
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
BACKUP_SUFFIX = ".bak_v5_0_1"


def backup(path: Path) -> None:
    if not path.exists():
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + f"{BACKUP_SUFFIX}_{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {path.name} -> {bak.name}")


def revert_all() -> None:
    restored = 0
    for bak in sorted(REPO_ROOT.rglob(f"*{BACKUP_SUFFIX}_*"), reverse=True):
        # e.g. fed_speeches.py.bak_v5_0_1_20260418_153045 → fed_speeches.py
        stem = bak.name
        idx = stem.rfind(BACKUP_SUFFIX)
        if idx < 0:
            continue
        orig_name = stem[:idx].rstrip(".")
        orig = bak.with_name(orig_name)
        if orig.exists():
            shutil.copy2(bak, orig)
            print(f"  [revert] {bak.name} -> {orig.name}")
            restored += 1
    print(f"[done] restored {restored} files")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. config.py パッチ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CONFIG_OLD_BLOCK = '''CHAIR_CANDIDATES: list[str] = [
    "Powell",    # 2018-02-05 ~ 2026-05-15 (任期満了)
    "Warsh",     # 2026-05-15 以降 (Trump 指名、上院承認待ち)
]

SCRAPE_TARGET_SPEAKERS: dict[str, int] = {
    "Powell": 3,
    "Warsh":  3,
}'''

CONFIG_NEW_BLOCK = '''CHAIR_CANDIDATES: list[str] = [
    "Powell",    # 2018-02-05 ~ 2026-05-15 (任期満了)
    "Warsh",     # 2026-05-15 以降 (Trump 指名、上院承認待ち)
    "Jefferson", # 副議長 (v5.0.1 追加、議長代行リスクヘッジ＋副議長発言取得)
]

SCRAPE_TARGET_SPEAKERS: dict[str, int] = {
    "Powell":    3,   # 議長 → ★★★
    "Warsh":     3,   # 議長 → ★★★
    "Jefferson": 2,   # 副議長 → ★★ (v5.0.1)
}'''


def patch_config(dry_run: bool) -> None:
    path = REPO_ROOT / "scripts" / "config.py"
    if not path.exists():
        print(f"  [ERROR] config.py not found: {path}")
        return

    content = path.read_text(encoding="utf-8")

    if CONFIG_OLD_BLOCK not in content:
        # 既に v5.0.1 適用済かもしれない
        if '"Jefferson": 2' in content:
            print("  [skip] config.py already patched (Jefferson found)")
            return
        print("  [ERROR] CONFIG_OLD_BLOCK not found verbatim in config.py")
        print("         Check manually: scripts/config.py")
        return

    if dry_run:
        print(f"  [dry-run] would replace CHAIR_CANDIDATES / SCRAPE_TARGET_SPEAKERS block")
        return

    backup(path)
    new_content = content.replace(CONFIG_OLD_BLOCK, CONFIG_NEW_BLOCK)
    path.write_text(new_content, encoding="utf-8", newline="\n")
    print(f"  [write] {path}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. fed_speeches.py パッチ (URL + 重要度分岐 + 命名)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ── 差分1: URL (月名英語 → 数字2桁) ──
URL_OLD = '''    # URL パターンは月名英語フル or MM 数字の2系統あり
    mname = MONTH_NAMES_EN[month - 1]
    url = f"{FED_BASE}/newsevents/{year}-{mname}.htm"'''

URL_NEW = '''    # v5.0.1: URL は数字形式が正規 (月名英語形式は 404 を返す)
    url = f"{FED_BASE}/newsevents/{year}-{month:02d}.htm"'''


# ── 差分2: import 追加 (SCRAPE_TARGET_SPEAKERS) ──
IMPORT_OLD = '''from config import (
    SCRAPE_TARGET_SPEAKERS,  # v5で新設: {"Powell": 3, "Warsh": 3, ...}
    CHAIR_CANDIDATES,        # v5で新設: ["Powell", "Warsh"]
    Importance,
    make_summary,
)'''

IMPORT_NEW = IMPORT_OLD  # 変更なし（既に SCRAPE_TARGET_SPEAKERS を import 済）


# ── 差分3: _make_event の重要度分岐 ──
MAKE_EVENT_OLD = '''def _make_event(
    event_date: date,
    speaker: str,
    raw_text: str,
    source: str,
    dt_time: time = DEFAULT_SPEECH_TIME,
) -> Event:
    """Event オブジェクトを生成（議長のみ ★★★ 固定）"""
    imp = Importance.HIGH  # 議長のみ対象 → 常に最高
    etype = _classify_event_type(raw_text)
    short_name = f"{speaker} {etype}"
    if etype == "講演":
        short_name = f"{speaker}講演"

    dt_utc = et_to_utc(event_date, dt_time)
    return Event(
        name_short=make_summary(imp, short_name),
        name_full=f"Fed Chair Speech: {speaker} — {etype} | {raw_text[:160]}",
        dt_utc=dt_utc,
        category="fed",
        importance=int(imp),
        details={
            "speaker": speaker,
            "event_type": etype,
            "description": raw_text[:300],
            "source": source,
        },
        uid_hint=f"FED_CHAIR:{speaker}:{event_date.isoformat()}:{etype}",
    )'''

MAKE_EVENT_NEW = '''def _make_event(
    event_date: date,
    speaker: str,
    raw_text: str,
    source: str,
    dt_time: time = DEFAULT_SPEECH_TIME,
) -> Event:
    """Event オブジェクトを生成（v5.0.1: スピーカー別に重要度分岐）"""
    # v5.0.1: SCRAPE_TARGET_SPEAKERS から重要度を参照
    imp_int = SCRAPE_TARGET_SPEAKERS.get(speaker, 2)
    imp = Importance.HIGH if imp_int == 3 else Importance.MEDIUM

    etype = _classify_event_type(raw_text)

    # v5.0.1: 議長は日本語略称 (パウエル/ウォーシュ)、副議長以下は姓英字
    speaker_jp_map = {
        "Powell": "パウエル",
        "Warsh":  "ウォーシュ",
    }
    speaker_disp = speaker_jp_map.get(speaker, speaker)

    # 役職別の接頭辞 (★★★ の場合は議長を明示、★★ は Jefferson 等の姓のみ)
    if imp == Importance.HIGH:
        # 議長: "パウエル講演" / "パウエル議会証言" 等
        short_name = f"{speaker_disp}{etype}"
    else:
        # 副議長以下: "Jefferson講演" 等 (姓 + 種別)
        short_name = f"{speaker_disp}{etype}"

    # UID 用のラベル
    role_label = "CHAIR" if imp == Importance.HIGH else "VICE_CHAIR"

    dt_utc = et_to_utc(event_date, dt_time)
    return Event(
        name_short=make_summary(imp, short_name),
        name_full=f"Fed {role_label} Speech: {speaker} — {etype} | {raw_text[:160]}",
        dt_utc=dt_utc,
        category="fed",
        importance=int(imp),
        details={
            "speaker": speaker,
            "role": role_label,
            "event_type": etype,
            "description": raw_text[:300],
            "source": source,
        },
        uid_hint=f"FED_{role_label}:{speaker}:{event_date.isoformat()}:{etype}",
    )'''


def patch_fed_speeches(dry_run: bool) -> None:
    path = REPO_ROOT / "scripts" / "fetchers" / "fed_speeches.py"
    if not path.exists():
        print(f"  [ERROR] fed_speeches.py not found: {path}")
        return

    content = path.read_text(encoding="utf-8")
    changes = []

    # 差分1: URL
    if URL_OLD in content:
        changes.append(("URL", URL_OLD, URL_NEW))
    elif f"/newsevents/{{year}}-{{month:02d}}.htm" in content:
        print("  [skip] URL already patched")
    else:
        print("  [WARN] URL_OLD block not found — manual check needed")

    # 差分3: _make_event
    if MAKE_EVENT_OLD in content:
        changes.append(("make_event", MAKE_EVENT_OLD, MAKE_EVENT_NEW))
    elif "speaker_jp_map" in content:
        print("  [skip] _make_event already patched")
    else:
        print("  [WARN] MAKE_EVENT_OLD block not found — manual check needed")

    if not changes:
        print("  [skip] no changes to apply")
        return

    if dry_run:
        for name, old, new in changes:
            print(f"  [dry-run] would replace {name} block ({len(old)} -> {len(new)} chars)")
        return

    backup(path)
    new_content = content
    for name, old, new in changes:
        new_content = new_content.replace(old, new)
    path.write_text(new_content, encoding="utf-8", newline="\n")
    print(f"  [write] {path}")
    for name, _, _ in changes:
        print(f"    applied: {name}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# メイン
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    ap = argparse.ArgumentParser(
        description="Apply v5.0.1 patch: URL bug fix + Jefferson scope extension"
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()

    if args.revert:
        print("=== v5.0.1 revert mode ===")
        revert_all()
        return

    mode = "dry-run" if args.dry_run else "apply"
    print(f"=== v5.0.1 patch ({mode}) ===\n")

    print("[1/2] patching scripts/config.py (add Jefferson) ...")
    patch_config(args.dry_run)

    print("\n[2/2] patching scripts/fetchers/fed_speeches.py (URL + importance) ...")
    patch_fed_speeches(args.dry_run)

    print("\n" + "=" * 60)
    if args.dry_run:
        print("[dry-run complete]")
        print("本適用: python apply_v5_0_1_patch.py")
    else:
        print("[v5.0.1 patch applied]")
        print()
        print("次のステップ:")
        print("  1. コミット & プッシュ:")
        print("     git add -A")
        print('     git commit -m "v5.0.1: Fix URL bug + extend scope to Jefferson (Vice Chair)"')
        print("     git push")
        print()
        print("  2. GitHub Actions → Run workflow で動作確認")
        print()
        print("  3. 検証:")
        print("     git pull")
        print("     python verify_fed_speeches.py --ics docs/us_fed.ics")
        print()
        print("  期待結果:")
        print("     - chair_speech:powell  (FOMC記者会見以外で 0〜1件)")
        print("     - chair_speech:jefferson (副議長発言 3〜7件)")
        print("     - 合計 3〜8 件が表示されれば成功")


if __name__ == "__main__":
    main()
