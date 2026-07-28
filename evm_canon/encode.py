"""Deterministic EVM calldata builder — the inverse of decoder.py.

Takes a named function plus human-readable arguments and returns the exact
bytes to put in a transaction's ``data`` field. It builds; it never signs and
never broadcasts, so the caller keeps every bit of custody.

Scope is deliberately the same pinned signature set the decoder understands
(``data/sigdb@<date>.json``): we encode only what we can also read back, and
selectors are derived from signature text rather than stored, so an encoder
and decoder disagreement is impossible by construction.

Two safety-relevant choices, stated because they are easy to get wrong:

- **Amount interpretation is never guessed silently.** A decimal string is a
  human amount and needs known decimals; a JSON integer is raw base units.
  Every response reports which reading was used, and an unresolvable decimal
  is a typed error rather than a number that is 10^18 times off.
- **Risky calls are encoded but flagged**, using the same rules as the
  decoder. This is a tool that hands back unsigned bytes the caller could
  assemble themselves, so refusing would only push them to a worse tool —
  but nobody should sign an unlimited approval without being told.
"""

import re
from decimal import Decimal, InvalidOperation

from eth_abi import encode as abi_encode
from eth_utils import to_checksum_address

from .decoder import SigDB, _risk_flags, default_sigdb
from .registry import Registry, default_registry

UINT_MAX = {256: 2**256 - 1, 160: 2**160 - 1, 48: 2**48 - 1, 8: 255}


class _Typed(Exception):
    def __init__(self, code, field, detail, **extra):
        self.err = {"code": code, "field": field, "detail": detail, **extra}


def encode_call(payload: dict, sigdb: SigDB | None = None,
                registry: Registry | None = None) -> dict:
    try:
        return _encode(payload, sigdb or default_sigdb(),
                       registry or default_registry())
    except _Typed as exc:
        return {"error": exc.err}


def _entry_for(name: str, sigdb: SigDB, args) -> dict:
    """Resolve a function name or full signature to one sigdb entry.

    Several names are overloaded (``approve`` is both ERC-20 and Permit2;
    ``safeTransferFrom`` has three forms). The supplied arguments usually
    settle it without guesswork — named args must match a signature's
    parameter names exactly, positional args its arity. When they don't
    narrow it to one, return the candidate set rather than pick, the same way
    the canonicalizer handles an ambiguous ticker.
    """
    exact = [e for e in sigdb.by_selector.values() if e["sig"] == name]
    if exact:
        return exact[0]
    matches = [e for e in sigdb.by_selector.values() if e["name"] == name]
    if not matches:
        raise _Typed("UNKNOWN_FUNCTION", "raw.function",
                     f"{name!r} is not in the pinned signature set")
    if len(matches) > 1:
        if isinstance(args, dict):
            narrowed = [m for m in matches if set(m["names"]) == set(args)]
        elif isinstance(args, list):
            narrowed = [m for m in matches if len(m["types"]) == len(args)]
        else:
            narrowed = [m for m in matches if not m["types"]]
        if len(narrowed) == 1:
            return narrowed[0]
        raise _Typed("AMBIGUOUS_FUNCTION", "raw.function",
                     f"{name!r} has several signatures and the arguments do "
                     f"not single one out; pass the full signature",
                     candidates=sorted(m["sig"] for m in matches))
    return matches[0]


def _int_arg(value, typ: str, field: str, decimals: int | None,
             report: dict) -> int:
    bits = int(re.sub(r"\D", "", typ) or 256)
    if isinstance(value, bool):
        raise _Typed("INVALID_NUMBER", field, "boolean where an integer is expected")
    if isinstance(value, float):
        raise _Typed("FLOAT_REJECTED", field,
                     "floats are not accepted; send numbers as strings")
    if isinstance(value, int):
        n = value                                   # JSON integer -> raw units
        report.setdefault("amount_interpreted_as", "raw")
    else:
        text = str(value).strip()
        if re.fullmatch(r"\d+", text):
            n = int(text)
            report.setdefault("amount_interpreted_as", "raw")
        elif re.fullmatch(r"\d*\.\d+", text):
            if decimals is None:
                raise _Typed("DECIMALS_UNKNOWN", field,
                             "decimal amount needs the token's decimals; pass "
                             "a known token/chain or send raw base units")
            scaled = Decimal(text).scaleb(decimals)
            if scaled != scaled.to_integral_value():
                raise _Typed("AMOUNT_NOT_REPRESENTABLE", field,
                             f"{text} has more precision than {decimals} decimals")
            n = int(scaled)
            report.setdefault("amount_interpreted_as", "human")
        else:
            try:
                n = int(Decimal(text))
            except (InvalidOperation, ValueError):
                raise _Typed("INVALID_NUMBER", field, f"not an integer: {value!r}")
    if n < 0:
        raise _Typed("INVALID_NUMBER", field, "must be non-negative")
    if n > UINT_MAX.get(bits, 2**bits - 1):
        raise _Typed("INVALID_NUMBER", field, f"exceeds {typ}")
    return n


