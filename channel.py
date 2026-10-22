"""Публикация в канал раз в CHANNEL_POST_EVERY_MIN минут.

Пост:
  1) монеты со всего спота Binance, сдвинувшиеся за сутки на MOVE_BLOCK_PCT%+ (в любую сторону);
  2) ракеты: рост от дна до последующей вершины на ROCKET_MIN_PCT%+;
  3) обвалы: падение от вершины до последующего дна на DUMP_MIN_PCT%+;
Строки в порядке «монета · час · сутки», рост 🟢, падение 🔴.
"""
from __future__ import annotations

import html
import os
import time

import numpy as np

import binance_data as bd
import config as C
from util import base, now_kyiv

def channel_id() -> str | None:
    cid = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
    return cid or None


def due(st: dict) -> bool:
    # Бот просыпается раз в 10 минут, и будильник бывает неточен, поэтому небольшой допуск
    return time.time() - st.get("channel_last", 0) >= (C.CHANNEL_POST_EVERY_MIN - 3) * 60


# --- Поиск сильных движений за сутки -----------------------------------------

_cache: dict = {}


def scan_moves(tickers: dict) -> list[dict]:
    """По всем ликвидным монетам: самый сильный рост (дно → вершина) и падение (вершина → дно)
    за последние 24 часа. Результат кэшируется на один запуск бота."""
    if _cache.get("id") == id(tickers):
        return _cache["moves"]
    need = 1 + min(C.MOVE_BLOCK_PCT, C.ROCKET_MIN_PCT, C.DUMP_MIN_PCT) / 100
    cands = [s for s, t in tickers.items()
             if t["quote_volume"] >= C.ROCKET_MIN_QUOTE_VOLUME and t["low"] > 0
             and t["high"] / t["low"] >= need]           # иначе сильного движения быть не могло
    out = []
    for sym in cands:
        try:
            k = bd.klines(sym, "15m", limit=97)
        except Exception as e:
            print(f"[движения] {sym}: {e}")
            continue
        if len(k) < 5:
            continue
        lows, highs = k["low"].to_numpy(), k["high"].to_numpy()
        price = tickers[sym]["price"]

        run_min = np.minimum.accumulate(lows)
        rise_r = highs / run_min
        j = int(np.argmax(rise_r))                       # вершина
        i = int(np.argmin(lows[: j + 1]))                # дно перед ней
        run_max = np.maximum.accumulate(highs)
        drop_r = lows / run_max
        q = int(np.argmin(drop_r))                       # дно
        p = int(np.argmax(highs[: q + 1]))               # вершина перед ним

        out.append({
            "symbol": sym,
            "h1": (price / k["open"].iloc[-4] - 1) * 100,   # примерно за последний час
            "d24": tickers[sym]["change_pct"],
            # рост (используется и для личных уведомлений о ракетах)
            "rise": (rise_r[j] - 1) * 100, "minutes": (j - i + 1) * 15,
            "low": float(run_min[j]), "peak": float(highs[j]),
            "peak_ago_min": (len(k) - 1 - j) * 15, "from_peak": (price / highs[j] - 1) * 100,
            # падение
            "drop": (1 - drop_r[q]) * 100, "drop_minutes": (q - p + 1) * 15,
        })
    _cache.update(id=id(tickers), moves=out)
    return out


def find_rockets(tickers: dict) -> list[dict]:
    rk = [m for m in scan_moves(tickers) if m["rise"] >= C.ROCKET_MIN_PCT]
    return sorted(rk, key=lambda m: -m["rise"])


def find_dumps(tickers: dict) -> list[dict]:
    dp = [m for m in scan_moves(tickers) if m["drop"] >= C.DUMP_MIN_PCT]
    return sorted(dp, key=lambda m: -m["drop"])


def find_movers(tickers: dict) -> list[dict]:
    """Движение от MOVE_BLOCK_PCT% в любую сторону, без тех, кто уже в ракетах или обвалах."""
    out = []
    for m in scan_moves(tickers):
        if m["rise"] >= C.ROCKET_MIN_PCT or m["drop"] >= C.DUMP_MIN_PCT:
            continue
        big = m["rise"] if m["rise"] >= m["drop"] else -m["drop"]
        if abs(big) >= C.MOVE_BLOCK_PCT:
            out.append({**m, "move": big})
    return sorted(out, key=lambda m: -abs(m["move"]))


# --- Оформление ------------------------------------------------------------

def _pct(x: float | None) -> str:
    """🟢 рост / 🔴 падение / ⚪ почти на месте, без знаков + и −."""
    if x is None:
        return "⚪ —"
    dot = "🟢" if x >= 0.05 else "🔴" if x <= -0.05 else "⚪"
    v = abs(x)
    return f"{dot} {v:.0f}%" if v >= 100 else f"{dot} {v:.1f}%"


def block(title: str, items: list[dict], key: str, empty: str) -> list[str]:
    """Блок обычным (не моноширинным) шрифтом: он крупнее и в нём видны цветные шарики.
    Порядок в строке как в таблице: монета · час · сутки."""
    lines = [title]
    if not items:
        return lines + [empty]
    lines.append("<i>монета · час · сутки</i>")
    shown = items[: C.BLOCK_MAX_SHOWN]
    for m in shown:
        lines.append(f"<b>{html.escape(base(m['symbol']))}</b>   {_pct(m['h1'])}   {_pct(m[key])}")
    if len(items) > len(shown):
        lines.append(f"…и ещё {len(items) - len(shown)}")
    return lines


def build(tickers: dict) -> str:
    now = now_kyiv()
    rockets = find_rockets(tickers)
    for r in rockets:
        r["rise_signed"] = r["rise"]
    dumps = find_dumps(tickers)
    for d in dumps:
        d["drop_signed"] = -d["drop"]
    lines = [f"📊 <b>Крипто НЮХ · {now:%H:%M}</b> (Киев)", ""]
    lines += block(f"📈 <b>Движение {C.MOVE_BLOCK_PCT}%+ за сутки</b>", find_movers(tickers), "move",
                   f"Монет с движением {C.MOVE_BLOCK_PCT}+% сегодня нет.")
    lines.append("")
    lines += block(f"🚀 <b>Ракеты {C.ROCKET_MIN_PCT}%+</b>", rockets, "rise_signed",
                   f"Ракет с движением {C.ROCKET_MIN_PCT}+% сегодня нет.")
    lines.append("")
    lines += block(f"💥 <b>Обвалы {C.DUMP_MIN_PCT}%+</b>", dumps, "drop_signed",
                   f"Монет с обвалом {C.DUMP_MIN_PCT}+% сегодня нет.")
    lines += ["", "<i>Данные Binance. Не финансовая рекомендация.</i>"]
    return "\n".join(lines)


def post(st: dict, symbols: list[str], tickers: dict, tg, rockets: list[dict] | None = None) -> bool:
    cid = channel_id()
    if not cid or not due(st):
        return False
    tg.send(build(tickers), chat_id=cid, silent=C.CHANNEL_SILENT)
    st["channel_last"] = time.time()
    return True
