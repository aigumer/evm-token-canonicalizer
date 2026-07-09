"""evm-canon CLI.

Usage:
  evm-canon canonicalize [-i input.json]   # default: read payload JSON from stdin
  evm-canon schema                          # print the default output schema
  evm-canon version                         # package + pinned registry version

Exit codes: 0 = schema-valid {result, report}; 1 = typed {error}; 2 = bad invocation.
Output is always canonical JSON (sorted keys) so byte-identity can be asserted.
"""

import argparse
import json
import sys

from .pipeline import canonicalize
from .registry import default_registry
from .validate import canonical_json, default_schema


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evm-canon")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_canon = sub.add_parser("canonicalize", help="canonicalize one payload")
    p_canon.add_argument("-i", "--input", help="payload file (default: stdin)")
    p_canon.add_argument("--pretty", action="store_true", help="indent output")
    sub.add_parser("schema", help="print default output schema")
    sub.add_parser("version", help="print versions")
    args = parser.parse_args(argv)

    if args.cmd == "schema":
        print(json.dumps(default_schema(), indent=2))
        return 0
    if args.cmd == "version":
        print(json.dumps({"evm_canon": "0.1.0",
                          "registry_version": default_registry().version}))
        return 0

    try:
        if args.input:
            with open(args.input, encoding="utf-8") as f:
                payload = json.load(f)
        else:
            payload = json.load(sys.stdin)
    except (OSError, json.JSONDecodeError) as e:
        print(json.dumps({"error": {"code": "SCHEMA_VALIDATION_FAILED",
                                    "field": "$", "detail": f"input is not valid JSON: {e}"}}))
        return 2

    out = canonicalize(payload)
    if args.pretty:
        print(json.dumps(out, sort_keys=True, indent=2, ensure_ascii=False))
    else:
        print(canonical_json(out))
    return 0 if "result" in out else 1


if __name__ == "__main__":
    sys.exit(main())
