from eth_abi import decode as abi_decode
from eth_utils import keccak

from evm_canon.decoder import UINT256_MAX, decode_calldata
from evm_canon.encode import encode_call

USDC_ARB = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
SPENDER = "0x" + "11" * 20


def _enc(**raw):
    return encode_call({"raw": raw})


def test_transfer_human_amount_uses_registry_decimals():
    out = _enc(function="transfer", token="USDC", chain="arbitrum",
               args={"to": SPENDER, "amount": "1.5"})
    r, rep = out["result"], out["report"]
    assert r["to"] == USDC_ARB
    assert r["selector"] == "0x" + keccak(text="transfer(address,uint256)")[:4].hex()
    assert rep["decimals_used"] == 6
    assert rep["amount_interpreted_as"] == "human"
    assert rep["signed"] is False
    # 1.5 USDC at 6 decimals == 1500000 raw
    args = abi_decode(["address", "uint256"], bytes.fromhex(r["data"][10:]))
    assert args[1] == 1500000


def test_integer_amount_is_raw_and_reported():
    out = _enc(function="transfer", token="USDC", chain="arbitrum",
               args={"to": SPENDER, "amount": 1500000})
    assert out["report"]["amount_interpreted_as"] == "raw"
    args = abi_decode(["address", "uint256"], bytes.fromhex(out["result"]["data"][10:]))
    assert args[1] == 1500000


def test_encode_decode_roundtrip():
    out = _enc(function="transfer", token="USDC", chain="arbitrum",
               args={"to": SPENDER, "amount": "2.25"})
    back = decode_calldata({"raw": {"data": out["result"]["data"]}})
    fn = back["result"]["function"]
    assert fn["name"] == "transfer"
    assert fn["args"][1]["value"] == "2250000"


def test_unlimited_approval_is_encoded_but_flagged():
    out = _enc(function="approve", token="USDC", chain="arbitrum",
               args={"spender": SPENDER, "amount": UINT256_MAX})
    assert out["result"]["data"].startswith("0x095ea7b3")
    assert "unlimited_approval" in out["report"]["risk_flags"]


def test_approval_for_all_flagged():
    out = _enc(function="setApprovalForAll",
               args={"operator": SPENDER, "approved": True})
    assert "approval_for_all_granted" in out["report"]["risk_flags"]


def test_admin_action_flagged():
    out = _enc(function="transferOwnership", args={"newOwner": SPENDER})
    assert "admin_action" in out["report"]["risk_flags"]


def test_array_and_path_arguments():
    out = _enc(function="swapExactTokensForTokens",
               args={"amountIn": 1000, "amountOutMin": 1,
                     "path": [USDC_ARB, SPENDER], "to": SPENDER,
                     "deadline": 1800000000})
    assert out["result"]["data"].startswith("0x38ed1739")
    back = decode_calldata({"raw": {"data": out["result"]["data"]}})
    assert len(back["result"]["function"]["args"][2]["value"]) == 2


def test_args_may_be_positional():
    a = _enc(function="transfer", token="USDC", chain="arbitrum",
             args=[SPENDER, 5])["result"]["data"]
    b = _enc(function="transfer", token="USDC", chain="arbitrum",
             args={"to": SPENDER, "amount": 5})["result"]["data"]
    assert a == b


def test_no_arg_function():
    out = _enc(function="deposit", value="1000")
    assert out["result"]["data"] == "0xd0e30db0"
    assert out["result"]["value"] == "1000"
    # wrapping is meant to carry value, so it is not flagged as unexpected
    assert "sends_native_value" not in out["report"]["risk_flags"]


def test_unexpected_native_value_is_flagged():
    out = _enc(function="transfer", token="USDC", chain="arbitrum",
               args={"to": SPENDER, "amount": 1}, value="500")
    assert "sends_native_value" in out["report"]["risk_flags"]


def test_overloaded_name_resolved_by_argument_names():
    erc20 = _enc(function="approve", token="USDC", chain="arbitrum",
                 args={"spender": SPENDER, "amount": 1})
    assert erc20["result"]["function"]["signature"] == "approve(address,uint256)"
    permit2 = _enc(function="approve",
                   args={"token": USDC_ARB, "spender": SPENDER,
                         "amount": 1, "expiration": 0})
    assert permit2["result"]["function"]["signature"] == \
        "approve(address,address,uint160,uint48)"


def test_overload_resolved_by_names_else_candidates():
    # argument names identify the ERC-721 3-arg form on their own
    ok = _enc(function="safeTransferFrom",
              args={"from": SPENDER, "to": SPENDER, "tokenId": 1})
    assert ok["result"]["data"].startswith("0x42842e0e")
    # the ERC-1155 form is a different set of names, also unambiguous
    erc1155 = _enc(function="safeTransferFrom",
                   args={"from": SPENDER, "to": SPENDER, "id": 1,
                         "amount": 2, "data": "0x"})
    assert erc1155["result"]["function"]["signature"].endswith(
        "(address,address,uint256,uint256,bytes)")
    # when nothing narrows it, the caller gets the candidate set, not a guess
    out = _enc(function="safeTransferFrom")
    assert out["error"]["code"] == "AMBIGUOUS_FUNCTION"
    assert len(out["error"]["candidates"]) >= 2


def test_decimal_amount_without_decimals_is_typed_error():
    out = _enc(function="transfer", args={"to": SPENDER, "amount": "1.5"})
    assert out["error"]["code"] == "DECIMALS_UNKNOWN"


def test_amount_beyond_token_precision_rejected():
    out = _enc(function="transfer", token="USDC", chain="arbitrum",
               args={"to": SPENDER, "amount": "1.1234567"})
    assert out["error"]["code"] == "AMOUNT_NOT_REPRESENTABLE"


def test_typed_errors():
    assert encode_call({})["error"]["code"] == "MALFORMED_INVOCATION"
    assert _enc(function="nosuchfn")["error"]["code"] == "UNKNOWN_FUNCTION"
    assert _enc(function="transfer", args={"to": "0x1", "amount": 1}
                )["error"]["code"] == "INVALID_ADDRESS"
    assert _enc(function="transfer", args={"to": SPENDER}
                )["error"]["code"] == "MISSING_ARGUMENT"
    assert _enc(function="transfer", args={"to": SPENDER, "amount": 1.5}
                )["error"]["code"] == "FLOAT_REJECTED"
    assert _enc(function="transfer", token="USDC", chain="narnia",
                args={"to": SPENDER, "amount": 1})["error"]["code"] == "UNKNOWN_CHAIN"
    assert _enc(function="transfer", token="NOSUCHTOKEN", chain="arbitrum",
                args={"to": SPENDER, "amount": 1}
                )["error"]["code"] == "TICKER_UNRESOLVED"


def test_deterministic():
    a = _enc(function="approve", token="USDC", chain="arbitrum",
             args={"spender": SPENDER, "amount": "10.5"})
    b = _enc(function="approve", token="USDC", chain="arbitrum",
             args={"spender": SPENDER, "amount": "10.5"})
    assert a == b
