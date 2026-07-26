"""HTTP wrapper exposing canonicalize() as a pay-per-call A2MCP endpoint.

Payment gating is x402: when EVM_CANON_PAY_TO is set, POST /canonicalize
returns 402 with a PAYMENT-REQUIRED challenge until the caller presents a
valid PAYMENT-SIGNATURE, verified and settled through the facilitator.
Without EVM_CANON_PAY_TO the endpoint is free (dev / self-hosted mode).

Three payment backends, auto-detected from the environment (or forced with
EVM_CANON_PAYMENT_BACKEND). They cannot share one process: the OKX SDK fork
and the x402 Foundation package both occupy the `x402` import namespace, so
each backend is its own deployment with its own extra — `.[serve]` for OKX,
`.[serve-base]` for CDP/testnet.

  okx      X Layer (eip155:196), USD₮0, OKX facilitator. Needs
           OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE.
  cdp      Base (eip155:8453), USDC, Coinbase facilitator. Needs
           CDP_API_KEY_ID / CDP_API_KEY_SECRET. Routes additionally declare
           Bazaar metadata, which is how they get discovered: the facilitator
           catalogs a resource the first time it settles a payment for it.
  testnet  Base Sepolia via the public x402.org facilitator; no credentials.

Env:
  EVM_CANON_PAY_TO        receiving EVM address (enables payment gating)
  EVM_CANON_PRICE[_*]     per-route price, e.g. "$0.002"
  EVM_CANON_NETWORK       override the backend's default network id
  EVM_CANON_FACILITATOR   override the facilitator URL

GET /healthz and GET /schema are always free.
"""

import importlib.metadata
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from evm_canon import canonicalize, default_registry, default_schema

PRICE_DEFAULT = "$0.002"
PRICE_DECODE_DEFAULT = "$0.003"
PRICE_RESOLVE_DEFAULT = "$0.001"
PRICE_LOTS_DEFAULT = "$0.005"
# Narrow tax-lot views: the three matching methods cost the same as the
# generic route; the two projection views return less and cost less.
LOTS_VIEWS = {"fifo": "$0.005", "lifo": "$0.005", "hifo": "$0.005",
              "acb": "$0.005", "holding-period": "$0.004",
              "gains": "$0.003", "inventory": "$0.003", "validate": "$0.002"}
LOTS_VIEW_DESCRIPTIONS = {
    "fifo": "FIFO cost-basis matching: oldest lots consumed first, with "
            "realized gain per disposal in exact decimal math",
    "lifo": "LIFO cost-basis matching: newest lots consumed first, with "
            "realized gain per disposal in exact decimal math",
    "hifo": "HIFO cost-basis matching: highest-cost lots consumed first, "
            "with realized gain per disposal in exact decimal math",
    "acb": "Average cost basis: every acquisition pooled at a running "
           "average unit cost, with realized gain per disposal",
    "holding-period": "Each disposal split into short- and long-term "
                      "portions by how long the consumed lots were held",
    "gains": "Realized gain/loss summary per disposal plus totals, without "
             "lot-level detail",
    "inventory": "Remaining holdings after all trades, each lot with its "
                 "acquisition time and unit cost",
    "validate": "Every problem in a trade ledger reported at once: "
                "unmatched sells, duplicates, bad numbers and timestamps",
}
# Three ways a narrow route can differ: it forces a matching method, it
# narrows the output, or it runs a different computation entirely.
LOTS_VIEW_METHOD = {"fifo": "FIFO", "lifo": "LIFO", "hifo": "HIFO",
                    "acb": "ACB"}


CDP_FACILITATOR = "https://api.cdp.coinbase.com/platform/v2/x402"
# Facilitator operations the auth provider is asked to sign for; the key names
# are fixed by the SDK, the paths by CDP.
_CDP_OPERATIONS = {"verify": ("POST", "/verify"),
                   "settle": ("POST", "/settle"),
                   "supported": ("GET", "/supported"),
                   "bazaar": ("GET", "/discovery/resources")}


