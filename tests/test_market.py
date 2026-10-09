import csv
from pathlib import Path

import pytest

from cloudfolio import ROOT, create_app
from cloudfolio.market import ema, load_market, rsi, sma


def test_known_indicator_sequences():
    prices = list(range(1, 31))
    assert sma(prices)[:19] == [None] * 19
    assert sma(prices)[19] == 10.5
    assert ema([10, 20, 30], period=3) == [10, 15, 22.5]
    assert rsi(prices)[:14] == [None] * 14
    assert rsi(prices)[14:] == [100] * 16
    assert rsi(list(reversed(prices)))[14:] == [0] * 16
    assert rsi([5] * 30)[14:] == [50] * 16


def test_wilder_rsi_known_example():
    prices = [
        44.34,
        44.09,
        44.15,
        43.61,
        44.33,
        44.83,
        45.10,
        45.42,
        45.84,
        46.08,
        45.89,
        46.03,
        45.61,
        46.28,
        46.28,
        46.00,
    ]
    assert rsi(prices)[14] == pytest.approx(70.464135, abs=0.00001)
    assert rsi(prices)[15] == pytest.approx(66.249619, abs=0.00001)


def test_sample_calendar_and_prices():
    market = load_market(ROOT / "data/sample_prices.csv")
    assert len(market) == 6
    assert all(len(bars) == 90 for bars in market.values())
    assert market["RELIANCE"][-1]["close"] == 2850
    assert market["TCS"][-1]["close"] == 3920
    assert market["INFY"][-1]["close"] == 1585


@pytest.mark.parametrize(
    "change",
    [
        {"close": "nan"},
        {"volume": "-1"},
        {"low": "999999"},
        {"date": "2024-02-30"},
        {"symbol": "BAD"},
    ],
)
def test_corrupted_prices_fail_before_serving(tmp_path, change):
    with open(ROOT / "data/sample_prices.csv", newline="") as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    rows[0].update(change)
    target = tmp_path / "bad.csv"
    with target.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError):
        load_market(target)


def test_production_refuses_insecure_defaults():
    with pytest.raises(ValueError, match="Production requires"):
        create_app({"APP_ENV": "production", "SECRET_KEY": "short", "SESSION_COOKIE_SECURE": True})
    with pytest.raises(ValueError, match="Production requires"):
        create_app(
            {"APP_ENV": "production", "SECRET_KEY": "x" * 64, "SESSION_COOKIE_SECURE": False}
        )


def test_production_cookie_and_hsts(tmp_path):
    app = create_app(
        {
            "APP_ENV": "production",
            "SECRET_KEY": "x" * 64,
            "SESSION_COOKIE_SECURE": True,
            "DATABASE_URL": f"sqlite:///{Path(tmp_path / 'prod.db').as_posix()}",
        }
    )
    response = app.test_client().get("/api/auth/session", base_url="https://example.test")
    assert "; Secure;" in response.headers["Set-Cookie"]
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
    app.extensions["engine"].dispose()
