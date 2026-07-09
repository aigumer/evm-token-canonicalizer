"""Pipeline orchestrator: detect -> resolve -> validate -> report.

``canonicalize(payload)`` always returns a JSON-serializable dict that is
either a schema-valid ``{"result": ..., "report": ...}`` or a typed
``{"error": ...}``. It never raises on bad input and never fabricates a value.
"""

from . import detect, resolve
from .errors import CanonError, TICKER_AMBIGUOUS, TICKER_UNRESOLVED
from .llm import LLMPicker, pick_candidate
from .registry import Registry, default_registry
from .validate import validate_output

RESULT_FIELDS = ["chainId", "chain", "address", "symbol", "name", "decimals",
                 "amount_raw", "amount_human", "timestamp_utc", "is_native",
                 "wrapped_of"]

# Deterministic confidence model (documented in README):
# base by provenance, minus 0.02 per null result field, minus 0.30 if a scam
# double is suspected; clamped to [0.05, 0.99] and rounded to 2 decimals.
_BASE_CONFIDENCE = {"registry": 0.99, "onchain": 0.95, "heuristic": 0.70}


def canonicalize(payload: dict, registry: Registry | None = None,
                 llm: LLMPicker | None = None) -> dict:
    try:
        return _canonicalize(payload, registry or default_registry(), llm)
    except CanonError as e:
        return e.to_dict()


def _candidate_view(registry: Registry, c: dict) -> dict:
    chain = registry.chain_by_id(c["chainId"])
    return {"chainId": c["chainId"], "chain": chain["name"],
            "address": c["address"], "symbol": c["symbol"], "name": c["name"],
            "decimals": c["decimals"], "is_native": bool(c.get("is_native"))}


