"""決算結果の Beat/Miss 判定 + 織込変動との比較

修正 2026-04-17: AMC/BMO銘柄のアフターアワーズ/プレマーケット株価反応を
yfinance経由で取得するよう変更。Finnhub /quote の dp はレギュラーセッション
終値ベースのため、時間外の決算反応を反映しない問題を解消。
"""
import logging

log = logging.getLogger(__name__)


def _get_extended_hours_reaction(symbol: str, hour: str) -> tuple:
    """
    yfinance で時間外価格を取得し、決算反応の (price, change_pct) を返す。

    AMC (引け後発表):
        反応 = postMarketPrice vs regularMarketPrice (当日終値)
    BMO (寄り前発表):
        反応 = preMarketPrice vs regularMarketPreviousClose (前日終値)

    取得失敗時は (None, None) を返す（呼び出し側で Finnhub dp にフォールバック）。
    """
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        info = t.info or {}

        if hour == "amc":
            post_price = info.get("postMarketPrice")
            reg_price = info.get("regularMarketPrice")
            if post_price and reg_price and reg_price > 0:
                change = (post_price - reg_price) / reg_price * 100
                log.info(
                    f"  {symbol} AH price: ${post_price:.2f} vs close ${reg_price:.2f} "
                    f"→ {change:+.2f}%"
                )
                return post_price, round(change, 2)
            else:
                log.info(f"  {symbol} AH price not available (post={post_price}, reg={reg_price})")

        elif hour == "bmo":
            pre_price = info.get("preMarketPrice")
            prev_close = info.get("regularMarketPreviousClose")
            if pre_price and prev_close and prev_close > 0:
                change = (pre_price - prev_close) / prev_close * 100
                log.info(
                    f"  {symbol} PM price: ${pre_price:.2f} vs prev close ${prev_close:.2f} "
                    f"→ {change:+.2f}%"
                )
                return pre_price, round(change, 2)
            else:
                log.info(f"  {symbol} PM price not available (pre={pre_price}, pc={prev_close})")

    except Exception as ex:
        log.warning(f"  {symbol} yfinance extended hours fetch failed: {ex}")

    return None, None


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

    # ── 株価変動 ──────────────────────────────────
    # Step 1: Finnhub dp をデフォルトとしてセット
    if quote:
        result["price_current"] = quote.get("c")
        result["price_change_pct"] = quote.get("dp")

    # Step 2: AMC/BMO → yfinance で時間外価格を取得して上書き
    hour = entry.get("hour", "")
    symbol = entry.get("symbol", "")
    if hour in ("amc", "bmo") and symbol:
        ext_price, ext_change = _get_extended_hours_reaction(symbol, hour)
        if ext_price is not None and ext_change is not None:
            result["price_current"] = ext_price
            result["price_change_pct"] = ext_change
        else:
            log.info(f"  {symbol} ({hour}): yfinance fallback failed, using Finnhub dp={result['price_change_pct']}")

    # ── 織込変動との比較 ──────────────────────────
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
