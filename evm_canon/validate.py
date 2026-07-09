"""Stage 3: validate — assert output against target/default JSON Schema."""

import json
from pathlib import Path

import jsonschema

from .errors import CanonError, SCHEMA_VALIDATION_FAILED

_SCHEMA_PATH = Path(__file__).parent / "data" / "default_schema.json"
_default_schema: dict | None = None


def default_schema() -> dict:
    global _default_schema
    if _default_schema is None:
        with open(_SCHEMA_PATH, encoding="utf-8") as f:
            _default_schema = json.load(f)
    return _default_schema


def validate_output(output: dict, target_schema: dict | None = None) -> None:
    """Raise SCHEMA_VALIDATION_FAILED (typed) if output doesn't conform."""
    schema = target_schema if target_schema is not None else default_schema()
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(output, schema,
                            cls=jsonschema.Draft202012Validator)
    except jsonschema.SchemaError as e:
        raise CanonError(SCHEMA_VALIDATION_FAILED, field="target_schema",
                         detail=f"target_schema is not a valid JSON Schema: {e.message}")
    except jsonschema.ValidationError as e:
        path = ".".join(str(p) for p in e.absolute_path) or "$"
        raise CanonError(SCHEMA_VALIDATION_FAILED, field=path, detail=e.message)


def canonical_json(obj: dict) -> str:
    """Byte-stable serialization: sorted keys, fixed separators, no NaN."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)
