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


def _requires(module_path: str):
    """The two payment backends ship mutually exclusive SDKs, so each one's
    wiring test only runs in the deployment that actually installs it."""
    return pytest.importorskip(module_path)


def test_okx_backend_app_constructs(monkeypatch):
    """Route/scheme wiring errors must surface at build time, not on Render."""
    okx_http = _requires("x402.http")
    if not hasattr(okx_http, "OKXFacilitatorClient"):
        pytest.skip("OKX SDK fork not installed")
    monkeypatch.setenv("EVM_CANON_PAY_TO",
                       "0x8797b596a56f8b2d46f428fca2e6ac2a62a353ee")
    monkeypatch.setenv("OKX_API_KEY", "test-key")
    monkeypatch.setenv("OKX_SECRET_KEY", "test-secret")
    monkeypatch.setenv("OKX_PASSPHRASE", "test-pass")
    monkeypatch.delenv("CDP_API_KEY_ID", raising=False)
    create_app()  # must not raise; facilitator is not contacted until a request


def test_cdp_backend_app_constructs(monkeypatch):
    _requires("x402.extensions.bazaar")
    _requires("cdp.auth.utils.http")
    monkeypatch.setenv("EVM_CANON_PAY_TO",
                       "0x8797b596a56f8b2d46f428fca2e6ac2a62a353ee")
    monkeypatch.setenv("CDP_API_KEY_ID", "test-id")
    monkeypatch.setenv("CDP_API_KEY_SECRET", "test-secret")
    monkeypatch.delenv("OKX_API_KEY", raising=False)
    create_app()  # declarations + extension registration must not raise


def test_bazaar_declared_on_post_only(monkeypatch):
    """A body declaration stamped "GET" fails the facilitator's validator, so
    the extension must ride on POST routes only — GET stays paid but silent."""
    _requires("x402.extensions.bazaar")
    _requires("cdp.auth.utils.http")
    monkeypatch.setenv("EVM_CANON_PAY_TO",
                       "0x8797b596a56f8b2d46f428fca2e6ac2a62a353ee")
    monkeypatch.setenv("CDP_API_KEY_ID", "test-id")
    monkeypatch.setenv("CDP_API_KEY_SECRET", "test-secret")
    monkeypatch.delenv("OKX_API_KEY", raising=False)
    app = create_app()
    routes = next(m.kwargs["routes"] for m in app.user_middleware
                  if "routes" in m.kwargs)
    posts = {k: v for k, v in routes.items() if k.startswith("POST ")}
    gets = {k: v for k, v in routes.items() if k.startswith("GET ")}
    assert len(posts) == len(gets) == 24
    assert all(v.extensions and "bazaar" in v.extensions for v in posts.values())
    assert all(not v.extensions for v in gets.values())
    # Every declaration must satisfy the facilitator's own validators — both
    # of them. The spec check catches a malformed declaration; the data check
    # catches an example that doesn't match the schema next to it (that is how
    # /encode/wrap silently missed the catalog: no "args" in the example).
    import copy

    from x402.extensions.bazaar import (validate_discovery_extension,
                                        validate_discovery_extension_spec)
    for name, cfg in posts.items():
        ext = cfg.extensions["bazaar"]
        assert validate_discovery_extension_spec(ext).valid, name
        # The server stamps the request's method onto the declaration before
        # it goes out; validate what the facilitator will actually receive.
        stamped = copy.deepcopy(ext)
        stamped["info"]["input"]["method"] = "POST"
        result = validate_discovery_extension(stamped)
        assert result.valid, (name, result.errors)


