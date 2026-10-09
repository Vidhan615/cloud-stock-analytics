"""Flask application factory with secure defaults and dependency health checks."""

import json
import logging
import os
import secrets
import time
import uuid
from datetime import timedelta
from pathlib import Path

import boto3
import click
from flask import Flask, g, jsonify, render_template, request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash

from .api import APIError, AuthThrottle, api
from .database import make_engine
from .market import load_market
from .models import Base, User
from .storage import ExportStore

ROOT = Path(__file__).resolve().parent.parent


def create_app(config=None):
    app = Flask(__name__, template_folder="templates", static_folder="static")
    instance = Path(os.environ.get("INSTANCE_DIR", ROOT / "instance"))
    app.config.from_mapping(
        APP_ENV=os.environ.get("APP_ENV", "development"),
        SECRET_KEY=os.environ.get("SECRET_KEY", ""),
        DATABASE_URL=os.environ.get(
            "DATABASE_URL", f"sqlite:///{(instance / 'cloudfolio.db').as_posix()}"
        ),
        SESSION_COOKIE_NAME="cloudfolio_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=1),
        SESSION_REFRESH_EACH_REQUEST=False,
        MAX_CONTENT_LENGTH=16384,
        AWS_REGION=os.environ.get("AWS_REGION", "ap-south-1"),
        S3_BUCKET=os.environ.get("S3_BUCKET", ""),
        DB_SECRET_ARN=os.environ.get("DB_SECRET_ARN", ""),
        DB_HOST=os.environ.get("DB_HOST", ""),
        DB_NAME=os.environ.get("DB_NAME", "cloudfolio"),
        DB_TLS_CA=os.environ.get("DB_TLS_CA", ""),
        EXPORT_DIR=str(instance / "exports"),
        DATASET_PATH=str(ROOT / "data" / "sample_prices.csv"),
    )
    if config:
        app.config.update(config)
    if not app.config["SECRET_KEY"] and os.environ.get("APP_SECRET_ARN"):
        app.config["SECRET_KEY"] = boto3.client(
            "secretsmanager", region_name=app.config["AWS_REGION"]
        ).get_secret_value(SecretId=os.environ["APP_SECRET_ARN"])["SecretString"]
    app.logger.setLevel(logging.INFO)
    if os.environ.get("TRUST_PROXY", "false").lower() == "true":
        # Enable only when the network admits exactly one trusted HTTP proxy.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    if app.config["APP_ENV"] == "production":
        if len(app.config["SECRET_KEY"]) < 32 or not app.config["SESSION_COOKIE_SECURE"]:
            raise ValueError("Production requires a strong SECRET_KEY and COOKIE_SECURE=true")
    elif not app.config["SECRET_KEY"]:
        instance.mkdir(parents=True, exist_ok=True)
        keyfile = instance / "session.key"
        try:
            fd = os.open(keyfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(secrets.token_hex(32))
        except FileExistsError:
            pass
        app.config["SECRET_KEY"] = keyfile.read_text().strip()
    instance.mkdir(parents=True, exist_ok=True)
    engine = make_engine(app.config)
    app.extensions["engine"] = engine
    app.extensions["exports"] = ExportStore(app.config)
    app.extensions["auth_throttle"] = AuthThrottle()
    app.config["MARKET"] = load_market(app.config["DATASET_PATH"])
    app.config["DUMMY_PASSWORD_HASH"] = generate_password_hash(secrets.token_urlsafe(24))
    app.register_blueprint(api)

    @app.before_request
    def start_request():
        g.request_id = uuid.uuid4().hex
        g.started = time.monotonic()
        g.db = Session(engine)

    @app.teardown_request
    def close_database(_):
        if hasattr(g, "db"):
            g.db.close()

    @app.after_request
    def headers(response):
        response.headers["X-Request-ID"] = g.get("request_id", "")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if app.config["APP_ENV"] == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        app.logger.info(
            json.dumps(
                {
                    "event": "request",
                    "request_id": g.get("request_id"),
                    "method": request.method,
                    "path": request.path,
                    "status": response.status_code,
                    "duration_ms": round(
                        (time.monotonic() - g.get("started", time.monotonic())) * 1000, 2
                    ),
                }
            )
        )
        return response

    @app.errorhandler(APIError)
    def api_error(error):
        return jsonify({"error": error.message, "request_id": g.get("request_id")}), error.status

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify({"error": error.description, "request_id": g.get("request_id")}), error.code

    @app.errorhandler(Exception)
    def internal_error(error):
        if hasattr(g, "db"):
            g.db.rollback()
        # Avoid exception strings, which can contain SQL parameters or credentials.
        app.logger.error(
            json.dumps(
                {
                    "event": "server_error",
                    "type": type(error).__name__,
                    "request_id": g.get("request_id"),
                }
            )
        )
        return jsonify(
            {"error": "Service temporarily unavailable", "request_id": g.get("request_id")}
        ), 503

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/health/live")
    def live():
        return jsonify({"status": "ok", "service": "cloudfolio"})

    @app.get("/health/ready")
    def ready():
        try:
            g.db.execute(select(User.id).limit(1))
        except SQLAlchemyError:
            g.db.rollback()
            return jsonify({"status": "unavailable", "database": "not_ready"}), 503
        return jsonify({"status": "ok", "database": engine.dialect.name, "dataset": "loaded"})

    @app.cli.command("init-db")
    def init_db():
        """Initialize the v1 schema; never drop existing data."""
        Base.metadata.create_all(engine)
        click.echo("Database schema initialized (existing rows preserved).")

    @app.cli.command("backup-dataset")
    def backup_dataset():
        """Store a versioned dataset copy privately; suitable for a scheduled job."""
        version = f"sample-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{uuid.uuid4().hex[:8]}"
        key = app.extensions["exports"].backup_dataset(
            Path(app.config["DATASET_PATH"]).read_bytes(), version
        )
        click.echo(f"Dataset backup stored: {key}")

    return app
