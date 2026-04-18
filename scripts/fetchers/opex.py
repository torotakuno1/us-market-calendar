"""
Options Expiration & VIX Settlement Fetcher
=============================================
- 月次OpEx: 第3金曜
- 四半期OpEx (Quad Witch): 3/6/9/12月の第3金曜
- VIX最終決済: 原則OpEx前日(水曜)、祝日等で不規則の場合は CSV 参照
- 0DTE参考: 月曜・水曜・金曜のSPX/SPYオプション満期
- S&P 500 四半期リバランス発表: 四半期最終金曜の1週間前金曜

v8.1 (2026-04-18): VIX決済日のCSV例外対応追加
v8.3 (2026-04-18): Russell Reconstitution 半年化対応（v8.3.1 で撤回）
v8.3.1 (2026-04-18): Russell 削除、S&P 500 四半期リバランス発表イベントに差し替え
"""

import csv
from datetime import date, time
from pathlib import Path
from typing import Optional

from config import Importance, make_summary, SP500_REBALANCE_DATES_2026
from utils import Event, et_to_utc, third_friday, previous_wednesday


QUAD_WITCH_MONTHS = {3, 6, 9, 12}


def _load_opex_exceptions(csv_path: Optional[Path]) -> dict[str, date]:
    """CSV: month(YYYY-MM),actual_expiration_date(YYYY-MM-DD)"""
    exc = {}
    if csv_path and csv_path.exists():
        with open(csv_path, encoding="utf-8") as f:
            for row in csv.reader(f):
                if not row or row[0].startswith("#"):
                    continue
                exc[row[0].strip()] = date.fromisoformat(row[1].strip())
    return exc


def _load_vix_exceptions(csv_path: Optional[Path]) -> dict[str, tuple[date, str]]:
    """
    VIX最終決済日の明示テーブル。
    CSV: month(YYYY-MM),settlement_date(YYYY-MM-DD),note
    戻り値: { "YYYY-MM": (date, note_str) }

    v8.1: このCSVがある月は previous_wednesday 算出を上書きする。
    """
    exc: dict[str, tuple[date, str]] = {}
    if csv_path and csv_path.exists():
        with open(csv_path, encoding="utf-8") as f:
            for row in csv.reader(f):
                if not row or row[0].startswith("#"):
                    continue
                if len(row) < 2:
                    continue
                month_key = row[0].strip()
                d = date.fromisoformat(row[1].strip())
                note = row[2].strip() if len(row) >= 3 else ""
                exc[month_key] = (d, note)
    return exc


def fetch_opex_events(
    start: date,
    end: date,
    exceptions_csv: Optional[Path] = None,
    vix_exceptions_csv: Optional[Path] = None,  # ← 追加
) -> list[Event]:
    events = []
    exceptions = _load_opex_exceptions(exceptions_csv)
    vix_exceptions = _load_vix_exceptions(vix_exceptions_csv)

    d = date(start.year, start.month, 1)
    while d <= end:
        year, month = d.year, d.month
        month_key = f"{year}-{month:02d}"

        # OpEx日
        if month_key in exceptions:
            opex_date = exceptions[month_key]
        else:
            opex_date = third_friday(year, month)

        if start <= opex_date <= end:
            is_quad = month in QUAD_WITCH_MONTHS
            label = "Quad Witch" if is_quad else "月次OpEx"
            imp = Importance.HIGH if is_quad else Importance.MEDIUM

            events.append(Event(
                name_short=make_summary(imp, label),
                name_full=f"Monthly Options Expiration {'(Quad Witching)' if is_quad else ''}",
                dt_utc=et_to_utc(opex_date, time(16, 0)),
                category="opex",
                importance=int(imp),
                all_day=True,
                details={
                    "note": "Quad Witch: 株式先物/オプション + 指数先物/オプション 同時満期" if is_quad else "",
                    "source": "Calculated (3rd Friday rule)",
                },
                uid_hint=f"OPEX:{opex_date.isoformat()}",
            ))

            # VIX最終決済
            # 優先順位: (1) vix_exceptions.csv → (2) previous_wednesday フォールバック
            vix_source: str
            vix_extra_note: str = ""
            if month_key in vix_exceptions:
                vix_date, vix_extra_note = vix_exceptions[month_key]
                vix_source = "CBOE (vix_exceptions.csv)"
            else:
                vix_date = previous_wednesday(opex_date)
                vix_source = "CBOE (fallback: previous_wednesday)"

            if vix_date != opex_date and start <= vix_date <= end:
                detail_note = "SOQ (Special Opening Quotation) で決済値算出"
                if vix_extra_note:
                    detail_note = f"{detail_note} | {vix_extra_note}"

                events.append(Event(
                    name_short=make_summary(Importance.MEDIUM, "VIX最終決済"),
                    name_full="VIX Final Settlement (AM settlement)",
                    dt_utc=et_to_utc(vix_date, time(8, 30)),
                    category="opex",
                    importance=2,
                    details={
                        "note": detail_note,
                        "source": vix_source,
                    },
                    uid_hint=f"VIX_SETTLE:{vix_date.isoformat()}",
                ))

        # 次月
        if month == 12:
            d = date(year + 1, 1, 1)
        else:
            d = date(year, month + 1, 1)

    # ── S&P 500 四半期リバランス発表イベント（静的リスト） ──
    sp500_events = _build_sp500_rebalance_events(start, end)
    events.extend(sp500_events)

    # ── 既存 Quad Witch イベントの DESCRIPTION に S&P リバランス情報を付与 ──
    events = _enrich_quad_witch_descriptions(events)

    return events


