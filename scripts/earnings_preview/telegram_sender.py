"""Telegram Bot API 送信ラッパー

parse_mode = HTML を使用するため、ユーザー入力文字列は呼び出し側で
html.escape() してから渡すこと。
"""
import logging
import requests
from time import sleep

TELEGRAM_API_BASE = "https://api.telegram.org"


def send_telegram(messages, bot_token: str, chat_id: str):
    """HTML形式メッセージを送信

    Args:
        messages: str or list[str]
        bot_token: BotFather から取得した token
        chat_id: 送信先 chat_id (ユーザー or チャンネル)
    Raises:
        requests.RequestException: 送信失敗時
    """
    if not bot_token or not chat_id:
        raise ValueError("bot_token and chat_id are required")

    if isinstance(messages, str):
        messages = [messages]

    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"

    for i, msg in enumerate(messages):
        if not msg or not msg.strip():
            continue

        payload = {
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            r = requests.post(url, json=payload, timeout=15)
            r.raise_for_status()
            logging.info(f"Telegram: sent message {i+1}/{len(messages)} ({len(msg)} chars)")
        except requests.RequestException as ex:
            logging.error(f"Telegram send failed (msg {i+1}): {ex}")
            if hasattr(ex, "response") and ex.response is not None:
                logging.error(f"Telegram response body: {ex.response.text}")
            raise

        if i < len(messages) - 1:
            sleep(1)  # 連投レート制限回避


def send_error_notification(error_text: str, bot_token: str, chat_id: str):
    """システムエラー発生時の簡易通知 (HTML無し)"""
    if not bot_token or not chat_id:
        return
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"⚠️ 決算プレビューシステムエラー\n{error_text[:1000]}",
        "disable_web_page_preview": True,
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception:
        pass  # エラー通知失敗は握りつぶす
