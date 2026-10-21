"""Один проход проверки сигналов. GitHub Actions запускает его каждые 15 минут.

Запуск руками:  python live.py           (отправит найденные сигналы в Telegram)
                python live.py --dry     (только напечатает, ничего не отправит)
"""
from __future__ import annotations

import argparse
import json
import os
import time
import traceback

import pandas as pd

import binance_data as bd
import config as C
from strategy import build_features, compute_signals

LOOKBACK_BARS = 3          # GitHub иногда запускает с опозданием, смотрим 3 последние свечи
ERROR_ALERT_EVERY_H = 6


def load_state() -> dict:
    try:
        with open(C.STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"sent": [], "last_error_alert": 0}


def save_state(st: dict) -> None:
    os.makedirs(os.path.dirname(C.STATE_FILE), exist_ok=True)
    st["sent"] = st["sent"][-1000:]
    with open(C.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def fmt_price(x: float) -> str:
    if x >= 1000:
        return f"{x:,.2f}".replace(",", " ")
    if x >= 1:
        return f"{x:.4f}"
    return f"{x:.8f}".rstrip("0")


def format_signal(sym: str, row: pd.Series) -> str:
    side = int(row["signal"])
    base = sym[: -len(C.QUOTE)]
    head = "🟢 <b>ПОКУПКА</b>" if side == 1 else "🔴 <b>ПРОДАЖА</b>"
    price = row["close"]
    sl_pct = abs(row["sl"] / price - 1) * 100
    tp_pct = abs(row["tp"] / price - 1) * 100
    mark = lambda ok: "✅" if ok else "▫️"
    t = (row["close_time"] + pd.Timedelta("1ms")).tz_convert("Europe/Kyiv").strftime("%d.%m %H:%M")
    return (
        f"{head} {base}/{C.QUOTE}\n"
        f"Цена: <b>{fmt_price(price)}</b>  (свеча {C.TF_ENTRY}, закрытие {t} Киев)\n"
        f"Стоп: {fmt_price(row['sl'])} ({sl_pct:.2f}%)\n"
        f"Тейк: {fmt_price(row['tp'])} ({tp_pct:.2f}%)\n"
        f"Подтверждения {int(row['score'])}/4: "
        f"{mark(row['f_adx'])} тренд ADX {row['adx']:.0f}  "
        f"{mark(row['f_rsi'])} RSI {row['rsi']:.0f}  "
        f"{mark(row['f_vol'])} объём  {mark(row['f_mid'])} 1h\n"
        f"https://www.binance.com/ru/trade/{base}_{C.QUOTE}?type=spot\n"
        f"<i>Не финансовая рекомендация.</i>"
    )


def check_symbol(sym: str) -> pd.DataFrame:
    e = bd.drop_unclosed(bd.klines(sym, C.TF_ENTRY, limit=600))
    m = bd.drop_unclosed(bd.klines(sym, C.TF_MID, limit=400))
    t = bd.drop_unclosed(bd.klines(sym, C.TF_TREND, limit=1000))
    df = compute_signals(build_features(e, m, t))
    return df.tail(LOOKBACK_BARS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    send = None
    if not a.dry:
        import telegram_api as tg
        send = tg.send

    st = load_state()
    sent = set(st["sent"])
    try:
        symbols = bd.top_symbols()
    except bd.BinanceUnavailable as ex:
        print(ex)
        if send and time.time() - st.get("last_error_alert", 0) > ERROR_ALERT_EVERY_H * 3600:
            send(f"⚠️ Бот не может получить данные Binance.\n<code>{str(ex)[:500]}</code>")
            st["last_error_alert"] = time.time()
        save_state(st)
        raise SystemExit(1)

    print("Монеты:", ", ".join(symbols))
    found = 0
    for sym in symbols:
        try:
            tail = check_symbol(sym)
        except Exception:
            traceback.print_exc()
            continue
        for _, row in tail[tail.signal != 0].iterrows():
            key = f"{sym}|{int(row.signal)}|{row.open_time.isoformat()}"
            if key in sent:
                continue
            msg = format_signal(sym, row)
            print(msg.replace("<b>", "").replace("</b>", ""), "\n")
            if send:
                send(msg)
            st["sent"].append(key)
            sent.add(key)
            found += 1
    print(f"Новых сигналов: {found}")
    save_state(st)


if __name__ == "__main__":
    main()
