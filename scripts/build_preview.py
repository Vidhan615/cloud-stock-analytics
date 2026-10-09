"""Build a read-only static preview from the same server computations."""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cloudfolio.market import CATALOG, indicators, load_market  # noqa: E402
from cloudfolio.portfolio import DEMO_HOLDINGS, summary  # noqa: E402


def build(destination):
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "data").mkdir(exist_ok=True)
    market = load_market(ROOT / "data" / "sample_prices.csv")
    stocks = [
        {
            "symbol": symbol,
            **CATALOG[symbol],
            "price": bars[-1]["close"],
            "change_pct": round((bars[-1]["close"] / bars[-2]["close"] - 1) * 100, 2),
            "as_of": bars[-1]["date"],
        }
        for symbol, bars in market.items()
    ]
    snapshot = {
        "stocks": stocks,
        "market": market,
        "indicators": {symbol: indicators(bars) for symbol, bars in market.items()},
        "demo": {**summary(DEMO_HOLDINGS, market), "demo": True},
    }
    (destination / "data" / "snapshot.json").write_text(
        json.dumps(snapshot, allow_nan=False, separators=(",", ":")), encoding="utf-8"
    )
    html = (
        (ROOT / "cloudfolio" / "templates" / "index.html")
        .read_text(encoding="utf-8")
        .replace('data-mode="application"', 'data-mode="snapshot"')
    )
    (destination / "index.html").write_text(html, encoding="utf-8")
    shutil.copytree(ROOT / "cloudfolio" / "static", destination / "static", dirs_exist_ok=True)
    (destination / ".nojekyll").touch()
    print(f"Read-only preview built in {destination}")


if __name__ == "__main__":
    build(ROOT / "site")
