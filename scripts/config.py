"""
US Market Calendar – master configuration
==========================================
iPhone最適化のための命名規則:
  SUMMARY ≤ 25文字（週表示で切れない）
  形式: [★n] 略称 | 補足
"""

from dataclasses import dataclass, field
from datetime import time
from enum import IntEnum
from typing import Optional


# ── 重要度 ──────────────────────────────────────────────
class Importance(IntEnum):
    LOW = 1       # ★
    MEDIUM = 2    # ★★
    HIGH = 3      # ★★★


# ── イベントカテゴリ → 個別ICSファイル ──────────────────
CALENDARS = {
    "data":     {"file": "us_data.ics",     "name": "🇺🇸 経済指標",   "color": "#AF52DE"},  # 紫
    "fed":      {"file": "us_fed.ics",      "name": "🇺🇸 Fed",        "color": "#FF3B30"},  # 赤
    "auction":  {"file": "us_auction.ics",  "name": "🇺🇸 国債入札",   "color": "#34C759"},  # 緑
    "opex":     {"file": "us_opex.ics",     "name": "🇺🇸 OpEx/VIX",   "color": "#FF9500"},  # オレンジ
    "earnings": {"file": "us_earnings.ics", "name": "🇺🇸 主要決算",   "color": "#007AFF"},  # 青
}


# ── 米国東部時間での定型発表時刻 ─────────────────────────
ET_0830 = time(8, 30)
ET_0915 = time(9, 15)
ET_1000 = time(10, 0)
ET_1300 = time(13, 0)
ET_1400 = time(14, 0)
ET_1430 = time(14, 30)
ET_1600 = time(16, 0)


# ── 定型経済指標マスタ ──────────────────────────────────
# rule: 日付算出ルール（fetchers/econ_data.py で解釈）
#   "first_friday"    = 月の第1金曜
#   "bday:N"          = 月のN番目の営業日
#   "weekday:DOW:N"   = 月のN番目のDOW(0=Mon)
#   "last_friday"     = 月の最終金曜
#   "every_thursday"  = 毎週木曜
#   "manual"          = CSVから読み込み

@dataclass
class IndicatorDef:
    key: str
    name_short: str       # iPhone表示用 (≤15文字目安)
    name_full: str        # DESCRIPTION用
    importance: Importance
    release_time_et: time
    rule: str
    category: str = "data"
    notes: str = ""

