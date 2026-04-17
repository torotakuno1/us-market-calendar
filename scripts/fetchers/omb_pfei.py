"""
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
# ノイズ混入(先頭に I/l/j/\' 等)に耐えるため、小文字正規化した文字列の部分一致で判定。
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
#   '12 l', 'I 30', '--\nI', "23\n4Q\'25", '-\n22\n-', '--1\n17 J\nl', '10\n-'
# ダッシュ直後の数字 (例: '--1' の 1) は欠測マーカー残骸なので除外する必要あり。
_DASH_RE = re.compile(r"^[\s\-]*$")
_DATE_RE = re.compile(r"(?:^|[^\w\-])(\d{1,2})(?:$|[^\w\-])")


def _extract_day(cell: Optional[str]) -> Optional[int]:
    """セル文字列から日付整数を抽出。欠測(--)やノイズのみの場合はNone"""
    if cell is None:
        return None
    for line in cell.split("\n"):
        line_clean = line.strip()
        if not line_clean or _DASH_RE.match(line_clean):
            continue
        # 四半期参照 (4Q\'25, 1Q\'26) は日付ではない
        if "Q\'" in line_clean or line_clean.lower().startswith("q"):
            continue
        # ダッシュ直後の数字(欠測残骸)を除去
        scrubbed = re.sub(r"-+\d+", "", line_clean)
        m = _DATE_RE.search(" " + scrubbed + " ")
        if m:
            day = int(m.group(1))
            if 1 <= day <= 31:
                return day
    return None


def _normalize_indicator_text(text: str) -> str:
    if not text:
        return ""
    t = text.replace("\n", " ")
    t = re.sub(r"[^a-zA-Z0-9\s\./&-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip().lower()
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

    print(f"\n=== PFEI {year} Extraction Results ===")
    for key in sorted(dates.keys()):
        ds = dates[key]
        print(f"{key:12s} ({len(ds):2d}): {[d.isoformat() for d in ds]}")
