"""Connection pools, SQLite pragmas and rotation-aware RDS credentials."""

import json
import threading
import time

import boto3
import pymysql
from sqlalchemy import create_engine, event


class RDSConnector:
    def __init__(self, config, client=None):
        self.config = config
        self.client = client or boto3.client("secretsmanager", region_name=config["AWS_REGION"])
        self.lock = threading.Lock()
        self.cached = None
        self.expiry = 0

    def credentials(self, refresh=False):
        with self.lock:
            if refresh or self.cached is None or time.monotonic() >= self.expiry:
                payload = self.client.get_secret_value(SecretId=self.config["DB_SECRET_ARN"])
                secret = json.loads(payload["SecretString"])
                if not secret.get("username") or not secret.get("password"):
                    raise ValueError("Database credential secret is incomplete")
                self.cached = secret
                self.expiry = time.monotonic() + 30
            return self.cached

    def connect(self):
        def attempt(secret):
            return pymysql.connect(
                host=self.config["DB_HOST"],
                user=secret["username"],
                password=secret["password"],
                database=self.config["DB_NAME"],
                charset="utf8mb4",
                connect_timeout=10,
                read_timeout=15,
                write_timeout=15,
                ssl_ca=self.config["DB_TLS_CA"],
                ssl_verify_cert=True,
                ssl_verify_identity=True,
            )

        try:
            return attempt(self.credentials())
        except pymysql.err.OperationalError as exc:
            if exc.args[0] != 1045:
                raise
            # A managed secret can rotate between cache reads. Retry authentication once.
            return attempt(self.credentials(refresh=True))


def make_engine(config):
    if config.get("DB_SECRET_ARN"):
        if not config.get("DB_HOST") or not config.get("DB_TLS_CA"):
            raise ValueError("RDS mode requires DB_HOST and a trusted DB_TLS_CA certificate bundle")
        connector = RDSConnector(config)
        return create_engine(
            "mysql+pymysql://", creator=connector.connect, pool_pre_ping=True, pool_recycle=300
        )
    url = config["DATABASE_URL"]
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite:"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 10}
    else:
        kwargs["pool_recycle"] = 300
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite:"):

        @event.listens_for(engine, "connect")
        def sqlite_settings(dbapi_connection, _):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.close()

    return engine
