"""Проверка сигналов по пампам на истории.

Запуск:  python pump_backtest.py --days 180 [--telegram]

Как экономим запросы к Binance: по дневным свечам находим дни, когда монета могла пампиться
(размах за двое суток от PUMP_MIN_GAIN_PCT%), и только для этих периодов качаем 15-минутки
(+ 8 дней до, чтобы знать обычный оборот, и 1 день после, чтобы закрыть сделки).

Честные допущения:
  - вход по открытию следующей 15-минутки после сигнала;
  - если стоп и тейк задеты одной свечой, считаем, что сработал стоп;
  - комиссия 0.1% + проскальзывание PUMP_SLIPPAGE за сторону;
  - правила НЕ подгонялись под эту историю.
Ограничение: берутся только монеты, которые торгуются сейчас. Делистнутые (часто как раз после
пампов и обвалов) в выборку не попадают, это делает результат немного лучше реального.
"""
from __future__ import annotations

import argparse
import os
import time

import numpy as np
import pandas as pd

import binance_data as bd
import config as C
import pump_strategy as ps
from backtest import _md_table, _plot, stats

DAY_MS = 86_400_000
BAR_MS = 900_000


def pump_windows(sym: str, days: int) -> list[tuple[int, int]]:
    d = bd.drop_unclosed(bd.klines(sym, "1d", limit=days + 2))
    if len(d) < 3:
        return []
    hi, lo = d["high"].to_numpy(), d["low"].to_numpy()
    t0 = d["open_time"].map(lambda x: int(x.value // 1_000_000)).to_numpy()
    need = 1 + C.PUMP_MIN_GAIN_PCT / 100
    wins = []
    for i in range(1, len(d)):
        h, l = max(hi[i - 1], hi[i]), min(lo[i - 1], lo[i])
        if l > 0 and h / l >= need:
            wins.append((int(t0[i - 1]), int(t0[i]) + DAY_MS))
    merged: list[list[int]] = []
    for a, b in wins:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def simulate(k: pd.DataFrame, sigs: list[dict], sym: str, t_lo: int, t_hi: int) -> list[dict]:
    o, h, l, c = (k[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    times = k["open_time"]
    cost = 2 * (C.FEE + C.PUMP_SLIPPAGE) * 100
    hold = C.PUMP_MAX_HOLD_H * 4
    cooldown = C.PUMP_COOLDOWN_H * 4
    last = {1: -10**9, -1: -10**9}
    busy_until = -1
    trades = []
    for s in sigs:
        t = s["t"]
        if not (t_lo <= t <= t_hi) or t + 1 >= len(k) or t <= busy_until:
            continue
        if t - last[s["side"]] < cooldown:
            continue
        e = t + 1
        entry = o[e]
        sl, tp = ps.entry_levels(s, entry)
        side = s["side"]
        exit_px, reason, j = None, "время", e
        end = min(len(k) - 1, e + hold)
        for j in range(e, end + 1):
            if j > e and ((side == 1 and o[j] <= sl) or (side == -1 and o[j] >= sl)):
                exit_px, reason = o[j], "стоп"
                break
            if (side == 1 and l[j] <= sl) or (side == -1 and h[j] >= sl):
                exit_px, reason = sl, "стоп"
                break
            if (side == 1 and h[j] >= tp) or (side == -1 and l[j] <= tp):
                exit_px, reason = tp, "тейк"
                break
        if exit_px is None:
            exit_px, j = c[end], end
            if end - e < hold:
                continue                       # данных не хватило закрыть сделку, не считаем
        net = side * (exit_px / entry - 1) * 100 - cost
        risk_pct = abs(entry - sl) / entry * 100
        trades.append({"symbol": sym, "side": side, "signal_time": times.iloc[t], "entry": entry,
                       "exit_time": times.iloc[j], "exit": float(exit_px), "reason": reason,
                       "net_pct": net, "r": net / risk_pct, **{k2: s[k2] for k2 in
                       ("gain", "mult", "mult1h", "buy_share", "buy_share_1h", "from_peak")}})
        last[side] = t
        busy_until = j
    return trades


def run(symbols: list[str], days: int, out_dir: str = "reports_pump") -> tuple[pd.DataFrame, str]:
    os.makedirs(out_dir, exist_ok=True)
    now = int(time.time() * 1000)
    start_ms = now - days * DAY_MS
    all_tr, n_windows = [], 0
    for i, sym in enumerate(symbols, 1):
        try:
            wins = [w for w in pump_windows(sym, days) if w[1] >= start_ms]
        except bd.BinanceUnavailable:
            raise
        except Exception as ex:
            print(f"[{sym}] пропуск: {ex}")
            continue
        for a, b in wins:
            n_windows += 1
            try:
                k = bd.drop_unclosed(bd.klines(sym, "15m", a - 8 * DAY_MS, min(b + DAY_MS, now)))
            except bd.BinanceUnavailable:
                raise
            except Exception as ex:
                print(f"[{sym}] 15m: {ex}")
                continue
            if len(k) < ps.need_bars():
                continue
            ot = k["open_time"].map(lambda x: int(x.value // 1_000_000)).to_numpy()
            t_lo = int(np.searchsorted(ot, max(a, start_ms)))
            t_hi = int(np.searchsorted(ot, b)) - 1
            sigs = ps.analyze(k, t_from=max(t_lo, ps.need_bars() - 2))
            all_tr += simulate(k, sigs, sym, t_lo, t_hi)
        if i % 25 == 0:
            print(f"проверено монет: {i}/{len(symbols)}, периодов пампа: {n_windows}, сделок: {len(all_tr)}",
                  flush=True)

    tr = pd.DataFrame(all_tr)
    tr.to_csv(os.path.join(out_dir, "pump_trades.csv"), index=False)
    head = [f"# Сигналы по пампам: проверка за {days} дней", "",
            f"Монет: {len(symbols)}, периодов пампа (размах {C.PUMP_MIN_GAIN_PCT}%+): {n_windows}. "
            f"Вход по 15m, стоп {C.STOP_ATR} ATR (мин. {C.MIN_STOP_PCT}%), тейк {C.TAKE_R}R, "
            f"максимум {C.PUMP_MAX_HOLD_H}ч, комиссия+проскальзывание "
            f"{(C.FEE + C.PUMP_SLIPPAGE) * 100:.1f}% за сторону.", ""]
    if tr.empty:
        report = "\n".join(head + ["Сигналов за период не было."])
    else:
        tr["signal_time"] = pd.to_datetime(tr["signal_time"], utc=True)
        mid = tr.signal_time.min() + (tr.signal_time.max() - tr.signal_time.min()) / 2
        rows = []
        for name, part in (("Лонг", tr[tr.side == 1]), ("Шорт", tr[tr.side == -1])):
            rows.append({"Тип": name, **stats(part)})
            rows.append({"Тип": f"{name}, 1-я половина", **stats(part[part.signal_time < mid])})
            rows.append({"Тип": f"{name}, 2-я половина", **stats(part[part.signal_time >= mid])})
        exits = tr.groupby(["side", "reason"]).size().rename("Сделок").reset_index()
        exits["side"] = exits["side"].map({1: "Лонг", -1: "Шорт"})
        report = "\n".join(head + ["## Итого", "", _md_table(pd.DataFrame([{"Тип": "Все", **stats(tr)}])), "",
                                   "## Лонг и шорт отдельно (и стабильность по половинам периода)", "",
                                   _md_table(pd.DataFrame(rows)), "", "## Как выходили", "",
                                   _md_table(exits.rename(columns={"side": "Тип", "reason": "Выход"})), ""])
        _plot(tr.rename(columns={}), os.path.join(out_dir, "pump_equity.png"))
    with open(os.path.join(out_dir, "pump_report.md"), "w", encoding="utf-8") as f:
        f.write(report)
    return tr, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=C.PUMP_BACKTEST_DAYS)
    ap.add_argument("--telegram", action="store_true")
    a = ap.parse_args()
    tk = bd.tickers_24h()
    symbols = [s for s, t in sorted(tk.items(), key=lambda kv: -kv[1]["quote_volume"])
               if t["quote_volume"] >= C.PUMP_MIN_QUOTE_VOLUME]
    print(f"Монет для проверки: {len(symbols)}")
    tr, report = run(symbols, a.days)
    print("\n" + report)
    if a.telegram:
        import telegram_api as tg
        if tr.empty:
            tg.send("📊 Проверка сигналов по пампам: сигналов за период не было.")
            return
        def line(part, name):
            s = stats(part)
            if not s.get("Сделок"):
                return f"{name}: сделок не было"
            return (f"{name}: сделок {s['Сделок']}, прибыльных {s['Прибыльных %']}%, "
                    f"Profit factor {s['Profit factor']}, итог при риске 1% {s['Итог при риске 1% %']}%")
        tg.send(f"📊 <b>Сигналы по пампам: проверка за {a.days} дн.</b>\n"
                + line(tr[tr.side == 1], "🟢 Лонг") + "\n" + line(tr[tr.side == -1], "🔴 Шорт")
                + "\n\nПодробности в файле. Прошлые результаты не гарантируют будущих.")
        tg.send_document("reports_pump/pump_report.md", "Полный отчёт по пампам")


if __name__ == "__main__":
    main()
