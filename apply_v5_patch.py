#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_v5_patch.py — V5 Fed Speaker Scraper 復活 + FOMCロスター更新
===================================================================

実施内容（v5+v5.1 一括）:
  1. scripts/fetchers/fed_speeches.py を新規作成（Playwright主経路 + HTMLアーカイブ副経路）
  2. scripts/fetchers/fed.py を修正（旧 _fetch_fed_speeches 廃止、新モジュール呼び出しに置換）
  3. scripts/config.py を修正:
     - FED_KEY_SPEAKERS を dict 化（役職・重要度を構造化、Miran追加）
     - CHAIR_CANDIDATES 定数追加（Powell, Warsh）
     - SCRAPE_TARGET_SPEAKERS 定数追加（スクレイピング対象 = 議長候補のみ）
  4. requirements.txt に playwright>=1.40 追加
  5. .github/workflows/build.yml に Chromium インストールステップ追加
  6. verify_fed_speeches.py 新規作成（検証ツール）

使い方:
    python apply_v5_patch.py          # パッチ適用
    python apply_v5_patch.py --dry-run # 変更内容の表示のみ
    python apply_v5_patch.py --revert  # バックアップから復元

前提:
  - Windows CMD / Python 3.11
  - リポジトリルートで実行
  - v10.2 までの状態を想定
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
BACKUP_SUFFIX = ".bak_v5"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ヘルパー
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def backup(path: Path) -> None:
    """ファイルをタイムスタンプ付きでバックアップ。"""
    if not path.exists():
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + f"{BACKUP_SUFFIX}_{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {path.name} -> {bak.name}")


def write(path: Path, content: str, *, dry_run: bool) -> None:
    if dry_run:
        print(f"  [dry-run] would write {path} ({len(content)} chars)")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    print(f"  [write] {path}")


