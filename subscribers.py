"""Подписчики на уведомления о ракетах.

Telegram не даёт боту писать первым: человек сам открывает бота и жмёт «Старт».
Бот спрашивает владельца, тот разрешает командой /approve. Отписка: /stop.
"""
from __future__ import annotations

import time

import config as C
from util import base, esc, fmt_price_short, link, on_cooldown, set_cooldown

MAX_PENDING = 20


def _name(user: dict) -> str:
    if user.get("username"):
        return "@" + user["username"]
    return " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x) or str(user.get("id"))


def handle_guest(msg: dict, st: dict, tg, owner: str) -> None:
    """Сообщение от кого-то, кроме владельца."""
    chat = msg.get("chat", {})
    if chat.get("type") != "private":
        return
    uid = str(chat["id"])
    text = (msg.get("text") or "").strip().lower()
    subs, pending = st.setdefault("subs", {}), st.setdefault("pending", {})
    name = _name(msg.get("from", {}))

    if uid in subs:
        if text.startswith("/stop"):
            subs.pop(uid)
            tg.send("Вы отписались от уведомлений о ракетах. Вернуться: /start", chat_id=uid)
            tg.send(f"👤 {esc(name)} отписался от ракет.")
        else:
            tg.send(f"🚀 Вы подписаны на уведомления о ракетах: монеты Binance, выросшие "
                    f"на {C.ROCKET_ALERT_PCT}%+ за сутки.\nОтписаться: /stop", chat_id=uid)
        return

    if text.startswith("/stop"):
        pending.pop(uid, None)
        tg.send("Хорошо, запрос отменён.", chat_id=uid)
        return

    if uid in pending:
        tg.send("Запрос уже отправлен владельцу бота, ждём подтверждения.", chat_id=uid)
        return
    if len(pending) >= MAX_PENDING:
        return                                    # защита от наплыва: молча игнорируем
    pending[uid] = name
    tg.send("👋 Привет! Это бот «Крипто НЮХ».\n"
            f"Запрос на уведомления о ракетах (рост монеты на {C.ROCKET_ALERT_PCT}%+ за сутки) "
            "отправлен владельцу. Как только он подтвердит, я начну присылать их сюда.",
            chat_id=uid)
    tg.send(f"👤 <b>{esc(name)}</b> хочет получать уведомления о ракетах.\n"
            f"Разрешить: <code>/approve {uid}</code>\nВсе подписчики и запросы: /subs")


def owner_command(cmd: str, args: list[str], st: dict, tg) -> str | None:
    """Команды владельца для подписчиков. None, если команда не про подписчиков."""
    subs, pending = st.setdefault("subs", {}), st.setdefault("pending", {})

    def find(arg: str, where: dict) -> str | None:
        a = arg.lstrip("@").lower()
        for uid, nm in where.items():
            n = nm if isinstance(nm, str) else nm.get("name", "")
            if uid == arg or n.lstrip("@").lower() == a:
                return uid
        return None

    if cmd == "/approve":
        if not args:
            if len(pending) == 1:
                args = [next(iter(pending))]
            else:
                return ("Кого разрешить? Напиши <code>/approve id</code> или <code>/approve @ник</code>.\n"
                        "Запросы: /subs")
        uid = find(args[0], pending)
        if not uid:
            return "Такого запроса нет. Человек должен сначала открыть бота и нажать «Старт». Запросы: /subs"
        name = pending.pop(uid)
        subs[uid] = {"name": name, "since": time.time()}
        tg.send(f"✅ Подписка подтверждена! Буду присылать сюда ракеты: монеты Binance, выросшие "
                f"на {C.ROCKET_ALERT_PCT}%+ за сутки.\nОтписаться: /stop", chat_id=uid)
        return f"✅ {esc(name)} теперь получает уведомления о ракетах."

    if cmd == "/unsub":
        if not args:
            return "Кого отписать? <code>/unsub @ник</code> или <code>/unsub id</code>"
        uid = find(args[0], subs)
        if not uid:
            uid = find(args[0], pending)
            if uid:
                return f"🗑 Запрос {esc(pending.pop(uid))} отклонён."
            return "Такого подписчика нет. Список: /subs"
        name = subs.pop(uid)["name"]
        return f"🗑 {esc(name)} больше не получает ракеты."

    if cmd == "/subs":
        lines = [f"🚀 <b>Подписчики на ракеты</b> (от {C.ROCKET_ALERT_PCT}%):"]
        lines += [f"• {esc(v['name'])} (<code>{k}</code>)" for k, v in subs.items()] or ["пока никого"]
        if pending:
            lines += ["", "<b>Ждут подтверждения:</b>"]
            lines += [f"• {esc(n)}: <code>/approve {k}</code>" for k, n in pending.items()]
        lines += ["", "Отписать: <code>/unsub @ник</code>"]
        return "\n".join(lines)

    return None


def alert_rockets(rockets: list[dict], st: dict, tg, owner: str) -> int:
    """Личные уведомления о ракетах от ROCKET_ALERT_PCT%. Каждая монета не чаще раза в сутки."""
    subs = st.setdefault("subs", {})
    targets = list(subs) + ([owner] if C.ROCKET_ALERT_TO_OWNER else [])
    if not targets:
        return 0
    sent = 0
    for r in rockets:
        if r["rise"] < C.ROCKET_ALERT_PCT:
            continue
        key = f"rocketalert|{r['symbol']}"
        if on_cooldown(st, key):
            continue
        set_cooldown(st, key, 24)
        h, m = divmod(int(r["minutes"]), 60)
        dur = f"{h}ч {m:02d}м" if h else f"{m}м"
        state = ("сейчас на пике" if r["peak_ago_min"] < 15
                 else f"от пика уже {r['from_peak']:+.0f}%")
        text = (f"🚀 <b>Ракета: {base(r['symbol'])} +{r['rise']:.0f}%</b> за {dur}\n"
                f"{fmt_price_short(r['low'])} → {fmt_price_short(r['peak'])}, {state}\n"
                f"{link(r['symbol'])}\n"
                f"<i>Большая часть такого движения обычно уже позади. Не финансовая рекомендация.</i>")
        for uid in targets:
            try:
                tg.send(text, chat_id=uid)
                sent += 1
            except Exception as e:
                print(f"[ракеты→{uid}] {e}")
                if "blocked" in str(e) or "deactivated" in str(e):
                    subs.pop(uid, None)          # человек заблокировал бота
    return sent
