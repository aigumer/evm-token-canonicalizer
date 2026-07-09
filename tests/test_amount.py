import pytest

from evm_canon.pipeline import canonicalize
from evm_canon.resolve import human_to_raw, raw_to_human


def test_raw_to_human_basic():
    assert raw_to_human("1500000", 6) == "1.5"
    assert raw_to_human("1", 18) == "0.000000000000000001"
    assert raw_to_human("0", 6) == "0"
    assert raw_to_human("1000000", 6) == "1"
    assert raw_to_human("123", 0) == "123"


def test_human_to_raw_basic():
    assert human_to_raw("1.5", 6) == "1500000"
    assert human_to_raw("0.000000000000000001", 18) == "1"
    assert human_to_raw("42", 0) == "42"


def test_human_to_raw_too_many_frac_digits_is_null():
    assert human_to_raw("1.1234567", 6) is None
    assert human_to_raw("-1", 6) is None


def test_raw_to_human_rejects_non_integer():
    with pytest.raises(ValueError):
        raw_to_human("1.5", 6)


def test_decimals_unknown_error_when_amount_present(registry):
    out = canonicalize({"raw": {"address": "0x" + "ab" * 20, "chain": "eth",
                                "amount": "1000"}}, registry)
    assert out["error"]["code"] == "DECIMALS_UNKNOWN"


def test_unrepresentable_human_amount_is_honest_null(registry):
    out = canonicalize({"raw": {"symbol": "USDC", "chain": "arbitrum",
                                "amount": "1.1234567"}}, registry)
    assert out["result"]["amount_raw"] is None
    assert out["result"]["amount_human"] is None
    assert "amount_raw" in out["report"]["fields_null"]
