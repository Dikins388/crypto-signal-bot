"""Логика сигнала. Один и тот же код используется и в бэктесте, и в живом боте,
поэтому то, что ты видишь в отчёте за год, ровно то, что бот присылает в Telegram.

Идея (мульти-таймфрейм + подтверждения):
  1. Фильтр тренда 4h (обязателен): покупаем только в бычьем рынке, продаём только в медвежьем.
  2. Триггер 15m (обязателен): гистограмма MACD пересекла ноль в сторону тренда,
     и цена по ту же сторону от EMA55.
  3. Подтверждения (нужно минимум MIN_SCORE из 4):
       - ADX >= ADX_MIN (есть направленное движение, а не пила);
       - RSI в «здоровой» зоне (импульс есть, но не перегрето);
       - объём свечи выше среднего;
       - тренд 1h совпадает.
  4. Стоп и тейк считаются от ATR, то есть подстраиваются под волатильность монеты.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C
from indicators import adx, atr, ema, macd, rsi


def _htf(df: pd.DataFrame, prefix: str, ema_lens: list[int]) -> pd.DataFrame:
    out = df[["close_time", "close"]].copy()
    for n in ema_lens:
        out[f"ema{n}"] = ema(df["close"], n)
    out = out.rename(columns={c: f"{prefix}_{c}" for c in out.columns if c != "close_time"})
    return out


def build_features(e: pd.DataFrame, mid: pd.DataFrame, trend: pd.DataFrame) -> pd.DataFrame:
    """e = 15m свечи, mid = 1h, trend = 4h. Все только закрытые."""
    df = e.copy()
    df["ema_fast"] = ema(df["close"], C.EMA_FAST)
    df["ema_slow"] = ema(df["close"], C.EMA_SLOW)
    df["rsi"] = rsi(df["close"], C.RSI_LEN)
    _, _, df["macd_hist"] = macd(df["close"], C.MACD_FAST, C.MACD_SLOW, C.MACD_SIGNAL)
    df["atr"] = atr(df, C.ATR_LEN)
    df["adx"] = adx(df, C.ADX_LEN)
    df["vol_avg"] = df["volume"].rolling(C.VOL_LEN).mean()

    # Старшие таймфреймы подклеиваем так, чтобы на свече 15m было видно только
    # уже ЗАКРЫТУЮ свечу 1h/4h. Иначе бэктест «подглядывал» бы в будущее.
    m = _htf(mid, "m", [C.MID_EMA]).sort_values("close_time")
    t = _htf(trend, "t", [C.TREND_EMA_FAST, C.TREND_EMA_SLOW]).sort_values("close_time")
    df = df.sort_values("close_time")
    df = pd.merge_asof(df, m, on="close_time", direction="backward")
    df = pd.merge_asof(df, t, on="close_time", direction="backward")
    return df.reset_index(drop=True)


def compute_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Добавляет колонки signal (1 покупка, -1 продажа, 0 нет), score, sl, tp."""
    tf, ts = f"t_ema{C.TREND_EMA_FAST}", f"t_ema{C.TREND_EMA_SLOW}"
    trend_up = (df["t_close"] > df[ts]) & (df[tf] > df[ts])
    trend_dn = (df["t_close"] < df[ts]) & (df[tf] < df[ts])

    h, hp = df["macd_hist"], df["macd_hist"].shift()
    trig_long = (hp <= 0) & (h > 0) & (df["close"] > df["ema_slow"])
    trig_short = (hp >= 0) & (h < 0) & (df["close"] < df["ema_slow"])

    strong = df["adx"] >= C.ADX_MIN
    vol_ok = df["volume"] >= C.VOLUME_MULT * df["vol_avg"]
    rsi_l = df["rsi"].between(*C.RSI_LONG_RANGE)
    rsi_s = df["rsi"].between(*C.RSI_SHORT_RANGE)
    mid_up = df["m_close"] > df[f"m_ema{C.MID_EMA}"]
    mid_dn = df["m_close"] < df[f"m_ema{C.MID_EMA}"]

    score_l = strong.astype(int) + vol_ok.astype(int) + rsi_l.astype(int) + mid_up.astype(int)
    score_s = strong.astype(int) + vol_ok.astype(int) + rsi_s.astype(int) + mid_dn.astype(int)

    raw = np.where(trend_up & trig_long & (score_l >= C.MIN_SCORE), 1,
          np.where(trend_dn & trig_short & (score_s >= C.MIN_SCORE), -1, 0))

    # Пауза после сигнала, чтобы не спамить одним и тем же движением.
    sig = np.zeros(len(df), dtype=int)
    last = {1: -10**9, -1: -10**9}
    for i in np.flatnonzero(raw):
        s = int(raw[i])
        if i - last[s] > C.COOLDOWN_BARS:
            sig[i] = s
            last[s] = i

    df = df.copy()
    df["signal"] = sig
    df["score"] = np.where(sig == 1, score_l, np.where(sig == -1, score_s, 0))
    df["sl"] = df["close"] - sig * C.SL_ATR * df["atr"]
    df["tp"] = df["close"] + sig * C.TP_ATR * df["atr"]
    # Расшифровка для сообщения
    df["f_adx"], df["f_vol"], df["f_rsi"] = strong, vol_ok, np.where(sig == 1, rsi_l, rsi_s)
    df["f_mid"] = np.where(sig == 1, mid_up, mid_dn)
    return df


def warmup_bars() -> int:
    """Сколько свечей нужно, чтобы индикаторы «прогрелись» (EMA200 на 4h и т.п.)."""
    return max(C.EMA_SLOW, C.MACD_SLOW + C.MACD_SIGNAL, C.ADX_LEN * 3, C.VOL_LEN) * 3
