from eth_abi import encode as abi_encode
from eth_utils import keccak

from evm_canon.decoder import UINT256_MAX, decode_calldata, default_sigdb


def _calldata(sig: str, types: list[str], args: list) -> str:
    return "0x" + (keccak(text=sig)[:4] + abi_encode(types, args)).hex()


def test_sigdb_arity_consistent():
    db = default_sigdb()
    assert db.version == "sigdb@2026-07-10"
    for entry in db.by_selector.values():
        assert len(entry["types"]) == len(entry["names"])


def test_erc20_transfer_decodes():
    data = _calldata("transfer(address,uint256)", ["address", "uint256"],
                     ["0xaf88d065e77c8cc2239327c5edb3a432268e5831", 1500000])
    out = decode_calldata({"raw": {"data": data}})
    fn = out["result"]["function"]
    assert fn["name"] == "transfer"
    assert fn["args"][0]["value"] == "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    assert fn["args"][1]["value"] == "1500000"   # ints are strings, never floats
    assert out["report"]["decoded"] is True
    assert out["report"]["risk_flags"] == []


def test_unlimited_approval_flagged():
    data = _calldata("approve(address,uint256)", ["address", "uint256"],
                     ["0x" + "11" * 20, UINT256_MAX])
    out = decode_calldata({"raw": {"data": data}})
    assert "unlimited_approval" in out["report"]["risk_flags"]


def test_bounded_approval_not_flagged():
    data = _calldata("approve(address,uint256)", ["address", "uint256"],
                     ["0x" + "11" * 20, 10**6])
    out = decode_calldata({"raw": {"data": data}})
    assert "unlimited_approval" not in out["report"]["risk_flags"]


def test_set_approval_for_all_flagged():
    data = _calldata("setApprovalForAll(address,bool)", ["address", "bool"],
                     ["0x" + "22" * 20, True])
    out = decode_calldata({"raw": {"data": data}})
    assert "approval_for_all_granted" in out["report"]["risk_flags"]


def test_admin_action_flagged():
    data = _calldata("transferOwnership(address)", ["address"],
                     ["0x" + "33" * 20])
    out = decode_calldata({"raw": {"data": data}})
    assert "admin_action" in out["report"]["risk_flags"]


def test_unknown_selector_honest_nulls():
    out = decode_calldata({"raw": {"data": "0xdeadbeef", "value": "5"}})
    r = out["result"]
    assert r["selector"] == "0xdeadbeef"
    assert r["function"] is None
    assert out["report"]["decoded"] is False
    assert "unknown_selector" in out["report"]["risk_flags"]
    assert "native_value_to_unknown_function" in out["report"]["risk_flags"]


def test_plain_native_transfer():
    out = decode_calldata({"raw": {"data": "",
                                   "to": "0x" + "44" * 20,
                                   "value": "1000000000000000000"}})
    assert out["result"]["is_plain_transfer"] is True
    assert out["result"]["value_eth"] == "1"


def test_args_mismatch_is_honest():
    sel = "0x" + keccak(text="transfer(address,uint256)")[:4].hex()
    out = decode_calldata({"raw": {"data": sel + "ff"}})
    assert out["report"]["decoded"] is False
    assert "calldata_args_mismatch" in out["report"]["risk_flags"]
    assert out["result"]["function"]["args"] is None


def test_typed_errors():
    assert decode_calldata({})["error"]["code"] == "MALFORMED_INVOCATION"
    assert decode_calldata({"raw": {"data": "zzz"}})["error"]["code"] == "INVALID_CALLDATA"
    assert decode_calldata({"raw": {"data": "0x", "to": "0x1"}})["error"]["code"] == "INVALID_ADDRESS"
    assert decode_calldata({"raw": {"data": "0x", "value": "-1"}})["error"]["code"] == "INVALID_VALUE"
