import pytest

from evm_canon.errors import CanonError, UNKNOWN_CHAIN
from evm_canon.resolve import resolve_chain


@pytest.mark.parametrize("alias,chain_id", [
    ("eth", 1), ("mainnet", 1), ("Ethereum", 1),
    ("matic", 137), ("polygon", 137),
    ("arb", 42161), ("Arbitrum One", 42161),
    ("base", 8453), ("op", 10), ("optimism", 10),
    ("bsc", 56), ("bnb", 56), ("avax", 43114), ("avalanche", 43114),
    ("xlayer", 196),
])
def test_alias_normalization(registry, alias, chain_id):
    assert resolve_chain(registry, alias, None)["chainId"] == chain_id


def test_chain_id_is_source_of_truth(registry):
    # conflicting alias is ignored when a chainId is present
    assert resolve_chain(registry, "polygon", 42161)["chainId"] == 42161


def test_unknown_alias_and_id(registry):
    with pytest.raises(CanonError) as e:
        resolve_chain(registry, "notachain", None)
    assert e.value.code == UNKNOWN_CHAIN
    with pytest.raises(CanonError) as e:
        resolve_chain(registry, None, 999999)
    assert e.value.code == UNKNOWN_CHAIN


def test_no_chain_given_is_none(registry):
    assert resolve_chain(registry, None, None) is None