def _canonicalize(payload: dict, registry: Registry, llm: LLMPicker | None) -> dict:
    raw = payload.get("raw")
    if not isinstance(raw, dict):
        raw = {}
    hints = payload.get("hints") or {}
    target_schema = payload.get("target_schema")

    fields = detect.detect_fields(raw)
    inferred: list[str] = []
    notes: list[str] = []
    resolved_by = "heuristic"
    symbol_ambiguous = False
    scam_suspected = False
    candidates_view: list[dict] | None = None

    r: dict = {k: None for k in RESULT_FIELDS}

    # --- chain (chainId is source of truth; hints.chain is a fallback) -----
    chain_rec = resolve.resolve_chain(registry, fields.get("chain"),
                                      fields.get("chain_id"))
    if chain_rec is None and hints.get("chain"):
        chain_rec = resolve.resolve_chain(registry, str(hints["chain"]), None)
        if chain_rec is not None:
            inferred.append("chainId")

    # --- address ------------------------------------------------------------
    address = None
    if fields.get("address"):
        address = resolve.canon_address(fields["address"])

    symbol = fields.get("symbol")
    name = fields.get("name")
    decimals = fields.get("decimals")
    is_native = None
    wrapped_of = None

    if address is not None:
        r["address"] = address
        is_native = False
        # Registry lookup by (chainId, address): canonical metadata wins.
        token = registry.token_by_address(chain_rec["chainId"], address) if chain_rec else None
        if token is not None:
            resolved_by = "registry"
            if symbol is not None and symbol.lower() != token["symbol"].lower():
                notes.append(f"raw symbol {symbol!r} overridden by registry "
                             f"symbol {token['symbol']!r} for this address")
            symbol, name, decimals = token["symbol"], token["name"], token["decimals"]
            wrapped_of = registry.wrapped_of(token["chainId"], address)
        else:
            # Scam check: known symbol on this chain but NOT the canonical
            # contract -> flag, keep the caller's address, do not "fix" it.
            if chain_rec is not None and symbol:
                canonical = registry.tokens_by_symbol(symbol, chain_rec["chainId"])
                if canonical and all(t["address"].lower() != address.lower() for t in canonical):
                    scam_suspected = True
                    notes.append(f"symbol {symbol!r} is known on chain "
                                 f"{chain_rec['chainId']} but this address is "
                                 f"not the canonical contract")
            if decimals is None and chain_rec is not None and hints.get("prefer") == "onchain":
                od = resolve.onchain_decimals(chain_rec["chainId"], address,
                                              rpc_call=payload.get("_rpc_call"))
                if od is not None:
                    decimals = od
                    resolved_by = "onchain"
                    inferred.append("decimals")
                else:
                    notes.append("prefer:onchain requested but no RPC configured; "
                                 "decimals not resolved on-chain")

    elif symbol:
        # --- ticker -> contract via pinned registry -------------------------
        chain_id = chain_rec["chainId"] if chain_rec else None
        cands = (registry.tokens_by_symbol(symbol, chain_id)
                 + registry.native_candidates(symbol, chain_id))
        if not cands:
            raise CanonError(TICKER_UNRESOLVED, field="raw.symbol",
                             detail=f"symbol {symbol!r} not in pinned registry"
                                    + (f" for chainId {chain_id}" if chain_id else ""))
        if len(cands) > 1:
            symbol_ambiguous = True
            candidates_view = [_candidate_view(registry, c) for c in cands]
            picked = pick_candidate(symbol, hints, cands, llm)
            if picked is None:
                raise CanonError(
                    TICKER_AMBIGUOUS, field="raw.symbol",
                    detail=f"symbol {symbol!r} matches {len(cands)} assets; "
                           "provide hints.chain or an address",
                    candidates=candidates_view)
            cands = [picked]
            resolved_by = "heuristic"  # LLM-assisted, registry-re-validated
            notes.append("ambiguous ticker resolved by LLM pick, "
                         "re-validated against registry candidates")
        else:
            resolved_by = "registry"
        tok = cands[0]
        if chain_rec is None:
            chain_rec = registry.chain_by_id(tok["chainId"])
            inferred.append("chainId")
        address = tok["address"]
        if address is not None:
            r["address"] = address
        symbol, name, decimals = tok["symbol"], tok["name"], tok["decimals"]
        is_native = bool(tok.get("is_native"))
        if not is_native and address is not None:
            wrapped_of = registry.wrapped_of(tok["chainId"], address)

    # Native fallback: no address, no symbol, but a chain — nothing to resolve.
    if chain_rec is not None:
        r["chainId"], r["chain"] = chain_rec["chainId"], chain_rec["name"]
        if is_native:
            decimals = chain_rec["native"]["decimals"]

    r["symbol"], r["name"] = symbol, name
    r["decimals"] = decimals
    r["is_native"] = is_native
    r["wrapped_of"] = wrapped_of

    # --- amounts (exact math only) -------------------------------------------
    amount_raw = fields.get("amount_raw")
    amount_human = fields.get("amount_human")
    need_amount = amount_raw is not None or amount_human is not None
    decimals = resolve.require_decimals(decimals, need_amount)
    if amount_raw is not None:
        r["amount_raw"] = amount_raw
        r["amount_human"] = resolve.raw_to_human(amount_raw, decimals)
    elif amount_human is not None:
        raw_out = resolve.human_to_raw(amount_human, decimals)
        if raw_out is None:
            notes.append(f"human amount {amount_human!r} not exactly representable "
                         f"with {decimals} decimals; amount left null")
        else:
            r["amount_raw"] = raw_out
            r["amount_human"] = resolve.raw_to_human(raw_out, decimals)
            inferred.append("amount_raw")
    elif "amount_key" in fields:
        notes.append(f"amount field {fields['amount_key']!r} present but not "
                     "parseable as a non-negative amount; left null")

    # --- timestamp -----------------------------------------------------------
    ts_iso, ts_inferred = resolve.norm_timestamp(fields.get("timestamp"))
    r["timestamp_utc"] = ts_iso
    if ts_inferred:
        inferred.append("timestamp_utc")
    if fields.get("timestamp") is not None and ts_iso is None:
        notes.append("timestamp magnitude ambiguous (between unix-s and unix-ms "
                     "ranges); left null")

    # --- report ---------------------------------------------------------------
    fields_null = [k for k in RESULT_FIELDS if r[k] is None]
    confidence = _BASE_CONFIDENCE[resolved_by] - 0.02 * len(fields_null)
    if scam_suspected:
        confidence -= 0.30
    confidence = round(min(0.99, max(0.05, confidence)), 2)

    report = {
        "symbol_ambiguous": symbol_ambiguous,
        "resolved_by": resolved_by,
        "scam_suspected": scam_suspected,
        "fields_inferred": sorted(set(inferred)),
        "fields_null": fields_null,
        "confidence": confidence,
        "registry_version": registry.version,
    }
    if candidates_view is not None:
        report["candidates"] = candidates_view
    if notes:
        report["notes"] = notes

    output = {"result": r, "report": report}
    validate_output(output, target_schema)
    return output
