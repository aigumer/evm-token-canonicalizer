"""HTTP wrapper exposing canonicalize() as a pay-per-call A2MCP endpoint.

Payment gating is x402: when EVM_CANON_PAY_TO is set, POST /canonicalize
returns 402 with a PAYMENT-REQUIRED challenge until the caller presents a
valid PAYMENT-SIGNATURE, verified and settled through the facilitator.
Without EVM_CANON_PAY_TO the endpoint is free (dev / self-hosted mode).

Env:
  EVM_CANON_PAY_TO        receiving EVM address (enables payment gating)
  EVM_CANON_PRICE         price per call, e.g. "$0.002" (default)
  EVM_CANON_NETWORK       x402 network id (default "eip155:196" = X Layer)
  OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE
                          OKX dev-portal credentials; when set, verification
                          and settlement go through the OKX facilitator
                          (web3.okx.com /api/v6/pay/x402/*)
  EVM_CANON_FACILITATOR   fallback facilitator URL when OKX creds absent
                          (default: x402.org — testnets only)

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


def create_app() -> FastAPI:
    app = FastAPI(title="evm-canon", docs_url=None, redoc_url=None)

    pay_to = os.environ.get("EVM_CANON_PAY_TO")
    if pay_to:
        from x402.http import PaymentOption
        from x402.http.middleware.fastapi import PaymentMiddlewareASGI
        from x402.http.types import RouteConfig
        from x402.mechanisms.evm.exact.server import ExactEvmScheme
        from x402.server import x402ResourceServer

        okx_key = os.environ.get("OKX_API_KEY")
        if okx_key:
            from x402.http import (OKXAuthConfig, OKXFacilitatorClient,
                                   OKXFacilitatorConfig)
            from x402.mechanisms.evm.deferred.server import AggrDeferredEvmScheme
            facilitator = OKXFacilitatorClient(OKXFacilitatorConfig(
                auth=OKXAuthConfig(
                    api_key=okx_key,
                    secret_key=os.environ["OKX_SECRET_KEY"],
                    passphrase=os.environ["OKX_PASSPHRASE"]),
                sync_settle=True))
            network = os.environ.get("EVM_CANON_NETWORK", "eip155:196")
            schemes = [ExactEvmScheme(), AggrDeferredEvmScheme()]
        else:
            from x402.http import FacilitatorConfig, HTTPFacilitatorClient
            facilitator_url = os.environ.get("EVM_CANON_FACILITATOR")
            facilitator = HTTPFacilitatorClient(
                FacilitatorConfig(url=facilitator_url)
                if facilitator_url else FacilitatorConfig())
            network = os.environ.get("EVM_CANON_NETWORK", "eip155:84532")
            schemes = [ExactEvmScheme()]

        server = x402ResourceServer(facilitator)
        for scheme in schemes:
            server.register(network, scheme)

        # OKX x402 validation probes the endpoint with plain GETs too, so the
        # challenge must gate every method the URL serves, not just POST.
        def paid_route(price: str, description: str) -> RouteConfig:
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
            )

        canon = paid_route(
            os.environ.get("EVM_CANON_PRICE", PRICE_DEFAULT),
            "Canonicalize one EVM token/value payload into "
            "schema-validated JSON (deterministic, honest nulls)")
        decode = paid_route(
            os.environ.get("EVM_CANON_PRICE_DECODE", PRICE_DECODE_DEFAULT),
            "Decode EVM calldata into typed function args with "
            "deterministic risk flags (unlimited approvals, admin actions)")
        resolve = paid_route(
            os.environ.get("EVM_CANON_PRICE_RESOLVE", PRICE_RESOLVE_DEFAULT),
            "Resolve ENS names to addresses and reverse, "
            "with forward-verification of reverse records")
        lots = paid_route(
            os.environ.get("EVM_CANON_PRICE_LOTS", PRICE_LOTS_DEFAULT),
            "Crypto tax-lot engine: FIFO/LIFO/HIFO cost-basis matching, "
            "realized gains and remaining inventory in exact decimal math")
        routes = {"POST /canonicalize": canon, "GET /canonicalize": canon,
                  "POST /decode": decode, "GET /decode": decode,
                  "POST /resolve": resolve, "GET /resolve": resolve,
                  "POST /lots": lots, "GET /lots": lots}
        # Narrow tax-lot views get their own listings, so they need their own
        # gated paths (a buyer who wants only FIFO shouldn't have to know a
        # parameter name, and the lighter views are priced lower).
        for path, price in LOTS_VIEWS.items():
            route = paid_route(
                os.environ.get(f"EVM_CANON_PRICE_LOTS_{path.upper()}", price),
                LOTS_VIEW_DESCRIPTIONS[path])
            routes[f"POST /lots/{path}"] = route
            routes[f"GET /lots/{path}"] = route
        app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)

    # Render's free tier stops the instance after 15 min without inbound
    # traffic, and external cron pingers (GitHub Actions) fire far less often
    # than scheduled under load. A request to our own public URL counts as
    # inbound traffic, so a self-ping loop keeps the instance awake as long
    # as it's running; the external pinger remains as the wake-up fallback.
    external_url = os.environ.get("RENDER_EXTERNAL_URL")
    if external_url:
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
