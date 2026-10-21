"""Проверка стратегии на истории за последний год.

Запуск:  python backtest.py            (отчёт в папку reports/)
         python backtest.py --telegram (плюс краткая сводка и файлы в Telegram)

Что делает честно:
  - вход по ОТКРЫТИЮ следующей свечи после сигнала (а не по цене сигнала);
  - учитывает комиссию Binance и проскальзывание в обе стороны;
  - если стоп и тейк задеты одной свечой, считает, что сработал стоп (худший случай);
  - старшие таймфреймы видны только после закрытия их свечи;
  - параметры НЕ подгонялись под историю, чтобы результат не был «нарисованным».
"""
from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

import binance_data as bd
import config as C
from strategy import build_features, compute_signals

DAY_MS = 86_400_000


@dataclass
class Trade:
    symbol: str
    side: int
    signal_time: pd.Timestamp
    entry_time: pd.Timestamp
    entry: float
    exit_time: pd.Timestamp
    exit: float
    reason: str
    net_pct: float
    r: float
    score: int


def simulate(df: pd.DataFrame, symbol: str, start: pd.Timestamp) -> list[Trade]:
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    sig, atr_, score = df["signal"].to_numpy(), df["atr"].to_numpy(), df["score"].to_numpy()
    times = df["open_time"]
    cost = 2 * (C.FEE + C.SLIPPAGE)
    trades: list[Trade] = []
    i, n = 0, len(df)
    while i < n - 1:
        s = sig[i]
        if s == 0 or times.iloc[i] < start or (s == -1 and not C.ALLOW_SHORT) or np.isnan(atr_[i]):
            i += 1
            continue
        e = i + 1
        entry = o[e]
        risk = C.SL_ATR * atr_[i]
        sl, tp = entry - s * risk, entry + s * C.TP_ATR * atr_[i]
        exit_px, reason, j = None, "time", e
        last = min(n - 1, e + C.MAX_HOLD_BARS)
        for j in range(e, last + 1):
            if j > e and ((s == 1 and o[j] <= sl) or (s == -1 and o[j] >= sl)):
                exit_px, reason = o[j], "stop"      # гэп через стоп
                break
            hit_sl = l[j] <= sl if s == 1 else h[j] >= sl
            hit_tp = h[j] >= tp if s == 1 else l[j] <= tp
            if hit_sl:
                exit_px, reason = sl, "stop"
                break
            if hit_tp:
                exit_px, reason = tp, "take"
                break
        if exit_px is None:
            exit_px, j = c[last], last
            if last == n - 1 and last - e < C.MAX_HOLD_BARS:
                reason = "open"                      # сделка ещё не закрылась к концу данных
        gross = s * (exit_px - entry) / entry
        net = gross - cost
        trades.append(Trade(symbol, int(s), times.iloc[i], times.iloc[e], entry,
                            times.iloc[j], float(exit_px), reason, net * 100,
                            net / (risk / entry), int(score[i])))
        i = j + 1                                    # одна позиция на монету одновременно
    return trades


def stats(tr: pd.DataFrame) -> dict:
    if tr.empty:
        return {"Сделок": 0}
    wins = tr[tr.net_pct > 0]
    loss = tr[tr.net_pct <= 0]
    pf = wins.net_pct.sum() / abs(loss.net_pct.sum()) if len(loss) and loss.net_pct.sum() != 0 else np.inf
    eq = equity_curve(tr)
    dd = (eq / eq.cummax() - 1).min() * 100
    return {
        "Сделок": len(tr),
        "Прибыльных %": round(100 * len(wins) / len(tr), 1),
        "Средняя сделка %": round(tr.net_pct.mean(), 3),
        "Средний R": round(tr.r.mean(), 3),
        "Profit factor": round(pf, 2),
        "Итог при риске 1% %": round((eq.iloc[-1] - 1) * 100, 1),
        "Макс. просадка %": round(dd, 1),
    }


def equity_curve(tr: pd.DataFrame, risk: float = 0.01) -> pd.Series:
    """Депозит, если в каждой сделке рисковать 1% (стоп = минус 1% депозита)."""
    tr = tr.sort_values("exit_time")
    eq = (1 + risk * tr.r.clip(lower=-3)).cumprod()
    return pd.concat([pd.Series([1.0]), eq.reset_index(drop=True)])


def load(symbol: str, days: int):
    now = int(time.time() * 1000)
    start = now - days * DAY_MS
    e = bd.drop_unclosed(bd.klines(symbol, C.TF_ENTRY, start - 7 * DAY_MS, now))
    m = bd.drop_unclosed(bd.klines(symbol, C.TF_MID, start - 30 * DAY_MS, now))
    t = bd.drop_unclosed(bd.klines(symbol, C.TF_TREND, start - 150 * DAY_MS, now))
    return e, m, t, pd.Timestamp(start, unit="ms", tz="UTC")


