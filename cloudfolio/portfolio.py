"""Portfolio valuations use Decimal and report monetary values at two decimals."""

from decimal import ROUND_HALF_UP, Decimal

from .market import CATALOG

CENT = Decimal("0.01")


def amount(value):
    return float(Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP))


def summary(holdings, market):
    invested, current, rows = Decimal(0), Decimal(0), []
    for holding in holdings:
        symbol, quantity = holding["symbol"], holding["quantity"]
        cost = Decimal(str(holding["average_price"])) * quantity
        value = Decimal(str(market[symbol][-1]["close"])) * quantity
        invested += cost
        current += value
        rows.append(
            {
                **holding,
                **CATALOG[symbol],
                "average_price": amount(holding["average_price"]),
                "average_price_exact": str(holding["average_price"]),
                "latest_price": market[symbol][-1]["close"],
                "invested": amount(cost),
                "value": amount(value),
                "pnl": amount(value - cost),
                "return_pct": amount((value - cost) / cost * 100) if cost else 0,
            }
        )
    pnl = current - invested
    return {
        "holdings": rows,
        "invested": amount(invested),
        "value": amount(current),
        "pnl": amount(pnl),
        "return_pct": amount(pnl / invested * 100) if invested else 0,
        "as_of": next(iter(market.values()))[-1]["date"],
        "data_source": "synthetic_sample",
        "note": "Marked holdings only; this is not a trade execution ledger. Fees, realised P&L and corporate actions are not modeled.",
    }


DEMO_HOLDINGS = [
    {"symbol": "RELIANCE", "quantity": 10, "average_price": "2720", "version": 1},
    {"symbol": "TCS", "quantity": 5, "average_price": "3800", "version": 1},
    {"symbol": "INFY", "quantity": 15, "average_price": "1520", "version": 1},
]
