from evm_canon.pipeline import canonicalize


def test_happy_path_full_record(registry):
    payload = {
        "raw": {
            "token": "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
            "chain": "arb",
            "amount": "1500000",
            "time": 1752062400,
        },
        "hints": {"chain": "arbitrum", "prefer": "registry"},
    }
    out = canonicalize(payload, registry)
    r, rep = out["result"], out["report"]
    assert r == {
        "chainId": 42161, "chain": "arbitrum",
        "address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "symbol": "USDC", "name": "USD Coin", "decimals": 6,
        "amount_raw": "1500000", "amount_human": "1.5",
        "timestamp_utc": "2025-07-09T12:00:00Z",
        "is_native": False, "wrapped_of": None,
    }
    assert rep["resolved_by"] == "registry"
    # wrapped_of is genuinely null for USDC and honest-nulls lists every null
    assert rep["fields_null"] == ["wrapped_of"]
    assert rep["scam_suspected"] is False
    assert rep["registry_version"] == registry.version


def test_wrapped_native_mapping(registry):
    out = canonicalize({"raw": {"symbol": "WETH", "chain": "ethereum"}}, registry)
    assert out["result"]["wrapped_of"] == "ETH"
    assert out["result"]["is_native"] is False


def test_honest_nulls_listed(registry):
    out = canonicalize({"raw": {"symbol": "DAI", "chain": "eth"}}, registry)
    nulls = out["report"]["fields_null"]
    for f in ("amount_raw", "amount_human", "timestamp_utc"):
        assert f in nulls
        assert out["result"][f] is None


def test_custom_target_schema_pass(registry):
    schema = {"type": "object", "required": ["result"],
              "properties": {"result": {"type": "object",
                                        "required": ["symbol", "decimals"]}}}
    out = canonicalize({"raw": {"symbol": "USDC", "chain": "base"},
                        "target_schema": schema}, registry)
    assert out["result"]["symbol"] == "USDC"


def test_custom_target_schema_failure_is_typed_error(registry):
    # requires a non-null amount, which this input cannot produce
    schema = {"type": "object",
              "properties": {"result": {"type": "object",
                                        "properties": {"amount_raw": {"type": "string"}},
                                        "required": ["amount_raw"]}}}
    out = canonicalize({"raw": {"symbol": "USDC", "chain": "base"},
                        "target_schema": schema}, registry)
    assert out["error"]["code"] == "SCHEMA_VALIDATION_FAILED"
    assert out["error"]["field"] == "result.amount_raw"


def test_default_output_always_schema_valid(registry):
    from evm_canon.validate import validate_output
    out = canonicalize({"raw": {"symbol": "USDT", "chainId": 137,
                                "amount": "2500000", "ts": "2026-01-01T00:00:00Z"}},
                       registry)
    validate_output(out)  # must not raise


def test_empty_raw_is_all_null_not_crash(registry):
    out = canonicalize({"raw": {}}, registry)
    assert all(v is None for v in out["result"].values())
    assert len(out["report"]["fields_null"]) == 11
