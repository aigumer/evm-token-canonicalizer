"""HTTP wrapper tests (free mode only — paid mode needs a live facilitator)."""

import pytest

fastapi = pytest.importorskip("fastapi")
testclient = pytest.importorskip("starlette.testclient")

from evm_canon.server import create_app  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("EVM_CANON_PAY_TO", raising=False)
    return testclient.TestClient(create_app())


def test_healthz(client):
    body = client.get("/healthz").json()
    assert body["ok"] is True
    assert body["registry_version"].startswith("tokenlists@")


def test_schema_is_free(client):
    assert client.get("/schema").json()["type"] == "object"


def test_canonicalize_roundtrip(client):
    r = client.post("/canonicalize", json={
        "raw": {"symbol": "USDC", "chain": "arbitrum", "amount": "1500000"}})
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["amount_human"] == "1.5"
    assert body["report"]["resolved_by"] == "registry"


def test_typed_error_is_delivered_as_200(client):
    r = client.post("/canonicalize", json={
        "raw": {"token": "0xdeadbeef", "chain": "ethereum"}})
    assert r.status_code == 200
    assert r.json()["error"]["code"] == "INVALID_ADDRESS"


def test_decode_roundtrip(client):
    r = client.post("/decode", json={"raw": {
        "data": "0xa9059cbb"
                "000000000000000000000000af88d065e77c8cc2239327c5edb3a432268e5831"
                "000000000000000000000000000000000000000000000000000000000016e360"}})
    assert r.status_code == 200
    assert r.json()["result"]["function"]["name"] == "transfer"


def test_bare_payload_accepted(client):
    """The platform probe sends fields without the {"raw": ...} wrapper."""
    r = client.post("/canonicalize", json={"symbol": "USDC",
                                           "chain": "arbitrum",
                                           "amount": "1500000"})
    assert r.status_code == 200
    assert r.json()["result"]["amount_human"] == "1.5"
    r = client.post("/decode", json={
        "data": "0xa9059cbb"
                "000000000000000000000000af88d065e77c8cc2239327c5edb3a432268e5831"
                "000000000000000000000000000000000000000000000000000000000016e360"})
    assert r.json()["result"]["function"]["name"] == "transfer"
    r = client.post("/lots", json={"method": "LIFO", "trades": [
        {"side": "buy", "asset": "BTC", "amount": "1", "price": "10", "time": 1},
        {"side": "sell", "asset": "BTC", "amount": "1", "price": "15", "time": 2}]})
    assert r.json()["result"]["method"] == "LIFO"
    assert r.json()["result"]["totals"]["gain"] == "5"


def test_paid_mode_app_constructs(monkeypatch):
    """Route/scheme wiring errors must surface at build time, not on Render."""
    monkeypatch.setenv("EVM_CANON_PAY_TO",
                       "0x8797b596a56f8b2d46f428fca2e6ac2a62a353ee")
    monkeypatch.setenv("OKX_API_KEY", "test-key")
    monkeypatch.setenv("OKX_SECRET_KEY", "test-secret")
    monkeypatch.setenv("OKX_PASSPHRASE", "test-pass")
    create_app()  # must not raise; facilitator is not contacted until a request


def test_malformed_invocation_is_400(client):
    # bare objects are wrapped into raw now → honest-nulls result, 200
    r = client.post("/canonicalize", json={"nope": 1})
    assert r.status_code == 200
    body = r.json()
    assert "error" in body or body["result"]["symbol"] is None
    assert client.post("/canonicalize",
                       content=b"not json",
                       headers={"content-type": "application/json"}
                       ).status_code == 400
    assert client.post("/canonicalize", json={}).status_code == 400


def test_lots_view_routes(client):
    trades = [
        {"side": "buy", "asset": "BTC", "amount": "1", "price": "10000", "time": 1},
        {"side": "buy", "asset": "BTC", "amount": "1", "price": "30000", "time": 2},
        {"side": "sell", "asset": "BTC", "amount": "1", "price": "40000", "time": 3}]
    # each method route forces its own matching, no parameter needed
    assert client.post("/lots/fifo", json={"trades": trades}
                       ).json()["result"]["disposals"][0]["cost_basis"] == "10000"
    assert client.post("/lots/lifo", json={"trades": trades}
                       ).json()["result"]["disposals"][0]["cost_basis"] == "30000"
    assert client.post("/lots/hifo", json={"trades": trades}
                       ).json()["result"]["disposals"][0]["cost_basis"] == "30000"
    # projections return less, not the same blob
    gains = client.post("/lots/gains", json={"trades": trades}).json()["result"]
    assert "inventory" not in gains and "lots_consumed" not in gains["disposals"][0]
    inv = client.post("/lots/inventory", json={"trades": trades}).json()["result"]
    assert "disposals" not in inv and inv["inventory"][0]["asset"] == "BTC"
    # GET describes the contract for free
    assert "usage" in client.get("/lots/fifo").json()


def test_lots_view_typed_errors(client):
    r = client.post("/lots/fifo", json={"trades": [
        {"side": "sell", "asset": "BTC", "amount": "1", "price": "1", "time": 1}]})
    assert r.json()["error"]["code"] == "INSUFFICIENT_INVENTORY"
