"""Мелкие помощники: форматирование, время, состояние бота."""
from __future__ import annotations

import html
import json
import os
import time

import pandas as pd

import config as C


def now_kyiv() -> pd.Timestamp:
    return pd.Timestamp.now(tz=C.TIMEZONE)


def fmt_price(x: float) -> str:
    if x >= 1000:
        return f"{x:,.2f}".replace(",", " ")
    if x >= 1:
        return f"{x:.4f}".rstrip("0").rstrip(".")
    return f"{x:.8f}".rstrip("0").rstrip(".")


def fmt_pct(x: float) -> str:
    return f"{x:+.2f}%"


def fmt_money(x: float) -> str:
    for unit, div in (("млрд", 1e9), ("млн", 1e6), ("тыс", 1e3)):
        if abs(x) >= div:
            return f"{x / div:.1f} {unit}"
    return f"{x:.0f}"


def tri_pct(x: float | None) -> str:
    """Изменение с треугольником: ▲0.18% / ▼2.59%. Цвет не нужен, направление видно по значку."""
    if x is None:
        return "—"
    mark = "▲" if x > 0 else "▼" if x < 0 else "•"
    return f"{mark}{abs(x):.2f}%"


def arrow(x: float) -> str:
    return "🟢" if x > 0 else "🔴" if x < 0 else "⚪"


def base(sym: str) -> str:
    return sym[: -len(C.QUOTE)] if sym.endswith(C.QUOTE) else sym


def link(sym: str) -> str:
    return f'<a href="https://www.binance.com/ru/trade/{base(sym)}_{C.QUOTE}?type=spot">график</a>'


def esc(s: str) -> str:
    return html.escape(str(s))


# --- Состояние: что бот уже отправил, алерты, дневник ---------------------
DEFAULT_STATE = {
    "tg_offset": 0,
    "alerts": [],            # личные ценовые алерты
    "next_alert_id": 1,
    "cooldowns": {},         # ключ события -> unix-время, до которого молчим
    "digest_date": "",
    "muted": False,          # выключен ли сканер рынка
    "journal": {"open": [], "closed": [], "seen": [], "last_bar": {}},
    "last_error_alert": 0,
}


def load_state() -> dict:
    try:
        with open(C.STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        st = {}
    for k, v in DEFAULT_STATE.items():
        st.setdefault(k, json.loads(json.dumps(v)))
    for k, v in DEFAULT_STATE["journal"].items():
        st["journal"].setdefault(k, json.loads(json.dumps(v)))
    return st


def save_state(st: dict) -> None:
    now = time.time()
    st["cooldowns"] = {k: v for k, v in st["cooldowns"].items() if v > now}
    j = st["journal"]
    j["seen"] = j["seen"][-500:]
    j["closed"] = j["closed"][-1000:]
    os.makedirs(os.path.dirname(C.STATE_FILE), exist_ok=True)
    tmp = C.STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, C.STATE_FILE)


def on_cooldown(st: dict, key: str) -> bool:
    return st["cooldowns"].get(key, 0) > time.time()


def set_cooldown(st: dict, key: str, hours: float) -> None:
    st["cooldowns"][key] = time.time() + hours * 3600
