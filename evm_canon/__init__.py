"""evm-canon: deterministic, schema-validated EVM token canonicalizer.

Public API:
    canonicalize(payload) -> {"result", "report"} | {"error"}
"""

from .errors import CanonError
from .pipeline import canonicalize
from .registry import Registry, default_registry
from .validate import canonical_json, default_schema

__version__ = "0.1.0"
__all__ = ["canonicalize", "CanonError", "Registry", "default_registry",
           "canonical_json", "default_schema", "__version__"]
