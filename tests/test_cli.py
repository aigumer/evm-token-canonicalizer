import io
import json

import pytest

from evm_canon.cli import main


def _run(argv, stdin_obj, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(stdin_obj)))
    code = main(argv)
    return code, capsys.readouterr().out


def test_canonicalize_exit_0_and_canonical_json(capsys, monkeypatch):
    payload = {"raw": {"symbol": "USDC", "chain": "base", "amount": "1000000"}}
    code, out = _run(["canonicalize"], payload, capsys, monkeypatch)
    assert code == 0
    parsed = json.loads(out)
    assert parsed["result"]["amount_human"] == "1"
    # canonical form: sorted keys, compact separators
    assert out.strip() == json.dumps(parsed, sort_keys=True,
                                     separators=(",", ":"), ensure_ascii=False)


def test_typed_error_exit_1(capsys, monkeypatch):
    code, out = _run(["canonicalize"], {"raw": {"symbol": "USDC"}},
                     capsys, monkeypatch)
    assert code == 1
    assert json.loads(out)["error"]["code"] == "TICKER_AMBIGUOUS"


def test_bad_json_exit_2(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    code = main(["canonicalize"])
    assert code == 2


def test_schema_and_version(capsys):
    assert main(["schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["required"] == ["result", "report"]
    assert main(["version"]) == 0
    v = json.loads(capsys.readouterr().out)
    assert v["registry_version"].startswith("tokenlists@")