INDICATORS: list[IndicatorDef] = [
    # ★★★ 最重要
    IndicatorDef("NFP",         "雇用統計 NFP",     "Nonfarm Payrolls",                   Importance.HIGH,   ET_0830, "first_friday"),
    IndicatorDef("CPI",         "CPI 消費者物価",    "Consumer Price Index",                Importance.HIGH,   ET_0830, "cday:12"),
    IndicatorDef("FOMC",        "FOMC 金利決定",     "FOMC Interest Rate Decision",         Importance.HIGH,   ET_1400, "manual", "fed"),
    IndicatorDef("GDP_ADV",     "GDP 速報値",        "GDP Advance Estimate",                Importance.HIGH,   ET_0830, "manual"),
    IndicatorDef("GDP_2ND",     "GDP 改定値",        "GDP Second Estimate",                 Importance.HIGH,   ET_0830, "manual"),
    IndicatorDef("GDP_3RD",     "GDP 確定値",        "GDP Third Estimate",                  Importance.HIGH,   ET_0830, "manual"),

    # ★★ 重要
    IndicatorDef("PPI",         "PPI 生産者物価",    "Producer Price Index",                Importance.MEDIUM, ET_0830, "cday:14"),
    IndicatorDef("RETAIL",      "小売売上高",        "Retail Sales",                        Importance.MEDIUM, ET_0830, "cday:16"),
    IndicatorDef("ISM_MFG",     "ISM製造業",         "ISM Manufacturing PMI",               Importance.MEDIUM, ET_1000, "bday:1"),
    IndicatorDef("ISM_SVC",     "ISM非製造業",       "ISM Services PMI",                    Importance.MEDIUM, ET_1000, "bday:3"),
    IndicatorDef("IP",          "鉱工業生産 G17",    "Industrial Production (G.17)",         Importance.MEDIUM, ET_0915, "manual"),
    IndicatorDef("HOUSING_S",   "住宅着工件数",      "Housing Starts & Building Permits",    Importance.MEDIUM, ET_0830, "cday:18"),
    IndicatorDef("EXIST_HOME",  "中古住宅販売",      "Existing Home Sales",                 Importance.MEDIUM, ET_1000, "cday:22"),
    IndicatorDef("CONS_CONF",   "消費者信頼感",      "Consumer Confidence (Conference Bd)",  Importance.MEDIUM, ET_1000, "weekday:1:4"),  # 最終火曜
    IndicatorDef("DURABLE",     "耐久財受注",        "Durable Goods Orders",                Importance.MEDIUM, ET_0830, "cday:26"),
    IndicatorDef("MICHIGAN_F",  "ミシガン確報",      "Univ of Michigan Sentiment (Final)",   Importance.MEDIUM, ET_1000, "last_friday"),
    IndicatorDef("MICHIGAN_P",  "ミシガン速報",      "Univ of Michigan Sentiment (Prelim)",  Importance.MEDIUM, ET_1000, "weekday:4:2"),  # 第2金曜
    IndicatorDef("JOLTS",       "JOLTS求人",         "JOLTS Job Openings",                  Importance.MEDIUM, ET_1000, "bday:2_next"),  # 翌月第2営業日
    IndicatorDef("ADP",         "ADP雇用",           "ADP Employment Change",               Importance.MEDIUM, ET_0830, "bday:-2_before_nfp"),
    IndicatorDef("CLAIMS",      "新規失業保険",      "Initial Jobless Claims",              Importance.MEDIUM, ET_0830, "every_thursday"),
    IndicatorDef("PCE_INCOME",  "個人所得/支出/PCE", "Personal Income, Spending & Core PCE Deflator",  Importance.HIGH, ET_0830, "last_friday",
                 notes="Core PCE含む（FOMC注目指標）"),
    IndicatorDef("TRADE_BAL",   "貿易収支",          "Trade Balance",                       Importance.MEDIUM, ET_0830, "cday:5"),

    # ★ 注目
    IndicatorDef("EMPIRE",      "NY連銀製造業",      "Empire State Manufacturing",           Importance.LOW,    ET_0830, "cday:15"),
    IndicatorDef("PHILLY",      "フィラデルフィア連銀", "Philadelphia Fed Manufacturing",    Importance.LOW,    ET_0830, "weekday:3:3"),  # 第3木曜
    IndicatorDef("NEW_HOME",    "新築住宅販売",      "New Home Sales",                      Importance.LOW,    ET_1000, "cday:25"),
    IndicatorDef("NAHB",        "NAHB住宅指数",      "NAHB Housing Market Index",           Importance.LOW,    ET_1000, "cday:15"),
    IndicatorDef("IMPORT_PX",   "輸入物価指数",      "Import/Export Price Index",            Importance.LOW,    ET_0830, "cday:13"),
    IndicatorDef("CASE_SHILL",  "S&Pケースシラー",   "S&P/CS Home Price Index",              Importance.LOW,    ET_1000, "weekday:1:4"),  # 最終火曜
    IndicatorDef("EIA_OIL",     "EIA原油在庫",       "EIA Crude Oil Inventories",            Importance.LOW,    time(10, 30), "every_wednesday"),
]


# ── 主要決算 ティッカーリスト ───────────────────────────
# Traders Web / 市場インパクト大の銘柄群（時価総額上位 + セクター代表）
MAJOR_EARNINGS_TICKERS: list[str] = [
    # Mag7 / メガテック
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
    # 半導体
    "TSM", "AVGO", "AMD", "INTC", "QCOM", "TXN", "ASML", "MU",
    # 金融
    "JPM", "BAC", "GS", "MS", "WFC", "C", "BLK", "SCHW",
    # ヘルスケア
    "UNH", "JNJ", "LLY", "PFE", "ABBV", "MRK", "TMO",
    # 消費
    "WMT", "COST", "HD", "MCD", "NKE", "SBUX", "TGT", "PG",
    # エネルギー / 素材
    "XOM", "CVX", "SLB", "COP", "FCX",
    # 工業
    "CAT", "BA", "GE", "UPS", "HON", "RTX", "DE", "LMT",
    # 通信 / メディア
    "DIS", "NFLX", "CMCSA", "T", "VZ",
    # その他注目
    "V", "MA", "PYPL", "CRM", "ORCL", "ADBE", "NOW",
    "COIN", "XYZ", "ABNB", "UBER",
]


# ── 国債入札 定型パターン ──────────────────────────────
# 実際の日程は Treasury.gov から取得、以下はフォールバック
@dataclass
class AuctionPattern:
    tenor: str
    name_short: str
    frequency: str  # weekly / monthly / quarterly
    importance: Importance

