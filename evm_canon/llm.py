"""The ONLY branch where an LLM may be consulted: ambiguous ticker resolution.

Contract (non-negotiable):
- The LLM is an optional injectable callable; by default there is none and the
  service returns a typed TICKER_AMBIGUOUS error with the candidate set.
- The LLM only ever *picks among registry candidates*. Its answer is
  re-validated: if it is not exactly one of the candidate identities, it is
  discarded and the ambiguity error stands. LLM output can never introduce a
  new address, symbol, or decimals into ``result``.
"""

from collections.abc import Callable

# An LLMPicker receives (symbol, hints, candidates) and returns the chosen
# candidate's identity as {"chainId": int, "address": str|None} — or None.
LLMPicker = Callable[[str, dict, list[dict]], dict | None]


def pick_candidate(symbol: str, hints: dict, candidates: list[dict],
                   llm: LLMPicker | None) -> dict | None:
    """Returns the validated winning candidate, or None if no LLM is wired or
    its answer fails re-validation against the candidate set."""
    if llm is None:
        return None
    try:
        answer = llm(symbol, hints, candidates)
    except Exception:
        return None  # a flaky LLM must never take the service down
    if not isinstance(answer, dict):
        return None
    # Re-validation gate: exact identity match against the registry-derived
    # candidate set, nothing else is accepted.
    for c in candidates:
        addr_a = (answer.get("address") or "").lower() or None
        addr_c = (c["address"] or "").lower() if c["address"] else None
        if answer.get("chainId") == c["chainId"] and addr_a == addr_c:
            return c
    return None