def _build_sp500_rebalance_events(start: date, end: date) -> list[Event]:
    """
    S&P 500 / 400 / 600 四半期リバランスの事前発表イベントを生成する。

    実施日（第3金曜）は既存の Quad Witch と同日なので重複登録せず、
    事前発表日のみを独立イベントとして登録する。

    事前発表は実施日の約1週間前の金曜 market close 後に S&P Dow Jones Indices が
    press release を出す。市場参加者は翌週の rebalance に向けて動くため注目度 ★★。
    """
    events = []

    for entry in SP500_REBALANCE_DATES_2026:
        ann_str = entry.get("announcement", "")
        eff_str = entry.get("effective", "")
        quarter = entry.get("quarter", "")

        try:
            ann_date = date.fromisoformat(ann_str)
            eff_date = date.fromisoformat(eff_str)
        except ValueError:
            print(f"  [opex] invalid SP500 rebalance date: {ann_str} / {eff_str}")
            continue

        if not (start <= ann_date <= end):
            continue

        # 発表は「金曜 market close 後」なので ET 16:30 を使う
        dt_utc = et_to_utc(ann_date, time(16, 30))

        events.append(Event(
            name_short=make_summary(Importance.MEDIUM, f"S&P {quarter}リバランス発表"),
            name_full=f"S&P 500/400/600 Quarterly Rebalance Announcement ({quarter})",
            dt_utc=dt_utc,
            category="opex",
            importance=int(Importance.MEDIUM),
            all_day=False,
            details={
                "source": "S&P Dow Jones Indices",
                "note": f"Effective {eff_date.isoformat()}（翌週の第3金曜 close = Quad Witch 同日）",
            },
            uid_hint=f"SP500_REBAL_ANN:{ann_date.isoformat()}",
        ))

    if events:
        print(f"  [opex] {len(events)} S&P 500 rebalance announcement events")
    return events


def _enrich_quad_witch_descriptions(events: list[Event]) -> list[Event]:
    """
    既存の Quad Witch イベント（opex カテゴリ、name_short に "Quad Witch" を含む）に、
    S&P 500 四半期リバランス実施日であることを DESCRIPTION 末尾に追記する。

    これにより Quad Witch イベントを見るだけで S&P リバランス実施日であることが分かる。
    """
    from datetime import timedelta

    quad_witch_dates = {
        date.fromisoformat(e["effective"])
        for e in SP500_REBALANCE_DATES_2026
    }

    for ev in events:
        if ev.category != "opex":
            continue
        if "Quad Witch" not in ev.name_short:
            continue

        ev_date = ev.dt_utc.date()
        # UTC からズレる可能性があるので ET ベースでも確認
        # （Quad Witch は 16:00 ET = 翌日 04:00 UTC の場合あり）
        # 簡便のため ev.dt_utc.date() と ±1day の両方を見る
        candidate_dates = {
            ev_date,
            ev_date - timedelta(days=1),
            ev_date + timedelta(days=1),
        }
        match_date = next((d for d in candidate_dates if d in quad_witch_dates), None)
        if not match_date:
            continue

        existing_note = ev.details.get("note", "")
        enrichment = "S&P 500/400/600 四半期リバランス実施日（前週末発表→当日close実施）"
        if enrichment not in existing_note:
            new_note = f"{existing_note} | {enrichment}" if existing_note else enrichment
            ev.details["note"] = new_note

    return events