def revert_all() -> None:
    """*.bak_v5_* ファイルからの復元。最新のバックアップを使う。"""
    restored = 0
    for bak in sorted(REPO_ROOT.rglob(f"*{BACKUP_SUFFIX}_*"), reverse=True):
        # 元パス推定: foo.py.bak_v5_20260418_153045 → foo.py
        parts = bak.name.rsplit(BACKUP_SUFFIX, 1)
        if len(parts) != 2:
            continue
        orig_name = parts[0].rstrip(".")  # 末尾のドット除去
        # 上と違い shutil.copy2 で生成された .bak_v5_TS のフォーマットを復元
        orig = bak.with_name(parts[0]) if parts[0].endswith(".py") or parts[0].endswith(".txt") or parts[0].endswith(".yml") else None
        if orig is None:
            # ファイル名全体を見て拡張子保存されているケース
            base = bak.name[: -len(bak.suffix)]
            orig = bak.with_name(base.rsplit(BACKUP_SUFFIX, 1)[0])
        if orig.exists():
            # 同名ファイルが既に存在する場合のみ上書きして復元
            shutil.copy2(bak, orig)
            print(f"  [revert] {bak.name} -> {orig.name}")
            restored += 1
    print(f"[done] restored {restored} files")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 新規ファイル: fed_speeches.py
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FED_SPEECHES_PY = r'''"""
Fed Chair Speeches Fetcher (v5)
=================================
議長（Powell / Warsh）の講演・証言のみを取得する。

データソース優先順:
  1. 月別カレンダー (/newsevents/YYYY-MM.htm) — Playwright でレンダリング
     - 事前予告を含む（1-4週間先まで）
  2. 年別アーカイブ (/newsevents/speech/YYYY-speeches.htm) — 静的HTML
     - 事後アーカイブ、数週間遅延で確定情報
  3. トップページ最新 (/) — 静的HTML
     - 直近10件程度、最も速報性高い

Playwright が使えない環境（ローカルWindows 等）では自動的に
アーカイブ＋トップページのみの取得に縮退する。
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional

import requests
from bs4 import BeautifulSoup

from config import (
    SCRAPE_TARGET_SPEAKERS,  # v5で新設: {"Powell": 3, "Warsh": 3, ...}
    CHAIR_CANDIDATES,        # v5で新設: ["Powell", "Warsh"]
    Importance,
    make_summary,
)
from utils import Event, et_to_utc


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 定数
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FED_BASE = "https://www.federalreserve.gov"
USER_AGENT = "US-Market-Calendar/1.0 (+https://github.com/torotakuno1/us-market-calendar)"
DEFAULT_TIMEOUT = 30
DEFAULT_SPEECH_TIME = time(12, 0)  # 未確定時のデフォルト 12:00 ET


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ヘルパー
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _is_chair(speaker: str) -> bool:
    """議長候補リストにマッチするか（部分一致）"""
    s = speaker.lower()
    return any(c.lower() in s for c in CHAIR_CANDIDATES)


def _detect_speaker(text: str) -> Optional[str]:
    """テキストから議長候補の姓を抽出"""
    t = text.lower()
    for name in CHAIR_CANDIDATES:
        if name.lower() in t:
            return name
    return None


def _classify_event_type(text: str) -> str:
    """講演種別を粗く分類"""
    t = text.lower()
    if "press conference" in t:
        return "FOMC記者会見"
    if "testimony" in t or "before the" in t or "semiannual" in t:
        return "議会証言"
    if "jackson hole" in t or "economic policy symposium" in t:
        return "ジャクソンホール"
    if "discussion" in t or "conversation" in t or "q&a" in t or "participates" in t:
        return "討論/対談"
    if "remarks" in t or "opening remarks" in t:
        return "挨拶"
    return "講演"


def _make_event(
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
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 経路A: Playwright で月別カレンダーをレンダリング
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MONTH_NAMES_EN = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]


def _iter_months(start: date, end: date) -> Iterable[tuple[int, int]]:
    """start〜end+3ヶ月 の (year, month) を列挙"""
    # v5 要件: 現在月〜+3ヶ月（計4ヶ月）を走査
    scan_end = end + timedelta(days=31 * 3)
    y, m = start.year, start.month
    while (y, m) <= (scan_end.year, scan_end.month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def _fetch_month_via_playwright(year: int, month: int) -> list[tuple[date, str]]:
    """
    月別カレンダーページ（JS 必須）を Playwright で取得。
    戻り値: [(event_date, raw_text), ...] (議長のみフィルタ済)
    失敗時は [] を返す（呼び出し側でフォールバック）
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [fed_speeches] playwright not installed — skipping month render")
        return []

    # URL パターンは月名英語フル or MM 数字の2系統あり
    mname = MONTH_NAMES_EN[month - 1]
    url = f"{FED_BASE}/newsevents/{year}-{mname}.htm"

    results: list[tuple[date, str]] = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=USER_AGENT)
            page = context.new_page()
            try:
                page.goto(url, timeout=45000, wait_until="domcontentloaded")
                # JS レンダリング待ち（カレンダー要素が現れるまで）
                try:
                    page.wait_for_selector("div#article, main, .col-md-9", timeout=15000)
                except Exception:
                    pass  # セレクタ不確定でも body 全体で試行
                html = page.content()
            finally:
                browser.close()
    except Exception as ex:
        print(f"  [fed_speeches] playwright failed for {year}-{month:02d}: {ex}")
        return []

    # HTML 解析
    soup = BeautifulSoup(html, "html.parser")
    # 本文領域の全テキストを行単位で舐める（構造が変わっても生きる保険）
    main = soup.find("div", id="article") or soup.find("main") or soup.body or soup
    text = main.get_text("\n", strip=True)

    # 行パース: 日付行の直後に "Speech - Chair Jerome H. Powell" 等が続く
    # Fed の実サイト構造に合わせた寛容パース
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    current_day: Optional[int] = None

    day_re = re.compile(r"^(\d{1,2})$")
    # "Speech - Chair Jerome H. Powell" / "Testimony - Chair Powell" /
    # "Press Conference" 等を拾う
    event_re = re.compile(
        r"(Speech|Testimony|Press Conference|Discussion|Remarks|Participates)\s*[-—–]\s*(.+)",
        re.IGNORECASE,
    )

    for ln in lines:
        dm = day_re.match(ln)
        if dm:
            try:
                day = int(dm.group(1))
                if 1 <= day <= 31:
                    current_day = day
            except ValueError:
                pass
            continue

        em = event_re.search(ln)
        if em and current_day is not None:
            role_and_name = em.group(2)
            if _is_chair(role_and_name):
                try:
                    d = date(year, month, current_day)
                except ValueError:
                    continue
                results.append((d, ln))

    if results:
        print(f"  [fed_speeches] playwright {year}-{month:02d}: {len(results)} chair events")
    return results


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 経路B: 年別アーカイブ（静的HTML）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fetch_year_speech_archive(year: int) -> list[tuple[date, str]]:
    """
    /newsevents/speech/YYYY-speeches.htm を取得。
    静的HTML、リクエスト1回で全件取れる。
    """
    url = f"{FED_BASE}/newsevents/speech/{year}-speeches.htm"
    results: list[tuple[date, str]] = []

    try:
        resp = requests.get(url, timeout=DEFAULT_TIMEOUT,
                            headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except Exception as ex:
        print(f"  [fed_speeches] archive fetch failed ({year}): {ex}")
        return results

    soup = BeautifulSoup(resp.text, "html.parser")
    main = soup.find("div", id="article") or soup.body or soup
    text = main.get_text("\n", strip=True)

    # アーカイブの実フォーマット:
    #   "3/3/2026"
    #   "Liquidity Resiliency, ..." (タイトル)
    #   "Vice Chair for Supervision Michelle W. Bowman" (役職・名前)
    #   "At The Roundtable..." (会場)
    # → 日付行の後、数行以内に役職/名前が出てくる
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    date_re = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")

    for i, ln in enumerate(lines):
        dm = date_re.match(ln)
        if not dm:
            continue
        try:
            mm, dd, yyyy = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
            d = date(yyyy, mm, dd)
        except ValueError:
            continue

        # 後続10行以内で名前を探す
        context_lines = lines[i + 1 : i + 10]
        ctx = " ".join(context_lines)
        if _is_chair(ctx):
            # タイトル行（日付直後の Italic/括弧行）と名前行を組み立て
            title = context_lines[0] if context_lines else ""
            results.append((d, f"{title} | {ctx[:200]}"))

    return results


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 経路C: トップページ最新 (Recent Developments)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fetch_top_recent() -> list[tuple[date, str]]:
    """
    federalreserve.gov/ トップの最新10件程度から Chair 発言を抽出。
    速報性が高いが、既に発表された直後のもの（事後）。
    """
    url = f"{FED_BASE}/"
    results: list[tuple[date, str]] = []

    try:
        resp = requests.get(url, timeout=DEFAULT_TIMEOUT,
                            headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except Exception as ex:
        print(f"  [fed_speeches] top page fetch failed: {ex}")
        return results

    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text("\n", strip=True)

    # "Speech - 4/17/2026" 形式の行を拾う
    # トップページは "Speech by Governor Waller on..." が別行
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    date_tag_re = re.compile(
        r"(?:Speech|Testimony|Press Release)\s*[-—–]\s*(\d{1,2})/(\d{1,2})/(\d{4})",
        re.IGNORECASE,
    )

    for i, ln in enumerate(lines):
        tm = date_tag_re.search(ln)
        if not tm:
            continue
        try:
            mm, dd, yyyy = int(tm.group(1)), int(tm.group(2)), int(tm.group(3))
            d = date(yyyy, mm, dd)
        except ValueError:
            continue

        # 前後 3行を context とする（タイトルが前行にあるケース）
        start_idx = max(0, i - 2)
        ctx_lines = lines[start_idx : i + 2]
        ctx = " ".join(ctx_lines)
        if _is_chair(ctx):
            results.append((d, ctx[:200]))

    return results


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# メインエントリポイント
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_fed_chair_speeches(start: date, end: date) -> list[Event]:
    """
    議長（Powell / Warsh）の講演・証言イベントを収集。

    実装:
      - 月別カレンダー (Playwright) を最優先
      - 年別アーカイブ (静的HTML) で補完
      - トップページ最新で追加補完
      - 全ソースの結果をマージし、(日付, 話者) の重複は除去
    """
    all_hits: dict[tuple[date, str], tuple[date, str, str]] = {}
    # key=(date, speaker), value=(date, text, source)

    # ── 経路A: Playwright 月別 ──
    for year, month in _iter_months(start, end):
        month_start = date(year, month, 1)
        if month_start > end + timedelta(days=120):
            break
        hits = _fetch_month_via_playwright(year, month)
        for d, txt in hits:
            sp = _detect_speaker(txt)
            if sp is None:
                continue
            key = (d, sp)
            if key not in all_hits:
                all_hits[key] = (d, txt, "month_calendar(playwright)")

    # ── 経路B: 年別アーカイブ ──
    years = {start.year, end.year}
    for y in sorted(years):
        hits = _fetch_year_speech_archive(y)
        for d, txt in hits:
            sp = _detect_speaker(txt)
            if sp is None:
                continue
            key = (d, sp)
            if key not in all_hits:
                all_hits[key] = (d, txt, "year_archive(static)")

    # ── 経路C: トップページ最新 ──
    hits = _fetch_top_recent()
    for d, txt in hits:
        sp = _detect_speaker(txt)
        if sp is None:
            continue
        key = (d, sp)
        if key not in all_hits:
            all_hits[key] = (d, txt, "top_page(static)")

    # ── 範囲フィルタ ──
    events: list[Event] = []
    for (d, sp), (d_, txt, src) in sorted(all_hits.items()):
        if not (start <= d <= end + timedelta(days=120)):
            continue
        events.append(_make_event(d, sp, txt, src))

    print(f"  [fed_speeches] total {len(events)} chair events found")
    return events
'''


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 新規ファイル: verify_fed_speeches.py
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

