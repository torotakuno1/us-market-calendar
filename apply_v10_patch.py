"""
apply_v10_patch.py — OMB PFEI PDF を正準ソース化する v10 パッチ

【概要】
  米国政府OMBが毎年9月頃に発行する PFEI (Principal Federal Economic Indicators)
  Schedule of Release Dates PDF を、経済指標発表日の正準ソースに昇格する。
  既存の FRED API / BLS iCal はフォールバックとして残す。

【変更内容】
  1. data/pfei_2026.pdf                      (既にDownloadsから配置されていれば上書き無し)
  2. scripts/fetchers/omb_pfei.py            (新規)
  3. scripts/fetchers/econ_data.py           (優先順位チェーンに PFEI 層を挿入)
  4. requirements.txt                        (pdfplumber 追記)

【優先順位（新）】
  CSV override > OMB PFEI > BLS iCal > FRED API > Rule-based estimate
      ^^^^^^^^^^^^^^^^^^
      新規挿入. 既存12指標のうち PFEI 掲載分はここで確定する.
      PFEI 未掲載 (ISM/CCI/JOLTS/Michigan/Empire/Philly/ADP/Case-Shiller/
                    Existing Home Sales/Initial Claims 等) は従来通り下位層で処理.

【使い方】
  cd C:\\Users\\CH07\\us-market-calendar\\us-market-calendar
  pip install pdfplumber --break-system-packages
  python apply_v10_patch.py
  git status  # 変更確認
  git add -A && git commit -m "v10: Adopt OMB PFEI PDF as canonical source"
  git push
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path.cwd()
DATA_DIR = REPO_ROOT / "data"
FETCHERS_DIR = REPO_ROOT / "scripts" / "fetchers"
ECON_DATA_PATH = FETCHERS_DIR / "econ_data.py"
OMB_PFEI_PATH = FETCHERS_DIR / "omb_pfei.py"
PDF_TARGET = DATA_DIR / "pfei_2026.pdf"
REQUIREMENTS_PATH = REPO_ROOT / "requirements.txt"


# ═══════════════════════════════════════════════════════════════════
# 1. omb_pfei.py ファイル内容
# ═══════════════════════════════════════════════════════════════════
OMB_PFEI_SRC = '''"""
OMB PFEI (Principal Federal Economic Indicators) PDF Parser
============================================================
Source: https://www.whitehouse.gov/wp-content/uploads/2025/09/pfei_schedule_release_dates_cy2026.pdf

全米国政府機関の主要経済指標の公式発表日を掲載した年次PDF。
日付のみPDFから、時刻(ET)は呼び出し側の config.release_time_et を使用。

優先順位: CSV override > OMB PFEI (本モジュール) > BLS iCal > FRED API > Rule-based

【カバー範囲】(2026-04-17 時点で config.INDICATORS にある12指標)
  NFP, CPI, PPI, IMPORT_PX, PCE_INCOME, TRADE_BAL,
  GDP_ADV, GDP_2ND, GDP_3RD (同一行を月フィルタで分配),
  HOUSING_S, NEW_HOME, RETAIL, DURABLE, IP

【PFEI未掲載 — 下位層で処理】
  JOLTS, ISM_MFG, ISM_SVC, CONS_CONF, MICHIGAN_P/F,
  EXIST_HOME, CASE_SHILL, EMPIRE, PHILLY, ADP, CLAIMS
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import requests
except ImportError:
    requests = None


PFEI_URL_TEMPLATE = "https://www.whitehouse.gov/wp-content/uploads/2025/09/pfei_schedule_release_dates_cy{year}.pdf"


# ─────────────────────────────────────────────────────────────────
# PFEI 行テキスト(部分一致) → config.py INDICATOR key
# ─────────────────────────────────────────────────────────────────
# ノイズ混入(先頭に I/l/j/\\' 等)に耐えるため、小文字正規化した文字列の部分一致で判定。
PFEI_TO_KEY: dict[str, str] = {
    # LABOR / BLS
    "the employment situation":                       "NFP",
    "producer price indexes":                         "PPI",
    "consumer price index":                           "CPI",
    "u.s. import and export price indexes":           "IMPORT_PX",

    # COMMERCE / BEA
    "personal income and outlays":                    "PCE_INCOME",
    "gross domestic product":                         "__GDP__",
    "u.s. international trade in goods and services": "TRADE_BAL",

    # COMMERCE / Census
    "new residential construction":                   "HOUSING_S",
    "new residential sales":                          "NEW_HOME",
    "advance monthly sales for retail":               "RETAIL",
    "advance report on durable goods":                "DURABLE",

    # FEDERAL RESERVE
    "industrial production and capacity utilization": "IP",
}

