"""
verify_pfei_vs_ics.py — PFEI PDF vs 生成ICS の突合検証

【目的】
  v10 で OMB PFEI PDF を正準ソース化したが、以下のサイレント失敗を検知したい:
    - URL形式変更でPDF取得失敗 → FRED等下位層にフォールバックして古い日付で生成
    - PDFレイアウト変更でパーサー壊れる → 一部指標が抜ける
    - ICS生成ロジックがPFEIソースを無視する変更を入れてしまう
    - overrides CSV に誤った上書きが入ってPFEI公式を上書きしている

【方針】
  - PFEIカバー済14キーの期待日付 (PDF抽出値)
  - 生成済 docs/us_data.ics から同14キーの実日付を抽出
  - 差分があれば exit code 1 (CI失敗)
  - 詳細なレポートを標準出力

【使い方】
  # ローカル
  python verify_pfei_vs_ics.py

  # CI (GitHub Actions)
  python verify_pfei_vs_ics.py --strict  # 差分で exit 1

  # 特定の月だけ検証 (過去月は自動で除外)
  python verify_pfei_vs_ics.py --year 2026 --start-month 4 --end-month 12
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import date
from pathlib import Path


# ─────────────────────────────────────────────────────────────────
# ICS パーサー (icalendar 非依存, 軽量)
# ─────────────────────────────────────────────────────────────────
# DTSTART 行のバリエーション:
#   DTSTART;VALUE=DATE:20260410
#   DTSTART:20260410T123000Z
#   DTSTART;TZID=America/New_York:20260410T083000
_UID_RE = re.compile(r"^UID:(.+?)$", re.MULTILINE)
_SUMMARY_RE = re.compile(r"^SUMMARY:(.+?)$", re.MULTILINE)
_DTSTART_RE = re.compile(r"^DTSTART[^:]*:(\d{8})", re.MULTILINE)
_DESC_RE = re.compile(r"^DESCRIPTION:(.+?)(?=\n[A-Z]+[;:])", re.MULTILINE | re.DOTALL)


def _parse_ics_events(ics_text: str) -> list[dict]:
    """ICS からイベント配列を抽出. 各イベント: {uid, summary, date, description}"""
    events = []
    # VEVENT ブロックで分割
    blocks = ics_text.split("BEGIN:VEVENT")
    for block in blocks[1:]:  # 先頭はヘッダ
        end_idx = block.find("END:VEVENT")
        if end_idx < 0:
            continue
        ev_text = block[:end_idx]

        uid_m = _UID_RE.search(ev_text)
        summary_m = _SUMMARY_RE.search(ev_text)
        dtstart_m = _DTSTART_RE.search(ev_text)

        if not (uid_m and summary_m and dtstart_m):
            continue

        date_str = dtstart_m.group(1)
        try:
            event_date = date(
                int(date_str[0:4]),
                int(date_str[4:6]),
                int(date_str[6:8]),
            )
        except ValueError:
            continue

        desc_m = _DESC_RE.search(ev_text)
        description = desc_m.group(1).strip() if desc_m else ""

        events.append({
            "uid": uid_m.group(1).strip(),
            "summary": summary_m.group(1).strip(),
            "date": event_date,
            "description": description,
        })
    return events


# ─────────────────────────────────────────────────────────────────
# ICS イベント → PFEI キー マッピング
# ─────────────────────────────────────────────────────────────────
# ICS の UID はハッシュ形式 ({16hex}@us-market-cal) でキー情報を含まない。
# → SUMMARY ("★★★ 雇用統計 NFP" 等) から config.py の name_short を逆引き。
#
# SUMMARY 正規化: 先頭の ★ とスペースを除去 → 本文一致で判定
# 例: "★★★ 雇用統計 NFP" → "雇用統計 NFP" → key "NFP"

# config.py の INDICATORS[*].name_short → key の逆引き表
# PFEI カバー済 14 キー限定 (verify 対象)
SUMMARY_TO_KEY: dict[str, str] = {
    "雇用統計 NFP":       "NFP",
    "CPI 消費者物価":     "CPI",
    "PPI 生産者物価":     "PPI",
    "輸入物価指数":       "IMPORT_PX",
    "個人所得/支出/PCE":  "PCE_INCOME",
    "貿易収支":           "TRADE_BAL",
    "GDP 速報値":         "GDP_ADV",
    "GDP 改定値":         "GDP_2ND",
    "GDP 確定値":         "GDP_3RD",
    "住宅着工件数":       "HOUSING_S",
    "新築住宅販売":       "NEW_HOME",
    "小売売上高":         "RETAIL",
    "耐久財受注":         "DURABLE",
    "鉱工業生産 G17":     "IP",
}

# PFEI カバー済キー集合 (上記 dict の値)
PFEI_COVERED_KEYS = set(SUMMARY_TO_KEY.values())

_STARS_RE = re.compile(r"^[\s★]+")


def _extract_key_from_summary(summary: str) -> str | None:
    """SUMMARY の ★プレフィックスを除去して name_short 逆引き。
    例: '★★★ 雇用統計 NFP' → 'NFP'"""
    if not summary:
        return None
    body = _STARS_RE.sub("", summary).strip()
    return SUMMARY_TO_KEY.get(body)


# ─────────────────────────────────────────────────────────────────
# メイン検証
# ─────────────────────────────────────────────────────────────────
def verify(
    year: int,
    start_month: int,
    end_month: int,
    ics_path: Path,
    pdf_path: Path | None,
    strict: bool,
) -> int:
    """
    戻り値: 0=成功, 1=差分あり(strict時), 2=PFEI取得失敗, 3=ICS読込失敗
    """
    # ── PFEI 期待値取得 ────────────────────────────────────
    try:
        # scripts/fetchers を import path に追加
        scripts_fetchers = Path(__file__).resolve().parent / "scripts" / "fetchers"
        sys.path.insert(0, str(scripts_fetchers))
        from omb_pfei import fetch_pfei_dates  # type: ignore
    except ImportError as e:
        print(f"[FATAL] omb_pfei import 失敗: {e}")
        print("         scripts/fetchers/omb_pfei.py が存在するか確認")
        return 2

    expected: dict[str, list[date]] = fetch_pfei_dates(year, local_fallback=pdf_path)
    if not expected:
        print(f"[FATAL] PFEI データ取得失敗 (year={year})")
        print(f"        pdf_path: {pdf_path}")
        return 2

    # 月範囲でフィルタ
    expected_filtered: dict[str, set[date]] = {}
    for key, dates in expected.items():
        filtered = {
            d for d in dates
            if d.year == year and start_month <= d.month <= end_month
        }
        if filtered:
            expected_filtered[key] = filtered

    total_expected = sum(len(v) for v in expected_filtered.values())
    print(f"[PFEI] 期待値: {len(expected_filtered)} keys, {total_expected} dates "
          f"(year={year}, month={start_month}-{end_month})")

    # ── ICS 実値取得 ───────────────────────────────────────
    if not ics_path.exists():
        print(f"[FATAL] ICS ファイルが存在しません: {ics_path}")
        return 3

    ics_text = ics_path.read_text(encoding="utf-8", errors="replace")
    events = _parse_ics_events(ics_text)
    print(f"[ICS]  読込: {len(events)} events from {ics_path.name}")

    actual: dict[str, set[date]] = {}
    for ev in events:
        key = _extract_key_from_summary(ev["summary"])
        if key is None:
            continue
        if not (ev["date"].year == year and start_month <= ev["date"].month <= end_month):
            continue
        actual.setdefault(key, set()).add(ev["date"])

    total_actual = sum(len(v) for v in actual.values())
    print(f"[ICS]  PFEI対象: {len(actual)} keys, {total_actual} dates")

    # ── 突合 ───────────────────────────────────────────────
    print("\n" + "=" * 66)
    print("  検証結果")
    print("=" * 66)

    all_keys = sorted(set(expected_filtered.keys()) | set(actual.keys()))
    diff_count = 0
    ok_count = 0

    for key in all_keys:
        exp = expected_filtered.get(key, set())
        act = actual.get(key, set())
        missing = exp - act  # PFEIにあるがICSに無い
        extra = act - exp    # ICSにあるがPFEIに無い

        if not missing and not extra:
            print(f"  [OK]   {key:12s}: {len(exp)} dates matched")
            ok_count += 1
        else:
            diff_count += 1
            print(f"  [DIFF] {key:12s}: expected={len(exp)}, actual={len(act)}")
            if missing:
                print(f"         missing (PFEI公式にあるがICS未生成):")
                for d in sorted(missing):
                    print(f"           - {d.isoformat()}")
            if extra:
                print(f"         extra (ICSにあるがPFEIに無い):")
                for d in sorted(extra):
                    print(f"           - {d.isoformat()}")

    # ── サマリ ─────────────────────────────────────────────
    print("\n" + "-" * 66)
    print(f"  サマリ: {ok_count} keys OK, {diff_count} keys 差分")

    if diff_count == 0:
        print("  [SUCCESS] PFEI vs ICS 完全一致 ✓")
        return 0

    print(f"  [{'FAIL' if strict else 'WARN'}] 差分あり")

    if strict:
        print("\n  対処のヒント:")
        print("   - missing: PFEI公式日程がICSに反映されていない")
        print("     → econ_data.py の PFEI 優先順位ロジックを確認")
        print("     → overrides CSV で該当キーが古い日付で上書きされていないか確認")
        print("     → run_all.py 再実行 (python scripts/run_all.py --months 12)")
        print("   - extra: PFEI未掲載の日付がICSに含まれる")
        print("     → overrides CSV の手動追加 or 下位層 (FRED/ルール) が生成した可能性")
        print("     → 想定内なら許容、想定外なら該当キーのルール再確認")

    return 1 if strict else 0


def main():
    parser = argparse.ArgumentParser(description="PFEI PDF vs Generated ICS 突合検証")
    parser.add_argument("--year", type=int, default=2026, help="検証対象年 (default: 2026)")
    parser.add_argument("--start-month", type=int, default=1, help="開始月 (default: 1)")
    parser.add_argument("--end-month", type=int, default=12, help="終了月 (default: 12)")
    parser.add_argument("--ics", type=Path, default=None,
                        help="ICSファイルパス (default: docs/us_data.ics)")
    parser.add_argument("--pdf", type=Path, default=None,
                        help="PFEI PDFパス (default: data/pfei_{year}.pdf)")
    parser.add_argument("--strict", action="store_true",
                        help="差分があれば exit 1 (CI向け)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    repo_root = Path(__file__).resolve().parent
    ics_path = args.ics or (repo_root / "docs" / "us_data.ics")
    pdf_path = args.pdf or (repo_root / "data" / f"pfei_{args.year}.pdf")
    if not pdf_path.exists():
        pdf_path = None  # URL取得試行させる

    exit_code = verify(
        year=args.year,
        start_month=args.start_month,
        end_month=args.end_month,
        ics_path=ics_path,
        pdf_path=pdf_path,
        strict=args.strict,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
