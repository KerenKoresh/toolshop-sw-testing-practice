# ToolShop

A small but complete full-stack demo store: a public web catalog backed by a fully documented REST API. It is built to be a clean, realistic target for API testing, automation practice, and front-end / back-end demos.

**Live demo:** https://kerens-software-testing-practice.onrender.com
**API docs (Swagger UI):** https://kerens-software-testing-practice.onrender.com/docs

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0-000000?logo=flask&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-CA2C2E)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-ready-336791?logo=postgresql&logoColor=white)
![OpenAPI](https://img.shields.io/badge/OpenAPI-3.0-6BA539?logo=openapiinitiative&logoColor=white)

---

## Table of contents

- [Overview](#overview)
- [Features](#features)
- [Tech stack](#tech-stack)
- [Architecture](#architecture)
- [API reference](#api-reference)
- [Ownership model (edit tokens)](#ownership-model-edit-tokens)
- [Data model](#data-model)
- [Data retention](#data-retention)
- [Project structure](#project-structure)
- [Running locally](#running-locally)
- [Database migrations](#database-migrations)
- [Deployment (Render + Postgres)](#deployment-render--postgres)
- [Design notes](#design-notes)
- [Author](#author)

---

## Overview

ToolShop serves two audiences from one codebase:

- **Humans** get a responsive catalog at `/` where they can browse tools, search by name or ID, and open any product's detail page.
- **Machines** get a public JSON REST API at `/api` with full create / read / update / delete support and interactive OpenAPI documentation at `/docs`.

There are no user accounts. Reading and searching are open to everyone; modifying a product is protected by a per-item secret (see [Ownership model](#ownership-model-edit-tokens)). All write operations are performed through the API only, so the website itself is read-only.

## Features

- **Public REST API** with full CRUD over JSON and permissive CORS.
- **Search** by partial, case-insensitive name (`?search=`) or exact ID (`?id=`).
- **Interactive API docs**: a complete OpenAPI 3.0 spec rendered with Swagger UI, including "Try it out".
- **Token-based ownership**: no logins; creating a product returns a one-time secret required to edit or delete it. Only a hash is stored.
- **Database-agnostic**: runs on PostgreSQL in production and SQLite locally, selected automatically via `DATABASE_URL`.
- **Self-healing schema**: detects and repairs schema drift on boot, with a concurrency-safe, multi-worker initialization lock.
- **Scheduled cleanup**: a daily job removes API-created products to keep the database small.
- **A dedicated guide page** (`/guide`) explaining the project and every endpoint.
- **One-command deploy** to Render via a Blueprint (`render.yaml`) that also provisions Postgres.

## Tech stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.12 |
| Web framework | Flask 3 |
| ORM | SQLAlchemy 2.0 |
| Database | PostgreSQL (prod) / SQLite (local) |
| API server | Gunicorn |
| API docs | OpenAPI 3.0 + Swagger UI |
| Front end | Server-rendered HTML + vanilla JS (no build step) |
| Hosting | Render |

## Architecture

```mermaid
flowchart TD
    Client["Clients: Browser, curl, Postman"]

    subgraph Flask["Flask application"]
        Pages["Web pages: / · /product · /guide · /docs"]
        API["REST API: /api/products · CRUD · search"]
        Spec["OpenAPI 3.0: /api/openapi.json"]
    end

    ORM["SQLAlchemy ORM"]
    DB[("Database: PostgreSQL / SQLite")]
    Swagger["Swagger UI: /docs"]
    Cron["Cleanup job: daily 00:00 IST"]

    Client --> Pages
    Client --> API
    Pages --> ORM
    API --> ORM
    ORM --> DB
    Swagger -->|loads spec| Spec
    Swagger -->|Try it out| API
    Cron -->|delete API items| API
```

A single Flask app serves both the rendered pages and the JSON API. The OpenAPI document is generated in Python and exposed at `/api/openapi.json`; the `/docs` page is a thin Swagger UI shell that consumes it, so the docs can never drift from the routes. The live site also includes an interactive version of this diagram on the `/guide` page.

## API reference

Base URL: `/api`. All responses are JSON. Reads and creates are open; updates and deletes require the product's edit token in the `X-Edit-Token` header.

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/products` | none | List all products |
| `GET` | `/api/products?search=plier` | none | Search by partial, case-insensitive name |
| `GET` | `/api/products?id=3` | none | Exact ID lookup (returns a list of 0 to 1 items) |
| `GET` | `/api/products/{id}` | none | Get a single product by ID |
| `POST` | `/api/products` | none | Create a product (returns a one-time `edit_token`) |
| `PUT` / `PATCH` | `/api/products/{id}` | `X-Edit-Token` | Update a product |
| `DELETE` | `/api/products/{id}` | `X-Edit-Token` | Delete a product |
| `GET` | `/api/health` | none | Health check (reports the active DB engine) |

### Examples

```bash
# Create, then keep the edit_token from the response
curl -X POST https://kerens-software-testing-practice.onrender.com/api/products \
  -H "Content-Type: application/json" \
  -d '{"name":"Rubber Mallet","price":9.9,"category":"Hammer"}'
# returns: { "id": 13, ..., "edit_token": "AbC123..." }

# Update (token required)
curl -X PUT https://kerens-software-testing-practice.onrender.com/api/products/13 \
  -H "Content-Type: application/json" \
  -H "X-Edit-Token: AbC123..." \
  -d '{"price":19.99,"in_stock":false}'

# Delete (token required)
curl -X DELETE https://kerens-software-testing-practice.onrender.com/api/products/13 \
  -H "X-Edit-Token: AbC123..."

# Search and exact lookup
curl "https://kerens-software-testing-practice.onrender.com/api/products?search=plier"
curl "https://kerens-software-testing-practice.onrender.com/api/products?id=3"
```

### Status codes

| Code | When |
|------|------|
| `200` | Successful read / update / delete |
| `201` | Product created |
| `400` | Invalid input (e.g. missing `name`, non-numeric `id`) |
| `401` | Edit token required but not provided |
| `403` | Wrong edit token, or attempting to modify a read-only baseline product |
| `404` | Product not found |

## Ownership model (edit tokens)

The API is public, so anyone can create products, but a product should only be editable by whoever created it without forcing accounts and passwords. ToolShop solves this with **per-item edit tokens**:

1. `POST /api/products` generates a random secret and returns it once as `edit_token`. Only its SHA-256 hash is persisted.
2. `PUT` and `DELETE` require that token in the `X-Edit-Token` header; the server compares hashes.
3. Seeded baseline products have no token and are therefore read-only.

This keeps the API open and scriptable while still scoping mutations to their creator, a pragmatic alternative to full authentication for a public demo.

## Data model

A single `products` table:

| Column | Type | Notes |
|--------|------|-------|
| `id` | integer | Primary key, auto-increment |
| `name` | string | Required |
| `description` | text | Optional |
| `price` | float | Defaults to 0 |
| `category` | string | Optional |
| `in_stock` | boolean | Defaults to true |
| `edit_token_hash` | string | SHA-256 of the edit token; `NULL` for read-only baseline items |

JSON representation:

```json
{
  "id": 1,
  "name": "Combination Pliers",
  "description": "Durable steel combination pliers.",
  "price": 14.15,
  "category": "Pliers",
  "in_stock": true,
  "editable": false
}
```

## Data retention

Products created through the API are temporary. A scheduled job deletes every product that was created via the API once a day at midnight (Israel time), keeping only the built-in baseline catalog. This keeps the database small.

There are two ways to schedule it:

**Option A: Render Cron Job** (configured in `render.yaml`). Runs `scripts/cleanup.py` on a schedule in UTC (`0 21 * * *` is midnight in Israel during summer time). You can also run the script manually:

```bash
python scripts/cleanup.py
```

**Option B: external scheduler** (free, no Render cron required). The app exposes a secret-protected endpoint that runs the same cleanup:

```
POST /api/maintenance/cleanup     # header: X-Cleanup-Token: <secret>
GET  /api/maintenance/cleanup?token=<secret>
```

Set a `CLEANUP_TOKEN` environment variable on the web service to a random secret (the endpoint returns `503` until it is set). Then point a free scheduler such as [cron-job.org](https://cron-job.org) at the URL once a day, passing the token. The endpoint is intentionally excluded from the public OpenAPI docs.

## Project structure

```
toolshop/
├── app.py                 # Flask app: API, pages, OpenAPI spec, DB init, cleanup
├── scripts/
│   ├── migrate.py         # Schema bootstrap, drift check, and reset
│   └── cleanup.py         # Daily removal of API-created products
├── templates/
│   ├── index.html         # Catalog + search
│   ├── product.html       # Product detail
│   ├── guide.html         # Project & API guide
│   └── docs.html          # Swagger UI
├── static/
│   └── app.css            # Front-end styling
├── requirements.txt
├── Procfile               # Process definition (Gunicorn)
├── Dockerfile             # Container build
├── render.yaml            # Render Blueprint (web service + Postgres + cron)
└── README.md
```

## Running locally

Requires Python 3.10 or newer.

```bash
git clone <your-repo-url>
cd toolshop
pip install -r requirements.txt
python app.py
# open http://localhost:5000
```

With no `DATABASE_URL` set, the app uses a local SQLite file (`toolshop.db`) and seeds a starter catalog automatically.

## Database migrations

`scripts/migrate.py` manages the schema against whatever `DATABASE_URL` points to:

```bash
python scripts/migrate.py            # create tables + seed if empty (idempotent)
python scripts/migrate.py --check    # compare live DB columns to the model (drift)
python scripts/migrate.py --status   # print row counts and the active engine
python scripts/migrate.py --reset    # DROP all tables, recreate, and reseed
```

To target a remote database, pass `DATABASE_URL` inline:

```bash
DATABASE_URL="postgresql://user:pass@host/db" python scripts/migrate.py --check
```

The app also self-heals on boot: if the live table is missing columns the model expects (schema drift from an older version), it recreates the table. Initialization is guarded by an OS-level lock so multiple Gunicorn workers never run DDL concurrently.

## Deployment (Render + Postgres)

The included `render.yaml` provisions a free PostgreSQL instance, a web service, and a daily cleanup cron job, wiring `DATABASE_URL` between them automatically.

1. Push this repository to GitHub.
2. In Render, choose **New, then Blueprint**, and connect the repo. Render reads `render.yaml`, creates the database, the service, and the cron job, and injects `DATABASE_URL`.
3. Deploy. You get a public URL such as `https://<app>.onrender.com`.

If you deploy as a plain Web Service instead of a Blueprint, add a `DATABASE_URL` environment variable pointing to your Postgres instance; otherwise the app falls back to an ephemeral SQLite file that resets on every deploy. The active engine is visible at `/api/health`.

```bash
# Build:  pip install -r requirements.txt
# Start:  python scripts/migrate.py && gunicorn app:app --bind 0.0.0.0:$PORT
```

A `Dockerfile` is also provided for any container host.

## Design notes

- **Docs that stay in sync**: Swagger UI consumes the same OpenAPI document the app publishes, so the reference always matches the routes.
- **No-login ownership**: edit tokens scope writes to their creator without storing credentials, fitting a public, scriptable API.
- **Operational resilience**: automatic schema-drift repair plus a multi-worker init lock keep deploys from crash-looping on schema changes.
- **Portability**: one codebase, two databases (`DATABASE_URL`), no front-end build step.

## Author

**Keren Koresh**

Built as a portfolio project demonstrating full-stack development, REST API design, OpenAPI documentation, and cloud deployment.
