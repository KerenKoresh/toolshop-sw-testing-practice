"""ToolShop - a small store API + website (inspired by practicesoftwaretesting.com).

Backend: Flask + SQLite. Serves a JSON REST API and a small web UI.
Auth: JWT (Bearer token). Every user has their own private set of products.
"""
import os
import sqlite3
import datetime
from functools import wraps

import jwt
from flask import Flask, request, jsonify, g, render_template
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "toolshop.db"))
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
TOKEN_DAYS = 7

app = Flask(__name__)
CORS(app)  # public API: allow cross-origin calls from anywhere


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            email         TEXT    NOT NULL UNIQUE,
            password_hash TEXT    NOT NULL,
            created_at    TEXT    NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            name        TEXT    NOT NULL,
            description TEXT    DEFAULT '',
            price       REAL    NOT NULL DEFAULT 0,
            category    TEXT    DEFAULT '',
            in_stock    INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    db.commit()
    db.close()


# Starter products given to each new user so their shop isn't empty.
STARTER_PRODUCTS = [
    ("Combination Pliers", "Durable steel combination pliers for everyday use.", 14.15, "Pliers", 1),
    ("Bolt Cutters", "Heavy-duty bolt cutters for thick metal.", 48.41, "Cutters", 1),
    ("Long Nose Pliers", "Precision long nose pliers for tight spaces.", 9.17, "Pliers", 1),
    ("Claw Hammer", "Classic claw hammer with wooden handle.", 11.21, "Hammer", 1),
    ("Wood Saw", "Sharp wood saw for clean cuts.", 12.40, "Saw", 1),
    ("Adjustable Wrench", "Adjustable wrench fits multiple bolt sizes.", 20.93, "Wrench", 1),
    ("Cordless Drill 24V", "Powerful cordless drill with battery.", 87.74, "Drill", 1),
    ("Tape Measure 7.5m", "Retractable tape measure, 7.5 meters.", 12.95, "Measures", 1),
]


def seed_user_products(db, user_id):
    db.executemany(
        "INSERT INTO products (user_id, name, description, price, category, in_stock) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(user_id, *p) for p in STARTER_PRODUCTS],
    )


def product_to_dict(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "price": row["price"],
        "category": row["category"],
        "in_stock": bool(row["in_stock"]),
    }


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def make_token(user_id):
    payload = {
        "user_id": user_id,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(days=TOKEN_DAYS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def auth_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return jsonify({"error": "Authentication required"}), 401
        token = header[7:]
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired, please log in again"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401
        g.user_id = payload["user_id"]
        return f(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------
@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "email and password are required"}), 400
    if len(password) < 6:
        return jsonify({"error": "password must be at least 6 characters"}), 400

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        return jsonify({"error": "email already registered"}), 409

    cur = db.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
        (email, generate_password_hash(password, method="pbkdf2:sha256"),
         datetime.datetime.utcnow().isoformat()),
    )
    user_id = cur.lastrowid
    seed_user_products(db, user_id)  # give the new user a starter catalog
    db.commit()

    return jsonify({"token": make_token(user_id), "user": {"id": user_id, "email": email}}), 201


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if user is None or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "invalid email or password"}), 401

    return jsonify(
        {"token": make_token(user["id"]), "user": {"id": user["id"], "email": user["email"]}}
    )


@app.route("/api/me", methods=["GET"])
@auth_required
def me():
    db = get_db()
    user = db.execute("SELECT id, email FROM users WHERE id = ?", (g.user_id,)).fetchone()
    if user is None:
        return jsonify({"error": "user not found"}), 404
    return jsonify({"id": user["id"], "email": user["email"]})


