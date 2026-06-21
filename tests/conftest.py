"""Shared pytest fixtures for the ToolShop test suite.

The app reads its configuration from environment variables at import time, so we
set them up here (a throwaway SQLite database, rate limiting off, a known cleanup
secret) before importing the application.
"""
import os
import tempfile

import pytest

_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.environ["DATABASE_URL"] = "sqlite:///" + _db_path
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["CLEANUP_TOKEN"] = "test-secret"

import app as app_module  # noqa: E402  (import after env is configured)


@pytest.fixture()
def client():
    """A fresh, seeded database and a Flask test client for each test."""
    app_module.Base.metadata.drop_all(app_module.engine)
    app_module.init_db()
    app_module.app.config.update(TESTING=True)
    with app_module.app.test_client() as c:
        yield c


def make_product(client, **fields):
    """Helper: create a product, returning (id, edit_token)."""
    payload = {"name": "Temp Tool", "price": 9.9, "category": "Drill"}
    payload.update(fields)
    res = client.post("/api/products", json=payload)
    assert res.status_code == 201, res.get_json()
    data = res.get_json()
    return data["id"], data["edit_token"]


@pytest.fixture()
def make_product_fixture(client):
    """Fixture wrapper around make_product for tests that prefer fixture injection."""
    def _make(**fields):
        return make_product(client, **fields)
    return _make
