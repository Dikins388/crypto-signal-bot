"""Разовая проверка: пускает ли Bybit запросы с серверов GitHub. Бота не трогает."""
import json
import time

import requests

HOSTS = ["https://api.bybit.com", "https://api.bytick.com"]
PATHS = [
    "/v5/market/time",
    "/v5/market/tickers?category=spot&symbol=BTCUSDT",
    "/v5/market/kline?category=spot&symbol=BTCUSDT&interval=15&limit=5",
    "/v5/market/tickers?category=spot",
]

ok_any = False
for host in HOSTS:
    for path in PATHS:
        url = host + path
        t0 = time.time()
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "crypto-signal-bot/1.0"})
            ms = int((time.time() - t0) * 1000)
            body = r.text[:150].replace("\n", " ")
            info = ""
            if r.status_code == 200:
                try:
                    d = r.json()
                    lst = (d.get("result") or {}).get("list")
                    info = f"retCode={d.get('retCode')} rows={len(lst) if isinstance(lst, list) else '-'}"
                    ok_any = ok_any or d.get("retCode") == 0
                except json.JSONDecodeError:
                    info = "не JSON"
            print(f"{r.status_code} {ms}ms {url}  {info}  {'' if r.status_code == 200 else body}")
        except requests.RequestException as e:
            print(f"ОШИБКА {url}: {e.__class__.__name__}: {e}")
print("\nИТОГ:", "Bybit ОТВЕЧАЕТ с GitHub" if ok_any else "Bybit НЕ отвечает с GitHub")
