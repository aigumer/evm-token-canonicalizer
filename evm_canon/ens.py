"""ENS resolution over raw JSON-RPC (urllib, no web3 client).

Forward: name -> address. Reverse: address -> primary name, then
forward-verified — a reverse record anyone can set is only trustworthy if the
name resolves back to the same address, so unverified reverses are reported
with ``reverse_verified: false`` rather than silently trusted.

This module talks to the chain, so unlike the canonicalizer/decoder its output
depends on live network state. The report says so (``resolved_by:
"ens_onchain"``) instead of pretending determinism.

Env: EVM_CANON_RPC_1 overrides the Ethereum mainnet RPC endpoint.
"""

import json
import os
import re
import urllib.request

from eth_utils import keccak, to_checksum_address

ENS_REGISTRY = "0x00000000000C2E074eC69A0dFb2997BA6C7d2e1e"
SEL_RESOLVER = keccak(text="resolver(bytes32)")[:4]
SEL_ADDR = keccak(text="addr(bytes32)")[:4]
SEL_NAME = keccak(text="name(bytes32)")[:4]
DEFAULT_RPCS = ("https://cloudflare-eth.com", "https://eth.drpc.org",
                "https://rpc.mevblocker.io")
ZERO_ADDR = "0x" + "00" * 20


def namehash(name: str) -> bytes:
    node = b"\x00" * 32
    if name:
        for label in reversed(name.lower().split(".")):
            node = keccak(node + keccak(text=label))
    return node


def _eth_call(to: str, data: bytes, rpc: str) -> bytes:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                       "params": [{"to": to, "data": "0x" + data.hex()},
                                  "latest"]}).encode()
    req = urllib.request.Request(rpc, data=body, headers={
        "content-type": "application/json",
        # some public RPCs 403 the default Python-urllib user agent
        "user-agent": "evm-canon/0.2 (+https://github.com/aigumer/evm-token-canonicalizer)"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        out = json.loads(resp.read())
    if "error" in out:
        raise RuntimeError(out["error"].get("message", "rpc error"))
    return bytes.fromhex(out["result"][2:])


def _resolver_of(node: bytes, rpc: str) -> str | None:
    ret = _eth_call(ENS_REGISTRY, SEL_RESOLVER + node, rpc)
    if len(ret) < 32:
        return None
    addr = "0x" + ret[12:32].hex()
    return None if addr == ZERO_ADDR else addr


def _addr_of(name: str, rpc: str) -> str | None:
    node = namehash(name)
    resolver = _resolver_of(node, rpc)
    if resolver is None:
        return None
    ret = _eth_call(resolver, SEL_ADDR + node, rpc)
    if len(ret) < 32:
        return None
    addr = "0x" + ret[12:32].hex()
    return None if addr == ZERO_ADDR else to_checksum_address(addr)


def _name_of(address: str, rpc: str) -> str | None:
    node = namehash(address.lower()[2:] + ".addr.reverse")
    resolver = _resolver_of(node, rpc)
    if resolver is None:
        return None
    ret = _eth_call(resolver, SEL_NAME + node, rpc)
    if len(ret) < 64:
        return None
    offset = int.from_bytes(ret[:32])
    length = int.from_bytes(ret[offset:offset + 32])
    name = ret[offset + 32:offset + 32 + length].decode("utf-8", "replace")
    return name or None


def resolve(payload: dict, rpc: str | None = None) -> dict:
    """{"raw": {"name": "x.eth"}} or {"raw": {"address": "0x.."}} ->
    {"result", "report"} or {"error"}."""
    raw = payload.get("raw") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return _error("MALFORMED_INVOCATION", "raw", "missing 'raw' object")

    env_rpc = os.environ.get("EVM_CANON_RPC_1")
    candidates = [rpc] if rpc else [env_rpc] if env_rpc else list(DEFAULT_RPCS)

    name, address = raw.get("name"), raw.get("address")
    if bool(name) == bool(address):
        return _error("MALFORMED_INVOCATION", "raw",
                      "provide exactly one of 'name' or 'address'")

    if name and not re.fullmatch(r"[a-zA-Z0-9\-_.]+\.[a-zA-Z]{2,}", str(name)):
        return _error("INVALID_NAME", "raw.name", "not a valid ENS name")
    if address and not re.fullmatch(r"0x[0-9a-fA-F]{40}", str(address)):
        return _error("INVALID_ADDRESS", "raw.address", "not 20-byte hex")

    last_exc: Exception | None = None
    for endpoint in candidates:
        try:
            if name:
                addr = _addr_of(str(name), endpoint)
                result = {"name": str(name).lower(), "address": addr,
                          "reverse_verified": None}
                nulls = [] if addr else ["address"]
            else:
                addr = to_checksum_address(str(address))
                primary = _name_of(addr, endpoint)
                verified = None
                if primary:
                    verified = _addr_of(primary, endpoint) == addr
                result = {"name": primary, "address": addr,
                          "reverse_verified": verified}
                nulls = [] if primary else ["name"]
            break
        except Exception as exc:
            last_exc = exc
    else:
        return _error("RPC_UNAVAILABLE", None, str(last_exc)[:200])

    return {"result": result,
            "report": {"resolved_by": "ens_onchain",
                       "fields_null": nulls,
                       "chainId": 1}}


def _error(code: str, field: str | None, detail: str) -> dict:
    return {"error": {"code": code, "field": field, "detail": detail}}
