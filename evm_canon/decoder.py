"""Deterministic EVM calldata decoder with rule-based risk flags.

Same contract philosophy as the canonicalizer: schema-shaped output, honest
nulls, typed errors, pinned data. The signature set lives in
``data/sigdb@<date>.json``; selectors are derived from signature text with
keccak at load time so a stored selector can never disagree with its
signature. An unknown selector is not an error — it decodes to nulls with an
``unknown_selector`` flag, because "we don't know this function" is a useful,
honest answer for a caller deciding whether to sign.
"""

import json
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from eth_abi import decode as abi_decode
from eth_utils import keccak, to_checksum_address

SIGDB_PINNED = "sigdb@2026-07-10"
_DATA_DIR = Path(__file__).parent / "data"

UINT256_MAX = 2**256 - 1
UINT160_MAX = 2**160 - 1
# "effectively unlimited" approvals below the literal max still drain wallets
UNLIMITED_THRESHOLD = 2**128


class SigDB:
    def __init__(self, path: Path | None = None):
        raw = json.loads((path or _DATA_DIR / f"{SIGDB_PINNED}.json").read_text())
        self.version = raw["version"]
        self.by_selector: dict[bytes, dict] = {}
        for fn in raw["functions"]:
            sig = fn["sig"]
            selector = keccak(text=sig)[:4]
            types = _split_types(sig)
            assert len(types) == len(fn["names"]), f"arity mismatch in {sig}"
            # first entry wins on selector collision (none in curated set)
            self.by_selector.setdefault(selector, {
                "sig": sig, "name": sig.split("(", 1)[0],
                "types": types, "names": fn["names"],
                "standard": fn["standard"]})


def _split_types(sig: str) -> list[str]:
    """Split top-level argument types of a canonical signature."""
    inner = sig[sig.index("(") + 1:-1]
    if not inner:
        return []
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(inner):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(inner[start:i])
            start = i + 1
    parts.append(inner[start:])
    return parts


@lru_cache(maxsize=1)
def default_sigdb() -> SigDB:
    return SigDB()


def _humanize(value, typ: str):
    """JSON-safe, human-oriented rendering of a decoded ABI value."""
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        # ints as strings: uint256 does not fit IEEE-754 and this repo
        # never lets big numbers near floats
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
        return to_checksum_address(value)
    if isinstance(value, (list, tuple)):
        item_t = typ[:-2] if typ.endswith("[]") else ""
        return [_humanize(v, item_t) for v in value]
    return value


def _risk_flags(entry: dict, args: list, value_wei: int) -> list[str]:
    flags = []
    name = entry["name"]
    argmap = dict(zip(entry["names"], args))
    if name in ("approve", "increaseAllowance"):
        amount = argmap.get("amount") or argmap.get("addedValue") or 0
        if isinstance(amount, int) and amount >= UNLIMITED_THRESHOLD:
            flags.append("unlimited_approval")
    if entry["standard"] == "Permit2" and name == "approve":
        amount = argmap.get("amount") or 0
        if isinstance(amount, int) and amount >= UINT160_MAX:
            flags.append("unlimited_approval")
    if name == "setApprovalForAll" and argmap.get("approved") is True:
        flags.append("approval_for_all_granted")
    if name == "permit":
        flags.append("signature_based_allowance")
    if name in ("transferOwnership", "renounceOwnership",
                "upgradeTo", "upgradeToAndCall"):
        flags.append("admin_action")
    if value_wei > 0 and name not in ("deposit",):
        flags.append("sends_native_value")
    return flags


def decode_calldata(payload: dict, sigdb: SigDB | None = None) -> dict:
    """{"raw": {"data": "0x..", "to"?: addr, "value"?: wei}} ->
    {"result": {...}, "report": {...}} or {"error": {...}}."""
    sigdb = sigdb or default_sigdb()
    raw = payload.get("raw") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return _error("MALFORMED_INVOCATION", "raw", "missing 'raw' object")

    data = raw.get("data", "")
    if not isinstance(data, str) or (data and not re.fullmatch(
            r"0x([0-9a-fA-F]{2})*", data)):
        return _error("INVALID_CALLDATA", "raw.data",
                      "data must be 0x-prefixed even-length hex")

    value_str = str(raw.get("value", "0") or "0")
    if not re.fullmatch(r"[0-9]+", value_str):
        return _error("INVALID_VALUE", "raw.value",
                      "value must be a non-negative integer in wei")
    value_wei = int(value_str)

    to = raw.get("to")
    to_checksummed = None
    if to is not None:
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", str(to)):
            return _error("INVALID_ADDRESS", "raw.to", "not 20-byte hex")
        to_checksummed = to_checksum_address(to)

    blob = bytes.fromhex(data[2:]) if data else b""
    fields_null: list[str] = []
    flags: list[str] = []

    if len(blob) == 0:
        result = _base_result(to_checksummed, value_wei)
        result.update({"selector": None, "function": None, "standard": None,
                       "is_plain_transfer": value_wei > 0})
        fields_null += ["selector", "function", "standard"]
        return _ok(result, sigdb, decoded=True, flags=flags,
                   fields_null=fields_null, confidence="high")

    if len(blob) < 4:
        return _error("INVALID_CALLDATA", "raw.data",
                      "calldata shorter than a 4-byte selector")

    selector, argdata = blob[:4], blob[4:]
    entry = sigdb.by_selector.get(selector)
    result = _base_result(to_checksummed, value_wei)
    result["selector"] = "0x" + selector.hex()
    result["is_plain_transfer"] = False

    if entry is None:
        result.update({"function": None, "standard": None})
        fields_null += ["function", "standard"]
        flags.append("unknown_selector")
        if value_wei > 0:
            flags.append("native_value_to_unknown_function")
        return _ok(result, sigdb, decoded=False, flags=flags,
                   fields_null=fields_null, confidence="low")

    try:
        args = list(abi_decode(entry["types"], argdata, strict=True))
    except Exception:
        result.update({"function": {"name": entry["name"],
                                    "signature": entry["sig"], "args": None},
                       "standard": entry["standard"]})
        fields_null.append("function.args")
        flags.append("calldata_args_mismatch")
        return _ok(result, sigdb, decoded=False, flags=flags,
                   fields_null=fields_null, confidence="low")

    flags += _risk_flags(entry, args, value_wei)
    result.update({
        "function": {
            "name": entry["name"],
            "signature": entry["sig"],
            "args": [{"name": n, "type": t, "value": _humanize(v, t)}
                     for n, t, v in zip(entry["names"], entry["types"], args)],
        },
        "standard": entry["standard"],
    })
    return _ok(result, sigdb, decoded=True, flags=flags,
               fields_null=fields_null, confidence="high")


def _base_result(to: str | None, value_wei: int) -> dict:
    return {"to": to,
            "value_wei": str(value_wei),
            "value_eth": format(Decimal(value_wei).scaleb(-18).normalize(), "f")
                          if value_wei else "0"}


def _ok(result: dict, sigdb: SigDB, *, decoded: bool, flags: list[str],
        fields_null: list[str], confidence: str) -> dict:
    return {"result": result,
            "report": {"decoded": decoded,
                       "risk_flags": sorted(set(flags)),
                       "fields_null": fields_null,
                       "confidence": confidence,
                       "sigdb_version": sigdb.version}}


def _error(code: str, field: str | None, detail: str) -> dict:
    return {"error": {"code": code, "field": field, "detail": detail}}
