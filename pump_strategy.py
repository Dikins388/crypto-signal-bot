"""Точки входа по монетам, которые пампятся. Один и тот же код для живого бота и для бэктеста.

На каждой закрытой 15-минутной свече t:
  1. Начало пампа = самая низкая точка за последние сутки (96 свечей) до t.
     Пампом считается, если от неё до вершины монета выросла на PUMP_MIN_GAIN_PCT%+.
  2. Деньги с начала пампа: оборот в USDT от начала до t против обычного оборота монеты
     (медиана 15-минутной свечи за 7 дней ДО начала пампа) за такой же срок.
  3. Кто давит: доля агрессивных покупок с начала пампа и отдельно за последний час.

ЛОНГ (памп продолжается):
  рост от начала в пределах [PUMP_MIN_GAIN_PCT, LONG_MAX_GAIN_PCT], цена у вершины,
  свежий максимум за последний час, деньги идут сильно (весь памп и последний час ≥ ×LONG_MIN_MULT),
  покупатели давят (с начала и за последний час ≥ LONG_MIN_BUY_PCT) и не выдыхаются.
ШОРТ (памп выдохся, разворот):
  был памп, цена уже ушла от вершины на SHORT_MIN_DROP_PCT%+, вершина 1–8 часов назад,
  за последний час давят продавцы (покупок ≤ SHORT_MAX_BUY_PCT и заметно меньше, чем в пампе),
  денег заходит меньше, чем во время роста.
Стоп = STOP_ATR × ATR(14) по 15-минуткам (но не меньше MIN_STOP_PCT), тейк = стоп × TAKE_R.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C
from indicators import atr as atr_ind

W = 96                     # окно поиска начала пампа: сутки 15-минуток
BASE = 672                 # обычный оборот: 7 дней 15-минуток до начала пампа
HOUR = 4


def need_bars() -> int:
    return BASE + W + 2


def analyze(k: pd.DataFrame, t_from: int | None = None) -> list[dict]:
    """Ищет сигналы на свечах t_from..конец. Возвращает список словарей сигналов."""
    n = len(k)
    if n < need_bars() or "quote_volume" not in k or "tb_quote" not in k:
        return []
    lo, hi = k["low"].to_numpy(float), k["high"].to_numpy(float)
    cl = k["close"].to_numpy(float)
    qv, tb = k["quote_volume"].to_numpy(float), k["tb_quote"].to_numpy(float)
    cq, ct = np.r_[0.0, np.cumsum(qv)], np.r_[0.0, np.cumsum(tb)]
    atr = atr_ind(k, 14).to_numpy(float)
    first = max(BASE + W, t_from if t_from is not None else BASE + W)
    out = []
    for t in range(first, n):
        s0 = t - W + 1
        bottom = s0 + int(np.argmin(lo[s0: t + 1]))
        if lo[bottom] <= 0 or bottom < BASE:
            continue
        pidx = bottom + int(np.argmax(hi[bottom: t + 1]))
        peak = hi[pidx]
        # Начало пампа: последний момент перед вершиной, когда цена ещё была у дна (в пределах 3%).
        # Иначе «начало» уезжает в часы бокового дрейфа и срок пампа и деньги считаются неверно.
        near = np.flatnonzero(lo[bottom: pidx + 1] <= lo[bottom] * 1.03)
        start = bottom + int(near[-1])
        low = lo[start]
        gain_peak = (peak / low - 1) * 100
        if gain_peak < C.PUMP_MIN_GAIN_PCT:
            continue
        usual = float(np.median(qv[start - BASE: start]))
        if usual <= 0:
            continue
        bars = t - start + 1
        money = cq[t + 1] - cq[start]
        if money <= 0:
            continue
        mult = money / (usual * bars)
        share = (ct[t + 1] - ct[start]) / money * 100
        q1 = cq[t + 1] - cq[t - HOUR + 1]
        if q1 <= 0:
            continue
        share1h = (ct[t + 1] - ct[t - HOUR + 1]) / q1 * 100
        mult1h = q1 / (usual * HOUR)
        gain_now = (cl[t] / low - 1) * 100
        from_peak = (cl[t] / peak - 1) * 100
        since_peak = t - pidx
        pump_rate = (cq[pidx + 1] - cq[start]) / (pidx - start + 1)   # средний оборот свечи во время роста

        side = 0
        if (C.PUMP_MIN_GAIN_PCT <= gain_now <= C.LONG_MAX_GAIN_PCT
                and from_peak >= -C.LONG_MAX_FROM_PEAK_PCT and since_peak < HOUR
                and mult >= C.LONG_MIN_MULT and mult1h >= C.LONG_MIN_MULT
                and share >= C.LONG_MIN_BUY_PCT and share1h >= C.LONG_MIN_BUY_PCT
                and share1h >= share - C.LONG_MAX_FADE_PP):
            side = 1
        elif (from_peak <= -C.SHORT_MIN_DROP_PCT and HOUR <= since_peak <= 8 * HOUR
              and share1h <= C.SHORT_MAX_BUY_PCT and share1h <= share - C.SHORT_MIN_FADE_PP
              and q1 / HOUR < pump_rate):
            side = -1
        if side == 0 or np.isnan(atr[t]):
            continue
        risk = max(C.STOP_ATR * atr[t], C.MIN_STOP_PCT / 100 * cl[t])
        out.append({
            "t": t, "side": side, "close": float(cl[t]), "risk": float(risk),
            "gain": round(gain_now if side == 1 else gain_peak, 1),
            "pump_minutes": int((pidx - start + 1) * 15),
            "from_peak": round(from_peak, 1),
            "mult": round(mult, 1), "mult1h": round(mult1h, 1),
            "buy_share": round(share), "buy_share_1h": round(share1h),
        })
    return out


def entry_levels(sig: dict, entry: float) -> tuple[float, float]:
    s, r = sig["side"], sig["risk"]
    return entry - s * r, entry + s * C.TAKE_R * r
