"""Stage 2: resolve — pure, deterministic resolvers.

Every function here is exact-math / registry-driven. No LLM, no floats in any
amount path (Decimal + int only), no network except the explicitly-opted-in
on-chain ``decimals()`` read.
"""

import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext

from eth_utils import to_checksum_address

from .detect import ADDRESS_RE
from .errors import (BAD_TIMESTAMP, CanonError, DECIMALS_UNKNOWN,
                     INVALID_ADDRESS, UNKNOWN_CHAIN)
from .registry import Registry

# ---------------------------------------------------------------- address --

def canon_address(addr: str) -> str:
    """Validate 20-byte hex and return the EIP-55 (keccak) checksum form."""
    s = addr.strip()
    if not ADDRESS_RE.match(s):
        raise CanonError(INVALID_ADDRESS, field="raw.address",
                         detail=f"not 20-byte hex: {addr!r}")
    return to_checksum_address(s)


# ------------------------------------------------------------------ chain --

def resolve_chain(registry: Registry, chain: str | None,
                  chain_id: int | None) -> dict | None:
    """Alias/id -> canonical chain record. chainId is the source of truth when
    both are present. Returns None only when neither was provided."""
    if chain_id is not None:
        c = registry.chain_by_id(chain_id)
        if c is None:
            raise CanonError(UNKNOWN_CHAIN, field="raw.chainId",
                             detail=f"chainId {chain_id} not in pinned registry")
        return c
    if chain is not None:
        c = registry.chain_by_alias(chain)
        if c is None:
            raise CanonError(UNKNOWN_CHAIN, field="raw.chain",
                             detail=f"unknown chain alias: {chain!r}")
        return c
    return None


# --------------------------------------------------------------- decimals --

DECIMALS_SELECTOR = "0x313ce567"  # keccak("decimals()")[:4]


def onchain_decimals(chain_id: int, address: str,
                     rpc_call=None) -> int | None:
    """eth_call decimals() against EVM_CANON_RPC_<chainId>. Returns None if no
    RPC is configured (caller falls back to registry / typed error)."""
    if rpc_call is None:
        url = os.environ.get(f"EVM_CANON_RPC_{chain_id}")
        if not url:
            return None

        def rpc_call(payload: dict) -> dict:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())

    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
               "params": [{"to": address, "data": DECIMALS_SELECTOR}, "latest"]}
    try:
        result = rpc_call(payload).get("result")
        if not result or result == "0x":
            return None
        value = int(result, 16)
        return value if 0 <= value <= 255 else None
    except Exception:
        return None


def require_decimals(decimals: int | None, need_amount: bool) -> int | None:
    if decimals is None and need_amount:
        raise CanonError(DECIMALS_UNKNOWN, field="result.decimals",
                         detail="amount present but decimals underivable "
                                "(not in registry, raw, or on-chain)")
    return decimals


# ----------------------------------------------------------------- amount --
# All conversions are exact: Decimal.scaleb shifts the exponent, it never
# divides, so there is no precision context to get wrong and no float anywhere.

def raw_to_human(amount_raw: str, decimals: int) -> str:
    if not re.match(r"^[0-9]+$", amount_raw):
        raise ValueError(f"amount_raw must be a non-negative integer string: {amount_raw!r}")
    with localcontext() as ctx:
        ctx.prec = 300
        q = Decimal(amount_raw).scaleb(-decimals)
        s = format(q, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def human_to_raw(amount_human: str, decimals: int) -> str | None:
    """Exact human -> raw. Returns None (honest null) when the human amount has
    more fractional digits than the token's decimals can represent."""
    with localcontext() as ctx:
        ctx.prec = 300
        try:
            q = Decimal(amount_human).scaleb(decimals)
        except InvalidOperation:
            return None
        if q != q.to_integral_value() or q < 0:
            return None
        return str(int(q))


# -------------------------------------------------------------- timestamp --
# Numeric magnitude rule (documented, deterministic):
#   0 < v < 1e11        -> unix seconds   (covers years 1970..5138)
#   1e12 <= v < 1e14    -> unix millis    (covers years 2001..5138)
#   anything else       -> ambiguous -> None (honest null)
# Relative timestamps ("2 hours ago") are rejected: they depend on "now" and
# would break determinism. Naive ISO strings are assumed UTC (flagged inferred).

_ISO_Z = "%Y-%m-%dT%H:%M:%SZ"


def norm_timestamp(value) -> tuple[str | None, bool]:
    """Returns (iso_utc_or_none, was_inferred). Raises BAD_TIMESTAMP only for
    a value that is clearly meant to be a timestamp but is unparseable."""
    if value is None:
        return None, False
    if isinstance(value, bool):
        raise CanonError(BAD_TIMESTAMP, field="raw.timestamp",
                         detail="boolean is not a timestamp")
    if isinstance(value, (int, float)):
        v = int(value)
        if 0 < v < 10**11:
            return _epoch_to_iso(v), False
        if 10**12 <= v < 10**14:
            return _epoch_to_iso(v // 1000), True  # ms->s inferred by magnitude
        return None, False  # ambiguous magnitude -> honest null
    if isinstance(value, str):
        s = value.strip()
        if re.match(r"^[0-9]+$", s):
            return norm_timestamp(int(s))
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            raise CanonError(BAD_TIMESTAMP, field="raw.timestamp",
                             detail=f"unparseable timestamp: {value!r}")
        inferred = dt.tzinfo is None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime(_ISO_Z), inferred
    raise CanonError(BAD_TIMESTAMP, field="raw.timestamp",
                     detail=f"unsupported timestamp type: {type(value).__name__}")


def _epoch_to_iso(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime(_ISO_Z)
