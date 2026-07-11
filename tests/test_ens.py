"""ENS unit tests — namehash vectors + resolve() against a mocked RPC."""

from unittest.mock import patch

from evm_canon import ens


def test_namehash_vectors():
    # reference vectors from EIP-137
    assert ens.namehash("") == b"\x00" * 32
    assert ens.namehash("eth").hex() == (
        "93cdeb708b7545dc668eb9280176169d1c33cfd8ed6f04690a0bcc88a93fc4ae")
    assert ens.namehash("foo.eth").hex() == (
        "de9b09fd7c5f901e23a3f19fecc54828e9c848539801e86591bd9801b019f84f")


def test_resolve_input_validation():
    assert ens.resolve({})["error"]["code"] == "MALFORMED_INVOCATION"
    assert ens.resolve({"raw": {}})["error"]["code"] == "MALFORMED_INVOCATION"
    assert ens.resolve({"raw": {"name": "a.eth", "address": "0x" + "11" * 20}}
                       )["error"]["code"] == "MALFORMED_INVOCATION"
    assert ens.resolve({"raw": {"name": "not_a_name"}}
                       )["error"]["code"] == "INVALID_NAME"
    assert ens.resolve({"raw": {"address": "0x123"}}
                       )["error"]["code"] == "INVALID_ADDRESS"


def test_forward_resolution_mocked():
    owner = "0x" + "aa" * 20

    def fake_call(to, data, rpc):
        if data[:4] == ens.SEL_RESOLVER:
            return bytes(12) + bytes.fromhex("bb" * 20)
        if data[:4] == ens.SEL_ADDR:
            return bytes(12) + bytes.fromhex("aa" * 20)
        raise AssertionError("unexpected selector")

    with patch.object(ens, "_eth_call", fake_call):
        out = ens.resolve({"raw": {"name": "alice.eth"}})
    assert out["result"]["address"].lower() == owner
    assert out["report"]["resolved_by"] == "ens_onchain"
    assert out["report"]["fields_null"] == []


def test_reverse_resolution_verified_mocked():
    def fake_call(to, data, rpc):
        if data[:4] == ens.SEL_RESOLVER:
            return bytes(12) + bytes.fromhex("bb" * 20)
        if data[:4] == ens.SEL_NAME:
            name = b"alice.eth"
            return ((32).to_bytes(32) + len(name).to_bytes(32)
                    + name + bytes(32 - len(name)))
        if data[:4] == ens.SEL_ADDR:
            return bytes(12) + bytes.fromhex("aa" * 20)
        raise AssertionError("unexpected selector")

    with patch.object(ens, "_eth_call", fake_call):
        out = ens.resolve({"raw": {"address": "0x" + "AA" * 20}})
    assert out["result"]["name"] == "alice.eth"
    assert out["result"]["reverse_verified"] is True


def test_unset_name_is_honest_null():
    def fake_call(to, data, rpc):
        return bytes(32)  # zero resolver everywhere

    with patch.object(ens, "_eth_call", fake_call):
        out = ens.resolve({"raw": {"name": "nobody-here.eth"}})
    assert out["result"]["address"] is None
    assert out["report"]["fields_null"] == ["address"]


def test_rpc_failure_is_typed():
    def fake_call(to, data, rpc):
        raise RuntimeError("connection refused")

    with patch.object(ens, "_eth_call", fake_call):
        out = ens.resolve({"raw": {"name": "alice.eth"}})
    assert out["error"]["code"] == "RPC_UNAVAILABLE"
