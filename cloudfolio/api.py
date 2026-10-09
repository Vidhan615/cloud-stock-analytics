import csv
import hmac
import io
import re
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request, send_file, session
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from .charts import render_chart
from .market import CATALOG, indicators
from .models import AuditEvent, Holding, User, Watchlist, now
from .portfolio import DEMO_HOLDINGS, summary

api = Blueprint("api", __name__, url_prefix="/api")


class APIError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


class AuthThrottle:
    """Best-effort per-process throttle; use a shared gateway for distributed deployments."""

    def __init__(self):
        self.attempts = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key, limit=20, window=600):
        with self.lock:
            current = time.monotonic()
            for old_key in list(self.attempts):
                while self.attempts[old_key] and self.attempts[old_key][0] <= current - window:
                    self.attempts[old_key].popleft()
                if not self.attempts[old_key]:
                    del self.attempts[old_key]
            if len(self.attempts) >= 10000 and key not in self.attempts:
                return False
            if len(self.attempts[key]) >= limit:
                return False
            self.attempts[key].append(current)
            return True


def body(allowed):
    value = request.get_json(silent=True)
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise APIError("Send a JSON object with the documented fields")
    return value


def text(value, label, minimum=1, maximum=80):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise APIError(f"{label} must have {minimum}–{maximum} characters")
    return value.strip()


def symbol_check(symbol):
    symbol = symbol.upper()
    if symbol not in CATALOG:
        raise APIError("Symbol not found", 404)
    return symbol


