"""Finnhub API シンプルラッパー

Finnhub 無料枠のエンドポイント:
  - /calendar/earnings  : 決算カレンダー (epsEstimate/revenueEstimate含む)
  - /quote              : 現在株価
  - /stock/profile2     : 企業プロファイル (時価総額・名称)
  - /stock/earnings     : 過去決算実績 (最新4四半期)

レート制限: 無料枠 60 calls/min
"""
import logging
import requests
from time import sleep

BASE_URL = "https://finnhub.io/api/v1"
DEFAULT_TIMEOUT = 15


class FinnhubClient:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("FINNHUB_API_KEY is empty")
        self.api_key = api_key
        self.session = requests.Session()

    def _get(self, path: str, params: dict = None, retries: int = 2) -> dict:
        params = dict(params or {})
        params["token"] = self.api_key

        last_error = None
        for attempt in range(retries + 1):
            try:
                r = self.session.get(
                    f"{BASE_URL}{path}",
                    params=params,
                    timeout=DEFAULT_TIMEOUT,
                )
                if r.status_code == 429:
                    # レート制限: 指数バックオフで再試行
                    wait = 2 ** attempt
                    logging.warning(f"Finnhub 429 rate-limit, retry in {wait}s (attempt {attempt+1})")
                    sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException as ex:
                last_error = ex
                if attempt < retries:
                    sleep(1 + attempt)
                    continue
                raise
        raise last_error

    def earnings_calendar(self, from_date: str, to_date: str, symbol: str = None):
        """
        決算カレンダー取得
        Args:
            from_date: YYYY-MM-DD
            to_date:   YYYY-MM-DD
            symbol:    指定すると単一銘柄のみ
        Returns:
            list of dict: 各要素キー = date, symbol, hour(bmo/amc/dmh), epsEstimate, revenueEstimate, ...
        """
        params = {"from": from_date, "to": to_date}
        if symbol:
            params["symbol"] = symbol
        data = self._get("/calendar/earnings", params)
        return data.get("earningsCalendar", []) or []

    def quote(self, symbol: str) -> dict:
        """
        現在株価取得
        Returns:
            dict: {c(current), d(change), dp(change_pct), h, l, o, pc(prev_close), t(timestamp)}
        """
        return self._get("/quote", {"symbol": symbol})

    def profile(self, symbol: str) -> dict:
        """企業プロファイル取得
        Returns:
            dict: {name, ticker, marketCapitalization(million USD), finnhubIndustry, ...}
        """
        return self._get("/stock/profile2", {"symbol": symbol})

    def stock_earnings(self, symbol: str) -> list:
        """過去決算実績 (最新4四半期)
        Returns:
            list of dict: {actual, estimate, period(YYYY-MM-DD), quarter, surprise, surprisePercent, symbol, year}
        """
        data = self._get("/stock/earnings", {"symbol": symbol})
        # API は list または dict を返す可能性あり
        if isinstance(data, list):
            return data
        return []
