"""Публикация статистики рынка в канал раз в CHANNEL_POST_EVERY_MIN минут.

Пост: настроение рынка, топ-N монет по объёму (цена, за 1ч, за 24ч),
внизу «ракеты»: монеты со всего Binance, которые за последние сутки
выросли от дна до вершины на ROCKET_MIN_PCT% и больше.
"""
from __future__ import annotations

import os
import time

import numpy as np

import binance_data as bd
import config as C
from digest import btc_trend, fear_greed
from util import base, fmt_price_short, market_mood, now_kyiv

HOT_1H_PCT = 1.5   # ⚡ в строке, если монета за час сдвинулась сильнее


def channel_id() -> str | None:
    cid = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
    return cid or None


def due(st: dict) -> bool:
    # Бот просыпается раз в 10 минут, и будильник бывает неточен, поэтому небольшой допуск
    return time.time() - st.get("channel_last", 0) >= (C.CHANNEL_POST_EVERY_MIN - 3) * 60


def change_1h(sym: str, price: float) -> float | None:
    try:
        k = bd.klines(sym, "5m", limit=13)       # ~час назад: открытие самой старой из 13 пятиминуток
        return (price / k["open"].iloc[0] - 1) * 100 if len(k) else None
    except Exception as e:
        print(f"[канал] {sym}: {e}")
        return None


def _num(x: float | None) -> str:
    """Компактно для строки монеты: 🟢+0.18 / 🔴-2.59 (проценты, знак % в легенде)."""
    if x is None:
        return "⚪ —"
    mark = "🟢" if x >= 0.05 else "🔴" if x <= -0.05 else "⚪"
    return f"{mark}{x:+.2f}"


def _dur(minutes: float) -> str:
    h, m = divmod(int(round(minutes / 15) * 15), 60)
    return f"{h}ч {m:02d}м" if h else f"{m}м"


def find_rockets(tickers: dict) -> list[dict]:
    """Монеты, которые за 24 часа выросли от дна до последующей вершины на ROCKET_MIN_PCT%+."""
    need = 1 + C.ROCKET_MIN_PCT / 100
    cands = [s for s, t in tickers.items()
             if t["quote_volume"] >= C.ROCKET_MIN_QUOTE_VOLUME and t["low"] > 0
             and t["high"] / t["low"] >= need]           # иначе ракетой быть не могла
    out = []
    for sym in cands:
        try:
            k = bd.klines(sym, "15m", limit=97)
        except Exception as e:
            print(f"[ракеты] {sym}: {e}")
            continue
        if len(k) < 2:
            continue
        lows, highs = k["low"].to_numpy(), k["high"].to_numpy()
        run_min = np.minimum.accumulate(lows)
        run_min_idx = np.array([int(np.argmin(lows[: i + 1])) for i in range(len(lows))])
        rise = highs / run_min
        j = int(np.argmax(rise))
        if rise[j] < need:
            continue
        i = run_min_idx[j]
        price = tickers[sym]["price"]
        out.append({
            "symbol": sym,
            "rise": (rise[j] - 1) * 100,
            "minutes": (j - i + 1) * 15,
            "low": float(run_min[j]), "peak": float(highs[j]),
            "now_vs_low": (price / run_min[j] - 1) * 100,
            "from_peak": (price / highs[j] - 1) * 100,
            "peak_ago_min": (len(k) - 1 - j) * 15,
        })
    out.sort(key=lambda r: -r["rise"])
    return out


def find_dumps(tickers: dict) -> list[dict]:
    """Монеты, которые за 24 часа упали от вершины до последующего дна на DUMP_MIN_PCT%+."""
    keep = 1 - C.DUMP_MIN_PCT / 100
    cands = [s for s, t in tickers.items()
             if t["quote_volume"] >= C.ROCKET_MIN_QUOTE_VOLUME and t["high"] > 0
             and t["low"] / t["high"] <= keep]           # иначе обвалом быть не мог
    out = []
    for sym in cands:
        try:
            k = bd.klines(sym, "15m", limit=97)
        except Exception as e:
            print(f"[обвалы] {sym}: {e}")
            continue
        if len(k) < 2:
            continue
        lows, highs = k["low"].to_numpy(), k["high"].to_numpy()
        run_max = np.maximum.accumulate(highs)
        ratio = lows / run_max
        j = int(np.argmin(ratio))
        if ratio[j] > keep:
            continue
        i = int(np.argmax(highs[: j + 1]))
        price = tickers[sym]["price"]
        out.append({
            "symbol": sym,
            "drop": (1 - ratio[j]) * 100,
            "minutes": (j - i + 1) * 15,
            "peak": float(run_max[j]), "low": float(lows[j]),
            "from_low": (price / lows[j] - 1) * 100,
            "vs_peak": (price / run_max[j] - 1) * 100,
            "low_ago_min": (len(k) - 1 - j) * 15,
        })
    out.sort(key=lambda r: -r["drop"])
    return out