VERIFY_FED_SPEECHES_PY = r'''#!/usr/bin/env python3
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
'''


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# config.py 差分（diff 形式ではなく、該当ブロック全置換）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 置換対象: 現行の FED_KEY_SPEAKERS ブロック（コメント込）
# 正規表現で検索
CONFIG_OLD_BLOCK_RE = r"""# ── Fed発言 フィルタ.*?"Waller",\s*#[^\n]*\n\]"""

CONFIG_NEW_BLOCK = '''# ── Fed発言 フィルタ (v5で構造化) ─────────────────────
# SCRAPE_TARGET_SPEAKERS: スクレイピング対象 = 議長候補のみ
#   - key: 姓（URLパラメータや発言スクレイピングで部分一致に使用）
#   - value: 重要度 (3=★★★)
#
# CHAIR_CANDIDATES: 議長候補リスト
#   - 2026-05-15 までは Powell
#   - 2026-05-15 以降は Warsh (上院承認待ち、遅延時は Jefferson 代行)
#   - Warsh/Jefferson を入れることで承認遅延時もイベント取得を継続
#
# FED_KEY_SPEAKERS: 全理事会メンバー（参考用・将来拡張可能性）
#   現在は SCRAPE_TARGET_SPEAKERS のみが fed_speeches.py で使用される

CHAIR_CANDIDATES: list[str] = [
    "Powell",    # 2018-02-05 ~ 2026-05-15 (任期満了)
    "Warsh",     # 2026-05-15 以降 (Trump 指名、上院承認待ち)
]

SCRAPE_TARGET_SPEAKERS: dict[str, int] = {
    "Powell": 3,
    "Warsh":  3,
}

# 全理事会メンバー（2026-04 時点・参考・将来 v5.2 以降で拡張可能）
FED_KEY_SPEAKERS: dict[str, dict] = {
    "Powell":    {"role": "Chair",                    "importance": 3, "term_end": "2026-05-15"},
    "Warsh":     {"role": "Chair (nominated)",        "importance": 3, "term_start": "2026-05-15"},
    "Jefferson": {"role": "Vice Chair",               "importance": 2, "term_end": "2027-09-07"},
    "Bowman":    {"role": "Vice Chair for Supervision", "importance": 2, "term_start": "2025-06-09"},
    "Barr":      {"role": "Governor",                 "importance": 2, "term_end": "2032-01-31"},
    "Cook":      {"role": "Governor",                 "importance": 2, "term_end": "2038-01-31"},
    "Miran":     {"role": "Governor",                 "importance": 2, "term_end": "2026-01-31 (holdover)"},
    "Waller":    {"role": "Governor",                 "importance": 2, "term_end": "2030-01-31"},
}'''


