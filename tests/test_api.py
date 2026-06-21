"""End-to-end tests for the ToolShop REST API."""
from conftest import make_product


# --------------------------------------------------------------------------- #
# Listing, search, filtering, sorting, pagination
# --------------------------------------------------------------------------- #
def test_list_returns_products_with_total_header(client):
    res = client.get("/api/products")
    assert res.status_code == 200
    data = res.get_json()
    assert isinstance(data, list) and len(data) == 8
    assert res.headers["X-Total-Count"] == "8"


def test_pagination_limit_and_offset(client):
    page1 = client.get("/api/products?limit=3&offset=0").get_json()
    page2 = client.get("/api/products?limit=3&offset=3").get_json()
    assert len(page1) == 3 and len(page2) == 3
    assert {p["id"] for p in page1}.isdisjoint({p["id"] for p in page2})


def test_sort_descending_price(client):
    data = client.get("/api/products?sort=-price&limit=1").get_json()
    assert data[0]["name"] == "Cordless Drill 24V"


def test_filter_by_category_case_insensitive(client):
    names = [p["name"] for p in client.get("/api/products?category=pliers").get_json()]
    assert "Combination Pliers" in names and "Claw Hammer" not in names


def test_filter_in_stock(client):
    res = client.get("/api/products?in_stock=true")
    assert res.status_code == 200
    assert all(p["in_stock"] for p in res.get_json())


def test_search_partial_name(client):
    names = [p["name"] for p in client.get("/api/products?search=plier").get_json()]
    assert "Combination Pliers" in names


def test_exact_id_lookup(client):
    data = client.get("/api/products?id=3").get_json()
    assert len(data) == 1 and data[0]["id"] == 3


def test_invalid_params_return_400(client):
    assert client.get("/api/products?id=abc").status_code == 400
    assert client.get("/api/products?sort=bogus").status_code == 400
    assert client.get("/api/products?limit=999").status_code == 400
    assert client.get("/api/products?in_stock=maybe").status_code == 400


# --------------------------------------------------------------------------- #
# Single product + ETag / conditional requests
# --------------------------------------------------------------------------- #
def test_get_one_returns_etag(client):
    res = client.get("/api/products/3")
    assert res.status_code == 200 and res.headers.get("ETag")


def test_conditional_get_304(client):
    etag = client.get("/api/products/3").headers["ETag"]
    res = client.get("/api/products/3", headers={"If-None-Match": etag})
    assert res.status_code == 304


def test_get_missing_404(client):
    assert client.get("/api/products/999999").status_code == 404


# --------------------------------------------------------------------------- #
# Create
# --------------------------------------------------------------------------- #
def test_create_returns_token_and_201(client):
    res = client.post("/api/products", json={"name": "New Tool", "price": 5})
    assert res.status_code == 201
    body = res.get_json()
    assert body["edit_token"] and body["editable"] is True


def test_create_without_name_400(client):
    assert client.post("/api/products", json={"price": 5}).status_code == 400


# --------------------------------------------------------------------------- #
# Update + optimistic concurrency
# --------------------------------------------------------------------------- #
def test_update_with_token(client):
    pid, token = make_product(client)
    res = client.put(f"/api/products/{pid}", json={"price": 42},
                     headers={"X-Edit-Token": token})
    assert res.status_code == 200 and res.get_json()["price"] == 42
    assert res.headers.get("ETag")


def test_update_changes_etag(client):
    pid, token = make_product(client)
    e1 = client.get(f"/api/products/{pid}").headers["ETag"]
    client.put(f"/api/products/{pid}", json={"price": 11}, headers={"X-Edit-Token": token})
    e2 = client.get(f"/api/products/{pid}").headers["ETag"]
    assert e1 != e2


def test_update_no_token_401(client):
    pid, _ = make_product(client)
    assert client.put(f"/api/products/{pid}", json={"price": 1}).status_code == 401


def test_update_wrong_token_403(client):
    pid, _ = make_product(client)
    res = client.put(f"/api/products/{pid}", json={"price": 1},
                     headers={"X-Edit-Token": "wrong"})
    assert res.status_code == 403


def test_update_missing_404(client):
    res = client.put("/api/products/999999", json={"price": 1},
                     headers={"X-Edit-Token": "whatever"})
    assert res.status_code == 404


def test_if_match_stale_412(client):
    pid, token = make_product(client)
    stale = client.get(f"/api/products/{pid}").headers["ETag"]
    # change it so the captured ETag is now stale
    client.put(f"/api/products/{pid}", json={"price": 7}, headers={"X-Edit-Token": token})
    res = client.put(f"/api/products/{pid}", json={"price": 8},
                     headers={"X-Edit-Token": token, "If-Match": stale})
    assert res.status_code == 412


def test_baseline_product_is_read_only_403(client):
    # Seeded products have no edit token, so they cannot be modified.
    baseline = next(p for p in client.get("/api/products").get_json() if not p["editable"])
    res = client.put(f"/api/products/{baseline['id']}", json={"price": 1},
                     headers={"X-Edit-Token": "anything"})
    assert res.status_code == 403


# --------------------------------------------------------------------------- #
# Delete
# --------------------------------------------------------------------------- #
def test_delete_with_token_then_gone(client):
    pid, token = make_product(client)
    assert client.delete(f"/api/products/{pid}", headers={"X-Edit-Token": token}).status_code == 204
    assert client.get(f"/api/products/{pid}").status_code == 404


def test_delete_no_token_401(client):
    pid, _ = make_product(client)
    assert client.delete(f"/api/products/{pid}").status_code == 401


def test_delete_wrong_token_403(client):
    pid, _ = make_product(client)
    res = client.delete(f"/api/products/{pid}", headers={"X-Edit-Token": "nope"})
    assert res.status_code == 403


# --------------------------------------------------------------------------- #
# Cross-cutting: health, request id, cleanup
# --------------------------------------------------------------------------- #
def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200 and res.get_json()["status"] == "ok"


def test_every_response_has_request_id(client):
    assert client.get("/api/products").headers.get("X-Request-ID")
    err = client.get("/api/products/999999")
    assert err.get_json()["request_id"] and err.get_json()["status"] == 404


def test_cleanup_requires_secret_and_keeps_baseline(client):
    pid, _ = make_product(client)
    assert client.post("/api/maintenance/cleanup").status_code == 401
    res = client.post("/api/maintenance/cleanup",
                      headers={"X-Cleanup-Token": "test-secret"})
    assert res.status_code == 200 and res.get_json()["removed"] >= 1
    # the created product is gone, baseline remains
    assert client.get(f"/api/products/{pid}").status_code == 404
    assert len(client.get("/api/products").get_json()) == 8
