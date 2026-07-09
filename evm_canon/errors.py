"""Typed errors. The service returns these as JSON; it never crashes on bad input."""

INVALID_ADDRESS = "INVALID_ADDRESS"
UNKNOWN_CHAIN = "UNKNOWN_CHAIN"
TICKER_UNRESOLVED = "TICKER_UNRESOLVED"
TICKER_AMBIGUOUS = "TICKER_AMBIGUOUS"
DECIMALS_UNKNOWN = "DECIMALS_UNKNOWN"
BAD_TIMESTAMP = "BAD_TIMESTAMP"
SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"

ALL_CODES = frozenset({
    INVALID_ADDRESS, UNKNOWN_CHAIN, TICKER_UNRESOLVED, TICKER_AMBIGUOUS,
    DECIMALS_UNKNOWN, BAD_TIMESTAMP, SCHEMA_VALIDATION_FAILED,
})


class CanonError(Exception):
    """A typed, caller-facing error. Serialize with .to_dict()."""

    def __init__(self, code: str, field: str | None = None,
                 detail: str | None = None, candidates: list | None = None):
        assert code in ALL_CODES, f"unknown error code {code!r}"
        super().__init__(f"{code}: {detail or ''}")
        self.code = code
        self.field = field
        self.detail = detail
        self.candidates = candidates

    def to_dict(self) -> dict:
        err: dict = {"code": self.code, "field": self.field, "detail": self.detail}
        if self.candidates is not None:
            err["candidates"] = self.candidates
        return {"error": err}
