from evm_canon.detect import detect_fields

ADDR = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


def test_key_aliases():
    f = detect_fields({"contract_address": ADDR, "ticker": "USDC",
                       "network": "arbitrum", "token_decimals": 6})
    assert f["address"] == ADDR
    assert f["symbol"] == "USDC"
    assert f["chain"] == "arbitrum"
    assert f["decimals"] == 6


def test_address_hiding_in_symbol_field():
    f = detect_fields({"symbol": ADDR})
    assert f["address"] == ADDR
    assert "symbol" not in f


def test_amount_raw_vs_human():
    assert detect_fields({"amount": "1500000"})["amount_raw"] == "1500000"
    assert detect_fields({"amount": 1500000})["amount_raw"] == "1500000"
    assert detect_fields({"amount": "1.5"})["amount_human"] == "1.5"
    assert detect_fields({"amount": "1,500,000"})["amount_raw"] == "1500000"


def test_negative_or_garbage_amount_unclassified():
    f = detect_fields({"amount": "-5"})
    assert "amount_raw" not in f and "amount_human" not in f
    assert f["amount_key"] == "amount"


def test_chain_id_from_string_and_int():
    assert detect_fields({"chainId": 42161})["chain_id"] == 42161
    assert detect_fields({"chain": "137"})["chain_id"] == 137
    assert detect_fields({"chain": "polygon"})["chain"] == "polygon"
