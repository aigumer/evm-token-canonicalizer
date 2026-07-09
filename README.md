# evm-canon — EVM Token Canonicalizer (OKX.AI A2MCP service)

Turns messy EVM token / on-chain value data into strict, schema-validated JSON.
Sold to **other agents** as a pay-per-call A2MCP service on OKX.AI.

The promise (this is the marketplace listing, verbatim):
**schema-validated, deterministic core, honest nulls.**

- Every response is either a schema-valid `{result, report}` or a typed error —
  never a best-effort blob.
- Same input + same pinned `registry_version` → byte-identical output.
- Any underivable field is `null` and listed in `report.fields_null`.
- No IEEE-754 floats anywhere near amounts or billing (enforced by
  `tests/test_precision.py::test_no_float_in_amount_source_paths`).

## Why Python

The brief allowed TypeScript or Python. Python wins here on three counts:

1. **`decimal.Decimal` + native bigints.** `Decimal.scaleb` shifts exponents
   without dividing, so raw↔human conversion is exact by construction — there
   is no precision context to misconfigure and no float to sneak in. In TS the
   equivalent needs a third-party big-decimal library and discipline around
   `JSON.parse` (which silently floats large numbers).
2. **`eth_utils`** provides audited keccak-based EIP-55 checksumming without
   pulling in a full web3 client.
3. **Boring and auditable.** The whole deterministic core is stdlib + two small
   libs; the dependency surface an OKX.AI caller has to trust is tiny.

The one thing viem/ethers would buy us — on-chain `decimals()` — is a single
`eth_call` with selector `0x313ce567`, done here with `urllib` (opt-in only,
via `hints.prefer: "onchain"` + an `EVM_CANON_RPC_<chainId>` env var).

## Install & run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# canonicalize one payload (stdin or -i file)
echo '{"raw":{"symbol":"USDC","chain":"arbitrum","amount":"1500000"}}' \
  | evm-canon canonicalize --pretty

