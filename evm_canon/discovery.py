"""Bazaar discovery metadata for the paid routes.

The Coinbase facilitator indexes a resource the first time it settles a
payment for it, using whatever the route declared here — there is no separate
registration step. Buyers (and their agents) then match on these schemas, so
the declarations have to describe what the endpoint really accepts and
returns; a wrong example is worse than none.

Only loaded on the Base/CDP deployment (see server.py) — the OKX venue has
its own listing mechanism.
"""

TRADE_SCHEMA = {
    "type": "object",
    "required": ["side", "asset", "amount", "price", "time"],
    "properties": {
        "side": {"type": "string", "enum": ["buy", "sell"]},
        "asset": {"type": "string"},
        "amount": {"type": "string", "description": "decimal string, never a float"},
        "price": {"type": "string", "description": "unit price in the quote currency"},
        "fee": {"type": "string"},
        "time": {"description": "unix seconds/millis or ISO-8601"},
    },
}

_TRADES_EXAMPLE = [
    {"side": "buy", "asset": "BTC", "amount": "1", "price": "10000", "time": 1},
    {"side": "buy", "asset": "BTC", "amount": "1", "price": "30000", "time": 2},
    {"side": "sell", "asset": "BTC", "amount": "1", "price": "40000", "time": 3},
]


def _lots_input(with_method: bool) -> dict:
    props = {"trades": {"type": "array", "items": TRADE_SCHEMA}}
    if with_method:
        props["method"] = {"type": "string",
                           "enum": ["FIFO", "LIFO", "HIFO", "ACB"],
                           "default": "FIFO"}
    return {"type": "object", "required": ["trades"], "properties": props}


def _spec(input_example, input_schema, output_example):
    return {"input": input_example, "input_schema": input_schema,
            "output_example": output_example}


# path (without leading slash) -> declaration inputs
SPECS: dict[str, dict] = {
    "canonicalize": _spec(
        {"symbol": "USDC", "chain": "arbitrum", "amount": "1500000"},
        {"type": "object",
         "properties": {
             "token": {"type": "string", "description": "contract address"},
             "symbol": {"type": "string"},
             "chain": {"type": "string", "description": "name, alias or chainId"},
             "amount": {"type": "string",
                        "description": "raw integer or human decimal string"},
             "decimals": {"type": "integer"},
             "time": {"description": "unix seconds/millis or ISO-8601"},
         }},
        {"result": {"chainId": 42161, "chain": "arbitrum",
                    "address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
                    "symbol": "USDC", "decimals": 6,
                    "amount_raw": "1500000", "amount_human": "1.5",
                    "is_native": False},
         "report": {"resolved_by": "registry", "scam_suspected": False,
                    "fields_null": ["timestamp_utc"], "confidence": 0.95}}),

    "decode": _spec(
        {"data": "0xa9059cbb000000000000000000000000af88d065e77c8cc2239327c5edb"
                 "3a432268e5831000000000000000000000000000000000000000000000000"
                 "0000000000016e360"},
        {"type": "object", "required": ["data"],
         "properties": {
             "data": {"type": "string", "description": "0x-prefixed calldata"},
             "to": {"type": "string"},
             "value": {"type": "string", "description": "native value in wei"},
         }},
        {"result": {"selector": "0xa9059cbb",
                    "function": {"name": "transfer",
                                 "signature": "transfer(address,uint256)",
                                 "args": [{"name": "to", "type": "address",
                                           "value": "0xaf88…5831"},
                                          {"name": "amount", "type": "uint256",
                                           "value": "1500000"}]},
                    "standard": "ERC-20"},
         "report": {"decoded": True, "risk_flags": [], "confidence": "high"}}),

    "resolve": _spec(
        {"name": "vitalik.eth"},
        {"type": "object",
         "properties": {"name": {"type": "string"},
                        "address": {"type": "string"}},
         "description": "provide exactly one of name or address"},
        {"result": {"name": "vitalik.eth",
                    "address": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
                    "reverse_verified": True},
         "report": {"resolved_by": "ens_onchain", "chainId": 1}}),

    "lots": _spec(
        {"method": "FIFO", "trades": _TRADES_EXAMPLE},
        _lots_input(True),
        {"result": {"method": "FIFO",
                    "disposals": [{"asset": "BTC", "amount": "1",
                                   "proceeds": "40000", "cost_basis": "10000",
                                   "gain": "30000"}],
                    "totals": {"gain": "30000"}},
         "report": {"disposal_count": 1, "engine_version": "lots@2"}}),

    "lots/validate": _spec(
        {"trades": _TRADES_EXAMPLE},
        _lots_input(False),
        {"result": {"ok": False,
                    "issues": [{"code": "INSUFFICIENT_INVENTORY",
                                "trade_index": 1,
                                "detail": "sell 2 BTC but only 1 held"}],
                    "summary": {"trades": 3, "buys": 2, "sells": 1}},
         "report": {"issue_count": 1}}),
}

# The method- and projection-specific lot routes share one shape; only the
# resulting numbers differ, so they reuse the generic lots declaration with
# the method parameter dropped where the route already fixes it.
for _view in ("fifo", "lifo", "hifo", "acb"):
    SPECS[f"lots/{_view}"] = _spec(
        {"trades": _TRADES_EXAMPLE}, _lots_input(False),
        SPECS["lots"]["output_example"])
SPECS["lots/gains"] = _spec(
    {"trades": _TRADES_EXAMPLE}, _lots_input(True),
    {"result": {"method": "FIFO",
                "disposals": [{"asset": "BTC", "amount": "1",
                               "proceeds": "40000", "cost_basis": "10000",
                               "gain": "30000"}],
                "totals": {"proceeds": "40000", "cost_basis": "10000",
                           "gain": "30000"}},
     "report": {"disposal_count": 1}})
