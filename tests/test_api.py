import csv
import io

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash

from cloudfolio.models import Base, User
from tests.conftest import Account


def test_demo_matches_reference_arithmetic(client):
    data = client.get("/api/demo/summary").json
    assert (data["invested"], data["value"], data["pnl"], data["return_pct"]) == (
        69000,
        71875,
        2875,
        4.17,
    )
    assert data["data_source"] == "synthetic_sample"


def test_search_and_causal_history(client):
    assert [s["symbol"] for s in client.get("/api/stocks?q=infosys").json["stocks"]] == ["INFY"]
    bars = client.get("/api/history/RELIANCE").json["bars"]
    cutoff = bars[35]["date"]
    prefix = client.get(f"/api/history/RELIANCE?end={cutoff}").json["bars"]
    assert prefix == bars[:36]
    full = client.get("/api/indicators/RELIANCE").json["symbol_data"]
    assert client.get(f"/api/indicators/RELIANCE?end={cutoff}").json["symbol_data"] == full[:36]


@pytest.mark.parametrize("end", ["2024-99-01", "2024-02-30", "2024-1-1", "<script>"])
def test_invalid_dates_rejected(client, end):
    assert client.get("/api/history/TCS", query_string={"end": end}).status_code == 400


def test_missing_symbol_and_early_history(client):
    assert client.get("/api/history/MISSING").status_code == 404
    assert client.get("/api/history/TCS?end=1900-01-01").status_code == 404


def test_authentication_required(client):
    assert client.get("/api/portfolio").status_code == 401
    assert client.get("/api/activity").status_code == 401


def test_csrf_required_even_for_login(client):
    assert client.post("/api/auth/login", json={}).status_code == 403
    token = client.get("/api/auth/session").json["csrf_token"]
    assert (
        client.post(
            "/api/auth/login", json={}, headers={"X-CSRF-Token": token + "wrong"}
        ).status_code
        == 403
    )


def test_hashed_password_and_rotated_session(account, app):
    with Session(app.extensions["engine"]) as db:
        user = db.scalar(select(User))
        assert user.password_hash.startswith("scrypt:")
        assert user.password_hash != "example-test-password-615"
        assert check_password_hash(user.password_hash, "example-test-password-615")
    response = account.client.get("/api/auth/session")
    assert response.json["user"]["email"] == "alice@example.test"
    cookie = response.headers.get("Set-Cookie", "")
    # A fresh anonymous session always emits the cookie.
    cookie = app.test_client().get("/api/auth/session").headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_duplicate_and_invalid_register(account, app):
    other = Account(app.test_client())
    assert other.register().status_code == 409
    assert (
        other.request(
            "POST",
            "/api/auth/register",
            {"name": "x", "email": "bad", "password": "long-enough-password"},
        ).status_code
        == 400
    )
    assert (
        other.request(
            "POST",
            "/api/auth/register",
            {"name": "x", "email": "x@example.test", "password": "short"},
        ).status_code
        == 400
    )


def test_logout_login_and_database_persistence(account, app):
    assert account.holding().status_code == 200
    assert account.request("POST", "/api/auth/logout", {}).status_code == 200
    assert account.client.get("/api/portfolio").status_code == 401
    fresh = Account(app.test_client())
    wrong = fresh.request(
        "POST", "/api/auth/login", {"email": "alice@example.test", "password": "incorrect"}
    )
    missing = fresh.request(
        "POST", "/api/auth/login", {"email": "absent@example.test", "password": "incorrect"}
    )
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json["error"] == missing.json["error"]
    assert (
        fresh.request(
            "POST",
            "/api/auth/login",
            {"email": "ALICE@example.test", "password": "example-test-password-615"},
        ).status_code
        == 200
    )
    assert fresh.client.get("/api/portfolio").json["value"] == 28500


def test_accounts_cannot_read_edit_delete_other_positions(account, app):
    assert account.holding().status_code == 200
    other = Account(app.test_client())
    assert other.register("bob@example.test").status_code == 201
    assert other.client.get("/api/portfolio").json["holdings"] == []
    assert other.holding(version=1).status_code == 409
    assert other.request("DELETE", "/api/portfolio/RELIANCE", {"version": 1}).status_code == 409
    assert other.client.get("/api/activity").json["events"] == []
    assert account.client.get("/api/portfolio").json["value"] == 28500