def _coerce(value, typ: str, field: str, decimals: int | None,
            report: dict):
    if typ == "address":
        if not isinstance(value, str) or not re.fullmatch(
                r"0x[0-9a-fA-F]{40}", value):
            raise _Typed("INVALID_ADDRESS", field, "not 20-byte hex")
        return to_checksum_address(value)
    if typ == "bool":
        if not isinstance(value, bool):
            raise _Typed("INVALID_NUMBER", field, "must be true or false")
        return value
    if typ.startswith("uint") or typ.startswith("int"):
        return _int_arg(value, typ, field, decimals, report)
    if typ == "bytes" or re.fullmatch(r"bytes\d+", typ):
        if not isinstance(value, str) or not re.fullmatch(
                r"0x([0-9a-fA-F]{2})*", value):
            raise _Typed("INVALID_BYTES", field, "expected 0x-prefixed hex")
        return bytes.fromhex(value[2:])
    if typ.endswith("[]"):
        if not isinstance(value, list):
            raise _Typed("INVALID_ARRAY", field, "expected an array")
        item = typ[:-2]
        return [_coerce(v, item, f"{field}[{i}]", decimals, report)
                for i, v in enumerate(value)]
    if typ == "string":
        return str(value)
    raise _Typed("UNSUPPORTED_TYPE", field,
                 f"cannot build arguments of type {typ}")


def _resolve_token(raw: dict, registry: Registry) -> tuple[str | None, int | None,
                                                           int | None, str]:
    """-> (token address, decimals, chainId, how)."""
    chain_id = None
    chain = raw.get("chain")
    if chain is not None:
        if isinstance(chain, int) or str(chain).isdigit():
            chain_id = int(chain)
            if registry.chain_by_id(chain_id) is None:
                raise _Typed("UNKNOWN_CHAIN", "raw.chain", f"unknown chainId {chain_id}")
        else:
            entry = registry.chain_by_alias(str(chain))
            if entry is None:
                raise _Typed("UNKNOWN_CHAIN", "raw.chain", f"unknown chain {chain!r}")
            chain_id = entry["chainId"]

    token = raw.get("token")
    if token is None:
        return None, raw.get("decimals"), chain_id, "none"
    if isinstance(token, str) and re.fullmatch(r"0x[0-9a-fA-F]{40}", token):
        addr = to_checksum_address(token)
        meta = registry.token_by_address(chain_id, addr) if chain_id else None
        if meta:
            return addr, meta["decimals"], chain_id, "registry"
        return addr, raw.get("decimals"), chain_id, "caller"
    matches = registry.tokens_by_symbol(str(token), chain_id)
    if not matches:
        raise _Typed("TICKER_UNRESOLVED", "raw.token",
                     f"{token!r} is not in the pinned registry for this chain")
    if len(matches) > 1:
        raise _Typed("TICKER_AMBIGUOUS", "raw.token",
                     f"{token!r} matches several tokens; pass an address or chain",
                     candidates=[{"chainId": m["chainId"], "address": m["address"],
                                  "name": m.get("name")} for m in matches[:8]])
    m = matches[0]
    return (to_checksum_address(m["address"]), m["decimals"],
            m.get("chainId", chain_id), "registry")


def _encode(payload: dict, sigdb: SigDB, registry: Registry) -> dict:
    raw = payload.get("raw") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        raise _Typed("MALFORMED_INVOCATION", "raw", "missing 'raw' object")

    fn = raw.get("function")
    if not fn or not isinstance(fn, str):
        raise _Typed("MALFORMED_INVOCATION", "raw.function",
                     "function name or signature required")
    args_in = raw.get("args")
    entry = _entry_for(fn.strip(), sigdb, args_in)

    token_addr, decimals, chain_id, resolved_by = _resolve_token(raw, registry)
    if isinstance(args_in, dict):
        missing = [n for n in entry["names"] if n not in args_in]
        if missing:
            raise _Typed("MISSING_ARGUMENT", "raw.args",
                         f"{entry['sig']} needs {', '.join(missing)}")
        ordered = [args_in[n] for n in entry["names"]]
    elif isinstance(args_in, list):
        if len(args_in) != len(entry["types"]):
            raise _Typed("MISSING_ARGUMENT", "raw.args",
                         f"{entry['sig']} takes {len(entry['types'])} arguments, "
                         f"got {len(args_in)}")
        ordered = list(args_in)
    elif args_in is None and not entry["types"]:
        ordered = []
    else:
        raise _Typed("MALFORMED_INVOCATION", "raw.args",
                     "args must be an object keyed by name, or an array")

    report: dict = {}
    values = [_coerce(v, t, f"raw.args.{n}", decimals, report)
              for v, t, n in zip(ordered, entry["types"], entry["names"])]

    value_wei = 0
    if raw.get("value") is not None:
        if not re.fullmatch(r"\d+", str(raw["value"])):
            raise _Typed("INVALID_VALUE", "raw.value",
                         "native value must be a non-negative integer in wei")
        value_wei = int(raw["value"])

    from eth_utils import keccak
    selector = keccak(text=entry["sig"])[:4]
    data = "0x" + (selector + abi_encode(entry["types"], values)).hex()

    flags = _risk_flags(entry, values, value_wei)
    return {
        "result": {
            "to": token_addr,
            "data": data,
            "value": str(value_wei),
            "selector": "0x" + selector.hex(),
            "function": {"name": entry["name"], "signature": entry["sig"],
                         "args": [{"name": n, "type": t,
                                   "value": v.hex() if isinstance(v, bytes)
                                            else (str(v) if isinstance(v, int)
                                                  else v)}
                                  for n, t, v in zip(entry["names"],
                                                     entry["types"], values)]},
            "chainId": chain_id,
        },
        "report": {
            "risk_flags": sorted(set(flags)),
            "standard": entry["standard"],
            "decimals_used": decimals,
            "amount_interpreted_as": report.get("amount_interpreted_as"),
            "resolved_by": resolved_by,
            "signed": False,
            "sigdb_version": sigdb.version,
        },
    }