def payment_backend() -> str:
    """Which facilitator this process talks to. Explicit setting wins;
    otherwise the presence of credentials decides."""
    forced = os.environ.get("EVM_CANON_PAYMENT_BACKEND")
    if forced:
        return forced.lower()
    if os.environ.get("CDP_API_KEY_ID"):
        return "cdp"
    if os.environ.get("OKX_API_KEY"):
        return "okx"
    return "testnet"


def _cdp_header_factory(base_url: str):
    """CDP signs a JWT per request path, so headers are generated per call."""
    from urllib.parse import urlparse

    from cdp.auth.utils.http import GetAuthHeadersOptions, get_auth_headers

    parsed = urlparse(base_url)
    host, prefix = parsed.netloc, parsed.path.rstrip("/")

    def create_headers() -> dict[str, dict[str, str]]:
        key_id = os.environ["CDP_API_KEY_ID"]
        secret = os.environ["CDP_API_KEY_SECRET"]
        return {name: get_auth_headers(GetAuthHeadersOptions(
                    api_key_id=key_id, api_key_secret=secret,
                    request_method=method, request_host=host,
                    request_path=f"{prefix}{path}"))
                for name, (method, path) in _CDP_OPERATIONS.items()}

    return create_headers


def create_app() -> FastAPI:
    app = FastAPI(title="evm-canon", docs_url=None, redoc_url=None)

    pay_to = os.environ.get("EVM_CANON_PAY_TO")
    if pay_to:
        from x402.http import PaymentOption
        from x402.http.middleware.fastapi import PaymentMiddlewareASGI
        from x402.http.types import RouteConfig
        from x402.mechanisms.evm.exact.server import ExactEvmScheme
        from x402.server import x402ResourceServer

        backend = payment_backend()
        if backend == "okx":
            from x402.http import (OKXAuthConfig, OKXFacilitatorClient,
                                   OKXFacilitatorConfig)
            from x402.mechanisms.evm.deferred.server import AggrDeferredEvmScheme
            facilitator = OKXFacilitatorClient(OKXFacilitatorConfig(
                auth=OKXAuthConfig(
                    api_key=os.environ["OKX_API_KEY"],
                    secret_key=os.environ["OKX_SECRET_KEY"],
                    passphrase=os.environ["OKX_PASSPHRASE"]),
                sync_settle=True))
            default_network = "eip155:196"
            schemes = [ExactEvmScheme(), AggrDeferredEvmScheme()]
        elif backend == "cdp":
            from x402.http import (CreateHeadersAuthProvider, FacilitatorConfig,
                                   HTTPFacilitatorClient)
            url = os.environ.get("EVM_CANON_FACILITATOR", CDP_FACILITATOR)
            facilitator = HTTPFacilitatorClient(FacilitatorConfig(
                url=url, auth_provider=CreateHeadersAuthProvider(
                    _cdp_header_factory(url))))
            default_network = "eip155:8453"
            schemes = [ExactEvmScheme()]
        else:
            from x402.http import FacilitatorConfig, HTTPFacilitatorClient
            facilitator_url = os.environ.get("EVM_CANON_FACILITATOR")
            facilitator = HTTPFacilitatorClient(
                FacilitatorConfig(url=facilitator_url)
                if facilitator_url else FacilitatorConfig())
            default_network = "eip155:84532"
            schemes = [ExactEvmScheme()]
        network = os.environ.get("EVM_CANON_NETWORK", default_network)

        server = x402ResourceServer(facilitator)
        for scheme in schemes:
            server.register(network, scheme)
        if backend == "cdp":
            # Registers the discovery extension so the declarations below
            # travel with each challenge and get indexed on first settlement.
            from x402.extensions.bazaar import bazaar_resource_server_extension
            server.register_extension(bazaar_resource_server_extension)

        # OKX x402 validation probes the endpoint with plain GETs too, so the
        # challenge must gate every method the URL serves, not just POST.
        def paid_route(price: str, description: str,
                       path: str | None = None) -> RouteConfig:
            extensions = None
            if backend == "cdp" and path:
                from evm_canon.discovery import declaration_for
                extensions = declaration_for(path)
            return RouteConfig(
                accepts=[PaymentOption(
                    scheme=s.scheme,
                    pay_to=pay_to,
                    price=price,
                    network=network,
                    max_timeout_seconds=300,
                ) for s in schemes],
                description=description,
                mime_type="application/json",
                extensions=extensions,
            )

        canon = paid_route(
            os.environ.get("EVM_CANON_PRICE", PRICE_DEFAULT),
            "Canonicalize one EVM token/value payload into "
            "schema-validated JSON (deterministic, honest nulls)",
            "canonicalize")
        decode = paid_route(
            os.environ.get("EVM_CANON_PRICE_DECODE", PRICE_DECODE_DEFAULT),
            "Decode EVM calldata into typed function args with "
            "deterministic risk flags (unlimited approvals, admin actions)",
            "decode")
        resolve = paid_route(
            os.environ.get("EVM_CANON_PRICE_RESOLVE", PRICE_RESOLVE_DEFAULT),
            "Resolve ENS names to addresses and reverse, "
            "with forward-verification of reverse records",
            "resolve")
        lots = paid_route(
            os.environ.get("EVM_CANON_PRICE_LOTS", PRICE_LOTS_DEFAULT),
            "Crypto tax-lot engine: FIFO/LIFO/HIFO cost-basis matching, "
            "realized gains and remaining inventory in exact decimal math",
            "lots")
        routes = {"POST /canonicalize": canon, "GET /canonicalize": canon,
                  "POST /decode": decode, "GET /decode": decode,
                  "POST /resolve": resolve, "GET /resolve": resolve,
                  "POST /lots": lots, "GET /lots": lots}
        # Narrow tax-lot views get their own listings, so they need their own
        # gated paths (a buyer who wants only FIFO shouldn't have to know a
        # parameter name, and the lighter views are priced lower).
        for path, price in LOTS_VIEWS.items():
            env_key = path.upper().replace("-", "_")
            route = paid_route(
                os.environ.get(f"EVM_CANON_PRICE_LOTS_{env_key}", price),
                LOTS_VIEW_DESCRIPTIONS[path], f"lots/{path}")
            routes[f"POST /lots/{path}"] = route
            routes[f"GET /lots/{path}"] = route
        app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)

    # Render's free tier stops the instance after 15 min without inbound
    # traffic, and external cron pingers (GitHub Actions) fire far less often
    # than scheduled under load. A request to our own public URL counts as
    # inbound traffic, so a self-ping loop keeps the instance awake as long
    # as it's running; the external pinger remains as the wake-up fallback.
    # Opt-out, because the free tier bills instance-hours across all services:
    # only the venue that gets actively probed (OKX) is worth keeping warm.
    external_url = os.environ.get("RENDER_EXTERNAL_URL")
    if external_url and os.environ.get("EVM_CANON_KEEPALIVE", "1") != "0":
        import threading
        import urllib.request

        def _self_ping():
            while True:
                try:
                    urllib.request.urlopen(external_url + "/healthz", timeout=30)
                except Exception:
                    pass
                threading.Event().wait(600)

        threading.Thread(target=_self_ping, daemon=True).start()

    @app.get("/healthz")
    def healthz():
        return {"ok": True,
                "version": importlib.metadata.version("evm-canon"),
                "registry_version": default_registry().version}

    @app.get("/schema")
    def schema():
        return default_schema()

    # Meta keys that live NEXT TO "raw" in the wrapped form, per route.
    META_KEYS = {"target_schema", "hints", "method"}

    async def _json_body(request: Request):
        """Accept both {"raw": {...}, meta...} and a bare fields object.

        The marketplace listing tells callers to "provide a JSON payload with
        the raw fields" — a reasonable client (and the platform's own
        validation probe) sends those fields at the top level, so treat a
        body without "raw" as the raw object itself instead of rejecting it.
        """
        try:
            payload = await request.json()
        except Exception:
            return None, JSONResponse(status_code=400, content={
                "error": {"code": "MALFORMED_INVOCATION", "field": None,
                          "detail": "body is not valid JSON"}})
        if not isinstance(payload, dict) or not payload:
            return None, JSONResponse(status_code=400, content={
                "error": {"code": "MALFORMED_INVOCATION", "field": None,
                          "detail": "body must be a non-empty JSON object"}})
        if "raw" not in payload:
            meta = {k: v for k, v in payload.items() if k in META_KEYS}
            raw = {k: v for k, v in payload.items() if k not in META_KEYS}
            payload = {"raw": raw, **meta}
        return payload, None

    @app.get("/decode")
    def decode_usage():
        return {"usage": {"method": "POST",
                          "body": {"raw": {"data": "0x... calldata (required)",
                                           "to": "optional address",
                                           "value": "optional wei"}}}}

    @app.post("/decode")
    async def decode_route(request: Request):
        from evm_canon.decoder import decode_calldata
        payload, err = await _json_body(request)
        return err if err else decode_calldata(payload)

    @app.get("/resolve")
    def resolve_usage():
        return {"usage": {"method": "POST",
                          "body": {"raw": {"name": "alice.eth (or)",
                                           "address": "0x... for reverse"}}}}

    @app.post("/resolve")
    async def resolve_route(request: Request):
        from evm_canon.ens import resolve
        payload, err = await _json_body(request)
        return err if err else resolve(payload)

    @app.get("/lots")
    def lots_usage():
        return {"usage": {"method": "POST",
                          "body": {"raw": {
                              "method": "FIFO | LIFO | HIFO",
                              "trades": [{"side": "buy|sell", "asset": "BTC",
                                          "amount": "1.5", "price": "60000",
                                          "fee": "10 (optional)",
                                          "time": "unix or ISO (sortable)"}]}}}}

    @app.post("/lots")
    async def lots_route(request: Request):
        from evm_canon.lots import calculate_lots
        payload, err = await _json_body(request)
        return err if err else calculate_lots(payload)

    def _register_lots_view(path: str):
        method = LOTS_VIEW_METHOD.get(path)
        view = path if path in ("gains", "inventory") else None

        async def handler(request: Request):
            from evm_canon.lots import (calculate_lots, check_ledger,
                                        holding_period, project)
            payload, err = await _json_body(request)
            if err:
                return err
            if method:
                payload = {**payload,
                           "raw": {**payload["raw"], "method": method}}
            if path == "validate":
                return check_ledger(payload)
            if path == "holding-period":
                return holding_period(payload)
            out = calculate_lots(payload)
            return project(out, view) if view else out

        def usage():
            body = {"trades": [{"side": "buy|sell", "asset": "BTC",
                                "amount": "1.5", "price": "60000",
                                "fee": "10 (optional)",
                                "time": "unix or ISO (sortable)"}]}
            if not method:
                body["method"] = "FIFO | LIFO | HIFO | ACB (default FIFO)"
            if path == "holding-period":
                body["long_term_days"] = "365 (optional)"
                body["trades"][0]["time"] = "unix seconds/millis or ISO-8601"
            return {"usage": {"method": "POST", "body": body,
                              "returns": LOTS_VIEW_DESCRIPTIONS[path]}}

        app.add_api_route(f"/lots/{path}", handler, methods=["POST"])
        app.add_api_route(f"/lots/{path}", usage, methods=["GET"])

    for _view_path in LOTS_VIEWS:
        _register_lots_view(_view_path)

    @app.get("/canonicalize")
    def canonicalize_usage():
        # Reached only after payment (or in free mode): describe the contract.
        return {"usage": {"method": "POST",
                          "body": {"raw": "required", "target_schema": "optional",
                                   "hints": "optional"},
                          "schema": "/schema"}}

    @app.post("/canonicalize")
    async def canonicalize_route(request: Request):
        payload, err = await _json_body(request)
        # canonicalize() returns {result, report} or {"error": {...}} —
        # a typed error is a delivered product, so both are HTTP 200.
        return err if err else canonicalize(payload)

    return app


app = create_app()


def main() -> None:
    import uvicorn
    uvicorn.run("evm_canon.server:app",
                host=os.environ.get("HOST", "0.0.0.0"),
                port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