AUCTION_PATTERNS: list[AuctionPattern] = [
    AuctionPattern("4W",  "4W T-Bill",   "weekly",     Importance.LOW),
    AuctionPattern("8W",  "8W T-Bill",   "weekly",     Importance.LOW),
    AuctionPattern("13W", "13W T-Bill",  "weekly",     Importance.LOW),
    AuctionPattern("26W", "26W T-Bill",  "weekly",     Importance.LOW),
    AuctionPattern("52W", "52W T-Bill",  "monthly",    Importance.LOW),
    AuctionPattern("2Y",  "2Y Note",     "monthly",    Importance.MEDIUM),
    AuctionPattern("3Y",  "3Y Note",     "monthly",    Importance.LOW),
    AuctionPattern("5Y",  "5Y Note",     "monthly",    Importance.MEDIUM),
    AuctionPattern("7Y",  "7Y Note",     "monthly",    Importance.LOW),
    AuctionPattern("10Y", "10Y Note",    "monthly",    Importance.MEDIUM),
    AuctionPattern("20Y", "20Y Bond",    "monthly",    Importance.LOW),
    AuctionPattern("30Y", "30Y Bond",    "quarterly",  Importance.MEDIUM),
]


# ── FOMC 2025-2026 日程（既知） ─────────────────────────
# (decision_date, minutes_release_date) — both as "YYYY-MM-DD"
FOMC_DATES: list[dict] = [
    # 2025
    {"decision": "2025-01-29", "minutes": "2025-02-19"},
    {"decision": "2025-03-19", "minutes": "2025-04-09"},
    {"decision": "2025-05-07", "minutes": "2025-05-28"},
    {"decision": "2025-06-18", "minutes": "2025-07-09"},
    {"decision": "2025-07-30", "minutes": "2025-08-20"},
    {"decision": "2025-09-17", "minutes": "2025-10-08"},
    {"decision": "2025-10-29", "minutes": "2025-11-26"},
    {"decision": "2025-12-17", "minutes": "2026-01-07"},
    # 2026
    {"decision": "2026-01-28", "minutes": "2026-02-18"},
    {"decision": "2026-03-18", "minutes": "2026-04-08"},
    {"decision": "2026-04-29", "minutes": "2026-05-20"},
    {"decision": "2026-06-17", "minutes": "2026-07-08"},
    {"decision": "2026-07-29", "minutes": "2026-08-19"},
    {"decision": "2026-09-16", "minutes": "2026-10-07"},
    {"decision": "2026-10-28", "minutes": "2026-11-18"},
    {"decision": "2026-12-16", "minutes": "2027-01-06"},
]


# ── ベージュブック（地区連銀経済報告）2025-2026 ────────
# FOMC約2週間前、水曜 14:00 ET に公表
BEIGE_BOOK_DATES: list[str] = [
    # 2025
    "2025-01-15", "2025-03-05", "2025-04-16", "2025-05-21",
    "2025-07-16", "2025-09-03", "2025-10-15", "2025-12-03",
    # 2026
    "2026-01-14", "2026-03-04", "2026-04-15", "2026-06-03",
    "2026-07-15", "2026-09-02", "2026-10-14", "2026-12-02",
]


# ── 鉱工業生産 G.17 リリース日 2026 ──────────────────
# Fed公式スケジュールから転記
G17_DATES_2026: list[str] = [
    "2026-01-16", "2026-02-14", "2026-03-17", "2026-04-16",
    "2026-05-15", "2026-06-16", "2026-07-17", "2026-08-14",
    "2026-09-15", "2026-10-16", "2026-11-17", "2026-12-15",
]


# ── Treasury Quarterly Refunding 日程 2025-2026 ─────────
# 通例: 年4回（2月・5月・8月・11月の月初〜中旬水曜）、8:30 ET に
#       Policy Statement + Auction Schedule + Buyback Schedule を一括公表。
#
# 2日前の月曜 15:00 ET に Financing Estimates（借入額見積り）が先行公表される。
# 両方とも債券市場の注目イベント（前者★★、後者★★★）。
#
# 具体日は Treasury が各四半期末に次回日程を明示的に通知する仕組みのため、
# 「第1水曜」等の機械ルールではなく、確定日を都度転記する運用とする。
#
# 出典: home.treasury.gov/policy-issues/financing-the-government/quarterly-refunding
#       各回の press release "The next quarterly refunding announcement will take place on..."
# 確認日: 2026-04-18 JST（一次情報、複数の Treasury press release で相互確認済）
QUARTERLY_REFUNDING_DATES: list[dict] = [
    # 2025（実施済み、参考履歴）
    {"estimates": "2025-01-27", "refunding": "2025-01-29"},
    {"estimates": "2025-04-28", "refunding": "2025-04-30"},
    {"estimates": "2025-07-28", "refunding": "2025-07-30"},
    {"estimates": "2025-11-03", "refunding": "2025-11-05"},
    # 2026 (一次ソース確認済)
    {"estimates": "2026-02-02", "refunding": "2026-02-04"},  # 実施済
    {"estimates": "2026-05-04", "refunding": "2026-05-06"},  # ★ 次回 (Treasury公式告知)
    # 以下は推定（第1水曜パターンでの仮置き、次の Refunding で確定する）
    {"estimates": "2026-08-03", "refunding": "2026-08-05"},  # 推定
    {"estimates": "2026-11-02", "refunding": "2026-11-04"},  # 推定
]


