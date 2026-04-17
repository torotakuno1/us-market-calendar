"""
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
