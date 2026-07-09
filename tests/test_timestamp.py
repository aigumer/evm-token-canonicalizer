import pytest

from evm_canon.errors import BAD_TIMESTAMP, CanonError
from evm_canon.resolve import norm_timestamp


def test_unix_seconds():
    assert norm_timestamp(1752062400) == ("2025-07-09T12:00:00Z", False)


def test_unix_millis_inferred():
    iso, inferred = norm_timestamp(1752062400000)
    assert iso == "2025-07-09T12:00:00Z"
    assert inferred is True


def test_numeric_string():
    assert norm_timestamp("1752062400")[0] == "2025-07-09T12:00:00Z"


def test_iso_with_offset_converted_to_utc():
    assert norm_timestamp("2026-07-09T15:00:00+03:00")[0] == "2026-07-09T12:00:00Z"


def test_iso_z():
    assert norm_timestamp("2026-07-09T12:00:00Z") == ("2026-07-09T12:00:00Z", False)


def test_naive_iso_assumed_utc_and_flagged():
    iso, inferred = norm_timestamp("2026-07-09 12:00:00")
    assert iso == "2026-07-09T12:00:00Z"
    assert inferred is True


def test_ambiguous_magnitude_is_null():
    # between 1e11 (max unix-s) and 1e12 (min unix-ms): honest null
    assert norm_timestamp(5 * 10**11) == (None, False)


def test_garbage_is_typed_error():
    with pytest.raises(CanonError) as e:
        norm_timestamp("yesterday around noon")
    assert e.value.code == BAD_TIMESTAMP


def test_none_passthrough():
    assert norm_timestamp(None) == (None, False)
