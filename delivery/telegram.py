import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

MAX_CHUNK = 4096  # Telegram message character limit


def send_telegram_message(text: str) -> bool:
    """Send a message to the configured Telegram chat. Splits if over 4096 chars."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram] Missing BOT_TOKEN or CHAT_ID.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = [text[i : i + MAX_CHUNK] for i in range(0, len(text), MAX_CHUNK)]

    for chunk in chunks:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            print(f"[Telegram] Send error: {e}")
            return False

    return True
