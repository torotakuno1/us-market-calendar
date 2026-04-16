"""Telegram HTML メッセージ整形 (決算サプライズ通知用)"""
import html
from datetime import datetime

TIER_STARS = {3: "★★★", 2: "★★", 1: "★"}

VERDICT_EMOJI = {
    "Beat": "✅ Beat",
    "Miss": "❌ Miss",
    "In-line": "➡️ In-line",
}


def _fmt_mmdd(date_str: str) -> str:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{dt.month}/{dt.day}"
    except (ValueError, TypeError):
        return date_str or "?"


def build_surprise_message(results: list, pending: list, date_str: str):
    """
    サプライズ通知メッセージを生成

    Args:
        results: list of dict (結果判明銘柄)
        pending: list of dict (結果未判明銘柄、epsActual=null)
        date_str: YYYY-MM-DD (対象日)
    Returns:
        list[str]: Telegram メッセージ(分割済み)
    """
    if not results and not pending:
        return []

    mmdd = _fmt_mmdd(date_str)
    lines = []
    lines.append(f"🚨 <b>決算サプライズ {mmdd}</b>")
    lines.append("")

    if results:
        lines.append(f"━━━━ 結果判明: {len(results)}銘柄 ━━━━")
        # tier降順 → symbol順
        results.sort(key=lambda r: (-r.get("tier", 1), r.get("symbol", "")))
        for r in results:
            lines.extend(_format_result_block(r))
            lines.append("")

    if pending:
        lines.append(f"━━━━ 結果未着: {len(pending)}銘柄 ━━━━")
        for p in pending:
            stars = TIER_STARS.get(p.get("tier", 1), "★")
            sym = html.escape(p.get("symbol", "?"))
            hour = p.get("hour", "")
            hour_label = {"bmo": "BMO", "amc": "AMC"}.get(hour, "")
            lines.append(f"{stars} {sym} {hour_label} (Finnhub未反映)")
        lines.append("")

    full_text = "\n".join(lines)
    return _split_message(full_text, max_len=3800)


def _format_result_block(r: dict):
    """1銘柄のサプライズ結果ブロック"""
    lines = []

    symbol = r.get("symbol", "?")
    tier = r.get("tier", 1)
    stars = TIER_STARS.get(tier, "★")
    company = html.escape(r.get("company_name", symbol))

    if "error" in r:
        lines.append(f"{stars} <b>{html.escape(symbol)}</b> ⚠️ データ取得失敗")
        return lines

    lines.append(f"{stars} <b>{html.escape(symbol)}</b> {company}")

    # EPS サプライズ
    surprise = r.get("surprise", {})
    eps_a = surprise.get("eps_actual")
    eps_e = surprise.get("eps_estimate")
    eps_pct = surprise.get("eps_surprise_pct")
    eps_v = surprise.get("eps_verdict")

    if eps_a is not None and eps_e is not None and eps_pct is not None:
        verdict_str = VERDICT_EMOJI.get(eps_v, "")
        lines.append(
            f"📊 EPS: ${eps_a:.2f} vs 予想${eps_e:.2f} "
            f"→ {eps_pct:+.1f}% {verdict_str}"
        )
    elif eps_a is not None:
        lines.append(f"📊 EPS: ${eps_a:.2f} (予想なし)")

    # Revenue サプライズ
    rev_a = surprise.get("revenue_actual")
    rev_e = surprise.get("revenue_estimate")
    rev_pct = surprise.get("rev_surprise_pct")
    rev_v = surprise.get("rev_verdict")

    if rev_a is not None and rev_e is not None and rev_pct is not None:
        verdict_str = VERDICT_EMOJI.get(rev_v, "")
        rev_a_b = rev_a / 1e9
        rev_e_b = rev_e / 1e9
        if rev_a_b >= 1:
            lines.append(
                f"💵 売上: ${rev_a_b:.2f}B vs 予想${rev_e_b:.2f}B "
                f"→ {rev_pct:+.1f}% {verdict_str}"
            )
        else:
            lines.append(
                f"💵 売上: ${rev_a/1e6:.0f}M vs 予想${rev_e/1e6:.0f}M "
                f"→ {rev_pct:+.1f}% {verdict_str}"
            )

    # 株価反応
    price = surprise.get("price_current")
    dp = surprise.get("price_change_pct")
    if price is not None and dp is not None:
        arrow = "📈" if dp >= 0 else "📉"
        lines.append(f"{arrow} 株価反応: {dp:+.2f}% (${price:,.2f})")

    # 織込変動との比較
    implied = surprise.get("implied_move_pct")
    vs = surprise.get("vs_implied")
    if implied is not None and dp is not None:
        vs_label = vs or ""
        if vs == "想定外の大変動":
            vs_label = f"⚠️ {vs}"
        lines.append(f"⚡ 織込±{implied:.1f}% → 実際{dp:+.1f}% ({vs_label})")
    elif implied is None and dp is not None:
        lines.append("⚡ (織込データなし)")

    return lines


def _split_message(text: str, max_len: int = 3800):
    """長文を行境界で分割"""
    if len(text) <= max_len:
        return [text]

    parts = []
    current = []
    current_len = 0

    for line in text.split("\n"):
        line_len = len(line) + 1
        if current_len + line_len > max_len and current:
            parts.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len

    if current:
        parts.append("\n".join(current))

    return parts