def run(symbols: list[str], days: int, out_dir: str = "reports",
        data_loader=None) -> tuple[pd.DataFrame, str]:
    data_loader = data_loader or load
    os.makedirs(out_dir, exist_ok=True)
    all_trades, rows, bh = [], [], {}
    for sym in symbols:
        print(f"[{sym}] загружаю историю...", flush=True)
        try:
            e, m, t, start = data_loader(sym, days)
        except bd.BinanceUnavailable:
            raise
        except Exception as ex:                      # одна монета не должна валить весь отчёт
            print(f"  пропуск: {ex}")
            continue
        if len(e) < 500 or len(t) < 250:
            print("  слишком мало истории, пропуск")
            continue
        df = compute_signals(build_features(e, m, t))
        trades = simulate(df, sym, start)
        all_trades += trades
        in_period = df[df.open_time >= start]
        bh[sym] = (in_period.close.iloc[-1] / in_period.open.iloc[0] - 1) * 100
        print(f"  сделок: {len(trades)}")

    tr = pd.DataFrame([t.__dict__ for t in all_trades])
    if not tr.empty:
        tr = tr[tr.reason != "open"]
    tr.to_csv(os.path.join(out_dir, "trades.csv"), index=False)

    for sym in symbols:
        if sym not in bh:
            continue
        st = stats(tr[tr.symbol == sym] if not tr.empty else tr)
        st = {"Монета": sym, **st, "Купи и держи %": round(bh[sym], 1)}
        rows.append(st)
    per_coin = pd.DataFrame(rows)

    lines = [f"# Бэктест за {days} дней", "",
             f"Таймфреймы: вход {C.TF_ENTRY}, фильтры {C.TF_MID} и {C.TF_TREND}. "
             f"Стоп {C.SL_ATR} ATR, тейк {C.TP_ATR} ATR, комиссия+проскальзывание "
             f"{(C.FEE + C.SLIPPAGE) * 100:.2f}% за сторону.", ""]
    if tr.empty:
        lines.append("Сигналов за период не было.")
    else:
        total = stats(tr)
        lines += ["## Итого по всем монетам", "", _md_table(pd.DataFrame([total])), ""]
        lines += ["## Покупки и продажи отдельно", "",
                  _md_table(pd.DataFrame([{"Тип": "Покупка", **stats(tr[tr.side == 1])},
                                          {"Тип": "Продажа (шорт)", **stats(tr[tr.side == -1])}])), ""]
        mid = tr.signal_time.min() + (tr.signal_time.max() - tr.signal_time.min()) / 2
        lines += ["## Стабильность: первая и вторая половина периода", "",
                  "Если стратегия работает только в одной половине, скорее всего, это везение рынка.", "",
                  _md_table(pd.DataFrame([{"Период": "1-я половина", **stats(tr[tr.signal_time < mid])},
                                          {"Период": "2-я половина", **stats(tr[tr.signal_time >= mid])}])), ""]
        lines += ["## По монетам", "", _md_table(per_coin), ""]
        lines += ["## Как выходили из сделок", "",
                  _md_table(tr.reason.map({"stop": "стоп", "take": "тейк", "time": "по времени"})
                            .value_counts().rename_axis("Выход").reset_index(name="Сделок")), ""]
        _plot(tr, os.path.join(out_dir, "equity.png"))
    report = "\n".join(lines)
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(report)
    return tr, report


def _md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in df.to_dict("records"):             # без iterrows, чтобы int не превращался в float
        out.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    return "\n".join(out)


def _plot(tr: pd.DataFrame, path: str) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    t = tr.sort_values("exit_time")
    eq = equity_curve(t)
    x = [t.exit_time.iloc[0]] + list(t.exit_time)
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(x, (eq.values - 1) * 100, lw=1.6)
    ax.axhline(0, color="gray", lw=0.8)
    ax.set_title("Депозит при риске 1% на сделку, %")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=C.BACKTEST_DAYS)
    p.add_argument("--telegram", action="store_true", help="отправить сводку в Telegram")
    a = p.parse_args()

    symbols = bd.top_symbols()
    print("Монеты:", ", ".join(symbols))
    tr, report = run(symbols, a.days)
    print("\n" + report)

    if a.telegram:
        import telegram_api as tg
        if tr.empty:
            tg.send("📊 Бэктест: сигналов за период не было.")
            return
        s = stats(tr)
        msg = (f"📊 <b>Бэктест за {a.days} дн.</b> ({len(symbols)} монет)\n"
               f"Сделок: {s['Сделок']}, прибыльных {s['Прибыльных %']}%\n"
               f"Profit factor: {s['Profit factor']}\n"
               f"Итог при риске 1%/сделку: {s['Итог при риске 1% %']}%\n"
               f"Макс. просадка: {s['Макс. просадка %']}%\n\n"
               f"Подробности в файле ниже. Прошлые результаты не гарантируют будущих.")
        tg.send(msg)
        tg.send_document("reports/report.md", "Полный отчёт")
        if os.path.exists("reports/equity.png"):
            tg.send_document("reports/equity.png", "Кривая депозита")


if __name__ == "__main__":
    main()
