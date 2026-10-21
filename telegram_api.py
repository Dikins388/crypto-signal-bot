"""Отправка сообщений в Telegram обычным HTTP-запросом (бот только пишет, отвечать не нужно)."""
import os
import time

import requests

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000


def _creds():
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        raise RuntimeError("Не заданы TELEGRAM_TOKEN и/или TELEGRAM_CHAT_ID")
    return token, chat


def send(text: str) -> None:
    token, chat = _creds()
    for chunk in _split(text):
        for attempt in range(3):
            r = requests.post(API.format(token=token, method="sendMessage"), json={
                "chat_id": chat, "text": chunk, "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }, timeout=20)
            if r.status_code == 200:
                break
            if r.status_code == 429:
                time.sleep(r.json().get("parameters", {}).get("retry_after", 3))
                continue
            raise RuntimeError(f"Telegram ответил {r.status_code}: {r.text[:200]}")
        time.sleep(0.5)


def get_updates(offset: int) -> list[dict]:
    """Новые сообщения боту (с момента offset). Бот не висит онлайн, а забирает их при запуске."""
    token, _ = _creds()
    r = requests.get(API.format(token=token, method="getUpdates"),
                     params={"offset": offset, "timeout": 0,
                             "allowed_updates": '["message"]'}, timeout=20)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"getUpdates: {data}")
    return data["result"]


def own_chat_id() -> str:
    return _creds()[1]


def send_document(path: str, caption: str = "") -> None:
    token, chat = _creds()
    with open(path, "rb") as f:
        r = requests.post(API.format(token=token, method="sendDocument"),
                          data={"chat_id": chat, "caption": caption[:1000]},
                          files={"document": f}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram ответил {r.status_code}: {r.text[:200]}")


def _split(text: str):
    while len(text) > MAX_LEN:
        cut = text.rfind("\n", 0, MAX_LEN)
        cut = cut if cut > 0 else MAX_LEN
        yield text[:cut]
        text = text[cut:]
    if text:
        yield text