def patch_config(dry_run: bool) -> None:
    """scripts/config.py の FED_KEY_SPEAKERS ブロックを置換"""
    import re

    config_path = REPO_ROOT / "scripts" / "config.py"
    if not config_path.exists():
        print(f"  [skip] config.py not found: {config_path}")
        return

    content = config_path.read_text(encoding="utf-8")

    # 現行ブロックを検索（コメント部 + list 定義）
    pattern = re.compile(
        r'# ── Fed発言 フィルタ[^\n]*\n'
        r'# [^\n]*\n'
        r'FED_KEY_SPEAKERS: list\[str\] = \[\n'
        r'(?:    "[^"]+",\s*#[^\n]*\n)+'
        r'\]',
        re.MULTILINE,
    )

    m = pattern.search(content)
    if not m:
        print("  [WARN] FED_KEY_SPEAKERS block not found in expected format")
        print("         Attempting looser match...")
        # ゆるいマッチで再トライ
        pattern2 = re.compile(
            r'FED_KEY_SPEAKERS: list\[str\] = \[[^\]]+\]',
            re.DOTALL,
        )
        m = pattern2.search(content)
        if not m:
            print("  [ERROR] could not locate FED_KEY_SPEAKERS definition")
            return

    if dry_run:
        print(f"  [dry-run] would replace {len(m.group(0))} chars in config.py")
        return

    backup(config_path)
    new_content = content[: m.start()] + CONFIG_NEW_BLOCK + content[m.end() :]
    config_path.write_text(new_content, encoding="utf-8", newline="\n")
    print(f"  [write] {config_path} (replaced FED_KEY_SPEAKERS block)")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# fed.py 差分
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 旧 _fetch_fed_speeches() と _parse_fed_date() は削除
# import 文と最終呼び出し箇所を新モジュール呼出に置換

