"""ToolShop - a small store API + website.

Backend: Flask + SQLAlchemy. Works with Postgres (set DATABASE_URL) or SQLite (default).
Serves a JSON REST API and a small web UI.

No user accounts. The catalog is public to read. When you create a product the API
returns a one-time secret `edit_token`; that token (sent as the `X-Edit-Token` header)
is required to update or delete that specific product. Only its hash is stored.
"""
import os
import hmac
import json
import uuid
import secrets
import hashlib
import contextlib

from flask import Flask, request, jsonify, g, render_template
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import create_engine, Integer, String, Float, Boolean, Text, select, func, inspect
from sqlalchemy.orm import declarative_base, sessionmaker, Mapped, mapped_column

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# DATABASE_URL: Postgres in production (Render), SQLite by default for local dev.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "toolshop.db"))
# Render exposes the legacy "postgres://" scheme; SQLAlchemy needs "postgresql://".
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Secret for the maintenance cleanup endpoint. If unset, the endpoint stays disabled.
CLEANUP_TOKEN = os.environ.get("CLEANUP_TOKEN", "")

# SQLite needs a special flag for multi-threaded gunicorn workers.
engine_kwargs = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()

app = Flask(__name__)
CORS(app)  # public API: allow cross-origin calls from anywhere

# Rate limiting. In-memory by default (per worker); use Redis for multi-instance prod.
RATE_LIMIT = os.environ.get("RATE_LIMIT", "240 per minute")
RATE_LIMIT_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "true").lower() != "false"
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[RATE_LIMIT],
    headers_enabled=True,           # adds X-RateLimit-* headers
    enabled=RATE_LIMIT_ENABLED,
    storage_uri="memory://",
)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    category: Mapped[str] = mapped_column(String(100), default="")
    in_stock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # NULL = baseline catalog item (visible to all, editable by no one)
    edit_token_hash: Mapped[str] = mapped_column(String(64), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description or "",
            "price": self.price,
            "category": self.category or "",
            "in_stock": bool(self.in_stock),
            "editable": self.edit_token_hash is not None,
        }


# Baseline catalog (no edit token -> visible to all, editable by no one).
SEED_PRODUCTS = [
    ("Combination Pliers", "Durable steel combination pliers for everyday use.", 14.15, "Pliers"),
    ("Bolt Cutters", "Heavy-duty bolt cutters for thick metal.", 48.41, "Cutters"),
    ("Long Nose Pliers", "Precision long nose pliers for tight spaces.", 9.17, "Pliers"),
    ("Claw Hammer", "Classic claw hammer with wooden handle.", 11.21, "Hammer"),
    ("Wood Saw", "Sharp wood saw for clean cuts.", 12.40, "Saw"),
    ("Adjustable Wrench", "Adjustable wrench fits multiple bolt sizes.", 20.93, "Wrench"),
    ("Cordless Drill 24V", "Powerful cordless drill with battery.", 87.74, "Drill"),
    ("Tape Measure 7.5m", "Retractable tape measure, 7.5 meters.", 12.95, "Measures"),
]


@contextlib.contextmanager
def _init_lock():
    """Serialize DB initialization across gunicorn workers (same host).

    Without this, multiple workers can run create_all / drop_all concurrently and
    crash with 'table already exists'. We use an OS file lock, which works for both
    SQLite and Postgres deployments. Falls back to no-op where fcntl is unavailable.
    """
    try:
        import fcntl
    except ImportError:  # e.g. Windows local dev
        yield
        return
    lock_path = os.path.join(BASE_DIR, ".init.lock")
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def init_db():
    with _init_lock():
        Base.metadata.create_all(engine, checkfirst=True)
        _repair_schema_drift()
        with SessionLocal() as s:
            count = s.scalar(select(func.count()).select_from(Product))
            if count == 0:
                s.add_all(
                    Product(name=n, description=d, price=p, category=c, in_stock=True,
                            edit_token_hash=None)
                    for (n, d, p, c) in SEED_PRODUCTS
                )
                s.commit()