def dumps_block(dumps: list[dict], st: dict) -> list[str]:
    now = time.time()
    seen = {k: v for k, v in st.get("dumps_seen", {}).items() if now - v < 26 * 3600}
    st["dumps_seen"] = seen
    lines = [f"💥 <b>Обвалы за сутки</b> (падение от {C.DUMP_MIN_PCT}%, весь Binance)"]
    if not dumps:
        lines.append("Сейчас таких нет.")
        return lines
    for r in dumps[: C.ROCKETS_MAX_SHOWN]:
        sym = r["symbol"]
        new = sym not in seen
        if new:
            seen[sym] = now
        tag = "🆕 " if new else ""
        ago = "сейчас на дне" if r["low_ago_min"] < 15 else f"дно {_dur(r['low_ago_min'])} назад"
        lines.append(
            f"{tag}<b>{base(sym)}</b> -{r['drop']:.0f}% за {_dur(r['minutes'])} "
            f"({fmt_price_short(r['peak'])} → {fmt_price_short(r['low'])})\n"
            f"      {ago}, от дна {r['from_low']:+.0f}%, от пика {r['vs_peak']:+.0f}%")
    if len(dumps) > C.ROCKETS_MAX_SHOWN:
        lines.append(f"…и ещё {len(dumps) - C.ROCKETS_MAX_SHOWN}")
    return lines


def rockets_block(rockets: list[dict], st: dict) -> list[str]:
    now = time.time()
    # Помним, какие ракеты уже показывали, чтобы отмечать новые значком 🆕. Старше суток забываем.
    seen = {k: v for k, v in st.get("rockets_seen", {}).items() if now - v < 26 * 3600}
    st["rockets_seen"] = seen
    lines = [f"🚀 <b>Ракеты за сутки</b> (рост от {C.ROCKET_MIN_PCT}%, весь Binance)"]
    if not rockets:
        lines.append("Сейчас таких нет.")
        return lines
    for r in rockets[: C.ROCKETS_MAX_SHOWN]:
        sym = r["symbol"]
        new = sym not in seen
        if new:
            seen[sym] = now
        tag = "🆕 " if new else ""
        ago = "сейчас на пике" if r["peak_ago_min"] < 15 else f"пик {_dur(r['peak_ago_min'])} назад"
        lines.append(
            f"{tag}<b>{base(sym)}</b> +{r['rise']:.0f}% за {_dur(r['minutes'])} "
            f"({fmt_price_short(r['low'])} → {fmt_price_short(r['peak'])})\n"
            f"      {ago}, от пика {r['from_peak']:+.0f}%, от дна {r['now_vs_low']:+.0f}%")
    if len(rockets) > C.ROCKETS_MAX_SHOWN:
        lines.append(f"…и ещё {len(rockets) - C.ROCKETS_MAX_SHOWN}")
    return lines


def build(symbols: list[str], tickers: dict, with_context: bool, rockets: list[dict], st: dict,
          dumps: list[dict] | None = None) -> str:
    now = now_kyiv()
    rows = [(s, tickers[s]) for s in symbols if s in tickers]
    lines = [f"📊 <b>Рынок на {now:%H:%M}</b> (Киев)",
             market_mood([t["change_pct"] for _, t in rows]), ""]
    if with_context:
        for extra in (btc_trend(), fear_greed()):
            if extra:
                lines.append(extra)
        lines.append("")
    lines.append(f"<b>Топ-{len(rows)}</b> · цена · % за 1ч · % за 24ч")
    for sym, t in rows:
        h1 = change_1h(sym, t["price"])
        hot = " ⚡" if h1 is not None and abs(h1) >= HOT_1H_PCT else ""
        lines.append(f"<b>{base(sym)}</b> {fmt_price_short(t['price'])}  "
                     f"{_num(h1)}  {_num(t['change_pct'])}{hot}")
    lines += ["", *rockets_block(rockets, st), "", *dumps_block(dumps or [], st), "",
              f"⚡ больше {HOT_1H_PCT:g}% за час · 🆕 появилась впервые",
              "<i>Данные Binance. Не финансовая рекомендация.</i>"]
    return "\n".join(lines)


def post(st: dict, symbols: list[str], tickers: dict, tg, rockets: list[dict] | None = None) -> bool:
    cid = channel_id()
    if not cid or not due(st):
        return False
    # Тренд BTC и индекс страха меняются медленно: показываем их раз в час, чтобы пост был короче
    with_context = time.time() - st.get("channel_context_last", 0) >= 55 * 60
    top = bd.top_symbols(C.CHANNEL_TOP_N, tickers)
    if rockets is None:
        rockets = find_rockets(tickers)
    dumps = find_dumps(tickers)
    text = build(top, tickers, with_context, rockets, st, dumps)
    tg.send(text, chat_id=cid, silent=C.CHANNEL_SILENT)
    st["channel_last"] = time.time()
    if with_context:
        st["channel_context_last"] = time.time()
    return True