def authenticated(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")
        user = g.db.get(User, user_id) if isinstance(user_id, int) else None
        if user is None:
            raise APIError("Sign in to use your personal portfolio", 401)
        g.user = user
        return fn(*args, **kwargs)

    return wrapped


def user_json(user):
    return {"id": user.id, "name": user.name, "email": user.email}


def establish(user):
    session.clear()
    session.permanent = True
    session["user_id"] = user.id
    session["csrf"] = secrets.token_urlsafe(32)
    return {"user": user_json(user), "csrf_token": session["csrf"]}


@api.before_request
def protect_mutations():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        expected, actual = session.get("csrf"), request.headers.get("X-CSRF-Token", "")
        if not isinstance(expected, str) or not hmac.compare_digest(expected, actual):
            raise APIError("Session check failed. Refresh the page and try again.", 403)


@api.get("/meta")
def meta():
    return jsonify(
        {
            "app": "Cloudfolio",
            "data_source": "synthetic_sample",
            "as_of": current_app.config["MARKET"]["RELIANCE"][-1]["date"],
            "mode": "application",
            "sessions": len(current_app.config["MARKET"]["RELIANCE"]),
            "cloud_configured": bool(current_app.config["S3_BUCKET"]),
            "note": "Synthetic historical prices. No live market connection.",
        }
    )


@api.get("/auth/session")
def session_info():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
        session.permanent = True
    user_id = session.get("user_id")
    user = g.db.get(User, user_id) if isinstance(user_id, int) else None
    return jsonify({"user": user_json(user) if user else None, "csrf_token": session["csrf"]})


@api.post("/auth/register")
def register():
    if not current_app.extensions["auth_throttle"].allow(request.remote_addr or "unknown"):
        raise APIError("Too many attempts. Try again later.", 429)
    data = body(["name", "email", "password"])
    name = text(data.get("name"), "Name")
    email = text(data.get("email"), "Email", maximum=254).lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise APIError("Enter a valid email address")
    password = data.get("password")
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise APIError("Use a password with 12–128 characters")
    user = User(name=name, email=email, password_hash=generate_password_hash(password))
    g.db.add(user)
    try:
        g.db.commit()
    except IntegrityError as exc:
        g.db.rollback()
        raise APIError("This email is already registered", 409) from exc
    return jsonify(establish(user)), 201


@api.post("/auth/login")
def login():
    if not current_app.extensions["auth_throttle"].allow(request.remote_addr or "unknown"):
        raise APIError("Too many attempts. Try again later.", 429)
    data = body(["email", "password"])
    email = text(data.get("email"), "Email", maximum=254).lower()
    password = data.get("password")
    if not isinstance(password, str) or not 1 <= len(password) <= 128:
        raise APIError("Invalid email or password", 401)
    user = g.db.scalar(select(User).where(User.email == email))
    hashed = user.password_hash if user else current_app.config["DUMMY_PASSWORD_HASH"]
    valid = check_password_hash(hashed, password)
    if user is None or not valid:
        raise APIError("Invalid email or password", 401)
    return jsonify(establish(user))


@api.post("/auth/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@api.get("/stocks")
def stocks():
    query = request.args.get("q", "").lower()
    if len(query) > 50:
        raise APIError("Search must be at most 50 characters")
    result = []
    for symbol, details in CATALOG.items():
        if query in symbol.lower() or query in details["name"].lower():
            bars = current_app.config["MARKET"][symbol]
            latest, previous = bars[-1], bars[-2]
            result.append(
                {
                    "symbol": symbol,
                    **details,
                    "price": latest["close"],
                    "change_pct": round((latest["close"] / previous["close"] - 1) * 100, 2),
                    "as_of": latest["date"],
                }
            )
    return jsonify({"stocks": result, "data_source": "synthetic_sample"})


def observed_bars(symbol):
    bars = current_app.config["MARKET"][symbol_check(symbol)]
    end = request.args.get("end")
    if end:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
            raise APIError("Use an ISO end date")
        try:
            date.fromisoformat(end)
        except ValueError as exc:
            raise APIError("Use a valid ISO end date") from exc
        bars = [bar for bar in bars if bar["date"] <= end]
        if not bars:
            raise APIError("No prices on or before this date", 404)
    return bars


@api.get("/history/<symbol>")
def history(symbol):
    return jsonify(
        {
            "symbol": symbol_check(symbol),
            "bars": observed_bars(symbol),
            "data_source": "synthetic_sample",
        }
    )


@api.get("/indicators/<symbol>")
def technical(symbol):
    return jsonify(
        {
            "symbol": symbol_check(symbol),
            **indicators(observed_bars(symbol)),
            "data_source": "synthetic_sample",
        }
    )


@api.get("/demo/summary")
def demo_summary():
    return jsonify({**summary(DEMO_HOLDINGS, current_app.config["MARKET"]), "demo": True})


def holdings_for_user():
    rows = g.db.scalars(
        select(Holding).where(Holding.user_id == g.user.id).order_by(Holding.symbol)
    ).all()
    return [
        {
            "symbol": r.symbol,
            "quantity": r.quantity,
            "average_price": str(r.average_price),
            "version": r.version,
            "updated_at": r.updated_at,
        }
        for r in rows
    ]


@api.get("/portfolio")
@api.get("/portfolio/summary")
@authenticated
def portfolio():
    return jsonify(summary(holdings_for_user(), current_app.config["MARKET"]))


def holding_input(data):
    symbol = symbol_check(text(data.get("symbol"), "Symbol", maximum=16))
    quantity, version = data.get("quantity"), data.get("version", 0)
    if type(quantity) is not int or not 1 <= quantity <= 1_000_000:
        raise APIError("Quantity must be a whole number from 1 to 1,000,000")
    if type(version) is not int or version < 0:
        raise APIError("Send the current holding version")
    raw = data.get("average_price")
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise APIError("Enter a positive average buy price")
    try:
        price = Decimal(str(raw))
        if not price.is_finite() or not Decimal("0.01") <= price <= Decimal("100000000"):
            raise APIError("Average buy price must be between ₹0.01 and ₹100,000,000")
        price = price.quantize(Decimal("0.000001"))
    except InvalidOperation as exc:
        raise APIError("Enter a valid average buy price") from exc
    return symbol, quantity, price, version


@api.post("/portfolio")
@authenticated
def save_holding():
    symbol, quantity, price, version = holding_input(
        body(["symbol", "quantity", "average_price", "version"])
    )
    if version == 0:
        g.db.add(Holding(user_id=g.user.id, symbol=symbol, quantity=quantity, average_price=price))
    else:
        result = g.db.execute(
            update(Holding)
            .where(
                Holding.user_id == g.user.id, Holding.symbol == symbol, Holding.version == version
            )
            .values(quantity=quantity, average_price=price, version=version + 1, updated_at=now())
        )
        if result.rowcount != 1:
            raise APIError("This holding changed in another session. Refresh before editing.", 409)
    g.db.add(
        AuditEvent(
            user_id=g.user.id,
            action="holding_saved",
            symbol=symbol,
            detail=f"quantity={quantity}; average_price={price}",
        )
    )
    try:
        g.db.commit()
    except IntegrityError as exc:
        g.db.rollback()
        raise APIError("This holding already exists. Refresh before editing.", 409) from exc
    return jsonify(summary(holdings_for_user(), current_app.config["MARKET"]))


@api.delete("/portfolio/<symbol>")
@authenticated
def remove_holding(symbol):
    symbol = symbol_check(symbol)
    version = body(["version"]).get("version")
    if type(version) is not int or version < 1:
        raise APIError("Send the current holding version")
    result = g.db.execute(
        delete(Holding).where(
            Holding.user_id == g.user.id, Holding.symbol == symbol, Holding.version == version
        )
    )
    if result.rowcount != 1:
        raise APIError("Holding missing or changed. Refresh before removing it.", 409)
    g.db.add(
        AuditEvent(
            user_id=g.user.id, action="holding_removed", symbol=symbol, detail=f"version={version}"
        )
    )
    g.db.commit()
    return jsonify({"ok": True})


@api.get("/watchlist")
@authenticated
def watchlist():
    return jsonify(
        {
            "symbols": list(
                g.db.scalars(select(Watchlist.symbol).where(Watchlist.user_id == g.user.id))
            )
        }
    )


@api.put("/watchlist/<symbol>")
@authenticated
def set_watchlist(symbol):
    symbol = symbol_check(symbol)
    enabled = body(["enabled"]).get("enabled")
    if not isinstance(enabled, bool):
        raise APIError("Enabled must be true or false")
    row = g.db.scalar(
        select(Watchlist).where(Watchlist.user_id == g.user.id, Watchlist.symbol == symbol)
    )
    if enabled and not row:
        g.db.add(Watchlist(user_id=g.user.id, symbol=symbol))
    elif not enabled and row:
        g.db.delete(row)
    try:
        g.db.commit()
    except IntegrityError:
        g.db.rollback()
    return watchlist()


@api.get("/activity")
@authenticated
def activity():
    rows = g.db.scalars(
        select(AuditEvent)
        .where(AuditEvent.user_id == g.user.id)
        .order_by(AuditEvent.id.desc())
        .limit(50)
    )
    return jsonify(
        {
            "events": [
                {
                    "action": row.action,
                    "symbol": row.symbol,
                    "detail": row.detail,
                    "created_at": row.created_at,
                }
                for row in rows
            ]
        }
    )


@api.post("/exports")
@authenticated
def create_export():
    body([])
    content = io.StringIO(newline="")
    writer = csv.writer(content)
    writer.writerow(
        [
            "symbol",
            "quantity",
            "average_price",
            "latest_price",
            "invested",
            "value",
            "pnl",
            "return_pct",
            "as_of",
        ]
    )
    data = summary(holdings_for_user(), current_app.config["MARKET"])
    for row in data["holdings"]:
        writer.writerow(
            [
                row["average_price_exact"] if key == "average_price" else row[key]
                for key in [
                    "symbol",
                    "quantity",
                    "average_price",
                    "latest_price",
                    "invested",
                    "value",
                    "pnl",
                    "return_pct",
                ]
            ]
            + [data["as_of"]]
        )
    export_id = uuid.uuid4().hex
    current_app.extensions["exports"].put(g.user.id, export_id, content.getvalue().encode())
    g.db.add(
        AuditEvent(user_id=g.user.id, action="portfolio_exported", symbol="", detail=export_id)
    )
    g.db.commit()
    return jsonify(
        {
            "id": export_id,
            "download_url": f"/api/exports/{export_id}",
            "storage": "private_s3"
            if current_app.config["S3_BUCKET"]
            else "local_private_directory",
        }
    ), 201


@api.get("/exports/<export_id>")
@authenticated
def download_export(export_id):
    if not re.fullmatch(r"[0-9a-f]{32}", export_id):
        raise APIError("Export not found", 404)
    event = g.db.scalar(
        select(AuditEvent).where(
            AuditEvent.user_id == g.user.id,
            AuditEvent.action == "portfolio_exported",
            AuditEvent.detail == export_id,
        )
    )
    if not event:
        raise APIError("Export not found", 404)
    try:
        content = current_app.extensions["exports"].read(g.user.id, export_id)
    except FileNotFoundError as exc:
        raise APIError("Export file is no longer available", 404) from exc
    return send_file(
        content, mimetype="text/csv", as_attachment=True, download_name="cloudfolio-portfolio.csv"
    )


@api.post("/charts/<symbol>")
@authenticated
def create_chart(symbol):
    body([])
    symbol = symbol_check(symbol)
    export_id = uuid.uuid4().hex
    image = render_chart(symbol, current_app.config["MARKET"][symbol])
    current_app.extensions["exports"].put(g.user.id, export_id, image, "svg")
    g.db.add(
        AuditEvent(user_id=g.user.id, action="chart_exported", symbol=symbol, detail=export_id)
    )
    g.db.commit()
    return jsonify(
        {
            "id": export_id,
            "download_url": f"/api/charts/{export_id}",
            "storage": "private_s3"
            if current_app.config["S3_BUCKET"]
            else "local_private_directory",
        }
    ), 201


@api.get("/charts/<export_id>")
@authenticated
def download_chart(export_id):
    if not re.fullmatch(r"[0-9a-f]{32}", export_id):
        raise APIError("Chart not found", 404)
    row = g.db.scalar(
        select(AuditEvent).where(
            AuditEvent.user_id == g.user.id,
            AuditEvent.action == "chart_exported",
            AuditEvent.detail == export_id,
        )
    )
    if not row:
        raise APIError("Chart not found", 404)
    try:
        content = current_app.extensions["exports"].read(g.user.id, export_id, "svg")
    except FileNotFoundError as exc:
        raise APIError("Chart file is no longer available", 404) from exc
    return send_file(
        content,
        mimetype="image/svg+xml",
        as_attachment=True,
        download_name=f"cloudfolio-{row.symbol.lower()}.svg",
    )
