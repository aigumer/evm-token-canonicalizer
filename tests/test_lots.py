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


def test_project_gains_drops_lot_detail():
    from evm_canon.lots import project
    out = _run(BUYS + [{"side": "sell", "asset": "BTC", "amount": "1",
                        "price": "40000", "time": 3}])
    g = project(out, "gains")
    assert "inventory" not in g["result"]
    assert "lots_consumed" not in g["result"]["disposals"][0]
    assert g["result"]["disposals"][0]["gain"] == "30000"
    assert g["result"]["totals"]["gain"] == "30000"


def test_project_inventory_drops_disposals():
    from evm_canon.lots import project
    out = _run(BUYS + [{"side": "sell", "asset": "BTC", "amount": "1",
                        "price": "40000", "time": 3}])
    inv = project(out, "inventory")
    assert "disposals" not in inv["result"]
    assert inv["result"]["inventory"][0]["lots"][0]["unit_cost"] == "30000"


def test_project_passes_errors_through():
    from evm_canon.lots import project
    err = calculate_lots({})
    assert project(err, "gains") == err


def test_acb_pools_at_running_average():
    out = _run(BUYS + [{"side": "sell", "asset": "BTC", "amount": "1",
                        "price": "40000", "time": 3}], method="ACB")
    d = out["result"]["disposals"][0]
    assert d["cost_basis"] == "20000"        # (10000 + 30000) / 2
    assert d["gain"] == "20000"
    assert d["lots_consumed"] == []          # a pool has no lot trail
    assert out["report"]["pooled"] is True
    lot = out["result"]["inventory"][0]["lots"][0]
    assert lot["time"] is None and lot["unit_cost"] == "20000"


def test_acb_repeated_average_is_exact():
    out = _run([
        {"side": "buy", "asset": "X", "amount": "3", "price": "10", "time": 1},
        {"side": "buy", "asset": "X", "amount": "3", "price": "20", "time": 2},
        {"side": "sell", "asset": "X", "amount": "3", "price": "30", "time": 3}],
        method="ACB")
    assert out["result"]["disposals"][0]["cost_basis"] == "45"   # 3 × 15
    assert out["result"]["totals"]["gain"] == "45"


def test_acb_oversell_still_typed():
    out = _run([{"side": "buy", "asset": "BTC", "amount": "1", "price": "1",
                 "time": 1},
                {"side": "sell", "asset": "BTC", "amount": "2", "price": "1",
                 "time": 2}], method="ACB")
    assert out["error"]["code"] == "INSUFFICIENT_INVENTORY"


def _hp(trades, **kw):
    from evm_canon.lots import holding_period
    return holding_period({"raw": {"trades": trades, **kw}})


DAY = 86400


def test_holding_period_splits_short_and_long():
    out = _hp([
        {"side": "buy", "asset": "BTC", "amount": "1", "price": "100", "time": 0},
        {"side": "buy", "asset": "BTC", "amount": "1", "price": "200",
         "time": 400 * DAY},
        {"side": "sell", "asset": "BTC", "amount": "2", "price": "500",
         "time": 500 * DAY}])
    d = out["result"]["disposals"][0]
    assert d["long_term"]["amount"] == "1"      # held 500 days
    assert d["short_term"]["amount"] == "1"     # held 100 days
    assert d["long_term"]["cost_basis"] == "100"
    assert d["short_term"]["cost_basis"] == "200"
    # proceeds split pro rata by amount
    assert d["long_term"]["proceeds"] == "500"
    assert out["result"]["totals"]["long_term"]["gain"] == "400"


def test_holding_period_threshold_is_configurable():
    trades = [
        {"side": "buy", "asset": "E", "amount": "1", "price": "1", "time": 0},
        {"side": "sell", "asset": "E", "amount": "1", "price": "2",
         "time": 40 * DAY}]
    assert _hp(trades)["result"]["disposals"][0]["short_term"]["amount"] == "1"
    assert _hp(trades, long_term_days=30
               )["result"]["disposals"][0]["long_term"]["amount"] == "1"


def test_holding_period_accepts_iso_times():
    out = _hp([
        {"side": "buy", "asset": "E", "amount": "1", "price": "1",
         "time": "2024-01-01T00:00:00Z"},
        {"side": "sell", "asset": "E", "amount": "1", "price": "2",
         "time": "2025-06-01T00:00:00Z"}])
    assert out["result"]["disposals"][0]["long_term"]["gain"] == "1"


def test_holding_period_rejects_pooled_and_opaque_times():
    assert _hp([{"side": "buy", "asset": "E", "amount": "1", "price": "1",
                 "time": 1}], method="ACB")["error"]["code"] == "NO_LOT_TRAIL"
    out = _hp([
        {"side": "buy", "asset": "E", "amount": "1", "price": "1", "time": "a"},
        {"side": "sell", "asset": "E", "amount": "1", "price": "2", "time": "b"}])
    assert out["error"]["code"] == "TIME_NOT_ABSOLUTE"


def _check(trades):
    from evm_canon.lots import check_ledger
    return check_ledger({"raw": {"trades": trades}})


def test_check_ledger_clean_ledger():
    out = _check(BUYS + [{"side": "sell", "asset": "BTC", "amount": "1",
                          "price": "40000", "time": 3}])
    assert out["result"]["ok"] is True
    assert out["result"]["issues"] == []
    assert out["result"]["summary"] == {"trades": 3, "buys": 2, "sells": 1,
                                        "assets": ["BTC"]}


def test_check_ledger_collects_every_problem():
    out = _check([
        {"side": "hold", "asset": "BTC", "amount": "1", "price": "1", "time": 1},
        {"side": "buy", "asset": "BTC", "amount": 0.5, "price": "1", "time": 2},
        {"side": "buy", "asset": "BTC", "amount": "-1", "price": "1", "time": 3},
        {"side": "sell", "asset": "BTC", "amount": "5", "price": "1", "time": 4},
        {"side": "buy", "asset": "ETH", "amount": "1", "price": "1", "time": 5},
        {"side": "buy", "asset": "ETH", "amount": "1", "price": "1", "time": 5}])
    codes = [i["code"] for i in out["result"]["issues"]]
    assert out["result"]["ok"] is False
    assert "INVALID_SIDE" in codes and "FLOAT_REJECTED" in codes
    assert "INVALID_NUMBER" in codes and "POSSIBLE_DUPLICATE" in codes
    assert "INSUFFICIENT_INVENTORY" in codes
    short = [i for i in out["result"]["issues"]
             if i["code"] == "INSUFFICIENT_INVENTORY"][0]
    assert "short 5" in short["detail"]      # continues past the failure


def test_check_ledger_flags_incomparable_times():
    out = _check([
        {"side": "buy", "asset": "E", "amount": "1", "price": "1", "time": 1},
        {"side": "buy", "asset": "E", "amount": "1", "price": "1",
         "time": "2024-01-01"}])
    assert any(i["code"] == "BAD_TIMESTAMP" for i in out["result"]["issues"])