def _repair_schema_drift():
    """If the existing `products` table is missing columns the model expects
    (e.g. it was created by an older app version), recreate it.

    create_all() never ALTERs an existing table, so a schema left over from a
    previous version would make every query fail. This keeps the app self-healing.
    Destructive: the catalog is re-seeded afterwards by init_db().
    """
    insp = inspect(engine)
    if not insp.has_table("products"):
        return
    actual = {c["name"] for c in insp.get_columns("products")}
    expected = set(Product.__table__.columns.keys())
    missing = expected - actual
    if missing:
        print(f"[init_db] schema drift detected (missing {sorted(missing)}); "
              f"recreating 'products' table")
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def delete_user_products():
    """Delete every product created through the API, keeping only the baseline
    catalog (rows with no edit token). Returns the number of rows removed.

    Used by the daily cleanup job to keep the database small.
    """
    with SessionLocal() as s:
        rows = s.scalars(
            select(Product).where(Product.edit_token_hash.is_not(None))
        ).all()
        removed = len(rows)
        for row in rows:
            s.delete(row)
        s.commit()
    return removed


# ---------------------------------------------------------------------------
# Per-request session
# ---------------------------------------------------------------------------
def get_session():
    if "session" not in g:
        g.session = SessionLocal()
    return g.session


@app.teardown_appcontext
def close_session(exception):
    s = g.pop("session", None)
    if s is not None:
        s.close()


# ---------------------------------------------------------------------------
# Cross-cutting: request id, error envelope, rate-limit handler
# ---------------------------------------------------------------------------
@app.before_request
def _assign_request_id():
    # Honour a client-supplied id for correlation, otherwise generate one.
    g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex


@app.after_request
def _finalize_response(response):
    rid = g.get("request_id")
    if rid:
        response.headers["X-Request-ID"] = rid
        # Enrich JSON error bodies with a consistent envelope.
        if response.is_json:
            body = response.get_json(silent=True)
            if isinstance(body, dict) and "error" in body and "request_id" not in body:
                body["request_id"] = rid
                body["status"] = response.status_code
                response.set_data(json.dumps(body))
    return response


@app.errorhandler(429)
def _rate_limited(e):
    return jsonify({"error": f"rate limit exceeded ({e.description})"}), 429


@app.errorhandler(404)
def _not_found(e):
    # JSON for API paths, default behaviour elsewhere.
    if request.path.startswith("/api/"):
        return jsonify({"error": "not found"}), 404
    return e


@app.errorhandler(405)
def _method_not_allowed(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "method not allowed"}), 405
    return e


def product_etag(p):
    """A strong-ish ETag derived from the product's current content."""
    raw = f"{p.id}:{p.name}:{p.description}:{p.price}:{p.category}:{int(bool(p.in_stock))}"
    return '"' + hashlib.md5(raw.encode("utf-8")).hexdigest() + '"'


# ---------------------------------------------------------------------------
# Product API
# ---------------------------------------------------------------------------
ALLOWED_SORT = {"id", "name", "price"}
MAX_ID = 2_147_483_647  # the Integer primary key is 32-bit; anything beyond can't exist


def fetch_owned_or_none(s, product_id):
    """Fetch a product by id, treating out-of-range ids as 'not found' instead of
    letting the DB driver overflow."""
    if product_id < 1 or product_id > MAX_ID:
        return None
    return s.get(Product, product_id)


