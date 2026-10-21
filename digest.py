"""Утренняя сводка рынка."""
from __future__ import annotations

import requests

import binance_data as bd
import config as C
from commands import top_movers
from indicators import ema
from util import arrow, base, fmt_pct, fmt_price, now_kyiv

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]


def fear_greed() -> str | None:
    """Индекс страха и жадности с alternative.me (бесплатный, без ключа)."""
    try:
        d = requests.get("https://api.alternative.me/fng/?limit=2", timeout=8).json()["data"]
        v, prev = int(d[0]["value"]), int(d[1]["value"])
        names = {"Extreme Fear": "сильный страх", "Fear": "страх", "Neutral": "нейтрально",
                 "Greed": "жадность", "Extreme Greed": "сильная жадность"}
        name = names.get(d[0]["value_classification"], d[0]["value_classification"])
        return f"😨 Индекс страха и жадности: <b>{v}</b> ({name}), вчера {prev}"
    except Exception as e:
        print(f"[digest] fear&greed: {e}")
        return None


def btc_trend() -> str | None:
    try:
        d = bd.drop_unclosed(bd.klines("BTCUSDT", "1d", limit=400))
    except Exception as e:
        print(f"[digest] btc: {e}")
        return None
    c = d["close"]
    e50, e200 = ema(c, 50).iloc[-1], ema(c, 200).iloc[-1]
    p = c.iloc[-1]
    if p > e50 > e200:
        state = "бычий (цена выше EMA50 и EMA200 на дневке)"
    elif p < e50 < e200:
        state = "медвежий (цена ниже EMA50 и EMA200 на дневке)"
    else:
        state = "неопределённый (цена между средними)"
    return f"🧭 Тренд BTC: {state}"


def build(symbols: list[str], tickers: dict, journal_stats: str | None) -> str:
    now = now_kyiv()
    lines = [f"☀️ <b>Сводка рынка, {now.day} {MONTHS[now.month - 1]}</b>", ""]
    for extra in (btc_trend(), fear_greed()):
        if extra:
            lines.append(extra)
    lines += ["", "<b>Топ-10 по объёму</b> (24ч / 7 дней):"]
    for sym in symbols:
        t = tickers.get(sym)
        if not t:
            continue
        w = ""
        try:
            d = bd.drop_unclosed(bd.klines(sym, "1d", limit=8))
            if len(d) >= 7:
                w = f" / {fmt_pct((t['price'] / d['close'].iloc[-7] - 1) * 100)}"
        except Exception:
            pass
        lines.append(f"{arrow(t['change_pct'])} {base(sym)} {fmt_price(t['price'])}  "
                     f"{fmt_pct(t['change_pct'])}{w}")
    lines += ["", top_movers(tickers)]
    if journal_stats:
        lines += ["", journal_stats]
    lines += ["", "Команды: /help"]
    return "\n".join(lines)
