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


def test_malformed_invocation_is_400(client):
    assert client.post("/canonicalize", json={"nope": 1}).status_code == 400
    assert client.post("/canonicalize",
                       content=b"not json",
                       headers={"content-type": "application/json"}
                       ).status_code == 400
