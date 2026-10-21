"""Сканер рынка: замечает то, что трейдер не хочет пропустить, пока не смотрит в график.

  ⚡ Резкое движение: за час монета прошла намного больше, чем обычно для неё.
     Порог свой для каждой монеты: для BTC 2% это событие, для мелкой монеты нет.
  📊 Всплеск объёма: за 15 минут проторговали в разы больше обычного. Часто это новость.
  🚀 Пробой уровня: цена закрылась выше вчерашнего максимума или максимума за 7 дней
     (или ниже минимума). Каждый уровень сообщается один раз в день.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import binance_data as bd
import config as C
from util import arrow, base, fmt_pct, fmt_price, link, on_cooldown, set_cooldown


def analyze(sym: str, k15: pd.DataFrame, d1: pd.DataFrame, st: dict) -> list[str]:
    """Возвращает список событий (строк) по монете и отмечает их в состоянии."""
    events: list[str] = []
    if len(k15) < 120:
        return events
    c = k15["close"].to_numpy()
    price = c[-1]

    # ⚡ Резкое движение за час (4 свечи по 15 минут)
    ch1h = (c[-1] / c[-5] - 1) * 100
    hourly = np.abs(c[4:] / c[:-4] - 1)[-672:] * 100      # часовые движения за ~7 дней
    typical = float(np.median(hourly)) if len(hourly) else 0
    threshold = max(C.MOVE_MIN_PCT, C.MOVE_MULT * typical)
    if abs(ch1h) >= threshold:
        key = f"move|{sym}|{int(np.sign(ch1h))}"
        if not on_cooldown(st, key):
            word = "Резкий рост" if ch1h > 0 else "Резкое падение"
            events.append(f"⚡ {word}: {fmt_pct(ch1h)} за час "
                          f"(обычно ~{typical:.1f}%)")
            set_cooldown(st, key, C.EVENT_COOLDOWN_H)

    # 📊 Всплеск объёма на последней закрытой 15-минутке
    v = k15["volume"].to_numpy()
    med = float(np.median(v[-97:-1]))
    candle_move = (c[-1] / k15["open"].iloc[-1] - 1) * 100
    if med > 0 and v[-1] >= C.VOL_SPIKE_MULT * med and abs(candle_move) >= C.VOL_SPIKE_MIN_PCT:
        key = f"vol|{sym}"
        if not on_cooldown(st, key):
            events.append(f"📊 Объём в {v[-1] / med:.1f} раза выше обычного, "
                          f"свеча {fmt_pct(candle_move)}")
            set_cooldown(st, key, C.EVENT_COOLDOWN_H)

    # 🚀 Пробои дневных уровней
    if len(d1) >= 8:
        today = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
        prev = d1.iloc[-1]                       # вчерашняя закрытая дневная свеча
        wk = d1.iloc[-7:]
        prev_close = c[-2]
        levels = [
            ("wh", wk["high"].max(), 1, "максимум за 7 дней"),
            ("wl", wk["low"].min(), -1, "минимум за 7 дней"),
            ("dh", prev["high"], 1, "вчерашний максимум"),
            ("dl", prev["low"], -1, "вчерашний минимум"),
        ]
        done_dirs = set()
        for code, lvl, d, name in levels:
            crossed = (prev_close <= lvl < price) if d == 1 else (prev_close >= lvl > price)
            if not crossed or d in done_dirs:
                continue
            key = f"lvl|{sym}|{today}|{code}"
            if on_cooldown(st, key):
                continue
            done_dirs.add(d)                     # недельный уровень важнее, вчерашний не дублируем
            icon, verb = ("🚀", "Пробит вверх") if d == 1 else ("🔻", "Пробит вниз")
            events.append(f"{icon} {verb} {name}: {fmt_price(lvl)}")
            set_cooldown(st, key, 24)
            if code in ("wh", "wl"):             # и вчерашний в ту же сторону уже не нужен
                set_cooldown(st, f"lvl|{sym}|{today}|d{code[1]}", 24)
    return events


def scan(symbols: list[str], tickers: dict, st: dict) -> list[str]:
    messages = []
    for sym in symbols:
        try:
            k15 = bd.drop_unclosed(bd.klines(sym, "15m", limit=700))
            d1 = bd.drop_unclosed(bd.klines(sym, "1d", limit=10))
        except bd.BinanceUnavailable:
            raise
        except Exception as e:
            print(f"[scan] {sym}: {e}")
            continue
        events = analyze(sym, k15, d1, st)
        if not events:
            continue
        t = tickers.get(sym, {})
        ch24 = t.get("change_pct", 0.0)
        head = (f"<b>{base(sym)}</b> {fmt_price(k15['close'].iloc[-1])}  "
                f"{arrow(ch24)} {fmt_pct(ch24)} за 24ч")
        messages.append(head + "\n" + "\n".join(events) + f"\n{link(sym)}")
    return messages