def test_optimistic_updates_and_stale_delete(account):
    assert account.holding().status_code == 200
    assert account.holding().status_code == 409
    assert account.holding(quantity=12, version=1).status_code == 200
    assert account.holding(quantity=99, version=1).status_code == 409
    assert account.request("DELETE", "/api/portfolio/RELIANCE", {"version": 1}).status_code == 409
    assert account.client.get("/api/portfolio").json["holdings"][0]["quantity"] == 12
    assert account.request("DELETE", "/api/portfolio/RELIANCE", {"version": 2}).status_code == 200
    assert account.client.get("/api/portfolio").json["value"] == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"quantity": True},
        {"quantity": 0},
        {"quantity": 1.5},
        {"quantity": 1000001},
        {"average_price": "NaN"},
        {"average_price": "Infinity"},
        {"average_price": "-1"},
        {"average_price": True},
        {"version": True},
        {"version": -1},
        {"unknown": "field"},
    ],
)
def test_invalid_positions_never_persist(account, changes):
    assert account.holding(**changes).status_code == 400
    assert account.client.get("/api/portfolio").json["holdings"] == []


def test_sql_injection_is_data(account):
    assert account.holding(symbol="TCS'; DROP TABLE users;--").status_code == 400
    assert account.client.get("/api/auth/session").json["user"]["name"] == "Alice"
    assert account.client.get("/api/stocks?q=%27%20OR%201=1").json["stocks"] == []


def test_watchlist_isolated_and_idempotent(account, app):
    assert account.request("PUT", "/api/watchlist/TCS", {"enabled": True}).json["symbols"] == [
        "TCS"
    ]
    assert account.request("PUT", "/api/watchlist/TCS", {"enabled": True}).json["symbols"] == [
        "TCS"
    ]
    other = Account(app.test_client())
    other.register("bob@example.test")
    assert other.client.get("/api/watchlist").json["symbols"] == []
    assert account.request("PUT", "/api/watchlist/TCS", {"enabled": False}).json["symbols"] == []


@pytest.mark.parametrize(
    "endpoint,content_type", [("/exports", "text/csv"), ("/charts/RELIANCE", "image/svg+xml")]
)
def test_private_downloads_require_owner(account, app, endpoint, content_type):
    account.holding()
    result = account.request("POST", "/api" + endpoint, {})
    assert result.status_code == 201
    url = result.json["download_url"]
    downloaded = account.client.get(url)
    assert downloaded.status_code == 200
    assert downloaded.mimetype == content_type
    assert "attachment;" in downloaded.headers["Content-Disposition"]
    if content_type == "text/csv":
        row = list(csv.DictReader(io.StringIO(downloaded.text)))[0]
        assert row["symbol"] == "RELIANCE" and float(row["pnl"]) == 1300
    else:
        assert "<svg" in downloaded.text and "Fictional" in downloaded.text
    assert app.test_client().get(url).status_code == 401
    other = Account(app.test_client())
    other.register("bob@example.test")
    assert other.client.get(url).status_code == 404


def test_expired_export_returns_404(account, app):
    result = account.request("POST", "/api/exports", {})
    store = app.extensions["exports"]
    with Session(app.extensions["engine"]) as db:
        user_id = db.scalar(select(User.id))
    (store.root / store.key(user_id, result.json["id"])).unlink()
    assert account.client.get(result.json["download_url"]).status_code == 404


def test_headers_and_ready_schema(client, app):
    response = client.get("/api/meta")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert len(response.headers["X-Request-ID"]) == 32
    assert client.get("/health/ready").status_code == 200
    Base.metadata.drop_all(app.extensions["engine"])
    assert client.get("/health/ready").status_code == 503
    assert client.get("/health/live").status_code == 200


def test_errors_and_logs_do_not_expose_secret(account, app, caplog):
    def fail(*args, **kwargs):
        raise RuntimeError("super-sensitive-database-password")

    app.extensions["exports"].put = fail
    result = account.request("POST", "/api/exports", {})
    assert result.status_code == 503
    assert "super-sensitive" not in result.text + caplog.text


def test_body_size_limit_and_throttle(client, app):
    wrapper = Account(client)
    assert wrapper.request("POST", "/api/auth/register", {"name": "x" * 20000}).status_code == 413
    limiter = app.extensions["auth_throttle"]
    assert all(limiter.allow("test", limit=2) for _ in range(2))
    assert not limiter.allow("test", limit=2)


def test_cli_preserves_holdings_and_backs_up(account, app):
    account.holding()
    runner = app.test_cli_runner()
    assert runner.invoke(args=["init-db"]).exit_code == 0
    assert account.client.get("/api/portfolio").json["value"] == 28500
    result = runner.invoke(args=["backup-dataset"])
    assert result.exit_code == 0
    assert list(app.extensions["exports"].root.glob("datasets/*.csv"))
