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

        price = os.environ.get("EVM_CANON_PRICE", PRICE_DEFAULT)
        routes = {
            "POST /canonicalize": RouteConfig(
                accepts=[PaymentOption(
                    scheme=s.scheme,
                    pay_to=pay_to,
                    price=price,
                    network=network,
                    max_timeout_seconds=300,
                ) for s in schemes],
                description="Canonicalize one EVM token/value payload into "
                            "schema-validated JSON (deterministic, honest nulls)",
                mime_type="application/json",
            ),
        }
        app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)

    @app.get("/healthz")
    def healthz():
        return {"ok": True,
                "version": importlib.metadata.version("evm-canon"),
                "registry_version": default_registry().version}

    @app.get("/schema")
    def schema():
        return default_schema()

    @app.post("/canonicalize")
    async def canonicalize_route(request: Request):
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={
                "error": {"code": "MALFORMED_INVOCATION", "field": None,
                          "detail": "body is not valid JSON"}})
        if not isinstance(payload, dict) or "raw" not in payload:
            return JSONResponse(status_code=400, content={
                "error": {"code": "MALFORMED_INVOCATION", "field": "raw",
                          "detail": "body must be an object with a 'raw' key"}})
        # canonicalize() returns {result, report} or {"error": {...}} —
        # a typed error is a delivered product, so both are HTTP 200.
        return canonicalize(payload)

    return app


app = create_app()


def main() -> None:
    import uvicorn
    uvicorn.run("evm_canon.server:app",
                host=os.environ.get("HOST", "0.0.0.0"),
                port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
