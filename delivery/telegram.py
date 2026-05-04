import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

MAX_CHUNK = 4000  # Telegram hard limit is 4096; leave headroom for safety


def _split_on_boundaries(text: str, limit: int) -> list[str]:
    """Split text into chunks <= limit, preferring paragraph > line > word boundaries.

    Avoids cutting through markdown pairs like *bold* or [text](url) which
    causes Telegram's legacy Markdown parser to reject the message with 400.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        # Prefer paragraph break, then line break, then space
        for sep in ("\n\n", "\n", " "):
            split_at = window.rfind(sep)
            if split_at > limit // 2:
                break
        else:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _post(url: str, chat_id: str, text: str, parse_mode: str | None) -> tuple[bool, str]:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.ok:
            return True, ""
        return False, f"{resp.status_code} {resp.text[:300]}"
    except Exception as e:
        return False, str(e)


def send_telegram_message(text: str) -> bool:
    """Send a message to the configured Telegram chat.

    Splits on paragraph/line boundaries to avoid mid-markdown cuts.
    Falls back to plain text if Markdown parsing fails, so delivery
    always succeeds even when the LLM emits edge-case markdown.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram] Missing BOT_TOKEN or CHAT_ID.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = _split_on_boundaries(text, MAX_CHUNK)

    all_ok = True
    for i, chunk in enumerate(chunks, 1):
        ok, err = _post(url, TELEGRAM_CHAT_ID, chunk, parse_mode="Markdown")
        if not ok:
            print(f"[Telegram] Markdown failed on chunk {i}/{len(chunks)}: {err} — retrying as plain text.")
            ok, err = _post(url, TELEGRAM_CHAT_ID, chunk, parse_mode=None)
            if not ok:
                print(f"[Telegram] Plain text also failed on chunk {i}/{len(chunks)}: {err}")
                all_ok = False

    return all_ok
