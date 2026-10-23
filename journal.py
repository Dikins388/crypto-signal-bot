"""Экспериментальные сигналы на 4h и дневник их результатов.

Бэктест смотрит в прошлое. Дневник показывает, как стратегия работает СЕЙЧАС:
каждый сигнал записывается как виртуальная сделка, бот сам следит за стопом и тейком
и сообщает результат. Через пару месяцев /stats покажет честную картину.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

import binance_data as bd
import config as C
import pump_strategy as ps
from strategy import build_features, compute_signals
from util import base, fmt_pct, fmt_price, link

BAR_MS = bd.INTERVAL_MS[C.TF_ENTRY]


def _current_bar_start_ms() -> int:
    now = int(time.time() * 1000)
    return now - now % BAR_MS


def check_new_signals(symbols: list[str], st: dict) -> list[str]:
    """Ищет сигналы на только что закрывшейся свече 4h. Тяжёлую загрузку делаем раз на свечу."""
    j = st["journal"]
    bar = _current_bar_start_ms()
    if j["last_bar"].get("all") == bar:
        return []
    msgs = []
    seen = set(j["seen"])
    open_syms = {(t["symbol"]) for t in j["open"]}
    ok = True
    for sym in symbols:
        try:
            raw = bd.klines(sym, C.TF_ENTRY, limit=600)
            e = bd.drop_unclosed(raw)
            m = bd.drop_unclosed(bd.klines(sym, C.TF_MID, limit=400))
            t = m if C.TF_TREND == C.TF_MID else bd.drop_unclosed(bd.klines(sym, C.TF_TREND, limit=400))
        except bd.BinanceUnavailable:
            raise
        except Exception as ex:
            print(f"[journal] {sym}: {ex}")   # монет много: одна ошибка не повод перепроверять все
            continue
        if len(e) < 100 or len(t) < 60:
            continue
        df = compute_signals(build_features(e, m, t))
        row = df.iloc[-1]
        side = int(row["signal"])
        if side == 0:
            continue
        key = f"{sym}|{side}|{row['open_time'].isoformat()}"
        if key in seen:
            continue
        j["seen"].append(key)
        seen.add(key)
        if sym in open_syms:
            continue                      # по монете уже есть открытая виртуальная сделка
        # Вход по открытию следующей свечи, как в бэктесте
        nxt = raw[raw["open_time"] > row["open_time"]]
        entry = float(nxt["open"].iloc[0]) if len(nxt) else float(row["close"])
        entry_time = nxt["open_time"].iloc[0] if len(nxt) else row["close_time"]
        risk = C.SL_ATR * float(row["atr"])
        trade = {
            "symbol": sym, "side": side, "entry": entry,
            "entry_ms": int(entry_time.value // 1_000_000),
            "sl": entry - side * risk, "tp": entry + side * C.TP_ATR * float(row["atr"]),
            "risk_pct": risk / entry * 100, "score": int(row["score"]),
            "flow_mult": None if pd.isna(row.get("flow_mult")) else round(float(row["flow_mult"]), 1),
            "buy_share": None if pd.isna(row.get("buy_share")) else round(float(row["buy_share"])),
        }
        j["open"].append(trade)
        open_syms.add(sym)
        msgs.append(format_signal(trade, row))
    if ok:
        j["last_bar"]["all"] = bar
    return msgs


def format_signal(tr: dict, row: pd.Series) -> str:
    side = tr["side"]
    head = "🟢 <b>ПОКУПКА</b>" if side == 1 else "🔴 <b>ПРОДАЖА</b>"
    mark = lambda ok: "✅" if ok else "▫️"
    return (
        f"🧪 Сигнал (эксперимент, {C.TF_ENTRY})\n"
        f"{head} {base(tr['symbol'])}/{C.QUOTE}\n"
        f"Вход: <b>{fmt_price(tr['entry'])}</b>\n"
        f"Стоп: {fmt_price(tr['sl'])} ({tr['risk_pct']:.2f}%)\n"
        f"Тейк: {fmt_price(tr['tp'])} ({tr['risk_pct'] * C.TP_ATR / C.SL_ATR:.2f}%)\n"
        f"Подтверждения {tr['score']}/4: {mark(row['f_adx'])} ADX {row['adx']:.0f}  "
        f"{mark(row['f_rsi'])} RSI {row['rsi']:.0f}  {mark(row['f_vol'])} объём  "
        f"{mark(row['f_mid'])} {C.TF_MID}\n"
        f"{link(tr['symbol'])}\n"
        f"<i>Я запишу эту сделку в дневник и сообщу результат. Не финансовая рекомендация.</i>"
    )


def update_open(st: dict) -> list[str]:
    """Проверяет открытые виртуальные сделки по 15-минутным свечам."""
    j = st["journal"]
    msgs, still = [], []
    cost_default = 2 * (C.FEE + C.SLIPPAGE) * 100
    for tr in j["open"]:
        sym, s = tr["symbol"], tr["side"]
        start = max(tr["entry_ms"], tr.get("checked_ms", 0))
        try:
            k = bd.drop_unclosed(bd.klines(sym, "15m", start_ms=start))
        except bd.BinanceUnavailable:
            raise
        except Exception as ex:
            print(f"[journal] {sym}: {ex}")
            still.append(tr)
            continue
        deadline = tr.get("deadline_ms") or tr["entry_ms"] + C.MAX_HOLD_BARS * BAR_MS
        exit_px = reason = None
        exit_ms = None
        for r in k.itertuples():
            om = int(r.open_time.value // 1_000_000)
            if om < tr["entry_ms"]:
                continue
            hit_sl = r.low <= tr["sl"] if s == 1 else r.high >= tr["sl"]
            hit_tp = r.high >= tr["tp"] if s == 1 else r.low <= tr["tp"]
            if hit_sl:
                gap = (s == 1 and r.open <= tr["sl"]) or (s == -1 and r.open >= tr["sl"])
                exit_px, reason = (r.open if gap and om > tr["entry_ms"] else tr["sl"]), "стоп"
            elif hit_tp:
                exit_px, reason = tr["tp"], "тейк"
            elif int(r.close_time.value // 1_000_000) + 1 >= deadline:
                exit_px, reason = r.close, "время вышло"
            if exit_px is not None:
                exit_ms = int(r.close_time.value // 1_000_000)
                break
        if exit_px is None:
            if len(k):
                tr["checked_ms"] = int(k["open_time"].iloc[-1].value // 1_000_000)
            still.append(tr)
            continue
        cost = tr.get("cost_pct", cost_default)
        net = s * (exit_px / tr["entry"] - 1) * 100 - cost
        done = {**tr, "exit": float(exit_px), "exit_ms": exit_ms, "reason": reason,
                "net_pct": net, "r": net / tr["risk_pct"]}
        done.pop("checked_ms", None)
        j["closed"].append(done)
        icon = "✅" if net > 0 else "❌"
        side_w = "покупка" if s == 1 else "продажа"
        msgs.append(f"{icon} <b>Итог сделки {base(sym)}</b> ({side_w}, {reason})\n"
                    f"Вход {fmt_price(tr['entry'])} → выход {fmt_price(exit_px)}\n"
                    f"Результат с комиссией: <b>{fmt_pct(net)}</b> ({done['r']:+.2f}R)")
    j["open"] = still
    return msgs


def stats_text(st: dict, since_ms: int | None = None, title: str = "Дневник сигналов") -> str:
    j = st["journal"]
    closed = [t for t in j["closed"] if since_ms is None or t["exit_ms"] >= since_ms]
    lines = [f"🧪 <b>{title}</b> ({C.TF_ENTRY})"]
    if not closed:
        lines.append("Закрытых сделок пока нет.")
    else:
        net = np.array([t["net_pct"] for t in closed])
        r = np.array([t["r"] for t in closed])
        wins, losses = net[net > 0], net[net <= 0]
        pf = f"{wins.sum() / abs(losses.sum()):.2f}" if losses.sum() != 0 else "убыточных пока нет"
        lines += [
            f"Сделок: {len(closed)}, прибыльных {100 * len(wins) / len(closed):.0f}%",
            f"Сумма: {r.sum():+.1f}R (при риске 1% на сделку ≈ {r.sum():+.1f}% депозита)",
            f"Profit factor: {pf}  (больше 1.2 уже интересно, меньше 1 убыток)",
        ]
        if len(closed) < 30:
            lines.append("<i>Сделок пока мало, выводы делать рано.</i>")
    if j["open"]:
        lines.append("\nОткрыто сейчас: " + ", ".join(
            f"{base(t['symbol'])} {'↑' if t['side'] == 1 else '↓'}" for t in j["open"]))
    return "\n".join(lines)


def check_pump_signals(tickers: dict, st: dict) -> list[str]:
    """Каждый запуск (раз в 10 минут): ищем точки входа по монетам, которые сейчас пампятся."""
    j = st["journal"]
    need = 1 + C.PUMP_MIN_GAIN_PCT / 100
    cands = [s for s, t in tickers.items()
             if t["quote_volume"] >= C.PUMP_MIN_QUOTE_VOLUME and t["low"] > 0 and t["high"] / t["low"] >= need]
    open_syms = {t["symbol"] for t in j["open"]}
    cooldowns = j.setdefault("pump_cooldown", {})
    now = time.time()
    for key in [k for k, v in cooldowns.items() if v < now]:
        cooldowns.pop(key)
    found = 0
    for sym in cands:
        if sym in open_syms:
            continue
        try:
            raw = bd.klines(sym, "15m", limit=ps.need_bars() + 4)
        except bd.BinanceUnavailable:
            raise
        except Exception as ex:
            print(f"[пампы] {sym}: {ex}")
            continue
        k = bd.drop_unclosed(raw)
        if len(k) < ps.need_bars():
            continue
        sigs = ps.analyze(k, t_from=len(k) - 2)        # две последние закрытые свечи (будильник неточен)
        if not sigs:
            continue
        sig = sigs[-1]
        key = f"{sym}|{sig['side']}"
        if key in cooldowns:
            continue
        row = k.iloc[sig["t"]]
        nxt = raw[raw["open_time"] > row["open_time"]]
        entry = float(nxt["open"].iloc[0]) if len(nxt) else float(row["close"])
        entry_time = nxt["open_time"].iloc[0] if len(nxt) else row["close_time"]
        entry_ms = int(entry_time.value // 1_000_000)
        sl, tp = ps.entry_levels(sig, entry)
        j["open"].append({
            "symbol": sym, "side": sig["side"], "entry": entry, "entry_ms": entry_ms,
            "sl": sl, "tp": tp, "risk_pct": abs(entry - sl) / entry * 100, "score": 0,
            "deadline_ms": entry_ms + C.PUMP_MAX_HOLD_H * 3_600_000,
            "cost_pct": 2 * (C.FEE + C.PUMP_SLIPPAGE) * 100, "kind": "pump",
            **{x: sig[x] for x in ("gain", "pump_minutes", "from_peak", "mult", "mult1h",
                                   "buy_share", "buy_share_1h")},
        })
        cooldowns[key] = now + C.PUMP_COOLDOWN_H * 3600
        open_syms.add(sym)
        found += 1
    print(f"[пампы] монет в пампе: {len(cands)}, новых сигналов: {found}")
    return []
