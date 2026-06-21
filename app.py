"""ToolShop - a small store API + website (inspired by practicesoftwaretesting.com).

Backend: Flask + SQLite. Serves a JSON REST API and a small web UI.
"""
import os
import sqlite3
from flask import Flask, request, jsonify, g, render_template, abort
from flask_cors import CORS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "toolshop.db"))

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
        CREATE TABLE IF NOT EXISTS products (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            description TEXT    DEFAULT '',
            price       REAL    NOT NULL DEFAULT 0,
            category    TEXT    DEFAULT '',
            in_stock    INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    # Seed once if empty
    count = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    if count == 0:
        db.executemany(
            "INSERT INTO products (name, description, price, category, in_stock) "
            "VALUES (?, ?, ?, ?, ?)",
            SEED_PRODUCTS,
        )
    db.commit()
    db.close()


SEED_PRODUCTS = [
    ("Combination Pliers", "Durable steel combination pliers for everyday use.", 14.15, "Pliers", 1),
    ("Pliers", "Standard pliers with a comfortable grip.", 12.01, "Pliers", 1),
    ("Bolt Cutters", "Heavy-duty bolt cutters for thick metal.", 48.41, "Cutters", 1),
    ("Long Nose Pliers", "Precision long nose pliers for tight spaces.", 9.17, "Pliers", 1),
    ("Slip Joint Pliers", "Adjustable slip joint pliers.", 9.61, "Pliers", 0),
    ("Claw Hammer", "Classic claw hammer with wooden handle.", 11.21, "Hammer", 1),
    ("Hammer", "All-purpose steel hammer.", 14.24, "Hammer", 1),
    ("Wood Saw", "Sharp wood saw for clean cuts.", 12.40, "Saw", 1),
    ("Adjustable Wrench", "Adjustable wrench fits multiple bolt sizes.", 20.93, "Wrench", 1),
    ("Cordless Drill 24V", "Powerful cordless drill with battery.", 87.74, "Drill", 1),
    ("Screwdriver", "Phillips head screwdriver.", 7.50, "Screwdriver", 1),
    ("Tape Measure 7.5m", "Retractable tape measure, 7.5 meters.", 12.95, "Measures", 1),
]


def row_to_dict(row):
    d = dict(row)
    d["in_stock"] = bool(d["in_stock"])
    return d


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------
@app.route("/api/products", methods=["GET"])
def list_products():
    """List products. Optional query params:
    - search: partial, case-insensitive match on name
    - id: exact product id
    """
    db = get_db()
    exact_id = request.args.get("id")
    search = request.args.get("search")

    if exact_id is not None:
        if not exact_id.isdigit():
            return jsonify({"error": "id must be a number"}), 400
        rows = db.execute("SELECT * FROM products WHERE id = ?", (int(exact_id),)).fetchall()
    elif search:
        rows = db.execute(
            "SELECT * FROM products WHERE name LIKE ? ORDER BY name",
            (f"%{search}%",),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM products ORDER BY name").fetchall()

    return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/products/<int:product_id>", methods=["GET"])
def get_product(product_id):
    db = get_db()
    row = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if row is None:
        return jsonify({"error": f"Product {product_id} not found"}), 404
    return jsonify(row_to_dict(row))


@app.route("/api/products", methods=["POST"])
def create_product():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    db = get_db()
    cur = db.execute(
        "INSERT INTO products (name, description, price, category, in_stock) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            name,
            data.get("description", ""),
            float(data.get("price", 0) or 0),
            data.get("category", ""),
            1 if data.get("in_stock", True) else 0,
        ),
    )
    db.commit()
    row = db.execute("SELECT * FROM products WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(row_to_dict(row)), 201


@app.route("/api/products/<int:product_id>", methods=["PUT", "PATCH"])
def update_product(product_id):
    db = get_db()
    row = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if row is None:
        return jsonify({"error": f"Product {product_id} not found"}), 404

    data = request.get_json(silent=True) or {}
    current = row_to_dict(row)
    name = data.get("name", current["name"])
    description = data.get("description", current["description"])
    price = data.get("price", current["price"])
    category = data.get("category", current["category"])
    in_stock = data.get("in_stock", current["in_stock"])

    db.execute(
        "UPDATE products SET name=?, description=?, price=?, category=?, in_stock=? WHERE id=?",
        (name, description, float(price or 0), category, 1 if in_stock else 0, product_id),
    )
    db.commit()
    updated = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    return jsonify(row_to_dict(updated))


@app.route("/api/products/<int:product_id>", methods=["DELETE"])
def delete_product(product_id):
    db = get_db()
    row = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if row is None:
        return jsonify({"error": f"Product {product_id} not found"}), 404
    db.execute("DELETE FROM products WHERE id = ?", (product_id,))
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


@app.route("/product/<int:product_id>")
def product_page(product_id):
    return render_template("product.html", product_id=product_id)


with app.app_context():
    init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
