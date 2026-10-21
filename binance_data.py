"""Получение свечей и списка монет с Binance через публичный API (ключ не нужен)."""
from __future__ import annotations

import time

import pandas as pd
import requests

import config

_session = requests.Session()
_session.headers["User-Agent"] = "crypto-signal-bot/1.0"
_working_base: str | None = None

INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "1d": 86_400_000,
}


class BinanceUnavailable(RuntimeError):
    pass


def _get(path: str, params: dict | None = None):
    """GET с перебором адресов Binance и повторами при временных сбоях."""
    global _working_base
    bases = [_working_base] if _working_base else []
    bases += [b for b in config.BINANCE_BASE_URLS if b != _working_base]
    errors = []
    for base in bases:
        for attempt in range(3):
            try:
                r = _session.get(base + path, params=params, timeout=20)
            except requests.RequestException as e:
                errors.append(f"{base}: {e.__class__.__name__}")
                time.sleep(1 + attempt)
                continue
            if r.status_code == 200:
                _working_base = base
                return r.json()
            if r.status_code in (418, 429):        # лимит запросов, ждём
                time.sleep(int(r.headers.get("Retry-After", 5)))
                continue
            errors.append(f"{base}: HTTP {r.status_code} {r.text[:120]}")
            break                                  # 451 и прочее: пробуем другой адрес
    raise BinanceUnavailable("Binance недоступен: " + " | ".join(errors[-5:]))


def top_symbols(n: int = config.TOP_N) -> list[str]:
    if config.SYMBOLS:
        return list(config.SYMBOLS)
    tickers = _get("/api/v3/ticker/24hr")
    info = _get("/api/v3/exchangeInfo", {"permissions": "SPOT"})
    trading = {
        s["symbol"] for s in info["symbols"]
        if s["status"] == "TRADING" and s["quoteAsset"] == config.QUOTE
    }
    rows = []
    for t in tickers:
        sym = t["symbol"]
        if sym not in trading:
            continue
        base = sym[: -len(config.QUOTE)]
        if base in config.EXCLUDE_BASES or base.endswith(("UP", "DOWN", "BULL", "BEAR")):
            continue
        rows.append((float(t["quoteVolume"]), sym))
    rows.sort(reverse=True)
    return [s for _, s in rows[:n]]


def klines(symbol: str, interval: str, start_ms: int | None = None,
           end_ms: int | None = None, limit: int | None = None) -> pd.DataFrame:
    """Свечи. limit=N без start_ms -> последние N свечей. С start_ms -> всё с этой даты (постранично)."""
    step = INTERVAL_MS[interval]
    out: list[list] = []
    if start_ms is None:
        need = limit or 500
        end = end_ms
        while need > 0:
            params = {"symbol": symbol, "interval": interval, "limit": min(1000, need)}
            if end:
                params["endTime"] = end
            batch = _get("/api/v3/klines", params)
            if not batch:
                break
            out = batch + out
            need -= len(batch)
            end = batch[0][0] - 1
            if len(batch) < params["limit"]:
                break
    else:
        cur = start_ms
        end_ms = end_ms or int(time.time() * 1000)
        while cur < end_ms:
            batch = _get("/api/v3/klines", {
                "symbol": symbol, "interval": interval,
                "startTime": cur, "endTime": end_ms, "limit": 1000,
            })
            if not batch:
                break
            out.extend(batch)
            cur = batch[-1][0] + step
            if len(batch) < 1000:
                break
    return to_frame(out)


def to_frame(raw: list[list]) -> pd.DataFrame:
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "trades", "tb_base", "tb_quote", "ignore"]
    df = pd.DataFrame(raw, columns=cols)
    if df.empty:
        return df
    for c in ("open", "high", "low", "close", "volume", "quote_volume"):
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df[["open_time", "open", "high", "low", "close", "volume", "close_time"]]


def drop_unclosed(df: pd.DataFrame) -> pd.DataFrame:
    """Убираем последнюю, ещё не закрытую свечу: по ней сигналы рисуются и исчезают."""
    now = pd.Timestamp.now(tz="UTC")
    return df[df["close_time"] < now].reset_index(drop=True)
