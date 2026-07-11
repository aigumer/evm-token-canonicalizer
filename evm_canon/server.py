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
        routes = {"POST /canonicalize": canon, "GET /canonicalize": canon,
                  "POST /decode": decode, "GET /decode": decode,
                  "POST /resolve": resolve, "GET /resolve": resolve}
        app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)

    @app.get("/healthz")
    def healthz():
        return {"ok": True,
                "version": importlib.metadata.version("evm-canon"),
                "registry_version": default_registry().version}

    @app.get("/schema")
    def schema():
        return default_schema()

    async def _json_body(request: Request):
        try:
            payload = await request.json()
        except Exception:
            return None, JSONResponse(status_code=400, content={
                "error": {"code": "MALFORMED_INVOCATION", "field": None,
                          "detail": "body is not valid JSON"}})
        if not isinstance(payload, dict) or "raw" not in payload:
            return None, JSONResponse(status_code=400, content={
                "error": {"code": "MALFORMED_INVOCATION", "field": "raw",
                          "detail": "body must be an object with a 'raw' key"}})
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
