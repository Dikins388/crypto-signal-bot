"""Публикация статистики рынка в канал раз в CHANNEL_POST_EVERY_MIN минут."""
from __future__ import annotations

import os
import time

import binance_data as bd
import config as C
from commands import top_movers
from digest import btc_trend, fear_greed
from util import base, dot_pct, fmt_price_short, market_mood, now_kyiv


def channel_id() -> str | None:
    cid = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
    return cid or None


def due(st: dict) -> bool:
    # Бот просыпается раз в 10 минут, и GitHub бывает неточен, поэтому небольшой допуск
    return time.time() - st.get("channel_last", 0) >= (C.CHANNEL_POST_EVERY_MIN - 3) * 60


def change_1h(sym: str, price: float) -> float | None:
    try:
        k = bd.klines(sym, "5m", limit=13)       # ~час назад: открытие самой старой из 13 пятиминуток
        return (price / k["open"].iloc[0] - 1) * 100 if len(k) else None
    except Exception as e:
        print(f"[канал] {sym}: {e}")
        return None


HOT_1H_PCT = 1.5   # ⚡ в строке, если монета за час сдвинулась сильнее


def build(symbols: list[str], tickers: dict, with_context: bool) -> str:
    now = now_kyiv()
    rows = [(s, tickers[s]) for s in symbols if s in tickers]
    lines = [f"📊 <b>Рынок на {now:%H:%M}</b> (Киев)",
             market_mood([t["change_pct"] for _, t in rows]), ""]
    if with_context:
        for extra in (btc_trend(), fear_greed()):
            if extra:
                lines.append(extra)
        lines.append("")
    for sym, t in rows:
        h1 = change_1h(sym, t["price"])
        hot = " ⚡" if h1 is not None and abs(h1) >= HOT_1H_PCT else ""
        lines.append(f"<b>{base(sym)}</b> {fmt_price_short(t['price'])}\n"
                     f"      1ч {dot_pct(h1)}   24ч {dot_pct(t['change_pct'])}{hot}")
    lines += ["", top_movers(tickers, n=3), "",
              f"⚡ движение больше {HOT_1H_PCT:g}% за час",
              "<i>Данные Binance. Не финансовая рекомендация.</i>"]
    return "\n".join(lines)


def post(st: dict, symbols: list[str], tickers: dict, tg) -> bool:
    cid = channel_id()
    if not cid or not due(st):
        return False
    # Тренд BTC и индекс страха меняются медленно: показываем их раз в час, чтобы пост был короче
    with_context = time.time() - st.get("channel_context_last", 0) >= 55 * 60
    text = build(symbols, tickers, with_context)
    tg.send(text, chat_id=cid, silent=C.CHANNEL_SILENT)
    st["channel_last"] = time.time()
    if with_context:
        st["channel_context_last"] = time.time()
    return True
