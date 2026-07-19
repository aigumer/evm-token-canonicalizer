"""LotLedger — deterministic crypto tax-lot engine (FIFO / LIFO / HIFO).

Matches disposals against acquisition lots and reports realized gains and
remaining inventory. Pure computation: no clock, no network, no floats —
every quantity is decimal string in/out, so identical input yields
byte-identical output. Errors are typed and honest: overselling is an
INSUFFICIENT_INVENTORY error naming the asset and the shortfall, never a
silently clamped number.

Conventions (stated once, deterministic):
- Buy fees are capitalized into the lot's cost basis; sell fees reduce
  proceeds. Fees are in the quote currency.
- Trades are processed in chronological order; ties keep input order.
- HIFO consumes the highest-unit-cost lots first; FIFO oldest; LIFO newest.
"""

from decimal import Decimal, InvalidOperation

ENGINE_VERSION = "lots@1"
METHODS = ("FIFO", "LIFO", "HIFO")
MAX_TRADES = 1000


def _dec(value, field: str) -> Decimal:
    if isinstance(value, float):
        raise _Typed("FLOAT_REJECTED", field,
                     "floats are not accepted; send numbers as strings")
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise _Typed("INVALID_NUMBER", field, f"not a decimal number: {value!r}")
    if not d.is_finite():
        raise _Typed("INVALID_NUMBER", field, "must be finite")
    return d


class _Typed(Exception):
    def __init__(self, code, field, detail):
        self.err = {"code": code, "field": field, "detail": detail}


def _fmt(d: Decimal) -> str:
    out = format(d.normalize(), "f")
    return "0" if out in ("-0", "0E-0") else out


def calculate_lots(payload: dict) -> dict:
    try:
        return _calculate(payload)
    except _Typed as exc:
        return {"error": exc.err}


def _calculate(payload: dict) -> dict:
    raw = payload.get("raw") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        raise _Typed("MALFORMED_INVOCATION", "raw", "missing 'raw' object")

    method = str(raw.get("method") or payload.get("method") or "FIFO").upper()
    if method not in METHODS:
        raise _Typed("UNKNOWN_METHOD", "raw.method",
                     f"method must be one of {'/'.join(METHODS)}")

    trades = raw.get("trades")
    if not isinstance(trades, list) or not trades:
        raise _Typed("MALFORMED_INVOCATION", "raw.trades",
                     "trades must be a non-empty array")
    if len(trades) > MAX_TRADES:
        raise _Typed("TOO_MANY_TRADES", "raw.trades",
                     f"max {MAX_TRADES} trades per call, got {len(trades)}")

    parsed = []
    for i, t in enumerate(trades):
        f = f"raw.trades[{i}]"
        if not isinstance(t, dict):
            raise _Typed("MALFORMED_INVOCATION", f, "trade must be an object")
        side = str(t.get("side", "")).lower()
        if side not in ("buy", "sell"):
            raise _Typed("INVALID_SIDE", f + ".side", "side must be buy or sell")
        asset = t.get("asset")
        if not asset or not isinstance(asset, str):
            raise _Typed("INVALID_ASSET", f + ".asset", "asset symbol required")
        amount = _dec(t.get("amount"), f + ".amount")
        price = _dec(t.get("price"), f + ".price")
        fee = _dec(t.get("fee", "0"), f + ".fee")
        if amount <= 0:
            raise _Typed("INVALID_NUMBER", f + ".amount", "must be positive")
        if price < 0 or fee < 0:
            raise _Typed("INVALID_NUMBER", f, "price/fee must be >= 0")
        time = t.get("time")
        if time is None:
            raise _Typed("BAD_TIMESTAMP", f + ".time", "time required")
        parsed.append({"i": i, "side": side, "asset": asset.upper(),
                       "amount": amount, "price": price, "fee": fee,
                       "time": time})

    try:
        parsed.sort(key=lambda t: (t["time"], t["i"]))
    except TypeError:
        raise _Typed("BAD_TIMESTAMP", "raw.trades[].time",
                     "times must be mutually comparable (all numbers or all strings)")

    inventory: dict[str, list[dict]] = {}
    disposals = []
    total_proceeds = total_basis = Decimal(0)

    for t in parsed:
        lots = inventory.setdefault(t["asset"], [])
        if t["side"] == "buy":
            cost = t["amount"] * t["price"] + t["fee"]
            lots.append({"time": t["time"], "amount": t["amount"],
                         "unit_cost": cost / t["amount"]})
            continue

        available = sum((l["amount"] for l in lots), Decimal(0))
        if t["amount"] > available:
            raise _Typed("INSUFFICIENT_INVENTORY", f"raw.trades[{t['i']}]",
                         f"sell {_fmt(t['amount'])} {t['asset']} but only "
                         f"{_fmt(available)} held at that time")

        if method == "FIFO":
            order = list(range(len(lots)))
        elif method == "LIFO":
            order = list(range(len(lots) - 1, -1, -1))
        else:  # HIFO: highest unit cost first, ties oldest-first (stable)
            order = sorted(range(len(lots)),
                           key=lambda k: (-lots[k]["unit_cost"], k))

        remaining = t["amount"]
        consumed, basis = [], Decimal(0)
        for k in order:
            if remaining == 0:
                break
            lot = lots[k]
            take = min(lot["amount"], remaining)
            lot["amount"] -= take
            remaining -= take
            basis += take * lot["unit_cost"]
            consumed.append({"acquired_time": lot["time"],
                             "amount": _fmt(take),
                             "unit_cost": _fmt(lot["unit_cost"])})
        inventory[t["asset"]] = [l for l in lots if l["amount"] > 0]

        proceeds = t["amount"] * t["price"] - t["fee"]
        disposals.append({"asset": t["asset"], "time": t["time"],
                          "amount": _fmt(t["amount"]),
                          "proceeds": _fmt(proceeds),
                          "cost_basis": _fmt(basis),
                          "gain": _fmt(proceeds - basis),
                          "lots_consumed": consumed})
        total_proceeds += proceeds
        total_basis += basis

    return {
        "result": {
            "method": method,
            "disposals": disposals,
            "inventory": [
                {"asset": a, "lots": [{"time": l["time"],
                                       "amount": _fmt(l["amount"]),
                                       "unit_cost": _fmt(l["unit_cost"])}
                                      for l in ls]}
                for a, ls in sorted(inventory.items()) if ls],
            "totals": {"proceeds": _fmt(total_proceeds),
                       "cost_basis": _fmt(total_basis),
                       "gain": _fmt(total_proceeds - total_basis)},
        },
        "report": {"trades_processed": len(parsed),
                   "disposal_count": len(disposals),
                   "engine_version": ENGINE_VERSION},
    }