FED_PY_NEW = '''"""
Fed Events Fetcher
===================
- FOMC 金利決定 / 記者会見 / 議事録（静的）
- ベージュブック（静的）
- 鉱工業生産 G.17（v10.2 で削除済み、PFEI側で一元管理）
- Fed議長発言（v5: fed_speeches.py に分離、Playwright + HTMLアーカイブ）
"""

from datetime import date, time

from config import (
    FOMC_DATES, BEIGE_BOOK_DATES,
    Importance, make_summary,
)
from utils import Event, et_to_utc


def fetch_fed_events(start: date, end: date) -> list[Event]:
    events = []

    # ── FOMC 金利決定 / 記者会見 / 議事録 ──
    for fomc in FOMC_DATES:
        decision_date = date.fromisoformat(fomc["decision"])
        if start <= decision_date <= end:
            dt_utc = et_to_utc(decision_date, time(14, 0))
            events.append(Event(
                name_short=make_summary(Importance.HIGH, "FOMC 金利決定"),
                name_full="FOMC Interest Rate Decision + Statement + Dot Plot (if SEP meeting)",
                dt_utc=dt_utc,
                category="fed",
                importance=3,
                details={
                    "note": "声明文 14:00 ET / 記者会見 14:30 ET",
                    "source": "federalreserve.gov",
                },
                uid_hint=f"FOMC_DEC:{decision_date.isoformat()}",
            ))

            dt_press = et_to_utc(decision_date, time(14, 30))
            # v5: 議長名を動的に（Powell or Warsh 後任）
            # 2026-05-15 以降は Warsh 想定
            press_chair = "Warsh" if decision_date >= date(2026, 5, 15) else "Powell"
            chair_jp = "ウォーシュ" if press_chair == "Warsh" else "パウエル"
            events.append(Event(
                name_short=make_summary(Importance.HIGH, f"{chair_jp}記者会見"),
                name_full=f"FOMC Press Conference (Chair {press_chair})",
                dt_utc=dt_press,
                category="fed",
                importance=3,
                details={"source": "federalreserve.gov", "chair": press_chair},
                uid_hint=f"FOMC_PRESS:{decision_date.isoformat()}",
            ))

        minutes_date = date.fromisoformat(fomc["minutes"])
        if start <= minutes_date <= end:
            dt_utc = et_to_utc(minutes_date, time(14, 0))
            events.append(Event(
                name_short=make_summary(Importance.MEDIUM, "FOMC議事録"),
                name_full=f"FOMC Minutes (from {fomc['decision']} meeting)",
                dt_utc=dt_utc,
                category="fed",
                importance=2,
                details={"source": "federalreserve.gov"},
                uid_hint=f"FOMC_MIN:{minutes_date.isoformat()}",
            ))

    # ── ベージュブック ──
    for d_str in BEIGE_BOOK_DATES:
        d = date.fromisoformat(d_str)
        if start <= d <= end:
            dt_utc = et_to_utc(d, time(14, 0))
            events.append(Event(
                name_short=make_summary(Importance.MEDIUM, "ベージュブック"),
                name_full="Beige Book (Summary of Commentary on Current Economic Conditions)",
                dt_utc=dt_utc,
                category="fed",
                importance=2,
                details={
                    "note": "地区連銀経済報告 — FOMC前の景気判断材料",
                    "source": "federalreserve.gov",
                },
                uid_hint=f"BEIGE:{d.isoformat()}",
            ))

    # ── 鉱工業生産 G.17 ──
    # v10.2 で削除済み（PFEI 側で econ_data.py から取得）

    # ── Fed議長発言（v5: Playwright + 静的アーカイブ） ──
    try:
        from fetchers.fed_speeches import fetch_fed_chair_speeches
        speeches = fetch_fed_chair_speeches(start, end)
        events.extend(speeches)
    except Exception as ex:
        print(f"  [fed] chair speeches module failed: {ex}")

    return events
'''


