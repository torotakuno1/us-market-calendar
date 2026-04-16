"""yfinance オプションチェーンから Implied Move を算出

Implied Move (%) = (ATM Call Mid + ATM Put Mid) / Stock Price

ATM判定: 現在株価に最も近い strike
満期選定: 決算日当日以降かつ DTE <= 14 の最短満期
"""
import logging
from datetime import datetime

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False
    logging.error("yfinance not installed")


def _row_mid(row, spread_threshold: float = 0.30):
    """Bid-Ask Mid 価格を返す。スプレッド過大・片側ゼロは None。
    Args:
        row: pandas.Series
        spread_threshold: スプレッド/ミッド 比率の上限 (これ超は流動性不足と判定)
    Returns:
        float or None
    """
    bid = row.get("bid", 0) or 0
    ask = row.get("ask", 0) or 0
    if bid > 0 and ask > 0 and ask > bid:
        mid_val = (ask + bid) / 2
        spread_pct = (ask - bid) / mid_val
        if spread_pct > spread_threshold:
            return None
        return mid_val
    return None


def calculate_implied_move(symbol: str, stock_price: float, earnings_date: str):
    """
    Args:
        symbol: ティッカー
        stock_price: 現在株価
        earnings_date: YYYY-MM-DD (Finnhub の calendar/earnings の date)
    Returns:
        dict or None (取得失敗時)
    """
    if not YF_AVAILABLE:
        return None

    if stock_price is None or stock_price <= 0:
        logging.warning(f"{symbol}: invalid stock_price={stock_price}")
        return None

    try:
        ticker = yf.Ticker(symbol)
        expirations = ticker.options

        if not expirations:
            logging.warning(f"{symbol}: no options available")
            return None

        earnings_dt = datetime.strptime(earnings_date, "%Y-%m-%d")

        # 決算日当日以降の最短満期 (DTE <= 14) を探索
        target_exp = None
        target_dte = None
        for exp_str in expirations:
            try:
                exp_dt = datetime.strptime(exp_str, "%Y-%m-%d")
            except ValueError:
                continue
            dte = (exp_dt - earnings_dt).days
            if 0 <= dte <= 14:
                target_exp = exp_str
                target_dte = dte
                break

        if not target_exp:
            logging.warning(f"{symbol}: no suitable option expiration within 14 days after {earnings_date}")
            return None

        # オプションチェーン取得
        chain = ticker.option_chain(target_exp)
        calls, puts = chain.calls, chain.puts

        if calls is None or puts is None or len(calls) == 0 or len(puts) == 0:
            logging.warning(f"{symbol}: empty option chain for {target_exp}")
            return None

        # ATM判定: stock_priceに最も近いstrike
        calls_idx = (calls["strike"] - stock_price).abs().idxmin()
        puts_idx = (puts["strike"] - stock_price).abs().idxmin()
        atm_call = calls.loc[calls_idx]
        atm_put = puts.loc[puts_idx]

        # Bid-Ask Mid (両方>0の場合のみ有効)
        call_mid = _row_mid(atm_call)
        put_mid = _row_mid(atm_put)

        # フォールバック: lastPrice
        if call_mid is None:
            lp = atm_call.get("lastPrice", 0) or 0
            call_mid = float(lp) if lp > 0 else None
        if put_mid is None:
            lp = atm_put.get("lastPrice", 0) or 0
            put_mid = float(lp) if lp > 0 else None

        if call_mid is None or put_mid is None or call_mid <= 0 or put_mid <= 0:
            logging.warning(f"{symbol}: invalid option prices call={call_mid}, put={put_mid}")
            return None

        straddle = call_mid + put_mid
        implied_move_pct = (straddle / stock_price) * 100

        return {
            "expiration": target_exp,
            "dte": target_dte,
            "straddle": round(straddle, 2),
            "implied_move_pct": round(implied_move_pct, 2),
            "range_low": round(stock_price - straddle, 2),
            "range_high": round(stock_price + straddle, 2),
            "call_strike": float(atm_call["strike"]),
            "put_strike": float(atm_put["strike"]),
        }
    except Exception as ex:
        logging.error(f"{symbol} implied_move error: {ex}")
        return None
