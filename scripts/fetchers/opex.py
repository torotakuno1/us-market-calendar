"""
Options Expiration & VIX Settlement Fetcher
=============================================
- 月次OpEx: 第3金曜
- 四半期OpEx (Quad Witch): 3/6/9/12月の第3金曜
- VIX最終決済: 原則OpEx前日(水曜)、祝日等で不規則の場合は CSV 参照
- 0DTE参考: 月曜・水曜・金曜のSPX/SPYオプション満期
- Russell Reconstitution: 2026年から半年化（6月第4金曜 + 12月第2金曜）

v8.1 (2026-04-18): VIX決済日のCSV例外対応追加
v8.3 (2026-04-18): Russell Reconstitution 半年化対応
"""

import csv
from datetime import date, time
from pathlib import Path
from typing import Optional

from config import Importance, make_summary, RUSSELL_DATES_2026
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

    # ── Russell Reconstitution イベント（静的リスト） ──
    russell_events = _build_russell_events(start, end)
    events.extend(russell_events)

    return events


def _build_russell_events(start: date, end: date) -> list[Event]:
    """
    FTSE Russell US Indexes Reconstitution イベントを生成する。

    2026年から半年化:
      - 6月: Rank Day (4月最終営業日) → Preliminary List (5月第4金曜) → Reconstitution (6月第4金曜)
      - 12月: Rank Day (10月最終営業日) → Reconstitution (12月第2金曜)

    タイムゾーン: 全て米国東部時間の市場終了 (16:00 ET) イベントとして一日扱い。
    Preliminary List は 18:00 ET 発表だが、単純化のため同じ日付ラベルで登録。

    重要度:
      - reconstitution: ★★★（年間最大の流動性イベント）
      - rank_day: ★★
      - preliminary: ★
    """
    events = []

    type_to_params = {
        "reconstitution": {
            "label": "Russell リバランス",
            "importance": Importance.HIGH,
            "full_name": "Russell US Indexes Reconstitution (終値で実施)",
            "time_et": time(16, 0),
        },
        "rank_day": {
            "label": "Russell Rank Day",
            "importance": Importance.MEDIUM,
            "full_name": "Russell US Indexes Rank Day (構成銘柄の基準日)",
            "time_et": time(16, 0),
        },
        "preliminary": {
            "label": "Russell 暫定リスト",
            "importance": Importance.LOW,
            "full_name": "Russell US Indexes Preliminary List (initial release)",
            "time_et": time(18, 0),
        },
    }

    for entry in RUSSELL_DATES_2026:
        ev_type = entry.get("type", "")
        date_str = entry.get("date", "")
        note = entry.get("note", "")

        params = type_to_params.get(ev_type)
        if not params:
            print(f"  [opex] unknown russell event type: {ev_type}")
            continue

        try:
            ev_date = date.fromisoformat(date_str)
        except ValueError:
            print(f"  [opex] invalid russell date: {date_str}")
            continue

        if not (start <= ev_date <= end):
            continue

        dt_utc = et_to_utc(ev_date, params["time_et"])

        events.append(Event(
            name_short=make_summary(params["importance"], params["label"]),
            name_full=params["full_name"],
            dt_utc=dt_utc,
            category="opex",
            importance=int(params["importance"]),
            all_day=False,
            details={
                "source": "FTSE Russell (LSEG)",
                "note": note,
            },
            uid_hint=f"RUSSELL_{ev_type.upper()}:{ev_date.isoformat()}",
        ))

    if events:
        print(f"  [opex] {len(events)} russell events (static list)")
    return events
