"""Generate reproducible fictional OHLC; close anchors support the report P/L example."""

import csv
import math
import random
from datetime import date, timedelta
from pathlib import Path

ANCHORS = {"RELIANCE": 2850, "TCS": 3920, "INFY": 1585, "HDFCBANK": 1680, "SBIN": 785, "ITC": 455}


def generate(target):
    dates, day = [], date(2024, 1, 2)
    while len(dates) < 90:
        if day.weekday() < 5:
            dates.append(day.isoformat())
        day += timedelta(days=1)
    with open(target, "w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["symbol", "date", "open", "high", "low", "close", "volume"])
        for n, (symbol, anchor) in enumerate(ANCHORS.items()):
            rng = random.Random(615 + n)
            closes = [
                anchor
                * (0.93 + i * 0.00075 + 0.023 * math.sin(i / 7 + n) + rng.uniform(-0.008, 0.008))
                for i in range(90)
            ]
            scale = anchor / closes[-1]
            closes = [round(c * scale, 2) for c in closes]
            for i, day in enumerate(dates):
                close = closes[i]
                opening = round(
                    (closes[i - 1] if i else close) * (1 + rng.uniform(-0.004, 0.004)), 2
                )
                high = round(max(opening, close) * (1 + rng.uniform(0.002, 0.01)), 2)
                low = round(min(opening, close) * (1 - rng.uniform(0.002, 0.01)), 2)
                writer.writerow(
                    [symbol, day, opening, high, low, close, rng.randint(400000, 4500000)]
                )


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    (root / "data").mkdir(exist_ok=True)
    generate(root / "data" / "sample_prices.csv")
