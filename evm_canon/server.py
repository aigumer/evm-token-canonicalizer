"""HTTP wrapper exposing canonicalize() as a pay-per-call A2MCP endpoint.

Payment gating is x402: when EVM_CANON_PAY_TO is set, POST /canonicalize
returns 402 with a PAYMENT-REQUIRED challenge until the caller presents a
valid PAYMENT-SIGNATURE, verified and settled through the facilitator.
Without EVM_CANON_PAY_TO the endpoint is free (dev / self-hosted mode).

Env:
  EVM_CANON_PAY_TO        receiving EVM address (enables payment gating)
  EVM_CANON_PRICE         price per call, e.g. "$0.0005" (default)
  EVM_CANON_NETWORK       x402 network id (default "base")
  EVM_CANON_FACILITATOR   facilitator URL (default: x402 SDK default)

GET /healthz and GET /schema are always free.
"""

import importlib.metadata
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from evm_canon import canonicalize, default_registry, default_schema

PRICE_DEFAULT = "$0.0005"


def create_app() -> FastAPI:
    app = FastAPI(title="evm-canon", docs_url=None, redoc_url=None)

    pay_to = os.environ.get("EVM_CANON_PAY_TO")
    if pay_to:
        from x402 import x402ResourceServer
        from x402.http import (FacilitatorConfig, HTTPFacilitatorClient,
                               PaymentOption, RouteConfig)
        from x402.http.middleware.fastapi import PaymentMiddlewareASGI
        from x402.mechanisms.evm.exact.register import register_exact_evm_server

        facilitator_url = os.environ.get("EVM_CANON_FACILITATOR")
        config = (FacilitatorConfig(url=facilitator_url)
                  if facilitator_url else FacilitatorConfig())
        server = x402ResourceServer(HTTPFacilitatorClient(config))
        register_exact_evm_server(server)
        routes = {
            "POST /canonicalize": RouteConfig(
                accepts=PaymentOption(
                    scheme="exact",
                    pay_to=pay_to,
                    price=os.environ.get("EVM_CANON_PRICE", PRICE_DEFAULT),
                    network=os.environ.get("EVM_CANON_NETWORK", "base"),
                ),
                description="Canonicalize one EVM token/value payload into "
                            "schema-validated JSON (deterministic, honest nulls)",
                mime_type="application/json",
                service_name="evm-token-canonicalizer",
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