# GDP は PFEI上は単一行だが発表月で ADV / 2ND / 3RD を判別
GDP_MONTH_FILTERS: dict[str, set[int]] = {
    "GDP_ADV": {1, 4, 7, 10},
    "GDP_2ND": {2, 5, 8, 11},
    "GDP_3RD": {3, 6, 9, 12},
}


# ─────────────────────────────────────────────────────────────────
# PDF取得 — URL優先、失敗時はローカルフォールバック
# ─────────────────────────────────────────────────────────────────
def _load_pdf_bytes(year: int, local_fallback: Optional[Path] = None) -> Optional[bytes]:
    url = PFEI_URL_TEMPLATE.format(year=year)

    if requests is not None:
        try:
            resp = requests.get(url, timeout=20, headers={
                "User-Agent": "Mozilla/5.0 (us-market-calendar/v10)",
            })
            if resp.status_code == 200 and len(resp.content) > 10000:
                logging.info(f"  [pfei] downloaded from WhiteHouse.gov ({len(resp.content)} bytes)")
                return resp.content
            logging.warning(f"  [pfei] URL fetch failed: status={resp.status_code}")
        except Exception as e:
            logging.warning(f"  [pfei] URL fetch error: {e}")

    if local_fallback and local_fallback.exists():
        logging.info(f"  [pfei] using local fallback: {local_fallback}")
        return local_fallback.read_bytes()

    logging.error(f"  [pfei] no PDF source available for {year}")
    return None


# ─────────────────────────────────────────────────────────────────
# セル値クリーニング
# ─────────────────────────────────────────────────────────────────
# pdfplumber が抽出するセルには罫線の残骸が混じる:
#   '12 l', 'I 30', '--\\nI', "23\\n4Q\\'25", '-\\n22\\n-', '--1\\n17 J\\nl', '10\\n-'
# ダッシュ直後の数字 (例: '--1' の 1) は欠測マーカー残骸なので除外する必要あり。
_DASH_RE = re.compile(r"^[\\s\\-]*$")
_DATE_RE = re.compile(r"(?:^|[^\\w\\-])(\\d{1,2})(?:$|[^\\w\\-])")


def _extract_day(cell: Optional[str]) -> Optional[int]:
    """セル文字列から日付整数を抽出。欠測(--)やノイズのみの場合はNone"""
    if cell is None:
        return None
    for line in cell.split("\\n"):
        line_clean = line.strip()
        if not line_clean or _DASH_RE.match(line_clean):
            continue
        # 四半期参照 (4Q\\'25, 1Q\\'26) は日付ではない
        if "Q\\'" in line_clean or line_clean.lower().startswith("q"):
            continue
        # ダッシュ直後の数字(欠測残骸)を除去
        scrubbed = re.sub(r"-+\\d+", "", line_clean)
        m = _DATE_RE.search(" " + scrubbed + " ")
        if m:
            day = int(m.group(1))
            if 1 <= day <= 31:
                return day
    return None


def _normalize_indicator_text(text: str) -> str:
    if not text:
        return ""
    t = text.replace("\\n", " ")
    t = re.sub(r"[^a-zA-Z0-9\\s\\./&-]", " ", t)
    t = re.sub(r"\\s+", " ", t).strip().lower()
    return t


def _match_indicator_key(normalized_text: str) -> Optional[str]:
    for needle, key in PFEI_TO_KEY.items():
        if needle in normalized_text:
            return key
    return None


