import os

import pytest
from sqlalchemy.engine import make_url

from cloudfolio import create_app
from cloudfolio.models import Base


@pytest.fixture
def app(tmp_path):
    url = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    # Refuse destructive schema isolation against an arbitrary MySQL database.
    if not url.startswith("sqlite:") and make_url(url).database != "cloudfolio_test":
        pytest.fail("MySQL tests require the dedicated cloudfolio_test database")
    instance = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-only-session-key-never-use-in-production",
            "DATABASE_URL": url,
            "EXPORT_DIR": str(tmp_path / "exports"),
            "DB_SECRET_ARN": "",
            "S3_BUCKET": "",
            "APP_ENV": "development",
            "SESSION_COOKIE_SECURE": False,
        }
    )
    engine = instance.extensions["engine"]
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield instance
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


class Account:
    def __init__(self, client):
        self.client = client
        self.csrf = client.get("/api/auth/session").json["csrf_token"]

    def request(self, method, path, payload=None):
        response = self.client.open(
            path, method=method, json=payload, headers={"X-CSRF-Token": self.csrf}
        )
        if response.is_json and "csrf_token" in response.json:
            self.csrf = response.json["csrf_token"]
        return response

    def register(self, email="alice@example.test"):
        return self.request(
            "POST",
            "/api/auth/register",
            {
                "name": "Alice",
                "email": email,
                "password": "example-test-password-615",
            },
        )

    def holding(self, **changes):
        data = {"symbol": "RELIANCE", "quantity": 10, "average_price": "2720", "version": 0}
        data.update(changes)
        return self.request("POST", "/api/portfolio", data)


@pytest.fixture
def account(client):
    wrapper = Account(client)
    assert wrapper.register().status_code == 201
    return wrapper
