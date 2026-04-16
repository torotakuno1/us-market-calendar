"""過去決算の Beat/Miss 統計

Finnhub /stock/earnings から直近N四半期の実績を集計
"""
import logging


def get_past_earnings_stats(client, symbol: str, lookback: int = 4):
    """
    Args:
        client: FinnhubClient インスタンス
        symbol: ティッカー
        lookback: 何四半期分を集計するか (default 4)
    Returns:
        dict or None (取得失敗 or データ不足時)
        {
            'last_report': {
                'period': 'YYYY-MM-DD',
                'eps_actual': float,
                'eps_estimate': float,
                'beat_pct': float or None,
            },
            'avg_eps_beat_pct': float,       # 平均 Beat/Miss 率 (%)
            'beat_count': int,               # Beat 回数
            'total_count': int,              # 有効データ数 (<= lookback)
        }
    """
    try:
        data = client.stock_earnings(symbol)
        if not data or not isinstance(data, list):
            return None

        # period降順で並べ替え (文字列比較で YYYY-MM-DD 形式は正しく動作)
        data = sorted(data, key=lambda x: x.get("period", ""), reverse=True)[:lookback]

        beat_pcts = []
        beats = 0
        for e in data:
            actual = e.get("actual")
            estimate = e.get("estimate")
            if actual is None or estimate is None or estimate == 0:
                continue
            beat_pct = (actual - estimate) / abs(estimate) * 100
            beat_pcts.append(beat_pct)
            if actual > estimate:
                beats += 1

        if not beat_pcts:
            return None

        last = data[0]
        last_actual = last.get("actual")
        last_estimate = last.get("estimate")
        last_beat_pct = None
        if last_actual is not None and last_estimate not in (None, 0):
            last_beat_pct = round(
                (last_actual - last_estimate) / abs(last_estimate) * 100, 1
            )

        return {
            "last_report": {
                "period": last.get("period"),
                "eps_actual": last_actual,
                "eps_estimate": last_estimate,
                "beat_pct": last_beat_pct,
            },
            "avg_eps_beat_pct": round(sum(beat_pcts) / len(beat_pcts), 1),
            "beat_count": beats,
            "total_count": len(beat_pcts),
        }
    except Exception as ex:
        logging.error(f"{symbol} past_earnings error: {ex}")
        return None