# ---------------------------------------------------------------------------
# Product API (all scoped to the authenticated user)
# ---------------------------------------------------------------------------
@app.route("/api/products", methods=["GET"])
@auth_required
def list_products():
    """List the current user's products. Optional query params:
    - search: partial, case-insensitive match on name
    - id: exact product id
    """
    db = get_db()
    exact_id = request.args.get("id")
    search = request.args.get("search")

    if exact_id is not None:
        if not exact_id.isdigit():
            return jsonify({"error": "id must be a number"}), 400
        rows = db.execute(
            "SELECT * FROM products WHERE id = ? AND user_id = ?",
            (int(exact_id), g.user_id),
        ).fetchall()
    elif search:
        rows = db.execute(
            "SELECT * FROM products WHERE user_id = ? AND name LIKE ? ORDER BY name",
            (g.user_id, f"%{search}%"),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM products WHERE user_id = ? ORDER BY name", (g.user_id,)
        ).fetchall()

    return jsonify([product_to_dict(r) for r in rows])


def _owned_product(db, product_id):
    """Return the product row only if it belongs to the current user, else None."""
    return db.execute(
        "SELECT * FROM products WHERE id = ? AND user_id = ?", (product_id, g.user_id)
    ).fetchone()


@app.route("/api/products/<int:product_id>", methods=["GET"])
@auth_required
def get_product(product_id):
    db = get_db()
    row = _owned_product(db, product_id)
    if row is None:
        return jsonify({"error": f"Product {product_id} not found"}), 404
    return jsonify(product_to_dict(row))


