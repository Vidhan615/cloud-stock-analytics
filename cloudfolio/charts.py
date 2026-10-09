"""Server-rendered SVG chart exported as a private, downloadable artifact."""

from html import escape

from .market import indicators


def render_chart(symbol, bars):
    data = indicators(bars)["symbol_data"]
    values = [r["close"] for r in data]
    low, high = min(values), max(values)
    spread = high - low or 1

    def points(key):
        return " ".join(
            f"{45 + i / (len(data) - 1) * 805:.2f},{345 - (row[key] - low) / spread * 245:.2f}"
            for i, row in enumerate(data)
            if row[key] is not None
        )

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="920" height="430" viewBox="0 0 920 430">'
        '<rect width="920" height="430" fill="#f6f8f5"/>'
        f'<text x="45" y="40" font-family="sans-serif" font-size="22" fill="#20332d">{escape(symbol)} · Cloudfolio</text>'
        '<text x="45" y="65" font-family="sans-serif" font-size="12" fill="#667b70">Fictional daily closes / SMA 20 · Educational sample</text>'
        f'<polyline points="{points("close")}" fill="none" stroke="#236652" stroke-width="2.5"/>'
        f'<polyline points="{points("sma20")}" fill="none" stroke="#729bc1" stroke-width="1.8"/>'
        f'<text x="45" y="400" font-family="sans-serif" font-size="12" fill="#667b70">{escape(data[0]["date"])} → {escape(data[-1]["date"])} · Last close ₹{values[-1]:,.2f}</text>'
        "</svg>"
    ).encode("utf-8")
