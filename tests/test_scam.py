from evm_canon.pipeline import canonicalize

FAKE_USDC = "0x1111111111111111111111111111111111111111"
REAL_USDC_ETH = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


def test_scam_double_flagged(registry):
    out = canonicalize({"raw": {"symbol": "USDC", "address": FAKE_USDC,
                                "chain": "ethereum"}}, registry)
    rep = out["report"]
    assert rep["scam_suspected"] is True
    # caller's address is preserved, never silently "fixed"
    assert out["result"]["address"] == FAKE_USDC
    assert rep["confidence"] < 0.5


def test_canonical_contract_not_flagged(registry):
    out = canonicalize({"raw": {"symbol": "USDC", "address": REAL_USDC_ETH.lower(),
                                "chain": "eth"}}, registry)
    assert out["report"]["scam_suspected"] is False
    assert out["result"]["address"] == REAL_USDC_ETH
    assert out["report"]["resolved_by"] == "registry"


def test_registry_symbol_overrides_lying_raw_symbol(registry):
    # address is canonical WETH, raw claims it's USDC -> registry truth wins
    out = canonicalize({"raw": {"symbol": "USDC",
                                "address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                                "chain": "eth"}}, registry)
    assert out["result"]["symbol"] == "WETH"
    assert out["result"]["wrapped_of"] == "ETH"
    assert any("overridden" in n for n in out["report"]["notes"])