@app.route("/api/products", methods=["GET"])
def list_products():
    """List products with optional search, filtering, sorting and pagination.

    Query params: search, id, category, in_stock, sort (e.g. name or -price),
    limit (1-100, default 50), offset (>=0). Adds an X-Total-Count header.
    """
    s = get_session()
    args = request.args

    # Exact-id lookup keeps its simple, list-returning behaviour.
    exact_id = args.get("id")
    if exact_id is not None:
        if not exact_id.isdigit():
            return jsonify({"error": "id must be a number"}), 400
        value = int(exact_id)
        rows = [] if value > MAX_ID else s.scalars(
            select(Product).where(Product.id == value)
        ).all()
        resp = jsonify([p.to_dict() for p in rows])
        resp.headers["X-Total-Count"] = str(len(rows))
        return resp

    # Filters
    conds = []
    if args.get("search"):
        conds.append(Product.name.ilike(f"%{args['search']}%"))
    if args.get("category"):
        conds.append(func.lower(Product.category) == args["category"].lower())
    in_stock = args.get("in_stock")
    if in_stock is not None:
        v = in_stock.lower()
        if v not in ("true", "false"):
            return jsonify({"error": "in_stock must be true or false"}), 400
        conds.append(Product.in_stock.is_(v == "true"))

    # Sorting
    sort = args.get("sort", "id")
    descending = sort.startswith("-")
    field = sort[1:] if descending else sort
    if field not in ALLOWED_SORT:
        return jsonify({"error": f"invalid sort '{sort}'; allowed: id, name, price"}), 400
    col = getattr(Product, field)

    # Pagination
    try:
        limit = int(args.get("limit", 50))
        offset = int(args.get("offset", 0))
    except ValueError:
        return jsonify({"error": "limit and offset must be integers"}), 400
    if not (1 <= limit <= 100):
        return jsonify({"error": "limit must be between 1 and 100"}), 400
    if not (0 <= offset <= MAX_ID):
        return jsonify({"error": f"offset must be between 0 and {MAX_ID}"}), 400

    base = select(Product)
    count_q = select(func.count()).select_from(Product)
    for c in conds:
        base = base.where(c)
        count_q = count_q.where(c)

    total = s.scalar(count_q)
    base = base.order_by(col.desc() if descending else col.asc()).limit(limit).offset(offset)
    rows = s.scalars(base).all()

    resp = jsonify([p.to_dict() for p in rows])
    resp.headers["X-Total-Count"] = str(total)
    return resp


@app.route("/api/products/<int:product_id>", methods=["GET"])
def get_product(product_id):
    s = get_session()
    p = fetch_owned_or_none(s, product_id)
    if p is None:
        return jsonify({"error": f"Product {product_id} not found"}), 404

    etag = product_etag(p)
    if request.headers.get("If-None-Match") == etag:
        resp = app.response_class(status=304)
        resp.headers["ETag"] = etag
        return resp
    resp = jsonify(p.to_dict())
    resp.headers["ETag"] = etag
    return resp


def _precondition_failed(p):
    """Optimistic concurrency: if the client sent If-Match and it no longer matches,
    the product changed underneath them. Returns a 412 response or None."""
    if_match = request.headers.get("If-Match")
    if if_match and if_match != product_etag(p):
        return jsonify({"error": "If-Match precondition failed; the product was modified"}), 412
    return None


@app.route("/api/products", methods=["POST"])
def create_product():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    token = secrets.token_urlsafe(24)  # the one-time secret returned to the creator
    s = get_session()
    p = Product(
        name=name,
        description=data.get("description", ""),
        price=float(data.get("price", 0) or 0),
        category=data.get("category", ""),
        in_stock=bool(data.get("in_stock", True)),
        edit_token_hash=hash_token(token),
    )
    s.add(p)
    s.commit()
    result = p.to_dict()
    result["edit_token"] = token  # shown ONCE; needed for future update/delete
    return jsonify(result), 201


def _require_edit_token(product):
    """Return None if the request may edit `product`, else an (response, status) tuple."""
    if product.edit_token_hash is None:
        return jsonify({"error": "This product is part of the baseline catalog and cannot be edited"}), 403
    token = request.headers.get("X-Edit-Token", "")
    if not token:
        return jsonify({"error": "X-Edit-Token header is required to modify this product"}), 401
    if hash_token(token) != product.edit_token_hash:
        return jsonify({"error": "Invalid edit token for this product"}), 403
    return None


@app.route("/api/products/<int:product_id>", methods=["PUT", "PATCH"])
def update_product(product_id):
    s = get_session()
    p = fetch_owned_or_none(s, product_id)
    if p is None:
        return jsonify({"error": f"Product {product_id} not found"}), 404

    denied = _require_edit_token(p)
    if denied is not None:
        return denied
    stale = _precondition_failed(p)
    if stale is not None:
        return stale

    data = request.get_json(silent=True) or {}
    p.name = data.get("name", p.name)
    p.description = data.get("description", p.description)
    p.price = float(data.get("price", p.price) or 0)
    p.category = data.get("category", p.category)
    if "in_stock" in data:
        p.in_stock = bool(data["in_stock"])
    s.commit()
    resp = jsonify(p.to_dict())
    resp.headers["ETag"] = product_etag(p)
    return resp


