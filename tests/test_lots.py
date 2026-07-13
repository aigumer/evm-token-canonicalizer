import json

from evm_canon.lots import calculate_lots


def _run(trades, method="FIFO"):
    return calculate_lots({"raw": {"method": method, "trades": trades}})


BUYS = [
    {"side": "buy", "asset": "BTC", "amount": "1", "price": "10000", "time": 1},
    {"side": "buy", "asset": "BTC", "amount": "1", "price": "30000", "time": 2},
]


def test_fifo_takes_oldest_lot():
    out = _run(BUYS + [{"side": "sell", "asset": "BTC", "amount": "1",
                        "price": "40000", "time": 3}])
    d = out["result"]["disposals"][0]
    assert d["cost_basis"] == "10000"
    assert d["gain"] == "30000"
    assert out["result"]["inventory"][0]["lots"][0]["unit_cost"] == "30000"


def test_lifo_takes_newest_lot():
    out = _run(BUYS + [{"side": "sell", "asset": "BTC", "amount": "1",
                        "price": "40000", "time": 3}], method="LIFO")
    assert out["result"]["disposals"][0]["cost_basis"] == "30000"


def test_hifo_takes_highest_cost_lot():
    out = _run(BUYS + [{"side": "sell", "asset": "BTC", "amount": "1",
                        "price": "40000", "time": 3}], method="HIFO")
    assert out["result"]["disposals"][0]["cost_basis"] == "30000"
    assert out["result"]["disposals"][0]["gain"] == "10000"


def test_partial_lot_split_across_lots():
    out = _run(BUYS + [{"side": "sell", "asset": "BTC", "amount": "1.5",
                        "price": "40000", "time": 3}])
    d = out["result"]["disposals"][0]
    assert d["cost_basis"] == "25000"          # 1 @ 10k + 0.5 @ 30k
    assert len(d["lots_consumed"]) == 2
    assert out["result"]["inventory"][0]["lots"][0]["amount"] == "0.5"


def test_fees_capitalized_and_deducted():
    out = _run([
        {"side": "buy", "asset": "ETH", "amount": "2", "price": "1000",
         "fee": "20", "time": 1},
        {"side": "sell", "asset": "ETH", "amount": "2", "price": "1500",
         "fee": "30", "time": 2}])
    d = out["result"]["disposals"][0]
    assert d["cost_basis"] == "2020"
    assert d["proceeds"] == "2970"
    assert d["gain"] == "950"


def test_oversell_is_typed_error():
    out = _run([{"side": "buy", "asset": "BTC", "amount": "1", "price": "1",
                 "time": 1},
                {"side": "sell", "asset": "BTC", "amount": "2", "price": "1",
                 "time": 2}])
    assert out["error"]["code"] == "INSUFFICIENT_INVENTORY"
    assert "only 1 held" in out["error"]["detail"]


def test_chronological_order_not_input_order():
    # sell arrives first in the array but later in time
    out = _run([{"side": "sell", "asset": "BTC", "amount": "1", "price": "2",
                 "time": 5},
                {"side": "buy", "asset": "BTC", "amount": "1", "price": "1",
                 "time": 1}])
    assert out["result"]["disposals"][0]["gain"] == "1"


def test_multi_asset_isolation():
    out = _run([
        {"side": "buy", "asset": "BTC", "amount": "1", "price": "10", "time": 1},
        {"side": "buy", "asset": "eth", "amount": "1", "price": "5", "time": 2},
        {"side": "sell", "asset": "ETH", "amount": "1", "price": "8", "time": 3}])
    assert out["result"]["disposals"][0]["asset"] == "ETH"
    assert out["result"]["totals"]["gain"] == "3"
    assert [i["asset"] for i in out["result"]["inventory"]] == ["BTC"]


def test_deterministic_byte_identical():
    trades = BUYS + [{"side": "sell", "asset": "BTC", "amount": "0.7",
                      "price": "45000.5", "time": 3}]
    a = json.dumps(_run(trades), sort_keys=True)
    b = json.dumps(_run(trades), sort_keys=True)
    assert a == b


def test_floats_rejected():
    out = _run([{"side": "buy", "asset": "BTC", "amount": 0.1, "price": "1",
                 "time": 1}])
    assert out["error"]["code"] == "FLOAT_REJECTED"


def test_validation_errors():
    assert calculate_lots({})["error"]["code"] == "MALFORMED_INVOCATION"
    assert _run([], "FIFO")["error"]["code"] == "MALFORMED_INVOCATION"
    assert _run(BUYS, "AVCO")["error"]["code"] == "UNKNOWN_METHOD"
    assert _run([{"side": "hold", "asset": "BTC", "amount": "1", "price": "1",
                  "time": 1}])["error"]["code"] == "INVALID_SIDE"
    assert _run([{"side": "buy", "asset": "BTC", "amount": "1", "price": "1"}]
                )["error"]["code"] == "BAD_TIMESTAMP"
    assert _run([{"side": "buy", "asset": "BTC", "amount": "1", "price": "1",
                  "time": 1},
                 {"side": "buy", "asset": "BTC", "amount": "1", "price": "1",
                  "time": "2024-01-01"}])["error"]["code"] == "BAD_TIMESTAMP"
    big = [{"side": "buy", "asset": "A", "amount": "1", "price": "1",
            "time": i} for i in range(1001)]
    assert _run(big)["error"]["code"] == "TOO_MANY_TRADES"