# ─────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────
def fetch_pfei_dates(
    year: int,
    local_fallback: Optional[Path] = None,
) -> dict[str, list[date]]:
    """
    PFEI PDFから指標別の発表日リストを取得。

    戻り値: { "NFP": [date(2026,1,9), date(2026,2,6), ...], ... }

    注意:
      - GDPは PFEI上は単一行だが、発表月で ADV/2ND/3RD を判別して3キーに分配。
      - 日付のみ取得。時刻は呼び出し側で config.release_time_et を使う。
      - PDFが取得できない場合は空 dict を返す (下位層にフォールバック)。
    """
    if pdfplumber is None:
        logging.warning("  [pfei] pdfplumber not installed — skipping PFEI layer")
        return {}

    pdf_bytes = _load_pdf_bytes(year, local_fallback)
    if pdf_bytes is None:
        return {}

    result: dict[str, list[date]] = {}

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables() or []
            for table in tables:
                for row in table:
                    if not row or len(row) < 14:
                        continue
                    indicator_cell = row[1] if len(row) > 1 else None
                    if not indicator_cell:
                        continue

                    norm = _normalize_indicator_text(indicator_cell)
                    matched = _match_indicator_key(norm)
                    if not matched:
                        continue

                    for month in range(1, 13):
                        cell = row[1 + month] if (1 + month) < len(row) else None
                        day = _extract_day(cell)
                        if day is None:
                            continue
                        try:
                            d = date(year, month, day)
                        except ValueError:
                            continue

                        if matched == "__GDP__":
                            for sub_key, months in GDP_MONTH_FILTERS.items():
                                if month in months:
                                    result.setdefault(sub_key, []).append(d)
                                    break
                        else:
                            result.setdefault(matched, []).append(d)

    # 重複排除・ソート
    for key in list(result.keys()):
        result[key] = sorted(set(result[key]))

    total = sum(len(v) for v in result.values())
    logging.info(f"  [pfei] extracted {len(result)} indicators, {total} dates total")
    return result


# ─────────────────────────────────────────────────────────────────
# 単体実行用（デバッグ）
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    year = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 2026
    fallback_arg = None
    for arg in sys.argv[1:]:
        if arg.endswith(".pdf"):
            fallback_arg = Path(arg)
            break

    dates = fetch_pfei_dates(year, local_fallback=fallback_arg)

    print(f"\\n=== PFEI {year} Extraction Results ===")
    for key in sorted(dates.keys()):
        ds = dates[key]
        print(f"{key:12s} ({len(ds):2d}): {[d.isoformat() for d in ds]}")
'''


# ═══════════════════════════════════════════════════════════════════
# 2. econ_data.py パッチ — 差分適用
# ═══════════════════════════════════════════════════════════════════
# 変更点:
#  (a) import に `from fetchers.omb_pfei import fetch_pfei_dates` を追加
#      ※ 既に scripts/ が sys.path に入っている前提なので from omb_pfei import ... でも可
#  (b) fetch_econ_data() 内で PFEI を呼び出して pfei_dates を得る
#  (c) 優先順位チェーンに「2. PFEI」を挿入し、既存 BLS/FRED を 3/4 に降格

# (a) importブロック
ECON_IMPORT_OLD = '''from config import (
    INDICATORS, IndicatorDef, Importance, make_summary,
    FRED_RELEASE_IDS,
)'''

ECON_IMPORT_NEW_VARIANT = '''from config import (
    INDICATORS, IndicatorDef, Importance, make_summary,
    FRED_RELEASE_IDS,
)
try:
    from fetchers.omb_pfei import fetch_pfei_dates
except ImportError:
    # 直接 scripts/fetchers から相対インポートされる場合のフォールバック
    try:
        from omb_pfei import fetch_pfei_dates
    except ImportError:
        fetch_pfei_dates = None  # pdfplumber未インストール時などのセーフティ'''

# config側でFRED_MONTH_FILTERSを追加済のバージョン(v4適用後)のimport
ECON_IMPORT_OLD_V4 = '''from config import (
    INDICATORS, IndicatorDef, Importance, make_summary,
    FRED_RELEASE_IDS, FRED_MONTH_FILTERS,
)'''

ECON_IMPORT_NEW_V4 = '''from config import (
    INDICATORS, IndicatorDef, Importance, make_summary,
    FRED_RELEASE_IDS, FRED_MONTH_FILTERS,
)
try:
    from fetchers.omb_pfei import fetch_pfei_dates
except ImportError:
    try:
        from omb_pfei import fetch_pfei_dates
    except ImportError:
        fetch_pfei_dates = None'''


