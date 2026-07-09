"""Pinned token/chain registry.

The snapshot file is the single source of truth for chain aliases, canonical
token contracts, decimals, and native/wrapped mappings. It is version-pinned
(``registry_version``); bumping it is a deliberate release action performed by
``scripts/refresh_registry.py`` — never resolved to "latest" at runtime.
"""

import json
from pathlib import Path

from eth_utils import to_checksum_address

PINNED_VERSION = "tokenlists@2026-07-01"
_DATA_DIR = Path(__file__).parent / "data"


class Registry:
    def __init__(self, path: str | Path | None = None):
        if path is None:
            path = _DATA_DIR / f"registry-{PINNED_VERSION}.json"
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.version: str = data["registry_version"]
        self.chains: list[dict] = data["chains"]
        # Checksum-normalize every address once at load so all comparisons
        # and outputs are EIP-55 canonical regardless of snapshot casing.
        self.tokens: list[dict] = [
            {**t, "address": to_checksum_address(t["address"])} for t in data["tokens"]
        ]
        for c in self.chains:
            c["wrapped_native"]["address"] = to_checksum_address(c["wrapped_native"]["address"])

        self._chain_by_id = {c["chainId"]: c for c in self.chains}
        self._chain_by_alias: dict[str, dict] = {}
        for c in self.chains:
            for alias in [c["name"], *c["aliases"]]:
                self._chain_by_alias[alias.lower().strip()] = c
        self._token_by_chain_addr = {
            (t["chainId"], t["address"].lower()): t for t in self.tokens
        }

    # -- chains ------------------------------------------------------------
    def chain_by_id(self, chain_id: int) -> dict | None:
        return self._chain_by_id.get(chain_id)

    def chain_by_alias(self, alias: str) -> dict | None:
        return self._chain_by_alias.get(alias.lower().strip())

    # -- tokens ------------------------------------------------------------
    def token_by_address(self, chain_id: int, address: str) -> dict | None:
        return self._token_by_chain_addr.get((chain_id, address.lower()))

    def tokens_by_symbol(self, symbol: str, chain_id: int | None = None) -> list[dict]:
        """Exact case-insensitive symbol match, ERC-20 tokens only."""
        sym = symbol.lower().strip()
        out = [t for t in self.tokens if t["symbol"].lower() == sym]
        if chain_id is not None:
            out = [t for t in out if t["chainId"] == chain_id]
        return out

    def native_candidates(self, symbol: str, chain_id: int | None = None) -> list[dict]:
        """Chains whose native asset matches ``symbol`` (e.g. ETH on 1/10/8453/42161)."""
        sym = symbol.lower().strip()
        chains = [self._chain_by_id[chain_id]] if chain_id is not None and chain_id in self._chain_by_id else self.chains
        out = []
        for c in chains:
            native = c["native"]
            if sym in (a.lower() for a in native.get("aliases", [native["symbol"]])):
                out.append({
                    "chainId": c["chainId"], "address": None,
                    "symbol": native["symbol"], "name": native["name"],
                    "decimals": native["decimals"], "is_native": True,
                })
        return out

    def wrapped_of(self, chain_id: int, address: str) -> str | None:
        """If ``address`` is the chain's canonical wrapped-native contract,
        return the native symbol it wraps (WETH -> ETH), else None."""
        chain = self._chain_by_id.get(chain_id)
        if chain and chain["wrapped_native"]["address"].lower() == address.lower():
            return chain["native"]["symbol"]
        return None


_default: Registry | None = None


def default_registry() -> Registry:
    global _default
    if _default is None:
        _default = Registry()
    return _default
