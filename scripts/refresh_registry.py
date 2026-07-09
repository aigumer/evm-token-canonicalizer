#!/usr/bin/env python3
"""Refresh + re-pin the token/chain registry snapshot.

This is a DELIBERATE release action, never a runtime behavior:

  1. Run:  python scripts/refresh_registry.py [--date YYYY-MM-DD]
  2. Review the diff of the new snapshot against the previous one by hand
     (added/removed tokens, changed decimals are all reputation-relevant).
  3. Update PINNED_VERSION in evm_canon/registry.py and
     metadata.registry_version in SKILL.md to the new "tokenlists@<date>".
  4. Run the full test suite; ship as a new package version.

Sources:
  - Uniswap Labs default token list  (https://tokens.uniswap.org)
  - ethereum-lists/chains            (https://chainid.network/chains.json)

The curated chain set and symbol allowlist below keep the snapshot small and
auditable; widen them deliberately, not automatically.
"""

import argparse
import datetime
import json
import sys
import urllib.request
from pathlib import Path

TOKENLIST_URL = "https://tokens.uniswap.org"
CHAINS_URL = "https://chainid.network/chains.json"

CURATED_CHAIN_IDS = [1, 10, 56, 137, 196, 8453, 42161, 43114]
SYMBOL_ALLOWLIST = {"USDC", "USDC.e", "USDT", "DAI", "WBTC",
                    "WETH", "WBNB", "WPOL", "WMATIC", "WAVAX", "WOKB"}

DATA_DIR = Path(__file__).parent.parent / "evm_canon" / "data"


def fetch(url: str) -> dict | list:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat(),
                    help="snapshot date used in registry_version (default: today)")
    args = ap.parse_args()
    version = f"tokenlists@{args.date}"
    out_path = DATA_DIR / f"registry-{version}.json"
    if out_path.exists():
        print(f"refusing to overwrite existing snapshot {out_path}", file=sys.stderr)
        return 1

    # Start from the previous snapshot so hand-curated chain metadata
    # (aliases, native/wrapped mappings) carries forward.
    prev = sorted(DATA_DIR.glob("registry-tokenlists@*.json"))[-1]
    base = json.loads(prev.read_text())

    tokenlist = fetch(TOKENLIST_URL)
    tokens = [
        {"chainId": t["chainId"], "address": t["address"],
         "symbol": t["symbol"], "name": t["name"], "decimals": t["decimals"]}
        for t in tokenlist["tokens"]
        if t["chainId"] in CURATED_CHAIN_IDS and t["symbol"] in SYMBOL_ALLOWLIST
    ]
    # Sanity-check chain ids against ethereum-lists/chains.
    known_ids = {c["chainId"] for c in fetch(CHAINS_URL)}
    missing = [cid for cid in CURATED_CHAIN_IDS if cid not in known_ids]
    if missing:
        print(f"chain ids not in chainid.network: {missing}", file=sys.stderr)
        return 1

    snapshot = {
        "registry_version": version,
        "sources": [f"{TOKENLIST_URL} ({tokenlist.get('name', 'token list')} "
                    f"v{tokenlist.get('version', {})})", CHAINS_URL],
        "chains": base["chains"],
        "tokens": sorted(tokens, key=lambda t: (t["chainId"], t["symbol"], t["address"])),
    }
    out_path.write_text(json.dumps(snapshot, indent=2) + "\n")
    print(f"wrote {out_path}")
    print(f"next: review the diff vs {prev.name}, then bump PINNED_VERSION in "
          f"evm_canon/registry.py and metadata.registry_version in SKILL.md "
          f"to {version!r}, and run pytest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
