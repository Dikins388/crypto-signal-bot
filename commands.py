"""Команды, которые можно писать боту, и личные ценовые алерты.

Бот живёт на GitHub и просыпается раз в 15 минут, поэтому отвечает не мгновенно,
а при следующем запуске (обычно в течение 15 минут).
"""
from __future__ import annotations

import time

import pandas as pd

import binance_data as bd
import config as C
from util import arrow, base, esc, fmt_money, fmt_pct, fmt_price, link

HELP = (
    "🤖 <b>Что я умею</b>\n\n"
    "<b>Сам присылаю:</b>\n"
    "⚡ резкие движения цены за час\n"
    "📊 всплески объёма (часто это новости)\n"
    "🚀 пробои вчерашних и недельных максимумов/минимумов\n"
    f"☀️ утреннюю сводку рынка в {C.DIGEST_HOUR}:00\n"
    + ("🧪 экспериментальные сигналы на 4h и их результаты\n" if C.SIGNALS_ENABLED else "")
    + "\n"
    "<b>Команды</b> (все есть в кнопке «Меню» слева от поля ввода):\n"
    "/price: цены топ-10 монет\n"
    "/price btc eth: цены конкретных монет\n"
    "/alert btc 65000: сообщу, когда цена дойдёт до уровня\n"
    "/alerts: мои алерты\n"
    "/del 3: удалить алерт №3\n"
    "/top: лидеры роста и падения за 24ч\n"
    "/digest: сводка рынка прямо сейчас\n"
    + ("/stats: статистика экспериментальных сигналов\n" if C.SIGNALS_ENABLED else "")
    + "/mute и /unmute: выключить/включить сканер рынка\n\n"
    "⏱ Я просыпаюсь раз в 15 минут, поэтому отвечаю с задержкой до 15 минут."
)


# Меню команд в Telegram: (команда, описание до 256 символов)
MENU = [
    ("help", "Инструкция: что умеет бот"),
    ("price", "Цены. Без монет: топ-10. Пример: /price btc eth"),
    ("alert", "Сообщить, когда цена дойдёт. Пример: /alert btc 65000"),
    ("alerts", "Список моих ценовых алертов"),
    ("del", "Удалить алерт по номеру. Пример: /del 3"),
    ("top", "Лидеры роста и падения за 24 часа"),
    ("digest", "Сводка рынка прямо сейчас"),
    ("mute", "Выключить уведомления сканера рынка"),
    ("unmute", "Включить уведомления сканера рынка"),
]
if C.SIGNALS_ENABLED:
    MENU.insert(7, ("stats", "Статистика экспериментальных сигналов"))
MENU_VERSION = 2   # увеличь, если меняешь MENU, чтобы бот заново отправил его в Telegram

DESCRIPTION = (
    "Помощник трейдера: следит за рынком Binance и пишет, когда происходит важное.\n\n"
    "⚡ резкие движения цены\n📊 всплески объёма\n🚀 пробои максимумов и минимумов\n"
    "🔔 твои ценовые алерты\n☀️ утренняя сводка рынка\n\n"
    "Команды в кнопке «Меню». Отвечаю с задержкой до 15 минут. Не финансовая рекомендация."
)
SHORT_DESCRIPTION = "Следит за рынком Binance: резкие движения, объёмы, пробои уровней, алерты, сводка."


def _price_lines(symbols: list[str], tickers: dict) -> str:
    out = []
    for sym in symbols:
        t = tickers.get(sym)
        if not t:
            continue
        out.append(f"<b>{base(sym)}</b> {fmt_price(t['price'])}  {arrow(t['change_pct'])} "
                   f"{fmt_pct(t['change_pct'])}  (24ч: {fmt_price(t['low'])} – {fmt_price(t['high'])}, "
                   f"объём {fmt_money(t['quote_volume'])}$)  {link(sym)}")
    return "\n".join(out)


def top_movers(tickers: dict, n: int = 5) -> str:
    liquid = {s: t for s, t in tickers.items() if t["quote_volume"] >= C.MIN_QUOTE_VOLUME}
    ranked = sorted(liquid.items(), key=lambda kv: kv[1]["change_pct"])
    up = [kv for kv in ranked[::-1] if kv[1]["change_pct"] > 0][:n]
    dn = [kv for kv in ranked if kv[1]["change_pct"] < 0][:n]
    fmt = lambda kv: f"{base(kv[0])} {fmt_pct(kv[1]['change_pct'])}"
    return ("📈 <b>Рост за 24ч:</b> " + (", ".join(map(fmt, up)) or "никто не вырос") + "\n"
            "📉 <b>Падение за 24ч:</b> " + (", ".join(map(fmt, dn)) or "никто не упал"))


