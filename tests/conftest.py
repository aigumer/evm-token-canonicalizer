import json
from pathlib import Path

import pytest

from evm_canon.registry import Registry

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def registry() -> Registry:
    return Registry()


@pytest.fixture(scope="session")
def ambiguous_registry() -> Registry:
    """Production snapshot + two same-chain tokens sharing symbol 'DUP' on
    Ethereum, to exercise the still->1-after-chain-hint branch."""
    base = json.loads(
        (Path("evm_canon/data/registry-tokenlists@2026-07-01.json")).read_text())
    base["tokens"] += [
        {"chainId": 1, "address": "0x" + "11" * 20, "symbol": "DUP",
         "name": "Dup Token A", "decimals": 18},
        {"chainId": 1, "address": "0x" + "22" * 20, "symbol": "DUP",
         "name": "Dup Token B", "decimals": 6},
    ]
    path = FIXTURES / "registry_ambiguous.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(base))
    return Registry(path)