# ── Fed発言 フィルタ (v5で構造化) ─────────────────────
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
    "Jefferson", # 副議長 (v5.0.1 追加、議長代行リスクヘッジ＋副議長発言取得)
]

SCRAPE_TARGET_SPEAKERS: dict[str, int] = {
    "Powell":    3,   # 議長 → ★★★
    "Warsh":     3,   # 議長 → ★★★
    "Jefferson": 2,   # 副議長 → ★★ (v5.0.1)
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
}


# ── iPhone表示ヘルパー ─────────────────────────────────
def stars(imp: Importance) -> str:
    return "★" * int(imp)

def make_summary(imp: Importance, short_name: str, suffix: str = "") -> str:
    """iPhone SUMMARY 用（≤25文字目標）"""
    prefix = stars(imp)
    s = f"{prefix} {short_name}"
    if suffix:
        s += f" {suffix}"
    # 25文字超は末尾カット
    if len(s) > 28:
        s = s[:27] + "…"
    return s


# ── FRED Release ID マッピング ─────────────────────────
# config key → FRED release_id
# https://fred.stlouisfed.org/releases で確認
FRED_RELEASE_IDS: dict[str, int] = {
    # ── 労働・雇用 ──
    "NFP":        50,   # Employment Situation
    "ADP":        194,  # ADP National Employment Report
    "JOLTS":      192,  # Job Openings and Labor Turnover Survey

    # ── 物価 ──
    "CPI":        10,   # Consumer Price Index
    "PPI":        46,   # Producer Price Index
    "IMPORT_PX":  188,  # U.S. Import and Export Price Indexes

    # ── 成長・所得・消費 ──
    "GDP_ADV":    53,   # Gross Domestic Product
    "GDP_2ND":    53,
    "GDP_3RD":    53,
    "RETAIL":     9,    # Advance Monthly Sales for Retail and Food Services
    "PCE_INCOME": 54,   # Personal Income and Outlays (Core PCE含む)
    "TRADE_BAL":  51,   # U.S. International Trade in Goods and Services

    # ── 住宅 ──
    # "HOUSING_S":  27,   # rid=27 は複数サブリリース混在でノイズ → rule "cday:18"
    "EXIST_HOME": 291,  # Existing Home Sales (NAR)
    # "NEW_HOME":   97,   # rid=97 は同上 → rule "cday:25"
    "CASE_SHILL": 199,  # S&P Cotality Case-Shiller Home Price Indices

    # ── 地区連銀サーベイ ──
    "EMPIRE":     321,  # Empire State Manufacturing Survey (NY連銀)
    "PHILLY":     351,  # Manufacturing Business Outlook Survey (Philly連銀)

    # ── 消費者心理 ──
    # "MICHIGAN_P": 91,   # rid=91 は確報日のみ → rule "weekday:4:2" (第2金曜)
    "MICHIGAN_F": 91,   # Surveys of Consumers (UMich) 確報のみ

    # ── 以下は FRED に将来日程なし。ルールベース + overrides CSV 運用 ──
    # "DURABLE":   58,    # rid=58 は 400エラー
    # "CONS_CONF": 108,   # Conference Board は FRED 未登録
    # "ISM_MFG":   29,    # 2016-06-24 FRED から除外済み
    # "ISM_SVC":   29,    # 同上
}

# ── FRED 月フィルタ ─────────────────────────────────
# 同一 release_id が複数指標（GDP Advance/2nd/3rd 等）を含む場合、
# 月で個別指標に振り分ける。設定されていないキーは全月マッチ。
FRED_MONTH_FILTERS: dict[str, set[int]] = {
    "GDP_ADV": {1, 4, 7, 10},   # Advance: 四半期終了後1ヶ月
    "GDP_2ND": {2, 5, 8, 11},   # 2nd Estimate: 2ヶ月後
    "GDP_3RD": {3, 6, 9, 12},   # 3rd Estimate: 3ヶ月後
}
