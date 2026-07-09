---
name: evm-token-canonicalizer
description: >
  Activates when an agent needs to canonicalize / normalize / clean EVM
  token or on-chain value data into a strict, schema-validated JSON shape.
  Triggers: canonicalize token, normalize address, checksum address,
  EIP-55, resolve ticker to contract, apply decimals, raw amount to human,
  normalize timestamp to UTC, native vs wrapped, dedupe token metadata.
  Multi-lingual triggers: "приведи адрес к канону", "нормализуй токен",
  "разреши тикер", "переведи raw в human", "规范化代币", "校验地址",
  "clean this token data", "fix these decimals", "normalize this timestamp".
  Use whenever raw, inconsistent EVM data must become deterministic typed JSON.
license: MIT
metadata:
  author: aigymer
  version: "0.1.0"
  homepage: "https://www.okx.ai/agents"
  registry_version: "tokenlists@2026-07-01"   # PIN for idempotency
  agent:
    requires:
      bins: ["python3"]
    install:
      - id: pip
        kind: python
        package: "evm-canon"
        bins: ["evm-canon"]
        label: "Install EVM canonicalizer CLI (pipx install evm-canon)"
---

# EVM Token Canonicalizer (A2MCP)

Turns messy EVM token / value data into strict, schema-validated JSON.
Deterministic core, honest nulls, machine-readable report. Built to be
**called by other agents** as a sub-step, not by humans.

## Core promise (put this in the marketplace listing)
- **Schema-validated:** output always conforms to the returned/target schema, or a typed error is returned.
- **Deterministic core:** same input + same `registry_version` → same output. LLM is used ONLY for ambiguous ticker resolution; all math and formatting are pure code.
- **Honest nulls:** a field that cannot be derived is `null` and listed in `report.fields_null`. The service NEVER fabricates a value.

## Input contract
```json
{
  "raw": { "...": "arbitrary, possibly dirty fields" },
  "target_schema": { "...optional JSON Schema..." },
  "hints": { "chain": "arbitrum", "prefer": "registry" }
}
```
- `raw` (required): the dirty payload. May contain any of: address, symbol,
  name, chain/chainId, amount (raw or human), decimals, timestamp.
- `target_schema` (optional): if provided, output MUST validate against it.
  If omitted, the default schema below is used and also returned.
- `hints` (optional): disambiguation help. `chain` narrows ticker resolution;
  `prefer: registry|onchain` chooses the source of truth for decimals.

## Output contract (default schema)
```json
{
  "result": {
    "chainId": 42161,
    "chain": "arbitrum",
    "address": "0xAf88...checksum",
    "symbol": "USDC",
    "name": "USD Coin",
    "decimals": 6,
    "amount_raw": "1500000",
    "amount_human": "1.5",
    "timestamp_utc": "2026-07-09T12:00:00Z",
    "is_native": false,
    "wrapped_of": null
  },
  "report": {
    "symbol_ambiguous": false,
    "resolved_by": "registry",
    "scam_suspected": false,
    "fields_inferred": [],
    "fields_null": [],
    "confidence": 0.99,
    "registry_version": "tokenlists@2026-07-01"
  }
}
```

## Pipeline: detect → resolve → validate → report
1. **detect** — classify each raw field. Address regex `^0x[0-9a-fA-F]{40}$`;
   symbol vs name; amount as raw integer vs human decimal; timestamp format.
2. **resolve** (deterministic first, LLM only if forced):
   - **Address:** validate hex + length → apply EIP-55 checksum. Invalid → `null` + error code.
   - **Chain:** normalize alias → canonical `chainId` (source of truth) + canonical name.
   - **Ticker → contract:** look up `(chainId, symbol)` in the pinned registry.
     - 0 matches → `null`, flag `fields_null`.
     - 1 match → resolve, `resolved_by: registry`.
     - >1 match → use `hints.chain`; if still >1, set `symbol_ambiguous: true`, return the candidate set in the report, DO NOT guess silently.
   - **Scam check:** if a symbol matches a well-known token but the contract
     is NOT the canonical one for that chain → `scam_suspected: true`.
   - **Decimals:** from registry (default) or on-chain `decimals()` if `prefer: onchain`. Native = 18.
   - **Amount:** `amount_human = amount_raw / 10**decimals` using arbitrary-precision Decimal / BigInt string math. **Never float.**
   - **Native vs wrapped:** flag `is_native`; set `wrapped_of` for WETH-style tokens.
   - **Timestamp:** detect unix-seconds vs unix-ms by magnitude; parse ISO/relative; emit UTC ISO-8601 (`Z`). Ambiguous → `null` + flag.
3. **validate** — assert output against `target_schema` (or default). On failure, return typed error, not a best-effort blob.
4. **report** — populate the report so the caller can programmatically accept/reject.

## Determinism rules (non-negotiable)
- Pin the registry (`metadata.registry_version`); bump it as an explicit version change, never float "latest".
- All numeric conversion via Decimal/BigInt string math — no IEEE-754 floats anywhere near amounts.
- LLM output is confined to the ambiguous-ticker branch and is always re-validated against the registry before it can appear in `result`.

## Typed errors (return, don't throw into the void)
```json
{ "error": { "code": "INVALID_ADDRESS", "field": "raw.address", "detail": "not 20-byte hex" } }
```
Codes: `INVALID_ADDRESS`, `UNKNOWN_CHAIN`, `TICKER_UNRESOLVED`,
`TICKER_AMBIGUOUS`, `DECIMALS_UNKNOWN`, `BAD_TIMESTAMP`, `SCHEMA_VALIDATION_FAILED`.

## Pricing (A2MCP / pay-per-call)
- Bill per record (scales with work), micro-unit in USDG/USDT.
- Tier 1 (base): validate + canonicalize against a given/default schema.
- Tier 2 (premium): schema inference, cross-record dedupe/reconciliation.

## Reference deterministic core (sketch)
```python
from decimal import Decimal, getcontext
from eth_utils import to_checksum_address, is_hex_address  # keccak-based EIP-55
getcontext().prec = 80

def canon_address(addr: str):
    if not is_hex_address(addr):
        return None, "INVALID_ADDRESS"
    return to_checksum_address(addr), None

def raw_to_human(amount_raw: str, decimals: int) -> str:
    q = Decimal(amount_raw) / (Decimal(10) ** decimals)   # exact, no float
    return format(q.normalize(), "f")

def norm_ts(v):
    # unix s vs ms by magnitude, else parse ISO; return UTC ISO-8601 or None
    ...
```

## A2MCP go-live checklist
- [ ] Register on-chain identity, role = `asp` (via `okx-ai-guide` skill).
- [ ] Integrate **OKX Payment SDK** (required for A2MCP before going live).
- [ ] Publish listing with the "schema-validated, deterministic, honest nulls" promise.
- [ ] Fund wallet for gas (X Layer gas-free).
