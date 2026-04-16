"""Telegram HTML メッセージ整形

Telegram Bot API 送信制限:
  - 1メッセージ 4096文字まで (HTMLタグ含む)
  - 本実装では 3800文字で分割 (マージン含む)
"""
import html

TIER_STARS = {3: "★★★", 2: "★★", 1: "★"}

HOUR_LABELS = {
    "bmo": "🌅 BMO 寄り前",
    "amc": "🌆 AMC 引け後",
    "dmh": "⏰ 場中",
    "other": "⏰ 時間未定",
}

SECTOR_LABELS = {
    "tech": "テック",
    "semi": "半導体",
    "auto_ev": "EV/自動車",
    "financials": "金融",
    "software": "SaaS",
    "ai_infra": "AIインフラ",
    "ai_power": "AI電力",
    "healthcare": "ヘルスケア",
    "consumer": "消費",
    "energy": "エネルギー",
    "media": "メディア",
    "industrials": "産業",
}


def build_telegram_message(previews, target_date):
    """
    複数銘柄の preview を Telegram HTML形式に整形
    4096文字制限対応で返り値は list[str]

    Args:
        previews: list of dict (main.build_preview の戻り値)
        target_date: str 'YYYY-MM-DD'
    Returns:
        list[str]: 1要素=1メッセージ
    """
    if not previews:
        return []

    lines = []
    lines.append(f"📊 <b>決算プレビュー {target_date}</b>")
    lines.append(f"対象: {len(previews)}銘柄")
    lines.append("")

    # BMO/AMC/その他 でグループ化
    groups = {"bmo": [], "amc": [], "other": []}
    for p in previews:
        hour = p.get("hour", "")
        if hour == "bmo":
            groups["bmo"].append(p)
        elif hour == "amc":
            groups["amc"].append(p)
        else:
            groups["other"].append(p)

    for key in ["bmo", "amc", "other"]:
        if groups[key]:
            label = HOUR_LABELS.get(key, "⏰ その他")
            lines.append(f"━━━━ {label} ━━━━")
            for p in groups[key]:
                lines.extend(format_ticker_block(p))
                lines.append("")

    # Telegram 4096文字制限 → 3800文字で分割
    full_text = "\n".join(lines)
    return split_message(full_text, max_len=3800)


def format_ticker_block(p: dict):
    """1銘柄の表示ブロックを行リストで返す"""
    lines = []

    symbol = p["symbol"]
    tier = p.get("tier", 1)
    stars = TIER_STARS.get(tier, "★")

    if "error" in p:
        lines.append(f"{stars} <b>{html.escape(symbol)}</b> ⚠️ データ取得失敗")
        err_msg = html.escape(str(p["error"])[:80])
        lines.append(f"  エラー: {err_msg}")
        return lines

    # 企業名 (HTMLエスケープ必須: &, <, > が含まれる可能性)
    company = html.escape(p.get("company_name", symbol))
    lines.append(f"{stars} <b>{html.escape(symbol)}</b> {company}")

    # 株価
    price = p.get("current_price")
    change = p.get("day_change_pct")
    if price is not None and change is not None:
        lines.append(f"💰 ${price:,.2f} ({change:+.2f}%)")
    elif price is not None:
        lines.append(f"💰 ${price:,.2f}")

    # コンセンサス予想
    eps_est = p.get("eps_estimate")
    rev_est = p.get("revenue_estimate")
    if eps_est is not None:
        lines.append(f"🎯 EPS予想: ${eps_est:.2f}")
    if rev_est:
        # Finnhub の revenueEstimate は USD (float) で返る
        rev_b = rev_est / 1e9
        if rev_b >= 1:
            lines.append(f"   売上予想: ${rev_b:.2f}B")
        else:
            rev_m = rev_est / 1e6
            lines.append(f"   売上予想: ${rev_m:.0f}M")

    # 過去実績
    past = p.get("past_stats")
    if past and past.get("last_report"):
        last = past["last_report"]
        beat_pct = last.get("beat_pct")
        eps_actual = last.get("eps_actual")
        eps_est_past = last.get("eps_estimate")
        if beat_pct is not None and eps_actual is not None and eps_est_past is not None:
            sign = "Beat" if beat_pct > 0 else ("Miss" if beat_pct < 0 else "In-line")
            lines.append(
                f"📜 前回: ${eps_actual:.2f} vs 予想${eps_est_past:.2f} "
                f"→ {beat_pct:+.1f}% {sign}"
            )
        total = past.get("total_count", 0)
        beats = past.get("beat_count", 0)
        avg_pct = past.get("avg_eps_beat_pct")
        if total > 0 and avg_pct is not None:
            losses = total - beats
            lines.append(
                f"📊 直近{total}回: {beats}勝{losses}負, 平均{avg_pct:+.1f}%"
            )

    # Implied Move
    im = p.get("implied_move")
    if im:
        lines.append(
            f"⚡ 織込変動: ±{im['implied_move_pct']:.1f}% "
            f"(DTE {im['dte']}d, Straddle ${im['straddle']:.2f})"
        )
        lines.append(
            f"   想定レンジ: ${im['range_low']:,.2f}〜${im['range_high']:,.2f}"
        )
    else:
        lines.append("⚡ 織込変動: 取得失敗 (流動性不足?)")

    return lines


def split_message(text: str, max_len: int = 3800):
    """長文を行境界で分割。
    1行が max_len を超える場合はそのまま残す (現実的にはありえない想定)。
    """
    if len(text) <= max_len:
        return [text]

    parts = []
    current = []
    current_len = 0

    for line in text.split("\n"):
        line_len = len(line) + 1  # +1 for newline
        if current_len + line_len > max_len and current:
            parts.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len

    if current:
        parts.append("\n".join(current))

    return parts
