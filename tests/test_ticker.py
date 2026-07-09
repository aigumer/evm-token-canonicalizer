from evm_canon.pipeline import canonicalize

ARB_USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"


def test_unique_resolution_with_chain_hint(registry):
    out = canonicalize({"raw": {"symbol": "USDC"},
                        "hints": {"chain": "arbitrum"}}, registry)
    assert out["result"]["address"] == ARB_USDC
    assert out["result"]["chainId"] == 42161
    assert out["result"]["decimals"] == 6
    assert out["report"]["resolved_by"] == "registry"
    assert out["report"]["symbol_ambiguous"] is False


def test_cross_chain_ambiguity_returns_candidate_set(registry):
    out = canonicalize({"raw": {"symbol": "USDC"}}, registry)
    assert out["error"]["code"] == "TICKER_AMBIGUOUS"
    cands = out["error"]["candidates"]
    assert len(cands) >= 6  # USDC on eth/op/bsc/polygon/base/arb/avax
    assert all(c["symbol"] == "USDC" for c in cands)


def test_same_chain_ambiguity_even_with_hint(ambiguous_registry):
    out = canonicalize({"raw": {"symbol": "DUP"}, "hints": {"chain": "eth"}},
                       ambiguous_registry)
    assert out["error"]["code"] == "TICKER_AMBIGUOUS"
    assert len(out["error"]["candidates"]) == 2


def test_unknown_ticker(registry):
    out = canonicalize({"raw": {"symbol": "NOPECOIN"}}, registry)
    assert out["error"]["code"] == "TICKER_UNRESOLVED"


def test_native_eth_with_chain(registry):
    out = canonicalize({"raw": {"symbol": "ETH", "chain": "ethereum"}}, registry)
    r = out["result"]
    assert r["is_native"] is True
    assert r["address"] is None
    assert r["decimals"] == 18
    assert "address" in out["report"]["fields_null"]


def test_native_eth_without_chain_is_ambiguous(registry):
    # ETH is native on ethereum, optimism, base, arbitrum
    out = canonicalize({"raw": {"symbol": "ETH"}}, registry)
    assert out["error"]["code"] == "TICKER_AMBIGUOUS"


def test_llm_pick_is_revalidated_and_used(ambiguous_registry):
    def llm(symbol, hints, candidates):
        return {"chainId": 1, "address": "0x" + "22" * 20}

    out = canonicalize({"raw": {"symbol": "DUP"}, "hints": {"chain": "eth"}},
                       ambiguous_registry, llm=llm)
    assert out["result"]["decimals"] == 6  # Dup Token B
    assert out["report"]["symbol_ambiguous"] is True
    assert out["report"]["resolved_by"] == "heuristic"
    assert out["report"]["candidates"]  # candidate set still surfaced


def test_llm_hallucinated_address_is_rejected(ambiguous_registry):
    def llm(symbol, hints, candidates):
        return {"chainId": 1, "address": "0x" + "99" * 20}  # not a candidate

    out = canonicalize({"raw": {"symbol": "DUP"}, "hints": {"chain": "eth"}},
                       ambiguous_registry, llm=llm)
    assert out["error"]["code"] == "TICKER_AMBIGUOUS"


def test_llm_exception_is_contained(ambiguous_registry):
    def llm(symbol, hints, candidates):
        raise RuntimeError("provider down")

    out = canonicalize({"raw": {"symbol": "DUP"}, "hints": {"chain": "eth"}},
                       ambiguous_registry, llm=llm)
    assert out["error"]["code"] == "TICKER_AMBIGUOUS"