# (b) fetch_econ_data のdocstring置換
ECON_DOCSTRING_OLD = '''    """
    優先順位:
      1. CSV上書き（手動補正）
      2. BLS iCal（CPI, NFP, PPI等の公式日程）
      3. FRED API（幅広い公式日程）
      4. ルールベース推定（フォールバック）
    """'''

ECON_DOCSTRING_NEW = '''    """
    優先順位:
      1. CSV上書き（手動補正）
      2. OMB PFEI PDF（米国政府年次公式日程 — v10で追加）
      3. BLS iCal（PFEI未掲載のBLS指標のフォールバック）
      4. FRED API（PFEI未掲載で FRED 登録ありの指標）
      5. ルールベース推定（最終フォールバック）
    """'''


# (c) PFEI呼び出しを挿入 — BLS呼び出しの直前に
ECON_FETCH_CALL_OLD = '''    # API・iCalから公式日程を取得
    fred_dates = _fetch_fred_dates(start, end)
    bls_dates = _fetch_bls_ical(start, end)'''

ECON_FETCH_CALL_NEW = '''    # API・iCal・PDFから公式日程を取得
    fred_dates = _fetch_fred_dates(start, end)
    bls_dates = _fetch_bls_ical(start, end)

    # v10: OMB PFEI PDFを最優先の公式ソースとして取り込む
    pfei_dates: dict[str, list[date]] = {}
    if fetch_pfei_dates is not None:
        pfei_pdf_path = Path(__file__).resolve().parents[2] / "data" / f"pfei_{start.year}.pdf"
        try:
            pfei_dates = fetch_pfei_dates(
                year=start.year,
                local_fallback=pfei_pdf_path if pfei_pdf_path.exists() else None,
            )
            if pfei_dates:
                print(f"  [pfei] {len(pfei_dates)} indicators loaded from OMB PFEI")
        except Exception as e:
            print(f"  [pfei] error: {e} — skipping PFEI layer")

        # 年またぎの場合は翌年分も取得
        if start.year != end.year:
            for y in range(start.year + 1, end.year + 1):
                pfei_pdf_y = Path(__file__).resolve().parents[2] / "data" / f"pfei_{y}.pdf"
                try:
                    additional = fetch_pfei_dates(
                        year=y,
                        local_fallback=pfei_pdf_y if pfei_pdf_y.exists() else None,
                    )
                    for k, v in additional.items():
                        pfei_dates.setdefault(k, []).extend(v)
                except Exception as e:
                    print(f"  [pfei] {y} error: {e}")'''


# (d) 優先順位チェーン — CSV の次、BLS の前に PFEI を挿入
ECON_CHAIN_OLD = '''            # 1. CSV上書き
            if override_key in overrides:
                release_date = overrides[override_key]["date"]
                source = "CSV override"
                extra_note = overrides[override_key].get("note", "")

            # 2. BLS iCal（月に1つマッチするものを探す）
            elif ind.key in bls_dates:'''

ECON_CHAIN_NEW = '''            # 1. CSV上書き
            if override_key in overrides:
                release_date = overrides[override_key]["date"]
                source = "CSV override"
                extra_note = overrides[override_key].get("note", "")

            # 2. OMB PFEI PDF (v10で追加 — 米国政府公式の年次スケジュール)
            elif ind.key in pfei_dates and pfei_dates[ind.key]:
                month_matches = [
                    dd for dd in pfei_dates[ind.key]
                    if dd.year == year and dd.month == month
                ]
                if month_matches:
                    release_date = month_matches[0]
                    source = "OMB PFEI"
                    extra_note = ""
                else:
                    release_date = None
                    source = ""
                    extra_note = ""

            # 3. BLS iCal（月に1つマッチするものを探す）
            elif ind.key in bls_dates:'''


