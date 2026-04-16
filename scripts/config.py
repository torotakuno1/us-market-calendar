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
    "data":     {"file": "us_data.ics",     "name": "🇺🇸 経済指標",   "color": "#E53935"},
    "fed":      {"file": "us_fed.ics",      "name": "🇺🇸 Fed",        "color": "#1E88E5"},
    "auction":  {"file": "us_auction.ics",  "name": "🇺🇸 国債入札",   "color": "#43A047"},
    "opex":     {"file": "us_opex.ics",     "name": "🇺🇸 OpEx/VIX",   "color": "#FB8C00"},
    "earnings": {"file": "us_earnings.ics", "name": "🇺🇸 主要決算",   "color": "#8E24AA"},
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
    "COIN", "SQ", "ABNB", "UBER",
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


# ── Fed発言 フィルタ ─────────────────────────────────
# スクレイピングで取得した発言のうち、以下の人物のみカレンダーに含める
FED_KEY_SPEAKERS: list[str] = [
    "Powell",        # 議長
    "Jefferson",     # 副議長
    "Barr",          # 金融監督担当副議長
    "Bowman",        # 理事
    "Cook",          # 理事
    "Kugler",        # 理事
    "Waller",        # 理事
]


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
    "NFP":        50,   # Employment Situation
    "CPI":        10,   # Consumer Price Index
    "PPI":        46,   # Producer Price Index
    "GDP_ADV":    53,   # Gross Domestic Product
    "GDP_2ND":    53,
    "GDP_3RD":    53,
    "RETAIL":     9,    # Advance Retail Sales
    "PCE_INCOME": 54,   # Personal Income and Outlays (includes Core PCE)
    "DURABLE":    58,   # Advance Report on Durable Goods
    "HOUSING_S":  14,   # New Residential Construction (Housing Starts)
    "EXIST_HOME": 99,   # Existing Home Sales
    "NEW_HOME":   55,   # New Residential Sales
    "TRADE_BAL":  51,   # U.S. International Trade in Goods and Services
    "JOLTS":      110,  # Job Openings and Labor Turnover Survey
    "IMPORT_PX":  97,   # U.S. Import/Export Price Indexes
    "CONS_CONF":  108,  # Consumer Confidence (Note: Conference Board, not in FRED but try)
    "ISM_MFG":    29,   # ISM Manufacturing PMI (ISM Report on Business)
    "ISM_SVC":    29,   # ISM Non-Manufacturing
    "MICHIGAN_P": 262,  # Surveys of Consumers (Univ of Michigan)
    "MICHIGAN_F": 262,
    "ADP":        474,  # ADP National Employment Report (if available)
    "EMPIRE":     199,  # Empire State Manufacturing Survey
    "CASE_SHILL": 199,  # S&P/Case-Shiller (partial)
}
