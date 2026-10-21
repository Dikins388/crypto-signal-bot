"""Узнать свой chat_id. Сначала напиши своему боту в Telegram любое сообщение, потом запусти:

    python get_chat_id.py ТВОЙ_ТОКЕН
"""
import sys

import requests

if len(sys.argv) < 2:
    sys.exit("Использование: python get_chat_id.py ТОКЕН_ОТ_BOTFATHER")

r = requests.get(f"https://api.telegram.org/bot{sys.argv[1]}/getUpdates", timeout=20).json()
if not r.get("ok"):
    sys.exit(f"Ошибка: {r}")
chats = {}
for u in r["result"]:
    msg = u.get("message") or u.get("channel_post") or {}
    chat = msg.get("chat")
    if chat:
        chats[chat["id"]] = chat.get("username") or chat.get("title") or chat.get("first_name")
if not chats:
    print("Пусто. Напиши боту любое сообщение (например /start) и запусти ещё раз.")
for cid, name in chats.items():
    print(f"chat_id = {cid}   ({name})")
