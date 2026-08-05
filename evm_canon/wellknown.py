"""The x402 discovery manifest served at /.well-known/x402.

This path is not in the x402 v2 specification — that only standardizes the
facilitator-side ``GET /discovery/resources`` (§8.1). Clients ask for it
anyway: one 2.5-hour window of production logs shows 14 requests to it, all
answered 404. So we serve the shape the spec does define for discovery
responses, at the path callers actually try. One unauthenticated GET then
tells a buyer everything it needs before spending anything.

The terms are read from the live route table and priced through the same
``parse_price`` the 402 challenge uses, so the manifest cannot drift from what
the endpoint really charges. When no price parser is available (a backend
whose scheme doesn't expose one), the human price is reported as-is rather
than an invented atomic amount.
"""

from typing import Any, Callable


def _accepts(config: Any, parse_price: Callable | None) -> list[dict]:
    options = config.accepts if isinstance(config.accepts, list) else [config.accepts]
    out = []
    for option in options:
        entry: dict[str, Any] = {"scheme": option.scheme, "network": option.network}
        # pay_to may be a callable (dynamic recipient); only a literal is a fact
        # we can publish.
        if isinstance(option.pay_to, str):
            entry["payTo"] = option.pay_to
        if option.max_timeout_seconds is not None:
            entry["maxTimeoutSeconds"] = option.max_timeout_seconds
        priced = None
        if parse_price is not None:
            try:
                priced = parse_price(option.price, option.network)
            except Exception:
                priced = None
        if priced is not None:
            entry["asset"] = priced.asset
            entry["amount"] = priced.amount
            if priced.extra:
                entry["extra"] = priced.extra
        elif isinstance(option.price, str):
            entry["price"] = option.price
        out.append(entry)
    return out


def _item(path: str, verb: str, config: Any, base_url: str,
          parse_price: Callable | None) -> dict:
    item: dict[str, Any] = {
        "resource": f"{base_url}{path}",
        "type": "http",
        "x402Version": 2,
        "accepts": _accepts(config, parse_price),
        "metadata": {"method": verb},
    }
    if config.description:
        item["description"] = config.description
    if config.mime_type:
        item["mimeType"] = config.mime_type
    if config.extensions:
        item["extensions"] = _stamped(config.extensions, verb)
    return item


def _stamped(extensions: dict, verb: str) -> dict:
    """Publish the discovery extension the way the challenge does.

    The SDK leaves ``info.input.method`` out of a stored declaration and fills
    it in from the request that triggers the 402. A manifest has no such
    request, so an unstamped copy would fail the very validator the facilitator
    runs — the same way a body declaration marked "GET" once kept /encode/wrap
    out of the catalog. Stamp it here instead of shipping something invalid.
    """
    import copy

    stamped = copy.deepcopy(extensions)
    bazaar = stamped.get("bazaar")
    if isinstance(bazaar, dict) and isinstance(bazaar.get("info"), dict):
        request_input = bazaar["info"].get("input")
        if isinstance(request_input, dict):
            request_input.setdefault("method", verb)
    return stamped


def build_manifest(routes: dict, base_url: str,
                   parse_price: Callable | None = None) -> dict:
    """Every paid resource with its real payment terms.

    Only POST entries are listed: the same URL is gated for GET as well, but
    it is one resource, and POST is the method that carries the request body.
    """
    items = [_item(pattern.partition(" ")[2], "POST", config, base_url, parse_price)
             for pattern, config in routes.items()
             if pattern.startswith("POST ")]
    items.sort(key=lambda i: i["resource"])
    return {"x402Version": 2,
            "items": items,
            "pagination": {"limit": len(items), "offset": 0, "total": len(items)}}
