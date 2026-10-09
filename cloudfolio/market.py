"""Strict CSV loading and causal descriptive indicators without a live feed."""

import csv
import math
from datetime import date
from pathlib import Path

CATALOG = {
    "RELIANCE": {"name": "Reliance Industries", "sector": "Diversified", "color": "#67a7f8"},
    "TCS": {"name": "Tata Consultancy Services", "sector": "Technology", "color": "#b59aed"},
    "INFY": {"name": "Infosys", "sector": "Technology", "color": "#52c9aa"},
    "HDFCBANK": {"name": "HDFC Bank", "sector": "Banking", "color": "#edb46f"},
    "SBIN": {"name": "State Bank of India", "sector": "Banking", "color": "#6ac6db"},
    "ITC": {"name": "ITC", "sector": "Consumer goods", "color": "#ee8eac"},
}


def load_market(path: str | Path):
    market = {symbol: [] for symbol in CATALOG}
    with open(path, newline="", encoding="utf-8") as stream:
        rows = csv.DictReader(stream)
        if not {"symbol", "date", "open", "high", "low", "close", "volume"}.issubset(
            rows.fieldnames or []
        ):
            raise ValueError("Dataset is missing required columns")
        for row in rows:
            symbol = row["symbol"]
            if symbol not in market:
                raise ValueError("Dataset contains an unknown symbol")
            day = date.fromisoformat(row["date"])
            if day.isoformat() != row["date"]:
                raise ValueError("Use canonical ISO dates")
            bar = {field: float(row[field]) for field in ["open", "high", "low", "close", "volume"]}
            if not all(math.isfinite(value) for value in bar.values()):
                raise ValueError("Non-finite dataset values")
            if min(bar[k] for k in ["open", "high", "low", "close"]) <= 0 or bar["volume"] < 0:
                raise ValueError("Prices must be positive and volume nonnegative")
            if bar["low"] > min(bar["open"], bar["close"]) or bar["high"] < max(
                bar["open"], bar["close"]
            ):
                raise ValueError("Invalid OHLC range")
            bar["date"] = row["date"]
            market[symbol].append(bar)
    reference = None
    for bars in market.values():
        bars.sort(key=lambda b: b["date"])
        dates = [bar["date"] for bar in bars]
        if len(dates) < 30 or len(dates) != len(set(dates)):
            raise ValueError("Each stock needs at least 30 unique daily bars")
        if reference is not None and dates != reference:
            raise ValueError("Stock calendars must align")
        reference = dates
    return market


def sma(prices, period=20):
    result, total = [], 0.0
    for i, price in enumerate(prices):
        total += price
        if i >= period:
            total -= prices[i - period]
        result.append(total / period if i >= period - 1 else None)
    return result


def ema(prices, period=20):
    if not prices:
        return []
    value, result, weight = prices[0], [], 2 / (period + 1)
    for price in prices:
        value = price * weight + value * (1 - weight)
        result.append(value)
    return result


def rsi(prices, period=14):
    result, gain, loss = [None] if prices else [], 0.0, 0.0
    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        if i <= period:
            gain += max(change, 0) / period
            loss += max(-change, 0) / period
        else:
            gain = (gain * (period - 1) + max(change, 0)) / period
            loss = (loss * (period - 1) + max(-change, 0)) / period
        result.append(
            None if i < period else (100 - 100 / (1 + gain / loss)) if loss else 100 if gain else 50
        )
    return result


def indicators(bars):
    prices = [b["close"] for b in bars]
    a, b, c = sma(prices), ema(prices), rsi(prices)
    return {
        "symbol_data": [
            {
                "date": bar["date"],
                "close": bar["close"],
                "sma20": a[i],
                "ema20": b[i],
                "rsi14": c[i],
            }
            for i, bar in enumerate(bars)
        ],
        "latest": {"close": prices[-1], "sma20": a[-1], "ema20": b[-1], "rsi14": c[-1]},
        "method": {
            "sma": "20-session simple mean",
            "ema": "20-session EMA initialized with the first close",
            "rsi": "14-session Wilder RSI; flat prices return 50",
        },
    }