@app.route("/api/products/<int:product_id>", methods=["DELETE"])
def delete_product(product_id):
    s = get_session()
    p = fetch_owned_or_none(s, product_id)
    if p is None:
        return jsonify({"error": f"Product {product_id} not found"}), 404

    denied = _require_edit_token(p)
    if denied is not None:
        return denied
    stale = _precondition_failed(p)
    if stale is not None:
        return stale

    s.delete(p)
    s.commit()
    return jsonify({"message": f"Product {product_id} deleted"})


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "db": engine.dialect.name})


@app.route("/api/maintenance/cleanup", methods=["POST", "GET"])
def maintenance_cleanup():
    """Delete API-created products, keeping the baseline catalog.

    Protected by a shared secret so an external scheduler (e.g. cron-job.org) can
    trigger the daily cleanup. Disabled unless the CLEANUP_TOKEN env var is set.
    Token may be passed as the `X-Cleanup-Token` header or a `?token=` query param.
    """
    if not CLEANUP_TOKEN:
        return jsonify({"error": "cleanup endpoint disabled (CLEANUP_TOKEN not set)"}), 503
    provided = request.headers.get("X-Cleanup-Token") or request.args.get("token", "")
    if not hmac.compare_digest(provided, CLEANUP_TOKEN):
        return jsonify({"error": "unauthorized"}), 401
    removed = delete_user_products()
    return jsonify({"message": "cleanup done", "removed": removed})


# ---------------------------------------------------------------------------
# Web UI routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/product/<int:product_id>")
def product_page(product_id):
    return render_template("product.html", product_id=product_id)


@app.route("/guide")
def guide_page():
    return render_template("guide.html")


@app.route("/playground")
def playground_page():
    return render_template("playground.html")


@app.route("/docs")
def docs_page():
    return render_template("docs.html")


@app.route("/api/openapi.json")
def openapi_spec():
    return jsonify(OPENAPI)


# ---------------------------------------------------------------------------
# OpenAPI spec (drives the Swagger UI at /docs)
# ---------------------------------------------------------------------------
PRODUCT_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer", "example": 1},
        "name": {"type": "string", "example": "Combination Pliers"},
        "description": {"type": "string", "example": "Durable steel pliers."},
        "price": {"type": "number", "format": "float", "example": 14.15},
        "category": {"type": "string", "example": "Pliers"},
        "in_stock": {"type": "boolean", "example": True},
        "editable": {"type": "boolean", "example": True},
    },
}

PRODUCT_INPUT = {
    "type": "object",
    "required": ["name"],
    "properties": {
        "name": {"type": "string", "example": "Rubber Mallet"},
        "description": {"type": "string", "example": "Soft mallet"},
        "price": {"type": "number", "example": 9.9},
        "category": {"type": "string", "example": "Hammer"},
        "in_stock": {"type": "boolean", "example": True},
    },
}

EDIT_TOKEN_HEADER = {
    "name": "X-Edit-Token",
    "in": "header",
    "required": True,
    "schema": {"type": "string"},
    "description": "The secret token returned when this product was created.",
}

IF_MATCH_HEADER = {
    "name": "If-Match",
    "in": "header",
    "required": False,
    "schema": {"type": "string"},
    "description": "The product's ETag, for optimistic concurrency (412 if stale).",
}