def test_well_known_manifest_is_free_and_priced(monkeypatch):
    """One unauthenticated GET must state the real terms of every paid route."""
    _requires("x402.extensions.bazaar")
    _requires("cdp.auth.utils.http")
    monkeypatch.setenv("EVM_CANON_PAY_TO",
                       "0x8797b596a56f8b2d46f428fca2e6ac2a62a353ee")
    monkeypatch.setenv("CDP_API_KEY_ID", "test-id")
    monkeypatch.setenv("CDP_API_KEY_SECRET", "test-secret")
    monkeypatch.setenv("EVM_CANON_PRICE_CHECKSUM", "$0.001")
    monkeypatch.delenv("OKX_API_KEY", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    body = testclient.TestClient(create_app()).get("/.well-known/x402").json()
    assert body["x402Version"] == 2
    assert body["pagination"]["total"] == len(body["items"]) == 24
    by_resource = {i["resource"].rsplit("/", 1)[-1]: i for i in body["items"]}
    checksum = by_resource["checksum"]
    assert checksum["metadata"]["method"] == "POST"
    assert checksum["description"] and checksum["mimeType"] == "application/json"
    accepts = checksum["accepts"][0]
    # priced through the scheme, so the manifest cannot drift from the challenge
    assert accepts["amount"] == "1000"
    assert accepts["network"] == "eip155:8453"
    assert accepts["payTo"] == "0x8797b596a56f8b2d46f428fca2e6ac2a62a353ee"
    assert accepts["asset"].lower().startswith("0x833589")
    # what we publish must survive the facilitator's validators, method included
    from x402.extensions.bazaar import (validate_discovery_extension,
                                        validate_discovery_extension_spec)
    for item in body["items"]:
        ext = item["extensions"]["bazaar"]
        assert ext["info"]["input"]["method"] == "POST", item["resource"]
        assert validate_discovery_extension_spec(ext).valid, item["resource"]
        result = validate_discovery_extension(ext)
        assert result.valid, (item["resource"], result.errors)


def test_well_known_manifest_empty_when_free(client):
    body = client.get("/.well-known/x402").json()
    assert body["items"] == [] and body["pagination"]["total"] == 0


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


def test_new_lots_routes(client):
    trades = [
        {"side": "buy", "asset": "BTC", "amount": "1", "price": "10000", "time": 0},
        {"side": "buy", "asset": "BTC", "amount": "1", "price": "30000",
         "time": 400 * 86400},
        {"side": "sell", "asset": "BTC", "amount": "2", "price": "50000",
         "time": 500 * 86400}]
    acb = client.post("/lots/acb", json={"trades": trades}).json()["result"]
    assert acb["method"] == "ACB"
    assert acb["disposals"][0]["cost_basis"] == "40000"
    hp = client.post("/lots/holding-period", json={"trades": trades}).json()["result"]
    assert hp["disposals"][0]["long_term"]["cost_basis"] == "10000"
    assert hp["disposals"][0]["short_term"]["cost_basis"] == "30000"
    v = client.post("/lots/validate", json={"trades": trades}).json()["result"]
    assert v["ok"] is True and v["summary"]["sells"] == 1
    for path in ("acb", "holding-period", "validate"):
        assert "usage" in client.get(f"/lots/{path}").json()


def test_payment_backend_detection(monkeypatch):
    from evm_canon.server import payment_backend
    for var in ("EVM_CANON_PAYMENT_BACKEND", "CDP_API_KEY_ID", "OKX_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert payment_backend() == "testnet"
    monkeypatch.setenv("OKX_API_KEY", "k")
    assert payment_backend() == "okx"
    monkeypatch.setenv("CDP_API_KEY_ID", "id")   # CDP wins when both present
    assert payment_backend() == "cdp"
    monkeypatch.setenv("EVM_CANON_PAYMENT_BACKEND", "OKX")
    assert payment_backend() == "okx"            # explicit setting overrides


def test_discovery_specs_cover_every_paid_route():
    """A route listed without discovery metadata is invisible in the Bazaar."""
    from evm_canon.discovery import SPECS
    from evm_canon.server import ENCODE_VIEWS, LOTS_VIEWS
    expected = {"canonicalize", "decode", "resolve", "lots", "encode",
                "checksum"}
    expected |= {f"lots/{v}" for v in LOTS_VIEWS}
    expected |= {f"encode/{v}" for v in ENCODE_VIEWS}
    assert expected == set(SPECS)
    for path, spec in SPECS.items():
        assert spec["input_schema"]["type"] == "object", path
        assert spec["output_example"], path


def test_discovery_declaration_shape():
    pytest.importorskip("x402.extensions.bazaar")   # Base deployment only
    from evm_canon.discovery import declaration_for
    assert declaration_for("nope") is None
    decl = declaration_for("decode")
    assert "bazaar" in decl


def test_encode_routes(client):
    SP = "0x1111111111111111111111111111111111111111"
    r = client.post("/encode", json={"function": "transfer", "token": "USDC",
                                     "chain": "arbitrum",
                                     "args": {"to": SP, "amount": "1.5"}})
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["data"].startswith("0xa9059cbb")
    assert body["report"]["signed"] is False
    # the narrow route fixes the function, so the caller need not name it
    r2 = client.post("/encode/transfer", json={"token": "USDC", "chain": "arbitrum",
                                               "args": {"to": SP, "amount": "1.5"}})
    assert r2.json()["result"]["data"] == body["result"]["data"]
    # risky intents are built but flagged
    r3 = client.post("/encode/approve-all",
                     json={"args": {"operator": SP, "approved": True}})
    assert "approval_for_all_granted" in r3.json()["report"]["risk_flags"]
    for path in ("transfer", "approve", "wrap", "swap"):
        assert "usage" in client.get(f"/encode/{path}").json()


def test_checksum_route(client):
    r = client.post("/checksum", json={"addresses": [
        "0xaf88d065e77c8cc2239327c5edb3a432268e5831", "0xnothex", "0x123"]})
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["addresses"][0]["address"] == \
        "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    assert res["addresses"][0]["was_checksummed"] is False
    # a bad entry is reported, it does not sink the batch
    assert res["addresses"][1]["valid"] is False
    assert res["all_valid"] is False
    assert r.json()["report"]["invalid_count"] == 2
    single = client.post("/checksum", json={
        "address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"}).json()
    assert single["result"]["addresses"][0]["was_checksummed"] is True


def test_manifest_is_free_and_complete(client):
    m = client.get("/").json()
    assert m["service"] == "evm-canon"
    paths = {s["path"] for s in m["services"]}
    assert "/checksum" in paths and "/encode/transfer" in paths
    assert "/lots/validate" in paths
    assert all(s["price"].startswith("$") for s in m["services"])
