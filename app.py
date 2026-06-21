"""ToolShop - a small store API + website.

Backend: Flask + SQLAlchemy. Works with Postgres (set DATABASE_URL) or SQLite (default).
Serves a JSON REST API and a small web UI.

No user accounts. The catalog is public to read. When you create a product the API
returns a one-time secret `edit_token`; that token (sent as the `X-Edit-Token` header)
is required to update or delete that specific product. Only its hash is stored.
"""
import os
import secrets
import hashlib
import contextlib

from flask import Flask, request, jsonify, g, render_template
from flask_cors import CORS
from sqlalchemy import create_engine, Integer, String, Float, Boolean, Text, select, func, inspect
from sqlalchemy.orm import declarative_base, sessionmaker, Mapped, mapped_column

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# DATABASE_URL: Postgres in production (Render), SQLite by default for local dev.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "toolshop.db"))
# Render exposes the legacy "postgres://" scheme; SQLAlchemy needs "postgresql://".
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# SQLite needs a special flag for multi-threaded gunicorn workers.
engine_kwargs = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()

app = Flask(__name__)
CORS(app)  # public API: allow cross-origin calls from anywhere


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
# Product API
# ---------------------------------------------------------------------------
@app.route("/api/products", methods=["GET"])
def list_products():
    """List products. Optional query params:
    - search: partial, case-insensitive match on name
    - id: exact product id
    """
    s = get_session()
    exact_id = request.args.get("id")
    search = request.args.get("search")

    if exact_id is not None:
        if not exact_id.isdigit():
            return jsonify({"error": "id must be a number"}), 400
        rows = s.scalars(select(Product).where(Product.id == int(exact_id))).all()
    elif search:
        rows = s.scalars(
            select(Product).where(Product.name.ilike(f"%{search}%")).order_by(Product.name)
        ).all()
    else:
        rows = s.scalars(select(Product).order_by(Product.name)).all()

    return jsonify([p.to_dict() for p in rows])


@app.route("/api/products/<int:product_id>", methods=["GET"])
def get_product(product_id):
    s = get_session()
    p = s.get(Product, product_id)
    if p is None:
        return jsonify({"error": f"Product {product_id} not found"}), 404
    return jsonify(p.to_dict())


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
    p = s.get(Product, product_id)
    if p is None:
        return jsonify({"error": f"Product {product_id} not found"}), 404

    denied = _require_edit_token(p)
    if denied is not None:
        return denied

    data = request.get_json(silent=True) or {}
    p.name = data.get("name", p.name)
    p.description = data.get("description", p.description)
    p.price = float(data.get("price", p.price) or 0)
    p.category = data.get("category", p.category)
    if "in_stock" in data:
        p.in_stock = bool(data["in_stock"])
    s.commit()
    return jsonify(p.to_dict())


@app.route("/api/products/<int:product_id>", methods=["DELETE"])
def delete_product(product_id):
    s = get_session()
    p = s.get(Product, product_id)
    if p is None:
        return jsonify({"error": f"Product {product_id} not found"}), 404

    denied = _require_edit_token(p)
    if denied is not None:
        return denied

    s.delete(p)
    s.commit()
    return jsonify({"message": f"Product {product_id} deleted"})


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "db": engine.dialect.name})


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

OPENAPI = {
    "openapi": "3.0.3",
    "info": {
        "title": "ToolShop API",
        "version": "3.0.0",
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
                "description": "All products. Use `search` for a partial (case-insensitive) "
                "name match, or `id` for an exact id.",
                "parameters": [
                    {"name": "search", "in": "query", "required": False,
                     "schema": {"type": "string"}, "example": "plier",
                     "description": "Partial, case-insensitive name match."},
                    {"name": "id", "in": "query", "required": False,
                     "schema": {"type": "integer"}, "example": 3,
                     "description": "Exact product id (returns 0 or 1 items)."},
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
                "responses": {
                    "200": {"description": "The product",
                            "content": {"application/json": {"schema": PRODUCT_SCHEMA}}},
                    "404": {"description": "Not found"},
                },
            },
            "put": {
                "tags": ["Products"], "summary": "Update a product (needs edit token)",
                "parameters": [EDIT_TOKEN_HEADER],
                "requestBody": {"required": True,
                                "content": {"application/json": {"schema": PRODUCT_INPUT}}},
                "responses": {
                    "200": {"description": "Updated",
                            "content": {"application/json": {"schema": PRODUCT_SCHEMA}}},
                    "401": {"description": "Missing edit token"},
                    "403": {"description": "Wrong token / baseline item"},
                    "404": {"description": "Not found"},
                },
            },
            "delete": {
                "tags": ["Products"], "summary": "Delete a product (needs edit token)",
                "parameters": [EDIT_TOKEN_HEADER],
                "responses": {
                    "200": {"description": "Deleted"},
                    "401": {"description": "Missing edit token"},
                    "403": {"description": "Wrong token / baseline item"},
                    "404": {"description": "Not found"},
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