OPENAPI = {
    "openapi": "3.0.3",
    "info": {
        "title": "ToolShop API",
        "version": "4.0.0",
        "description": "Public REST API for the ToolShop store. Anyone can read and create. "
        "Creating a product returns a one-time `edit_token`; send it as the `X-Edit-Token` "
        "header to update or delete that product. No user accounts.",
    },
    "servers": [{"url": "/", "description": "This server"}],
    "tags": [{"name": "Products", "description": "Manage and search products"}],
    "paths": {
        "/api/products": {
            "get": {
                "tags": ["Products"],
                "summary": "List / search products",
                "description": "Search, filter, sort and paginate. The response includes an "
                "`X-Total-Count` header with the total number of matches.",
                "parameters": [
                    {"name": "search", "in": "query", "required": False,
                     "schema": {"type": "string"}, "example": "plier",
                     "description": "Partial, case-insensitive name match."},
                    {"name": "id", "in": "query", "required": False,
                     "schema": {"type": "integer"}, "example": 3,
                     "description": "Exact product id (returns 0 or 1 items)."},
                    {"name": "category", "in": "query", "required": False,
                     "schema": {"type": "string"}, "example": "Pliers",
                     "description": "Exact, case-insensitive category filter."},
                    {"name": "in_stock", "in": "query", "required": False,
                     "schema": {"type": "boolean"}, "description": "Filter by stock status."},
                    {"name": "sort", "in": "query", "required": False,
                     "schema": {"type": "string", "enum": ["id", "-id", "name", "-name", "price", "-price"]},
                     "description": "Sort field; prefix with '-' for descending."},
                    {"name": "limit", "in": "query", "required": False,
                     "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}},
                    {"name": "offset", "in": "query", "required": False,
                     "schema": {"type": "integer", "minimum": 0, "default": 0}},
                ],
                "responses": {
                    "200": {"description": "A list of products",
                            "content": {"application/json": {
                                "schema": {"type": "array", "items": PRODUCT_SCHEMA}}}},
                    "400": {"description": "id is not a number"},
                },
            },
            "post": {
                "tags": ["Products"],
                "summary": "Create a product",
                "description": "Returns the product plus a one-time `edit_token`. Save it, "
                "as it is required (and only shown once) to edit or delete this product.",
                "requestBody": {"required": True,
                                "content": {"application/json": {"schema": PRODUCT_INPUT}}},
                "responses": {
                    "201": {"description": "Created (includes edit_token)"},
                    "400": {"description": "name is required"},
                },
            },
        },
        "/api/products/{id}": {
            "parameters": [
                {"name": "id", "in": "path", "required": True,
                 "schema": {"type": "integer"}, "example": 9}
            ],
            "get": {
                "tags": ["Products"], "summary": "Get one product",
                "description": "Returns an `ETag` header. Send it back as `If-None-Match` "
                "to get a `304 Not Modified` when unchanged.",
                "parameters": [
                    {"name": "If-None-Match", "in": "header", "required": False,
                     "schema": {"type": "string"}, "description": "Conditional GET."},
                ],
                "responses": {
                    "200": {"description": "The product",
                            "content": {"application/json": {"schema": PRODUCT_SCHEMA}}},
                    "304": {"description": "Not modified (ETag matched)"},
                    "404": {"description": "Not found"},
                },
            },
            "put": {
                "tags": ["Products"], "summary": "Update a product (needs edit token)",
                "description": "Optionally send `If-Match` with the product's ETag for "
                "optimistic concurrency; a stale ETag yields `412`.",
                "parameters": [EDIT_TOKEN_HEADER, IF_MATCH_HEADER],
                "requestBody": {"required": True,
                                "content": {"application/json": {"schema": PRODUCT_INPUT}}},
                "responses": {
                    "200": {"description": "Updated",
                            "content": {"application/json": {"schema": PRODUCT_SCHEMA}}},
                    "401": {"description": "Missing edit token"},
                    "403": {"description": "Wrong token / baseline item"},
                    "404": {"description": "Not found"},
                    "412": {"description": "If-Match precondition failed"},
                },
            },
            "delete": {
                "tags": ["Products"], "summary": "Delete a product (needs edit token)",
                "parameters": [EDIT_TOKEN_HEADER, IF_MATCH_HEADER],
                "responses": {
                    "200": {"description": "Deleted"},
                    "401": {"description": "Missing edit token"},
                    "403": {"description": "Wrong token / baseline item"},
                    "404": {"description": "Not found"},
                    "412": {"description": "If-Match precondition failed"},
                },
            },
        },
        "/api/health": {
            "get": {"tags": ["Products"], "summary": "Health check",
                    "responses": {"200": {"description": "Service is up"}}}
        },
    },
    "components": {
        "schemas": {"Product": PRODUCT_SCHEMA, "ProductInput": PRODUCT_INPUT}
    },
}


with app.app_context():
    init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