# ═══════════════════════════════════════════════════════════════════
# ヘルパー
# ═══════════════════════════════════════════════════════════════════
def apply_str_replace(path: Path, old: str, new: str, label: str, required: bool = True) -> bool:
    """単発の文字列置換. 見つからなければrequired=Trueでエラー."""
    content = path.read_text(encoding="utf-8")
    if new in content and old not in content:
        print(f"  [skip] {label}: 既に適用済み")
        return True
    if old not in content:
        if required:
            print(f"  [ERROR] {label}: マッチ対象文字列が見つかりません")
            return False
        else:
            print(f"  [skip] {label}: 対象なし(このバージョンには存在しない想定)")
            return True
    new_content = content.replace(old, new, 1)
    path.write_text(new_content, encoding="utf-8")
    print(f"  [OK]   {label}")
    return True


def try_apply_either(path: Path, pairs: list, label: str) -> bool:
    """複数の (old, new) パターンのうち最初にマッチしたものを適用.
    pairs: [(old1, new1), (old2, new2), ...]"""
    content = path.read_text(encoding="utf-8")
    # 既に適用済み判定: いずれかの new が既に存在
    for _, new in pairs:
        if new in content:
            print(f"  [skip] {label}: 既に適用済み")
            return True
    # 未適用: 最初にマッチした old を置換
    for old, new in pairs:
        if old in content:
            new_content = content.replace(old, new, 1)
            path.write_text(new_content, encoding="utf-8")
            print(f"  [OK]   {label}")
            return True
    print(f"  [ERROR] {label}: どのパターンにもマッチしませんでした")
    return False


