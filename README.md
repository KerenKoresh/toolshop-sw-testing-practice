# ToolShop

A small but complete full-stack demo store — a public web catalog backed by a fully documented REST API. Inspired by [practicesoftwaretesting.com](https://practicesoftwaretesting.com), it's built to be a clean, realistic target for API testing, automation practice, and front-end/back-end demos.

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

There are no user accounts. Reading and searching are open to everyone; modifying a product is protected by a per-item secret (see [Ownership model](#ownership-model-edit-tokens)). All write operations are performed through the API only — the website is intentionally read-only.

## Features

- **Public REST API** with full CRUD over JSON and permissive CORS.
- **Search** by partial, case-insensitive name (`?search=`) or exact ID (`?id=`).
- **Interactive API docs** — a complete OpenAPI 3.0 spec rendered with Swagger UI, including "Try it out".
- **Token-based ownership** — no logins; creating a product returns a one-time secret required to edit or delete it. Only a hash is stored.
- **Database-agnostic** — runs on PostgreSQL in production and SQLite locally, selected automatically via `DATABASE_URL`.
- **Self-healing schema** — detects and repairs schema drift on boot, with a concurrency-safe, multi-worker initialization lock.
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

```
Browser ──► Flask ──► SQLAlchemy ──► PostgreSQL / SQLite
   │           │
   │           ├── HTML pages:  /  /product/<id>  /guide  /docs
   │           └── JSON API:    /api/products ...  +  /api/openapi.json
   └── Swagger UI (loads the OpenAPI spec and calls the same API)
```

A single Flask app serves both the rendered pages and the JSON API. The OpenAPI document is generated in Python and exposed at `/api/openapi.json`; the `/docs` page is a thin Swagger UI shell that consumes it, so the docs can never drift from the routes.

## API reference

Base URL: `/api`. All responses are JSON. Reads and creates are open; updates and deletes require the product's edit token in the `X-Edit-Token` header.

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/products` | — | List all products |
| `GET` | `/api/products?search=plier` | — | Search by partial, case-insensitive name |
| `GET` | `/api/products?id=3` | — | Exact ID lookup (returns a list of 0–1 items) |
| `GET` | `/api/products/{id}` | — | Get a single product by ID |
| `POST` | `/api/products` | — | Create a product (returns a one-time `edit_token`) |
| `PUT` / `PATCH` | `/api/products/{id}` | `X-Edit-Token` | Update a product |
| `DELETE` | `/api/products/{id}` | `X-Edit-Token` | Delete a product |
| `GET` | `/api/health` | — | Health check (reports the active DB engine) |

### Examples

```bash
# Create — keep the edit_token from the response
curl -X POST https://kerens-software-testing-practice.onrender.com/api/products \
  -H "Content-Type: application/json" \
  -d '{"name":"Rubber Mallet","price":9.9,"category":"Hammer"}'
# -> { "id": 13, ..., "edit_token": "AbC123..." }

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

The API is public, so anyone can create products — but a product should only be editable by whoever created it, without forcing accounts and passwords. ToolShop solves this with **per-item edit tokens**:

1. `POST /api/products` generates a random secret and returns it once as `edit_token`. Only its SHA-256 hash is persisted.
2. `PUT` / `DELETE` require that token in the `X-Edit-Token` header; the server compares hashes.
3. Seeded baseline products have no token and are therefore read-only.

This keeps the API open and scriptable while still scoping mutations to their creator — a pragmatic alternative to full authentication for a public demo.

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

## Project structure

```
toolshop/
├── app.py                 # Flask app: API, pages, OpenAPI spec, DB init
├── scripts/
│   └── migrate.py         # Schema bootstrap, drift check, and reset
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
├── render.yaml            # Render Blueprint (web service + Postgres)
└── README.md
```

## Running locally

Requires Python 3.10+.

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

The included `render.yaml` provisions a free PostgreSQL instance and a web service, wiring `DATABASE_URL` between them automatically.

1. Push this repository to GitHub.
2. In Render, choose **New → Blueprint** and connect the repo. Render reads `render.yaml`, creates the database and the service, and injects `DATABASE_URL`.
3. Deploy. You'll get a public URL such as `https://<app>.onrender.com`.

If you deploy as a plain Web Service instead of a Blueprint, add a `DATABASE_URL` environment variable pointing to your Postgres instance — otherwise the app falls back to an ephemeral SQLite file that resets on every deploy. The active engine is visible at `/api/health`.

```bash
# Build:  pip install -r requirements.txt
# Start:  python scripts/migrate.py && gunicorn app:app --bind 0.0.0.0:$PORT
```

A `Dockerfile` is also provided for any container host.

## Design notes

- **Docs that can't lie** — Swagger UI consumes the same OpenAPI document the app publishes, so the reference always matches the routes.
- **No-login ownership** — edit tokens scope writes to their creator without storing credentials, fitting a public, scriptable API.
- **Operational resilience** — automatic schema-drift repair plus a multi-worker init lock keep deploys from crash-looping on schema changes.
- **Portability** — one codebase, two databases (`DATABASE_URL`), no front-end build step.

## Author

**Keren Koresh**

Built as a portfolio project demonstrating full-stack development, REST API design, OpenAPI documentation, and cloud deployment.