def handle_message(text: str, st: dict, tickers: dict, extra: dict) -> str | None:
    """Разбирает одну команду и возвращает ответ."""
    parts = text.strip().split()
    if not parts:
        return None
    cmd = parts[0].lower().split("@")[0]
    args = parts[1:]

    if cmd in ("/start", "/help", "помощь"):
        return HELP

    if cmd == "/price":
        if not args:
            return ("💰 <b>Топ-10 по объёму</b>\n" + _price_lines(bd.top_symbols(C.TOP_N, tickers), tickers)
                    + "\n\nКонкретные монеты: /price btc eth")
        syms = [s for s in (bd.normalize_symbol(a) for a in args[:10]) if s]
        return _price_lines(syms, tickers) or "Не нашёл таких монет на Binance (пары к USDT)."

    if cmd == "/alert":
        if len(args) != 2:
            return ("🔔 Чтобы поставить алерт, напиши монету и цену:\n"
                    "<code>/alert btc 65000</code>\n\n"
                    "Если цена выше текущей, сообщу, когда монета поднимется до неё, если ниже, когда опустится.")
        sym = bd.normalize_symbol(args[0])
        if not sym:
            return f"Не нашёл монету «{esc(args[0])}» на Binance."
        try:
            target = float(args[1].replace(",", ".").replace(" ", ""))
        except ValueError:
            return "Цена должна быть числом, например /alert btc 65000"
        cur = tickers.get(sym, {}).get("price")
        if cur is None:
            return "Не смог получить текущую цену, попробуй позже."
        direction = 1 if target > cur else -1
        aid = st["next_alert_id"]
        st["next_alert_id"] += 1
        st["alerts"].append({"id": aid, "symbol": sym, "price": target, "dir": direction,
                             "created": time.time()})
        word = "поднимется до" if direction == 1 else "опустится до"
        return (f"✅ Алерт №{aid}: сообщу, когда {base(sym)} {word} {fmt_price(target)}\n"
                f"Сейчас {fmt_price(cur)} ({fmt_pct((target / cur - 1) * 100)} до цели)")

    if cmd == "/alerts":
        if not st["alerts"]:
            return "Алертов нет. Добавить: /alert btc 65000"
        lines = []
        for a in st["alerts"]:
            cur = tickers.get(a["symbol"], {}).get("price")
            dist = f" (сейчас {fmt_price(cur)}, {fmt_pct((a['price'] / cur - 1) * 100)})" if cur else ""
            sign = "≥" if a["dir"] == 1 else "≤"
            lines.append(f"№{a['id']}: {base(a['symbol'])} {sign} {fmt_price(a['price'])}{dist}")
        return "🔔 <b>Твои алерты:</b>\n" + "\n".join(lines) + "\n\nУдалить: /del номер"

    if cmd == "/del":
        if not args or not args[0].isdigit():
            return "Напиши номер алерта: <code>/del 3</code>\nНомера видно в /alerts"
        aid = int(args[0])
        before = len(st["alerts"])
        st["alerts"] = [a for a in st["alerts"] if a["id"] != aid]
        return f"🗑 Алерт №{aid} удалён." if len(st["alerts"]) < before else f"Алерта №{aid} нет."

    if cmd == "/top":
        return top_movers(tickers)

    if cmd == "/digest":
        return extra["digest"]()

    if cmd == "/stats":
        return extra["stats"]()

    if cmd == "/mute":
        st["muted"] = True
        return "🔕 Сканер рынка выключен. Алерты и сводка продолжают работать. Включить: /unmute"

    if cmd == "/unmute":
        st["muted"] = False
        return "🔔 Сканер рынка снова работает."

    return "Не понял команду. Список команд: /help"


def process_commands(st: dict, tickers: dict, extra: dict, tg) -> int:
    """Забирает новые сообщения и отвечает. Слушается только владельца (TELEGRAM_CHAT_ID)."""
    owner = str(tg.own_chat_id())
    updates = tg.get_updates(st["tg_offset"])
    n = 0
    for u in updates:
        st["tg_offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        chat = str(msg.get("chat", {}).get("id", ""))
        text = msg.get("text")
        if not text or chat != owner:
            continue                              # чужим не отвечаем
        try:
            reply = handle_message(text, st, tickers, extra)
        except Exception as e:                    # одна кривая команда не должна ронять бота
            reply = f"Ошибка при обработке команды: {esc(e)}"
        if reply:
            tg.send(reply)
            n += 1
    return n


def check_alerts(st: dict, tickers: dict) -> list[str]:
    """Проверяет личные алерты по максимумам/минимумам свечей с момента создания."""
    fired, keep = [], []
    by_symbol: dict[str, pd.DataFrame] = {}
    for a in st["alerts"]:
        sym = a["symbol"]
        if sym not in by_symbol:
            try:
                by_symbol[sym] = bd.klines(sym, "15m", limit=8)
            except Exception as e:
                print(f"[alerts] {sym}: {e}")
                by_symbol[sym] = pd.DataFrame()
        k = by_symbol[sym]
        created = pd.Timestamp(a["created"], unit="s", tz="UTC")
        hi = lo = tickers.get(sym, {}).get("price")
        if not k.empty:
            # только свечи, открывшиеся после создания алерта: экстремум до него не считается
            recent = k[k["open_time"] >= created.floor("15min")]
            if not recent.empty:
                hi = max(hi or 0, recent["high"].max())
                lo = min(lo if lo is not None else float("inf"), recent["low"].min())
        hit = hi is not None and ((a["dir"] == 1 and hi >= a["price"]) or
                                  (a["dir"] == -1 and lo <= a["price"]))
        if hit:
            cur = tickers.get(sym, {}).get("price", a["price"])
            word = "достиг" if a["dir"] == 1 else "опустился до"
            fired.append(f"🔔 <b>Алерт №{a['id']}</b>: {base(sym)} {word} {fmt_price(a['price'])}\n"
                         f"Сейчас {fmt_price(cur)}  {link(sym)}")
        else:
            keep.append(a)
    st["alerts"] = keep
    return fired