# ═══════════════════════════════════════════════════════════════════
# メイン
# ═══════════════════════════════════════════════════════════════════
def main():
    print("=" * 66)
    print("  apply_v10_patch.py — OMB PFEI Canonical Source Integration")
    print("=" * 66)

    # 前提チェック
    if not ECON_DATA_PATH.exists():
        print(f"[FATAL] {ECON_DATA_PATH} が見つかりません")
        print("        カレントディレクトリがリポジトリルートか確認してください")
        sys.exit(1)

    # ── Step 0: data/ ディレクトリ確保とPDF配置案内 ─────────────────
    print("\n[Step 0] data/ ディレクトリ準備とPDF配置確認")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not PDF_TARGET.exists():
        # Downloads から自動コピーを試みる
        downloads_candidates = [
            Path.home() / "Downloads" / "pfei_schedule_release_dates_cy2026.pdf",
            Path("C:/Users/CH07/Downloads/pfei_schedule_release_dates_cy2026.pdf"),
        ]
        copied = False
        for src in downloads_candidates:
            if src.exists():
                shutil.copy2(src, PDF_TARGET)
                print(f"  [OK]   Downloads からコピー: {src.name} → data/pfei_2026.pdf")
                copied = True
                break
        if not copied:
            print(f"  [WARN] data/pfei_2026.pdf が存在しません")
            print(f"         以下のいずれかを実施してください:")
            print(f"         (A) 下記URLから手動DLして data/pfei_2026.pdf に配置")
            print(f"             https://www.whitehouse.gov/wp-content/uploads/2025/09/pfei_schedule_release_dates_cy2026.pdf")
            print(f"         (B) Downloads から手動コピー")
            print(f"         ※ URL取得がCIで成功すればPDFなしでも動作しますが推奨はリポジトリ同梱")
    else:
        print(f"  [OK]   data/pfei_2026.pdf (既存)")

    # ── Step 1: omb_pfei.py 生成 ───────────────────────────────────
    print("\n[Step 1] scripts/fetchers/omb_pfei.py 生成")
    OMB_PFEI_PATH.parent.mkdir(parents=True, exist_ok=True)
    OMB_PFEI_PATH.write_text(OMB_PFEI_SRC, encoding="utf-8")
    print(f"  [OK]   {OMB_PFEI_PATH.relative_to(REPO_ROOT)} ({len(OMB_PFEI_SRC)} bytes)")

    # ── Step 2: econ_data.py パッチ適用 ────────────────────────────
    print("\n[Step 2] scripts/fetchers/econ_data.py パッチ適用")

    ok1 = try_apply_either(
        ECON_DATA_PATH,
        [
            (ECON_IMPORT_OLD_V4, ECON_IMPORT_NEW_V4),     # v4 適用済 (FRED_MONTH_FILTERS あり)
            (ECON_IMPORT_OLD,    ECON_IMPORT_NEW_VARIANT), # v4未適用 (従来版)
        ],
        "import: omb_pfei 追加",
    )
    ok2 = apply_str_replace(
        ECON_DATA_PATH,
        ECON_DOCSTRING_OLD, ECON_DOCSTRING_NEW,
        "docstring: 優先順位5層に更新",
    )
    ok3 = apply_str_replace(
        ECON_DATA_PATH,
        ECON_FETCH_CALL_OLD, ECON_FETCH_CALL_NEW,
        "fetch呼び出し: PFEI 取得処理を追加",
    )
    ok4 = apply_str_replace(
        ECON_DATA_PATH,
        ECON_CHAIN_OLD, ECON_CHAIN_NEW,
        "優先順位チェーン: PFEI を第2層に挿入",
    )

    if not all([ok1, ok2, ok3, ok4]):
        print("\n[FATAL] パッチ適用に失敗したステップがあります")
        sys.exit(2)

    # ── Step 3: requirements.txt 更新 ──────────────────────────────
    print("\n[Step 3] requirements.txt に pdfplumber を追記")
    req_text = REQUIREMENTS_PATH.read_text(encoding="utf-8") if REQUIREMENTS_PATH.exists() else ""
    if "pdfplumber" not in req_text:
        req_text = req_text.rstrip() + "\npdfplumber>=0.10\n"
        REQUIREMENTS_PATH.write_text(req_text, encoding="utf-8")
        print(f"  [OK]   pdfplumber>=0.10 追記")
    else:
        print(f"  [skip] pdfplumber 既に記載済み")

    # ── Step 4: 軽量セルフテスト ────────────────────────────────────
    print("\n[Step 4] セルフテスト — PDFパース動作確認")
    if PDF_TARGET.exists():
        try:
            sys.path.insert(0, str(FETCHERS_DIR))
            # import cache 回避のためreload
            import importlib
            if "omb_pfei" in sys.modules:
                importlib.reload(sys.modules["omb_pfei"])
            from omb_pfei import fetch_pfei_dates as _fetch

            result = _fetch(2026, local_fallback=PDF_TARGET)
            expected_keys = {
                "NFP", "CPI", "PPI", "IMPORT_PX", "PCE_INCOME", "TRADE_BAL",
                "GDP_ADV", "GDP_2ND", "GDP_3RD",
                "HOUSING_S", "NEW_HOME", "RETAIL", "DURABLE", "IP",
            }
            got = set(result.keys())
            missing = expected_keys - got
            extra = got - expected_keys
            total = sum(len(v) for v in result.values())
            print(f"  Found {len(got)}/{len(expected_keys)} indicators, {total} dates")
            if missing:
                print(f"  [WARN] 欠損: {sorted(missing)}")
            if extra:
                print(f"  [INFO] 想定外キー: {sorted(extra)}")
            if not missing:
                print(f"  [OK]   全12指標取得確認")
        except Exception as e:
            print(f"  [WARN] セルフテスト失敗: {e}")
            print(f"         (PDFが存在しない / pdfplumber未インストール 等)")
    else:
        print(f"  [skip] data/pfei_2026.pdf 未配置のためスキップ")

    # ── 完了 ────────────────────────────────────────────────────────
    print("\n" + "=" * 66)
    print("  v10 パッチ適用完了")
    print("=" * 66)
    print("""
次のステップ:

  1. 依存インストール（既にpdfplumberがあれば不要）
       pip install pdfplumber --break-system-packages

  2. ローカル動作確認（任意 — GitHub Actionsで自動実行される）
       python scripts/run_all.py --months 3
       # ログに '[pfei] 14 indicators loaded from OMB PFEI' が出ればOK

  3. コミット & プッシュ
       git add -A
       git commit -m "v10: Adopt OMB PFEI PDF as canonical source (#12 indicators)"
       git push

  4. Actions で Run workflow → docs/ に反映確認

  5. overrides CSV の整理（任意 — v11以降で実施可）
       PFEIカバー済の行はコメントアウトまたは削除可能:
         NFP, CPI, PPI, IMPORT_PX, PCE_INCOME, TRADE_BAL,
         GDP_ADV, GDP_2ND, GDP_3RD, HOUSING_S, NEW_HOME,
         RETAIL, DURABLE, IP
""")


if __name__ == "__main__":
    main()
