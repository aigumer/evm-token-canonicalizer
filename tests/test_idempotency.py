"""Same input + same pinned registry_version -> byte-identical output."""

import json

from evm_canon.pipeline import canonicalize
from evm_canon.registry import Registry
from evm_canon.validate import canonical_json

PAYLOADS = [
    {"raw": {"symbol": "USDC", "chain": "arbitrum", "amount": "1500000",
             "timestamp": 1752062400}},
    {"raw": {"address": "0xdac17f958d2ee523a2206206994597c13d831ec7",
             "chain": "eth", "value": 999999999999}},
    {"raw": {"symbol": "USDC"}},                       # ambiguous -> typed error
    {"raw": {"symbol": "ETH", "chain": "base", "amount": "0.05"}},
    {"raw": {}},
]


def test_byte_identical_across_runs(registry):
    for payload in PAYLOADS:
        a = canonical_json(canonicalize(json.loads(json.dumps(payload)), registry))
        b = canonical_json(canonicalize(json.loads(json.dumps(payload)), registry))
        assert a == b, payload


def test_byte_identical_across_registry_instances():
    payload = {"raw": {"symbol": "USDT", "chain": "polygon", "amount": "123456"}}
    a = canonical_json(canonicalize(payload, Registry()))
    b = canonical_json(canonicalize(payload, Registry()))
    assert a == b


def test_registry_version_pinned_and_surfaced(registry):
    out = canonicalize({"raw": {"symbol": "DAI", "chain": "eth"}}, registry)
    assert out["report"]["registry_version"] == "tokenlists@2026-07-01"
