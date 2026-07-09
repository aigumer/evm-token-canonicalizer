import pytest

from evm_canon.payment import A2MCPBilling, PRICE_PER_RECORD_MICRO


def test_quote_is_pure_int_math():
    b = A2MCPBilling()
    q = b.quote(3)
    assert isinstance(q, int)
    assert q == 3 * PRICE_PER_RECORD_MICRO


def test_dry_run_charge_receipt():
    b = A2MCPBilling(asset="USDT")
    r = b.charge("agent:0xabc", records=2)
    assert r.settled is False
    assert r.amount_micro == 2 * PRICE_PER_RECORD_MICRO
    assert r.asset == "USDT"


def test_live_mode_blocks_until_sdk_wired(monkeypatch):
    monkeypatch.setenv("EVM_CANON_BILLING", "live")
    with pytest.raises(NotImplementedError):
        A2MCPBilling().charge("agent:0xabc")
