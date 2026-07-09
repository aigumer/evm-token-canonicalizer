"""No IEEE-754 floats anywhere near amounts: exact round-trips at any scale,
plus a source-level guard that the amount path never touches float."""

import re
from pathlib import Path

from evm_canon.pipeline import canonicalize
from evm_canon.resolve import human_to_raw, raw_to_human

MAX_UINT256 = 2**256 - 1


def test_uint256_max_exact():
    raw = str(MAX_UINT256)
    human = raw_to_human(raw, 18)
    assert human == ("115792089237316195423570985008687907853"
                     "269984665640564039457.584007913129639935")
    assert human_to_raw(human, 18) == raw


def test_large_amount_round_trip_no_drift():
    for decimals in (0, 6, 8, 18, 24):
        for raw in ("1", "999999999999999999999999999999999999999",
                    str(MAX_UINT256), "10" + "0" * 50):
            assert human_to_raw(raw_to_human(raw, decimals), decimals) == raw


def test_float_poison_values_do_not_drift():
    # 0.1 + 0.2 style traps: exact strings in, exact strings out
    assert human_to_raw("0.3", 18) == "300000000000000000"
    assert raw_to_human("300000000000000000", 18) == "0.3"
    assert human_to_raw("0.1", 6) == "100000"


def test_pipeline_large_amount(registry):
    out = canonicalize({"raw": {"symbol": "WETH", "chain": "eth",
                                "amount": str(MAX_UINT256)}}, registry)
    assert out["result"]["amount_raw"] == str(MAX_UINT256)
    assert out["result"]["amount_human"].endswith(".584007913129639935")


def test_no_float_in_amount_source_paths():
    """Acceptance criterion: grep the codebase — no float() / float literals in
    any module that touches amounts or billing."""
    src_dir = Path(__file__).parent.parent / "evm_canon"
    amount_modules = ["resolve.py", "payment.py"]
    for mod in amount_modules:
        text = (src_dir / mod).read_text()
        code = "\n".join(line.split("#")[0] for line in text.splitlines()
                         if not line.strip().startswith(('"""', "'''", "#")))
        assert "float(" not in code, f"float() call found in {mod}"
        # no float literals like 0.5 outside of Decimal(...) strings
        for m in re.finditer(r"(?<![\w.\"'])\d+\.\d+(?![\w.\"'])", code):
            raise AssertionError(f"bare float literal {m.group()} in {mod}")
