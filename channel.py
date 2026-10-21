"""Публикация статистики рынка в канал раз в CHANNEL_POST_EVERY_MIN минут."""
from __future__ import annotations

import os
import time

import binance_data as bd
import config as C
from commands import top_movers
from digest import btc_trend, fear_greed
from util import arrow, base, fmt_pct, fmt_price, now_kyiv


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


def build(symbols: list[str], tickers: dict, with_context: bool) -> str:
    now = now_kyiv()
    lines = [f"📊 <b>Рынок на {now:%H:%M}</b> (Киев)", ""]
    if with_context:
        for extra in (btc_trend(), fear_greed()):
            if extra:
                lines.append(extra)
        lines.append("")
    lines.append("<b>Монета · цена · 1ч · 24ч</b>")
    for sym in symbols:
        t = tickers.get(sym)
        if not t:
            continue
        h1 = change_1h(sym, t["price"])
        h1s = fmt_pct(h1) if h1 is not None else "—"
        lines.append(f"{arrow(t['change_pct'])} <b>{base(sym)}</b> {fmt_price(t['price'])} · "
                     f"{h1s} · {fmt_pct(t['change_pct'])}")
    lines += ["", top_movers(tickers, n=3), "", "<i>Данные Binance. Не финансовая рекомендация.</i>"]
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