evm-canon schema     # default output JSON Schema
evm-canon version    # package + pinned registry version
pytest               # full suite incl. idempotency + precision gates
```

Exit codes: `0` schema-valid result, `1` typed error, `2` malformed invocation.
Non-pretty output is canonical JSON (sorted keys, fixed separators) so callers
can assert byte-identity.

### Library

```python
from evm_canon import canonicalize
out = canonicalize({
    "raw": {"token": "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
            "chain": "arb", "amount": "1500000", "time": 1752062400},
    "target_schema": None,          # optional caller schema
    "hints": {"chain": "arbitrum", "prefer": "registry"},
})
# -> {"result": {...}, "report": {...}}  or  {"error": {"code": ...}}
```

An LLM may be injected **only** for the ambiguous-ticker branch
(`canonicalize(payload, llm=picker)`); its pick is re-validated against the
registry candidate set and discarded if it names anything outside it
(`evm_canon/llm.py`). By default no LLM is wired and ambiguity returns a typed
`TICKER_AMBIGUOUS` error carrying the full candidate set.

## Contracts

Input: `{"raw": {...dirty fields...}, "target_schema": {...optional...},
"hints": {"chain": ..., "prefer": "registry|onchain"}}`.

Output: see `evm_canon/data/default_schema.json`. Typed error codes:
`INVALID_ADDRESS`, `UNKNOWN_CHAIN`, `TICKER_UNRESOLVED`, `TICKER_AMBIGUOUS`,
`DECIMALS_UNKNOWN`, `BAD_TIMESTAMP`, `SCHEMA_VALIDATION_FAILED` — returned as
`{"error": {"code", "field", "detail", "candidates"?}}`, never thrown at the caller.

### Documented deterministic choices (assumptions stated once, here)

- A bare JSON integer amount is treated as **raw** (smallest units); a decimal
  string is **human**. A human amount with more fractional digits than the
  token's `decimals` is not representable → honest null, noted in the report.
- `amount_human` is the exact value with trailing zeros trimmed (`1500000` @ 6
  decimals → `"1.5"`), which keeps output canonical.
- Timestamp magnitude rule: `0 < v < 1e11` → unix seconds; `1e12 ≤ v < 1e14` →
  unix millis (flagged inferred); in between / beyond → ambiguous → null.
  Naive ISO strings are assumed UTC and flagged inferred. Relative timestamps
  ("2 hours ago") depend on *now* and are rejected as `BAD_TIMESTAMP` —
  determinism beats convenience.
- `chainId` is the source of truth when both a chainId and a chain alias are
  present. `hints.chain` is only consulted when `raw` carries no chain.
- When a provided address is a canonical registry contract, registry metadata
  overrides a contradicting raw symbol (noted in `report.notes`). When the
  address is unknown but the symbol is canonical for that chain →
  `scam_suspected: true` and the caller's address is preserved, never "fixed".
- Confidence is a deterministic formula, not a vibe: base by provenance
  (registry 0.99 / onchain 0.95 / heuristic 0.70), −0.02 per null field,
  −0.30 if scam suspected, clamped to [0.05, 0.99].

## Registry: refresh + re-pin procedure

The snapshot `evm_canon/data/registry-tokenlists@2026-07-01.json` (Uniswap-style
token list + ethereum-lists/chains, curated to 8 chains) is pinned by
`PINNED_VERSION` in `evm_canon/registry.py` and surfaced in every report.

To bump — a deliberate release action, never automatic:

```bash
python scripts/refresh_registry.py --date 2026-08-01
# 1. hand-review the diff vs the previous snapshot (decimals changes and
#    added/removed tokens are reputation-relevant)
# 2. bump PINNED_VERSION in evm_canon/registry.py
# 3. bump metadata.registry_version in SKILL.md
# 4. pytest && release a new package version
```

## Go-live on OKX.AI (A2MCP)

`scripts/go_live.sh` scripts the scriptable parts; the wallet OTP and listing
publication are interactive by design (keys stay in the TEE).

1. `npx skills add okx/onchainos-skills`
2. Create the Agentic Wallet: tell your agent **"Log in to Agentic Wallet with
   email"** → enter email + OTP. Private keys live in the TEE, never in the model.
3. `npx skills add https://github.com/okx/onchainos-skills --skill okx-ai-guide`
4. Register the on-chain identity with role **`asp`** (ERC-8004 on X Layer);
   set avatar + service metadata.
5. Integrate the **OKX Payment SDK** — required before A2MCP goes live. The
   seam is `evm_canon/payment.py` (`A2MCPBilling.charge`); the `TODO(go-live)`
   block marks exactly where live credentials + wallet attach. Pricing is
   int micro-units of USDG/USDT (default 500 µ = 0.0005 per record).
6. Publish the listing with the promise: *schema-validated, deterministic
   core, honest nulls.* Fund the wallet for gas (X Layer is gas-free).

## Layout

```
SKILL.md                     Onchain OS skill file (contracts + pipeline)
evm_canon/
  detect.py                  stage 1: classify dirty fields (pure)
  resolve.py                 stage 2: address/chain/decimals/amount/timestamp (exact math)
  registry.py                pinned snapshot loader + lookups
  llm.py                     the ONLY LLM seam (ambiguous ticker, re-validated)
  pipeline.py                orchestrator + report/confidence
  validate.py                stage 3: JSON Schema gate + canonical serializer
  payment.py                 A2MCP billing stub (int micro-units, TODO(go-live))
  cli.py                     evm-canon CLI
  data/                      pinned registry snapshot + default schema
scripts/refresh_registry.py  refresh + re-pin procedure
scripts/go_live.sh           scripted go-live steps
tests/                       79 tests: per-stage units, scam-double,
                             cross-/same-chain ambiguity, idempotency,
                             uint256 precision, no-float source grep
```