SPECS["lots/inventory"] = _spec(
    {"trades": _TRADES_EXAMPLE}, _lots_input(True),
    {"result": {"method": "FIFO",
                "inventory": [{"asset": "BTC",
                               "lots": [{"time": 2, "amount": "1",
                                         "unit_cost": "30000"}]}]},
     "report": {"disposal_count": 1}})
SPECS["lots/holding-period"] = _spec(
    {"trades": _TRADES_EXAMPLE, "long_term_days": 365},
    {"type": "object", "required": ["trades"],
     "properties": {"trades": {"type": "array", "items": TRADE_SCHEMA},
                    "long_term_days": {"type": "integer", "default": 365},
                    "method": {"type": "string",
                               "enum": ["FIFO", "LIFO", "HIFO"]}}},
    {"result": {"method": "FIFO", "long_term_days": 365,
                "disposals": [{"asset": "BTC",
                               "short_term": {"amount": "0", "gain": "0"},
                               "long_term": {"amount": "1", "gain": "30000"}}],
                "totals": {"long_term": {"gain": "30000"}}},
     "report": {"disposal_count": 1}})


_ENCODE_OUT = {"result": {"to": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
                          "data": "0xa9059cbb…", "value": "0",
                          "selector": "0xa9059cbb",
                          "function": {"name": "transfer",
                                       "signature": "transfer(address,uint256)"}},
               "report": {"risk_flags": [], "decimals_used": 6,
                          "amount_interpreted_as": "human", "signed": False}}
_ENCODE_IN_SCHEMA = {
    "type": "object", "required": ["args"],
    "properties": {
        "function": {"type": "string",
                     "description": "name or full signature (generic route only)"},
        "args": {"type": "object",
                 "description": "arguments keyed by parameter name"},
        "token": {"type": "string", "description": "symbol or contract address"},
        "chain": {"description": "chain name, alias or numeric chainId"},
        "value": {"type": "string", "description": "native value in wei"},
    }}

SPECS["encode"] = _spec(
    {"function": "transfer", "token": "USDC", "chain": "arbitrum",
     "args": {"to": "0x1111111111111111111111111111111111111111",
              "amount": "1.5"}},
    _ENCODE_IN_SCHEMA, _ENCODE_OUT)
for _path, _example in (
        ("transfer", {"token": "USDC", "chain": "arbitrum",
                      "args": {"to": "0x1111111111111111111111111111111111111111",
                               "amount": "1.5"}}),
        ("approve", {"token": "USDC", "chain": "arbitrum",
                     "args": {"spender": "0x1111111111111111111111111111111111111111",
                              "amount": "100"}}),
        ("transfer-from", {"token": "USDC", "chain": "arbitrum",
                           "args": {"from": "0x1111111111111111111111111111111111111111",
                                    "to": "0x2222222222222222222222222222222222222222",
                                    "amount": "5"}}),
        ("nft-transfer", {"args": {"from": "0x1111111111111111111111111111111111111111",
                                   "to": "0x2222222222222222222222222222222222222222",
                                   "tokenId": 42}}),
        ("approve-all", {"args": {"operator": "0x1111111111111111111111111111111111111111",
                                  "approved": True}}),
        # deposit() takes no arguments, but "args" is required by the shared
        # schema — an example that omits it fails validation and the route is
        # dropped from the catalog instead of being listed.
        ("wrap", {"args": {}, "value": "1000000000000000000"}),
        ("unwrap", {"args": {"amount": 1000000000000000000}}),
        ("swap", {"args": {"amountIn": 1000000, "amountOutMin": 1,
                           "path": ["0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
                                    "0x2222222222222222222222222222222222222222"],
                           "to": "0x1111111111111111111111111111111111111111",
                           "deadline": 1800000000}}),
        ("permit", {"args": {"owner": "0x1111111111111111111111111111111111111111",
                             "spender": "0x2222222222222222222222222222222222222222",
                             "value": 1000000, "deadline": 1800000000,
                             "v": 27, "r": "0x" + "00" * 32, "s": "0x" + "00" * 32}}),
):
    SPECS[f"encode/{_path}"] = _spec(_example, _ENCODE_IN_SCHEMA, _ENCODE_OUT)


SPECS["lots/balances"] = _spec(
    {"trades": _TRADES_EXAMPLE}, _lots_input(False),
    {"result": {"assets": [{"asset": "BTC", "bought": "2", "sold": "1",
                            "net": "1", "trades": 3,
                            "first_trade": 1, "last_trade": 3}],
                "total_fees": "0", "negative_assets": []},
     "report": {"trades_processed": 3, "asset_count": 1}})

SPECS["checksum"] = _spec(
    {"addresses": ["0xaf88d065e77c8cc2239327c5edb3a432268e5831",
                   "0x1111111111111111111111111111111111111111"]},
    {"type": "object",
     "properties": {"address": {"type": "string"},
                    "addresses": {"type": "array", "items": {"type": "string"}}},
     "description": "one 'address' or an 'addresses' batch, max 200"},
    {"result": {"addresses": [{"input": "0xaf88…5831",
                               "address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
                               "valid": True, "was_checksummed": False}],
                "all_valid": True},
     "report": {"count": 1, "invalid_count": 0}})


def declaration_for(path: str):
    """Bazaar extension dict for a route, or None if the path has no spec."""
    spec = SPECS.get(path)
    if spec is None:
        return None
    from x402.extensions.bazaar import OutputConfig, declare_discovery_extension
    return declare_discovery_extension(
        input=spec["input"],
        input_schema=spec["input_schema"],
        body_type="json",
        output=OutputConfig(example=spec["output_example"]),
    )
