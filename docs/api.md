# API and application boundaries

All responses use JSON except authenticated CSV/SVG downloads. Monetary output is in INR. Every response has a request ID; API responses use `Cache-Control: no-store`.

## Authentication

1. `GET /api/auth/session` establishes an anonymous signed cookie and returns `csrf_token`.
2. Send that token as `X-CSRF-Token` on **every** POST, PUT, PATCH or DELETE, including register, login and logout. Send the cookie on subsequent requests.
3. `POST /api/auth/register` accepts `name`, `email`, `password`; `POST /api/auth/login` accepts `email`, `password`. A successful response rotates the session and returns a fresh CSRF token.
4. `POST /api/auth/logout` clears the browser's cookie. Request a new anonymous session before signing in again.

Passwords require 12–128 characters and use Werkzeug's salted scrypt hashes. Production cookies are Secure, HttpOnly and SameSite=Lax; sessions have a one-hour lifetime. The signed cookie stores identity and CSRF state, never a password. Signed cookies are not encrypted, and logout does not centrally revoke a previously copied cookie. Production needing immediate revocation should introduce server-side sessions.

The basic login/register throttle is per worker (20 attempts per IP per ten minutes), not a distributed abuse-control service. Add a shared limiter/gateway before a public production launch. Email verification, password recovery, MFA and account administration are outside this v1 prototype.

## Routes

| Method / path | Purpose | Account required |
|---|---|---|
| GET `/api/meta` | Dataset provenance and mode | No |
| GET `/api/stocks?q=infy` | Symbol/company search | No |
| GET `/api/history/RELIANCE?end=2024-03-01` | Historical observed prefix | No |
| GET `/api/indicators/RELIANCE?end=2024-03-01` | SMA, EMA, RSI and warm-up nulls | No |
| GET `/api/demo/summary` | Clearly marked illustrative portfolio | No |
| GET `/api/portfolio/summary` | Current user's saved holdings and totals | Yes |
| POST `/api/portfolio` | Add or replace an entire holding | Yes |
| DELETE `/api/portfolio/RELIANCE` | Remove current holding version | Yes |
| GET `/api/watchlist` | Current user's saved symbols | Yes |
| PUT `/api/watchlist/TCS` | Set `enabled` to true/false, idempotently | Yes |
| GET `/api/activity` | Most recent 50 account events | Yes |
| POST `/api/exports` | Create a private portfolio CSV | Yes |
| GET `/api/exports/{id}` | Download an export owned by this account | Yes |
| POST `/api/charts/TCS` | Create a private chart SVG | Yes |
| GET `/api/charts/{id}` | Download a chart owned by this account | Yes |
| GET `/health/live` | Process is responding | No |
| GET `/health/ready` | Database/schema reachable and dataset loaded | No |

Position example:

```json
{"symbol":"RELIANCE","quantity":10,"average_price":"2720","version":0}
```

Use `version: 0` for a new position. Use the version returned by your last summary when editing or removing a position. Existing-record writes match account, symbol and version in a single SQL statement; a stale or duplicate write returns 409. Quantity is a positive whole number; this endpoint sets a complete holding and is not a buy/sell order. Average prices are stored to six decimal places and displayed to two.

Exports and charts accept an empty JSON object `{}`. Their returned download URL requires your session and an audit record proving ownership. No public S3 URL or public ACL is created. Missing/expired exports return 404. S3 authorization belongs to the app's IAM role; per-user authorization is enforced in Flask before storage access. The browser interface exposes portfolio and charts; watchlists are available through the API in v1.

## Operational notes

- SQLite is a local-development option. Use MySQL/RDS for the provided cloud deployment.
- `init-db` creates missing v1 tables and preserves records. It is not a schema-migration system; add reviewed migrations before schema changes.
- Database queries use SQLAlchemy parameters and account-scoped filters. The body limit is 16 KiB; unknown JSON fields and invalid dates/prices/quantities are rejected.
- The app uses a strict same-origin content security policy and text-based DOM updates. HTTPS and proxy trust depend on the documented ALB/nginx network boundary. Never enable `TRUST_PROXY` on a directly reachable app server.
- Application logs record request ID, method, path, status and duration. They omit request bodies and query strings; unexpected exceptions log their type, not database error text or parameters.
- Dataset validation fails startup for unknown symbols, non-finite/invalid prices, duplicate dates or unaligned calendars. The API never falls back to invented live data.
- Export activity records are metadata, not a tamper-proof or immutable audit ledger. Failed storage/database transactions can leave orphaned private objects; lifecycle expiry bounds their retention in AWS.
- RDS-managed credentials are cached for 30 seconds. New connections refresh them after expiry or one authentication failure; TLS verifies both the certificate and hostname. See [AWS's managed-secret documentation](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-secrets-manager.html) and [PyMySQL connection options](https://pymysql.readthedocs.io/en/latest/modules/connections.html).
- For reproducible bootstrap, the template uses the RDS-managed **master database user**. Its database privileges are broader than a normal app needs. Before handling real customer data, provision a restricted app database user and a separately rotated secret, along with shared throttling, session revocation, migrations, restore exercises and operational alert recipients.
