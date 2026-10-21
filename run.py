"""Главный запуск. GitHub Actions вызывает его каждые 15 минут.

Порядок: команды от тебя -> личные алерты -> сканер рынка -> сигналы и дневник -> утренняя сводка.

Запуск руками:  python run.py          (с отправкой в Telegram)
                python run.py --dry    (только печать, без Telegram и без чтения команд)
"""
from __future__ import annotations

import argparse
import time
import traceback

import binance_data as bd
import config as C
import commands
import digest
import journal
import monitor
from util import esc, load_state, now_kyiv, save_state

ERROR_ALERT_EVERY_H = 6


class DryTelegram:
    def send(self, text):
        print("---- TELEGRAM ----\n" + text + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    if a.dry:
        tg = DryTelegram()
    else:
        import telegram_api as tg

    st = load_state()
    try:
        _run(st, tg, dry=a.dry)
    except bd.BinanceUnavailable as ex:
        print(ex)
        if time.time() - st.get("last_error_alert", 0) > ERROR_ALERT_EVERY_H * 3600:
            tg.send(f"⚠️ Не могу получить данные Binance.\n<code>{esc(str(ex)[:400])}</code>")
            st["last_error_alert"] = time.time()
        save_state(st)
        raise SystemExit(1)
    save_state(st)


def _step(name, fn):
    """Каждый блок отдельно: если один сломался, остальные всё равно работают."""
    try:
        return fn()
    except bd.BinanceUnavailable:
        raise
    except Exception:
        print(f"[{name}] ошибка:")
        traceback.print_exc()
        return []


def _run(st: dict, tg, dry: bool):
    tickers = bd.tickers_24h()
    top = bd.top_symbols(C.TOP_N, tickers)
    watch = bd.top_symbols(C.MONITOR_TOP_N, tickers)
    print("Топ:", ", ".join(top))

    digest_now = lambda: digest.build(top, tickers, journal.stats_text(st) if C.SIGNALS_ENABLED else None)
    extra = {"digest": digest_now, "stats": lambda: journal.stats_text(st)}

    if not dry:
        n = _step("команды", lambda: commands.process_commands(st, tickers, extra, tg))
        print("Ответов на команды:", n)

    out: list[str] = []
    out += _step("алерты", lambda: commands.check_alerts(st, tickers))
    if C.SCAN_ENABLED and not st["muted"]:
        out += _step("сканер", lambda: monitor.scan(watch, tickers, st))
    if C.SIGNALS_ENABLED:
        out += _step("дневник", lambda: journal.update_open(st))
        out += _step("сигналы", lambda: journal.check_new_signals(top, st))

    now = now_kyiv()
    today = now.strftime("%Y-%m-%d")
    if now.hour >= C.DIGEST_HOUR and st["digest_date"] != today:
        text = _step("сводка", digest_now)
        if text:
            if now.weekday() == 0 and C.SIGNALS_ENABLED:      # по понедельникам итоги недели
                week_ms = int((time.time() - 7 * 86400) * 1000)
                text += "\n\n" + journal.stats_text(st, week_ms, "Итоги недели по сигналам")
            out.append(text)
        st["digest_date"] = today

    for msg in out:
        tg.send(msg)
    print(f"Отправлено сообщений: {len(out)}")


if __name__ == "__main__":
    main()