def patch_fed_py(dry_run: bool) -> None:
    path = REPO_ROOT / "scripts" / "fetchers" / "fed.py"
    if not path.exists():
        print(f"  [skip] fed.py not found: {path}")
        return
    if dry_run:
        print(f"  [dry-run] would overwrite {path} ({len(FED_PY_NEW)} chars)")
        return
    backup(path)
    path.write_text(FED_PY_NEW, encoding="utf-8", newline="\n")
    print(f"  [write] {path}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# requirements.txt 差分
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def patch_requirements(dry_run: bool) -> None:
    path = REPO_ROOT / "requirements.txt"
    if not path.exists():
        print(f"  [skip] requirements.txt not found: {path}")
        return

    content = path.read_text(encoding="utf-8")

    new_line = "playwright>=1.40"
    if "playwright" in content.lower():
        print(f"  [skip] playwright already in requirements.txt")
        return

    if dry_run:
        print(f"  [dry-run] would append '{new_line}' to requirements.txt")
        return

    backup(path)
    if not content.endswith("\n"):
        content += "\n"
    content += f"{new_line}\n"
    path.write_text(content, encoding="utf-8", newline="\n")
    print(f"  [write] {path} (added {new_line})")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# build.yml 差分
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BUILD_YML_NEW = '''name: build-ics

on:
  schedule:
    - cron: "0 18 * * *"   # 03:00 JST (毎日)
    - cron: "0 6 * * *"    # 15:00 JST (米市場開場前の更新)
  workflow_dispatch:
    inputs:
      months:
        description: 'Months to generate'
        default: '3'
      no_earnings:
        description: 'Skip earnings fetch'
        type: boolean
        default: false

permissions:
  contents: write

concurrency:
  group: build-ics
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    timeout-minutes: 25   # v5: Playwright Chromium ダウンロード分の余裕

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Cache pip
        uses: actions/cache@v4
        with:
          path: ~/.cache/pip
          key: ${{ runner.os }}-pip-${{ hashFiles('requirements.txt') }}

      # v5: Playwright ブラウザキャッシュ
      - name: Cache Playwright browsers
        uses: actions/cache@v4
        with:
          path: ~/.cache/ms-playwright
          key: ${{ runner.os }}-playwright-chromium-v1

      - name: Install deps
        run: pip install -r requirements.txt

      # v5: Chromium インストール（キャッシュヒット時はスキップ動作）
      - name: Install Playwright Chromium
        run: python -m playwright install --with-deps chromium

      - name: Ensure output dir
        run: mkdir -p docs

      - name: Generate ICS
        env:
          FRED_API_KEY: ${{ secrets.FRED_API_KEY }}
          FINNHUB_API_KEY: ${{ secrets.FINNHUB_API_KEY }}
          FMP_API_KEY: ${{ secrets.FMP_API_KEY }}
        run: |
          ARGS="--months ${{ github.event.inputs.months || '3' }}"
          if [ "${{ github.event.inputs.no_earnings }}" = "true" ]; then
            ARGS="$ARGS --no-earnings"
          fi
          python scripts/run_all.py $ARGS

      - name: List output
        run: ls -la docs/

      - name: Commit & Push
        run: |
          git config user.name "github-actions"
          git config user.email "actions@github.com"
          git add docs/*.ics
          if git diff --cached --quiet; then
            echo "No changes"
          else
            git commit -m "update ics $(date -u +%Y-%m-%dT%H:%M)"
            # ICS は毎回完全再生成されるので、コンフリクト時は自分の生成結果を優先
            for i in 1 2 3; do
              if git push; then
                echo "Push succeeded on attempt $i"
                exit 0
              fi
              echo "Push failed (attempt $i), rebasing with -X theirs..."
              git fetch origin main
              git rebase -X theirs origin/main || { git rebase --abort; sleep $((i*3)); }
            done
            echo "Push failed after 3 attempts" >&2
            exit 1
          fi
'''


def patch_build_yml(dry_run: bool) -> None:
    path = REPO_ROOT / ".github" / "workflows" / "build.yml"
    if not path.exists():
        print(f"  [skip] build.yml not found: {path}")
        return
    if dry_run:
        print(f"  [dry-run] would overwrite {path} ({len(BUILD_YML_NEW)} chars)")
        return
    backup(path)
    path.write_text(BUILD_YML_NEW, encoding="utf-8", newline="\n")
    print(f"  [write] {path}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# メイン
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    ap = argparse.ArgumentParser(
        description="Apply v5 patch: Fed Chair Speech scraper (Playwright)"
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="変更内容を表示のみ（書き込まない）")
    ap.add_argument("--revert", action="store_true",
                    help="バックアップから復元")
    args = ap.parse_args()

    if args.revert:
        print("=== v5 revert mode ===")
        revert_all()
        return

    mode = "dry-run" if args.dry_run else "apply"
    print(f"=== v5 patch ({mode}) ===\n")

    # 1. config.py: FED_KEY_SPEAKERS 構造化
    print("[1/6] patching scripts/config.py ...")
    patch_config(args.dry_run)

    # 2. fed.py: 旧スクレイパー削除、新モジュール呼び出しに
    print("\n[2/6] patching scripts/fetchers/fed.py ...")
    patch_fed_py(args.dry_run)

    # 3. fed_speeches.py: 新規作成
    print("\n[3/6] creating scripts/fetchers/fed_speeches.py ...")
    fs_path = REPO_ROOT / "scripts" / "fetchers" / "fed_speeches.py"
    if fs_path.exists() and not args.dry_run:
        backup(fs_path)
    write(fs_path, FED_SPEECHES_PY, dry_run=args.dry_run)

    # 4. requirements.txt: playwright 追加
    print("\n[4/6] patching requirements.txt ...")
    patch_requirements(args.dry_run)

    # 5. build.yml: Chromium インストールステップ追加
    print("\n[5/6] patching .github/workflows/build.yml ...")
    patch_build_yml(args.dry_run)

    # 6. verify_fed_speeches.py: 新規
    print("\n[6/6] creating verify_fed_speeches.py ...")
    vf_path = REPO_ROOT / "verify_fed_speeches.py"
    if vf_path.exists() and not args.dry_run:
        backup(vf_path)
    write(vf_path, VERIFY_FED_SPEECHES_PY, dry_run=args.dry_run)

    print("\n" + "=" * 60)
    if args.dry_run:
        print("[dry-run complete] 変更は書き込まれていません")
        print("本適用: python apply_v5_patch.py")
    else:
        print("[v5 patch applied]")
        print()
        print("次のステップ:")
        print("  1. ローカルテスト（任意）:")
        print("     pip install -r requirements.txt")
        print("     python -m playwright install chromium")
        print("     python scripts/run_all.py --months 1")
        print("     python verify_fed_speeches.py")
        print()
        print("  2. コミット & プッシュ:")
        print("     git add -A")
        print('     git commit -m "v5: Fed Chair speech scraper + FOMC roster update"')
        print("     git push")
        print()
        print("  3. GitHub Actions の Run workflow で動作確認")
        print("     https://github.com/torotakuno1/us-market-calendar/actions")
        print()
        print("  4. 検証:")
        print("     python verify_fed_speeches.py --ics docs/us_fed.ics")


if __name__ == "__main__":
    main()