@app.route("/api/products", methods=["POST"])
@auth_required
def create_product():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    db = get_db()
    cur = db.execute(
        "INSERT INTO products (user_id, name, description, price, category, in_stock) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            g.user_id,
            name,
            data.get("description", ""),
            float(data.get("price", 0) or 0),
            data.get("category", ""),
            1 if data.get("in_stock", True) else 0,
        ),
    )
    db.commit()
    row = db.execute("SELECT * FROM products WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(product_to_dict(row)), 201


@app.route("/api/products/<int:product_id>", methods=["PUT", "PATCH"])
@auth_required
def update_product(product_id):
    db = get_db()
    row = _owned_product(db, product_id)
    if row is None:
        return jsonify({"error": f"Product {product_id} not found"}), 404

    data = request.get_json(silent=True) or {}
    current = product_to_dict(row)
    name = data.get("name", current["name"])
    description = data.get("description", current["description"])
    price = data.get("price", current["price"])
    category = data.get("category", current["category"])
    in_stock = data.get("in_stock", current["in_stock"])

    db.execute(
        "UPDATE products SET name=?, description=?, price=?, category=?, in_stock=? "
        "WHERE id=? AND user_id=?",
        (name, description, float(price or 0), category, 1 if in_stock else 0,
         product_id, g.user_id),
    )
    db.commit()
    updated = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    return jsonify(product_to_dict(updated))


@app.route("/api/products/<int:product_id>", methods=["DELETE"])
@auth_required
def delete_product(product_id):
    db = get_db()
    row = _owned_product(db, product_id)
    if row is None:
        return jsonify({"error": f"Product {product_id} not found"}), 404
    db.execute("DELETE FROM products WHERE id = ? AND user_id = ?", (product_id, g.user_id))
    db.commit()
    return jsonify({"message": f"Product {product_id} deleted"})


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Web UI routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login")
def login_page():
    return render_template("login.html")


@app.route("/product/<int:product_id>")
def product_page(product_id):
    return render_template("product.html", product_id=product_id)


@app.route("/admin")
def admin_page():
    return render_template("admin.html")


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

CREDENTIALS = {
    "type": "object",
    "required": ["email", "password"],
    "properties": {
        "email": {"type": "string", "example": "user@example.com"},
        "password": {"type": "string", "example": "secret123"},
    },
}

_secured = [{"bearerAuth": []}]

OPENAPI = {
    "openapi": "3.0.3",
    "info": {
        "title": "ToolShop API",
        "version": "2.0.0",
        "description": "Public REST API for the ToolShop store. Register or log in to get a "
        "JWT, then send it as `Authorization: Bearer <token>`. Every user has a private "
        "set of products.",
    },
    "servers": [{"url": "/", "description": "This server"}],
    "tags": [
        {"name": "Auth", "description": "Register & log in"},
        {"name": "Products", "description": "Manage and search your products"},
    ],
    "paths": {
        "/api/register": {
            "post": {
                "tags": ["Auth"],
                "summary": "Register a new user",
                "description": "Creates a user, seeds a starter catalog, and returns a JWT.",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": CREDENTIALS}},
                },
                "responses": {
                    "201": {"description": "Created; returns token + user"},
                    "400": {"description": "Missing fields / weak password"},
                    "409": {"description": "Email already registered"},
                },
            }
        },
        "/api/login": {
            "post": {
                "tags": ["Auth"],
                "summary": "Log in",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": CREDENTIALS}},
                },
                "responses": {
                    "200": {"description": "Returns token + user"},
                    "401": {"description": "Invalid email or password"},
                },
            }
        },
        "/api/me": {
            "get": {
                "tags": ["Auth"],
                "summary": "Current user",
                "security": _secured,
                "responses": {
                    "200": {"description": "The logged-in user"},
                    "401": {"description": "Not authenticated"},
                },
            }
        },
        "/api/products": {
            "get": {
                "tags": ["Products"],
                "summary": "List / search your products",
                "security": _secured,
                "parameters": [
                    {"name": "search", "in": "query", "required": False,
                     "schema": {"type": "string"}, "example": "plier",
                     "description": "Partial, case-insensitive name match."},
                    {"name": "id", "in": "query", "required": False,
                     "schema": {"type": "integer"}, "example": 3,
                     "description": "Exact product id (returns 0 or 1 items)."},
                ],
                "responses": {
                    "200": {"description": "A list of your products",
                            "content": {"application/json": {
                                "schema": {"type": "array", "items": PRODUCT_SCHEMA}}}},
                    "400": {"description": "id is not a number"},
                    "401": {"description": "Not authenticated"},
                },
            },
            "post": {
                "tags": ["Products"],
                "summary": "Create a product",
                "security": _secured,
                "requestBody": {"required": True,
                                "content": {"application/json": {"schema": PRODUCT_INPUT}}},
                "responses": {
                    "201": {"description": "Created",
                            "content": {"application/json": {"schema": PRODUCT_SCHEMA}}},
                    "400": {"description": "name is required"},
                    "401": {"description": "Not authenticated"},
                },
            },
        },
        "/api/products/{id}": {
            "parameters": [
                {"name": "id", "in": "path", "required": True,
                 "schema": {"type": "integer"}, "example": 3}
            ],
            "get": {
                "tags": ["Products"], "summary": "Get one of your products",
                "security": _secured,
                "responses": {
                    "200": {"description": "The product",
                            "content": {"application/json": {"schema": PRODUCT_SCHEMA}}},
                    "401": {"description": "Not authenticated"},
                    "404": {"description": "Not found (or not yours)"},
                },
            },
            "put": {
                "tags": ["Products"], "summary": "Update one of your products",
                "security": _secured,
                "requestBody": {"required": True,
                                "content": {"application/json": {"schema": PRODUCT_INPUT}}},
                "responses": {
                    "200": {"description": "Updated",
                            "content": {"application/json": {"schema": PRODUCT_SCHEMA}}},
                    "401": {"description": "Not authenticated"},
                    "404": {"description": "Not found (or not yours)"},
                },
            },
            "delete": {
                "tags": ["Products"], "summary": "Delete one of your products",
                "security": _secured,
                "responses": {
                    "200": {"description": "Deleted"},
                    "401": {"description": "Not authenticated"},
                    "404": {"description": "Not found (or not yours)"},
                },
            },
        },
        "/api/health": {
            "get": {"tags": ["Products"], "summary": "Health check",
                    "responses": {"200": {"description": "Service is up"}}}
        },
    },
    "components": {
        "securitySchemes": {
            "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
        },
        "schemas": {
            "Product": PRODUCT_SCHEMA,
            "ProductInput": PRODUCT_INPUT,
            "Credentials": CREDENTIALS,
        },
    },
}


with app.app_context():
    init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
