#!/usr/bin/env python3
"""Database migration / bootstrap for ToolShop.

Creates the schema (and seeds the baseline catalog if empty) on whatever database
DATABASE_URL points to: Postgres in production, SQLite locally.

Usage:
    python scripts/migrate.py            # create tables + seed if empty (idempotent)
    python scripts/migrate.py --check    # compare real DB columns to the model (drift)
    python scripts/migrate.py --reset    # DROP all tables, recreate, reseed
    python scripts/migrate.py --status   # print the current state

Run it once after provisioning a fresh Postgres, or any time you want to reset.
To target a remote DB, set DATABASE_URL, e.g.:
    DATABASE_URL="postgresql://...external..." python scripts/migrate.py --check
"""
import os
import sys
import argparse

# Make the app package importable when run as `python scripts/migrate.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func, inspect
import app as toolshop  # importing also ensures tables exist (app.init_db runs on import)


def check():
    """Report whether the real 'products' table matches the model (schema drift)."""
    insp = inspect(toolshop.engine)
    print(f"Database : {toolshop.engine.url.render_as_string(hide_password=True)}")
    print(f"Dialect  : {toolshop.engine.dialect.name}")
    if not insp.has_table("products"):
        print("Table 'products' does NOT exist. Run: python scripts/migrate.py")
        return False
    actual = {c["name"] for c in insp.get_columns("products")}
    expected = set(toolshop.Product.__table__.columns.keys())
    print("Actual columns  :", sorted(actual))
    print("Expected columns:", sorted(expected))
    missing = expected - actual
    extra = actual - expected
    ok = not missing and not extra
    if missing:
        print("!! MISSING columns:", sorted(missing))
        print("   -> schema drift. Fix with: python scripts/migrate.py --reset")
    if extra:
        print("!! EXTRA columns (from an older schema):", sorted(extra))
    if ok:
        print("Schema OK. Matches the model.")
    return ok


def status():
    with toolshop.SessionLocal() as s:
        total = s.scalar(select(func.count()).select_from(toolshop.Product))
        owned = s.scalar(
            select(func.count()).select_from(toolshop.Product)
            .where(toolshop.Product.edit_token_hash.is_not(None))
        )
    print(f"Database : {toolshop.engine.url.render_as_string(hide_password=True)}")
    print(f"Dialect  : {toolshop.engine.dialect.name}")
    print(f"Products : {total} total ({owned} created via API, {total - owned} baseline)")


def reset():
    print("Dropping all tables...")
    toolshop.Base.metadata.drop_all(toolshop.engine)
    print("Recreating schema and seeding...")
    toolshop.init_db()
    print("Done.")


def main():
    parser = argparse.ArgumentParser(description="ToolShop DB migration / bootstrap")
    parser.add_argument("--reset", action="store_true", help="drop everything and reseed")
    parser.add_argument("--status", action="store_true", help="print state and exit")
    parser.add_argument("--check", action="store_true", help="check for schema drift and exit")
    args = parser.parse_args()

    if args.check:
        check()
        return

    if args.status:
        status()
        return

    if args.reset:
        reset()
    else:
        # Importing `app` already ran init_db(); make it explicit and idempotent.
        toolshop.init_db()
        print("Schema ensured and baseline seeded (if it was empty).")

    status()


if __name__ == "__main__":
    main()
