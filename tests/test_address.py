import pytest

from evm_canon.errors import CanonError, INVALID_ADDRESS
from evm_canon.resolve import canon_address


def test_lowercase_to_eip55_checksum():
    assert (canon_address("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
            == "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")


def test_weth_checksum():
    assert (canon_address("0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2")
            == "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")


@pytest.mark.parametrize("bad", [
    "0x123", "not-an-address", "0xZZb86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb4",   # 39 chars
    "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",     # no 0x
])
def test_invalid_address_typed_error(bad):
    with pytest.raises(CanonError) as e:
        canon_address(bad)
    assert e.value.code == INVALID_ADDRESS
