"""決算結果の Beat/Miss 判定 + 織込変動との比較"""
import logging


def check_surprise(entry: dict, quote: dict, preview_implied_move: float = None) -> dict:
    """
    決算結果を分析してサプライズ情報を返す

    Args:
        entry: Finnhub /calendar/earnings の1行
            {epsActual, epsEstimate, revenueActual, revenueEstimate, symbol, date, hour, ...}
        quote: Finnhub /quote の結果
            {c(current), d(change), dp(change_pct), ...}
        preview_implied_move: プレビュー時の織込変動率(%) or None
    Returns:
        dict: サプライズ分析結果
    """
    result = {
        "eps_actual": entry.get("epsActual"),
        "eps_estimate": entry.get("epsEstimate"),
        "eps_surprise_pct": None,
        "eps_verdict": None,          # "Beat" / "Miss" / "In-line"
        "revenue_actual": entry.get("revenueActual"),
        "revenue_estimate": entry.get("revenueEstimate"),
        "rev_surprise_pct": None,
        "rev_verdict": None,
        "price_current": None,
        "price_change_pct": None,
        "implied_move_pct": preview_implied_move,
        "vs_implied": None,           # "織込内" / "織込やや超" / "想定外の大変動"
    }

    # EPS サプライズ計算
    eps_a = entry.get("epsActual")
    eps_e = entry.get("epsEstimate")
    if eps_a is not None and eps_e is not None and eps_e != 0:
        surprise_pct = (eps_a - eps_e) / abs(eps_e) * 100
        result["eps_surprise_pct"] = round(surprise_pct, 1)
        if abs(surprise_pct) < 1.0:
            result["eps_verdict"] = "In-line"
        elif surprise_pct > 0:
            result["eps_verdict"] = "Beat"
        else:
            result["eps_verdict"] = "Miss"

    # Revenue サプライズ計算
    rev_a = entry.get("revenueActual")
    rev_e = entry.get("revenueEstimate")
    if rev_a is not None and rev_e is not None and rev_e != 0:
        rev_surprise_pct = (rev_a - rev_e) / abs(rev_e) * 100
        result["rev_surprise_pct"] = round(rev_surprise_pct, 1)
        if abs(rev_surprise_pct) < 1.0:
            result["rev_verdict"] = "In-line"
        elif rev_surprise_pct > 0:
            result["rev_verdict"] = "Beat"
        else:
            result["rev_verdict"] = "Miss"

    # 株価変動
    if quote:
        result["price_current"] = quote.get("c")
        result["price_change_pct"] = quote.get("dp")

    # 織込変動との比較
    price_dp = result["price_change_pct"]
    if preview_implied_move is not None and price_dp is not None:
        actual_abs = abs(price_dp)
        if actual_abs <= preview_implied_move:
            result["vs_implied"] = "織込内"
        elif actual_abs <= preview_implied_move * 1.5:
            result["vs_implied"] = "織込やや超"
        else:
            result["vs_implied"] = "想定外の大変動"

    return result
