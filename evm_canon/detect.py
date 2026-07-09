"""Stage 1: detect — classify arbitrary dirty fields into canonical slots.

Pure, deterministic string/shape classification. No registry access, no I/O.
Output is a dict of canonical slots; missing slots are simply absent.
"""

import re

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
RAW_INT_RE = re.compile(r"^[0-9]+$")
HUMAN_DEC_RE = re.compile(r"^[0-9]+\.[0-9]+$")

# Key aliases: first match wins within each slot; raw keys are lowercased and
# stripped of separators before matching.
_KEYS = {
    "address": ["address", "contractaddress", "contract", "tokenaddress", "token"],
    "symbol": ["symbol", "ticker", "tokensymbol"],
    "name": ["name", "tokenname"],
    "chain_id": ["chainid", "networkid"],
    "chain": ["chain", "network", "blockchain"],
    "decimals": ["decimals", "decimal", "tokendecimals"],
    "amount": ["amountraw", "rawamount", "amounthuman", "amount", "value", "qty",
               "quantity", "balance", "wei"],
    "timestamp": ["timestamp", "time", "ts", "datetime", "date", "blocktime",
                  "blocktimestamp"],
}


def _norm_key(k: str) -> str:
    return re.sub(r"[^a-z0-9]", "", k.lower())


def detect_fields(raw: dict) -> dict:
    """Map dirty keys/values to canonical slots.

    Slots produced (all optional): address, symbol, name, chain, chain_id,
    decimals, amount_raw, amount_human, timestamp, amount_key (the original
    key the amount came from, for error reporting).
    """
    normed = {}
    for k, v in raw.items():
        nk = _norm_key(str(k))
        if nk not in normed:  # first occurrence wins, deterministic on dict order
            normed[nk] = (k, v)

    out: dict = {}

    for slot, aliases in _KEYS.items():
        for alias in aliases:
            if alias in normed:
                orig_key, value = normed[alias]
                _assign(out, slot, alias, orig_key, value)
                break

    # Value-shape overrides: an address can hide in symbol/token/name fields.
    if "address" not in out:
        for slot in ("symbol", "name"):
            v = out.get(slot)
            if isinstance(v, str) and ADDRESS_RE.match(v.strip()):
                out["address"] = v.strip()
                del out[slot]
                break
    return out


def _assign(out: dict, slot: str, alias: str, orig_key: str, value) -> None:
    if value is None:
        return
    if slot == "amount":
        out["amount_key"] = orig_key
        if isinstance(value, bool):
            return
        if isinstance(value, int):
            # A bare JSON integer is treated as a RAW (smallest-unit) amount.
            out["amount_raw"] = str(value)
        elif isinstance(value, float):
            # Floats are never allowed near amounts; hand the decimal string
            # form to the exact-math layer via repr (shortest round-trip).
            out["amount_human"] = repr(value)
        elif isinstance(value, str):
            s = value.strip().replace(",", "").replace("_", "").replace(" ", "")
            if RAW_INT_RE.match(s):
                if "amounthuman" in alias:
                    out["amount_human"] = s
                else:
                    out["amount_raw"] = s
            elif HUMAN_DEC_RE.match(s):
                out["amount_human"] = s
            # anything else (negative, hex, garbage) is left unclassified ->
            # amount stays null downstream (honest nulls).
        return
    if slot == "chain_id":
        if isinstance(value, int) and not isinstance(value, bool):
            out["chain_id"] = value
        elif isinstance(value, str) and RAW_INT_RE.match(value.strip()):
            out["chain_id"] = int(value.strip())
        return
    if slot == "chain":
        if isinstance(value, int) and not isinstance(value, bool):
            out["chain_id"] = out.get("chain_id", value)
        elif isinstance(value, str):
            s = value.strip()
            if RAW_INT_RE.match(s):
                out["chain_id"] = out.get("chain_id", int(s))
            else:
                out["chain"] = s
        return
    if slot == "decimals":
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 255:
            out["decimals"] = value
        elif isinstance(value, str) and RAW_INT_RE.match(value.strip()):
            out["decimals"] = int(value.strip())
        return
    if slot == "timestamp":
        out["timestamp"] = value
        return
    # address / symbol / name: plain strings only
    if isinstance(value, str) and value.strip():
        out[slot] = value.strip()
