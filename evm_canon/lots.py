"""LotLedger — deterministic crypto tax-lot engine (FIFO / LIFO / HIFO / ACB).

Matches disposals against acquisition lots and reports realized gains and
remaining inventory. Pure computation: no clock, no network, no floats —
every quantity is decimal string in/out, so identical input yields
byte-identical output. Errors are typed and honest: overselling is an
INSUFFICIENT_INVENTORY error naming the asset and the shortfall, never a
silently clamped number.

This module computes; it does not advise. Method names describe mechanics
(which lots are consumed in which order), not what a caller ought to file.

Conventions (stated once, deterministic):
- Buy fees are capitalized into the lot's cost basis; sell fees reduce
  proceeds. Fees are in the quote currency.
- Trades are processed in chronological order; ties keep input order.
- HIFO consumes the highest-unit-cost lots first; FIFO oldest; LIFO newest;
  ACB pools every acquisition at a running average unit cost.
"""

import datetime
import re
from decimal import Decimal, InvalidOperation, localcontext

ENGINE_VERSION = "lots@2"
METHODS = ("FIFO", "LIFO", "HIFO", "ACB")
# ACB has no discrete lots to point at, so disposals carry no lot trail.
POOLED_METHODS = ("ACB",)
MAX_TRADES = 1000
# Division only occurs for pooled unit costs; a fixed context keeps that
# deterministic regardless of the caller's ambient decimal settings.
PRECISION = 50
LONG_TERM_DAYS_DEFAULT = 365


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
        with localcontext() as ctx:
            ctx.prec = PRECISION
            return _calculate(payload)
    except _Typed as exc:
        return {"error": exc.err}


def _epoch_seconds(value, field: str) -> int:
    """Absolute time in seconds. Holding periods need real durations, so a
    merely sortable time (an opaque label) is rejected rather than guessed."""
    if isinstance(value, bool) or value is None:
        raise _Typed("TIME_NOT_ABSOLUTE", field, "absolute timestamp required")
    if isinstance(value, (int, float)) or (isinstance(value, str)
                                           and re.fullmatch(r"\d+", value)):
        n = int(value)
        return n // 1000 if n >= 10**12 else n      # unix ms vs s by magnitude
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        raise _Typed("TIME_NOT_ABSOLUTE", field,
                     "time must be unix seconds/millis or an ISO-8601 datetime")
    if dt.tzinfo is None:                            # naive input is UTC
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())


def holding_period(payload: dict) -> dict:
    """Split each disposal into short- and long-term portions by how long the
    consumed lots were held. Mechanical definition, stated so a caller can
    check it: a portion is long-term when the holding duration is strictly
    greater than ``long_term_days`` (default 365); proceeds are allocated
    across portions pro rata by amount."""
    try:
        with localcontext() as ctx:
            ctx.prec = PRECISION
            return _holding_period(payload)
    except _Typed as exc:
        return {"error": exc.err}


def _holding_period(payload: dict) -> dict:
    raw = payload.get("raw") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        raise _Typed("MALFORMED_INVOCATION", "raw", "missing 'raw' object")
    method = str(raw.get("method") or payload.get("method") or "FIFO").upper()
    if method in POOLED_METHODS:
        raise _Typed("NO_LOT_TRAIL", "raw.method",
                     "pooled methods have no acquisition dates to measure; "
                     "use FIFO, LIFO or HIFO")
    days = raw.get("long_term_days", LONG_TERM_DAYS_DEFAULT)
    if isinstance(days, bool) or not str(days).lstrip("-").isdigit() \
            or int(days) < 0:
        raise _Typed("INVALID_NUMBER", "raw.long_term_days",
                     "must be a non-negative whole number of days")
    cutoff = int(days) * 86400

    full = _calculate(payload)
    out_disposals, short_tot, long_tot = [], _zero_bucket(), _zero_bucket()

    for i, d in enumerate(full["result"]["disposals"]):
        sold_at = _epoch_seconds(d["time"], f"raw.trades[{i}].time")
        amount = Decimal(d["amount"])
        proceeds = Decimal(d["proceeds"])
        short, long = _zero_bucket(), _zero_bucket()
        for lot in d["lots_consumed"]:
            take = Decimal(lot["amount"])
            basis = take * Decimal(lot["unit_cost"])
            share = proceeds * take / amount if amount else Decimal(0)
            held = sold_at - _epoch_seconds(lot["acquired_time"],
                                            "raw.trades[].time")
            bucket = long if held > cutoff else short
            bucket["amount"] += take
            bucket["proceeds"] += share
            bucket["cost_basis"] += basis
        for bucket, total in ((short, short_tot), (long, long_tot)):
            for k in bucket:
                total[k] += bucket[k]
        out_disposals.append({
            "asset": d["asset"], "time": d["time"], "amount": d["amount"],
            "short_term": _render_bucket(short),
            "long_term": _render_bucket(long)})

    return {"result": {"method": method,
                       "long_term_days": int(days),
                       "disposals": out_disposals,
                       "totals": {"short_term": _render_bucket(short_tot),
                                  "long_term": _render_bucket(long_tot)}},
            "report": {"trades_processed": full["report"]["trades_processed"],
                       "disposal_count": len(out_disposals),
                       "engine_version": ENGINE_VERSION}}


def _zero_bucket() -> dict:
    return {"amount": Decimal(0), "proceeds": Decimal(0),
            "cost_basis": Decimal(0)}


def _render_bucket(b: dict) -> dict:
    gain = b["proceeds"] - b["cost_basis"]
    return {"amount": _fmt(b["amount"]), "proceeds": _fmt(b["proceeds"]),
            "cost_basis": _fmt(b["cost_basis"]), "gain": _fmt(gain)}


def balances(payload: dict) -> dict:
    """Net position per asset straight from the trade list.

    One pass, no lot matching, no cost basis — which is the point: it answers
    "what does this ledger say I hold" even when the ledger is too broken for
    cost-basis matching to run at all. A net that goes negative is reported
    rather than treated as an error, because that is exactly the case a
    caller is trying to find.
    """
    try:
        with localcontext() as ctx:
            ctx.prec = PRECISION
            return _balances(payload)
    except _Typed as exc:
        return {"error": exc.err}


def _balances(payload: dict) -> dict:
    raw = payload.get("raw") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        raise _Typed("MALFORMED_INVOCATION", "raw", "missing 'raw' object")
    trades = raw.get("trades")
    if not isinstance(trades, list) or not trades:
        raise _Typed("MALFORMED_INVOCATION", "raw.trades",
                     "trades must be a non-empty array")
    if len(trades) > MAX_TRADES:
        raise _Typed("TOO_MANY_TRADES", "raw.trades",
                     f"max {MAX_TRADES} trades per call, got {len(trades)}")

    per: dict[str, dict] = {}
    fees = Decimal(0)
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
        if amount <= 0:
            raise _Typed("INVALID_NUMBER", f + ".amount", "must be positive")
        fees += _dec(t.get("fee", "0"), f + ".fee")
        time = t.get("time")
        if time is None:
            raise _Typed("BAD_TIMESTAMP", f + ".time", "time required")

        row = per.setdefault(asset.upper(), {
            "bought": Decimal(0), "sold": Decimal(0), "trades": 0,
            "first": time, "last": time})
        row["bought" if side == "buy" else "sold"] += amount
        row["trades"] += 1
        try:
            row["first"] = min(row["first"], time)
            row["last"] = max(row["last"], time)
        except TypeError:
            raise _Typed("BAD_TIMESTAMP", "raw.trades[].time",
                         "times must be mutually comparable")

    out, negative = [], []
    for asset, row in sorted(per.items()):
        net = row["bought"] - row["sold"]
        if net < 0:
            negative.append(asset)
        out.append({"asset": asset, "bought": _fmt(row["bought"]),
                    "sold": _fmt(row["sold"]), "net": _fmt(net),
                    "trades": row["trades"],
                    "first_trade": row["first"], "last_trade": row["last"]})
    return {"result": {"assets": out, "total_fees": _fmt(fees),
                       "negative_assets": negative},
            "report": {"trades_processed": len(trades),
                       "asset_count": len(out),
                       "engine_version": ENGINE_VERSION}}


def check_ledger(payload: dict) -> dict:
    """Report every problem in a trade ledger instead of failing on the first.

    Real exchange exports are messy, and an accounting agent needs the whole
    list before deciding what to fix — so this collects issues and keeps
    going where the calculators stop.
    """
    raw = payload.get("raw") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return {"error": {"code": "MALFORMED_INVOCATION", "field": "raw",
                          "detail": "missing 'raw' object"}}
    trades = raw.get("trades")
    if not isinstance(trades, list) or not trades:
        return {"error": {"code": "MALFORMED_INVOCATION", "field": "raw.trades",
                          "detail": "trades must be a non-empty array"}}

    issues, clean, seen = [], [], {}
    buys = sells = 0
    for i, t in enumerate(trades):
        def flag(code, detail, idx=i):
            issues.append({"code": code, "trade_index": idx, "detail": detail})
        if not isinstance(t, dict):
            flag("MALFORMED_TRADE", "trade must be an object")
            continue
        side = str(t.get("side", "")).lower()
        if side not in ("buy", "sell"):
            flag("INVALID_SIDE", f"side must be buy or sell, got {t.get('side')!r}")
        asset = t.get("asset")
        if not asset or not isinstance(asset, str):
            flag("INVALID_ASSET", "asset symbol required")
        ok = True
        nums = {}
        for field, default in (("amount", None), ("price", None), ("fee", "0")):
            v = t.get(field, default)
            if isinstance(v, float):
                flag("FLOAT_REJECTED", f"{field} is a float; send it as a string")
                ok = False
                continue
            try:
                nums[field] = Decimal(str(v))
            except (InvalidOperation, TypeError):
                flag("INVALID_NUMBER", f"{field} is not a decimal number: {v!r}")
                ok = False
        if ok:
            if nums.get("amount", Decimal(0)) <= 0:
                flag("INVALID_NUMBER", "amount must be positive")
                ok = False
            if nums.get("price", Decimal(0)) < 0 or nums.get("fee", Decimal(0)) < 0:
                flag("INVALID_NUMBER", "price/fee must be >= 0")
                ok = False
        if t.get("time") is None:
            flag("BAD_TIMESTAMP", "time required")
            ok = False
        if ok and side in ("buy", "sell") and isinstance(asset, str):
            key = (side, asset.upper(), str(nums["amount"]), str(nums["price"]),
                   str(t.get("time")))
            if key in seen:
                flag("POSSIBLE_DUPLICATE",
                     f"identical to trade #{seen[key]}")
            else:
                seen[key] = i
            clean.append({"i": i, "side": side, "asset": asset.upper(),
                          "amount": nums["amount"], "time": t.get("time")})
            buys += side == "buy"
            sells += side == "sell"

    try:
        clean.sort(key=lambda t: (t["time"], t["i"]))
    except TypeError:
        issues.append({"code": "BAD_TIMESTAMP", "trade_index": None,
                       "detail": "times are not mutually comparable "
                                 "(mix of numbers and strings)"})
        clean = []

    balances: dict[str, Decimal] = {}
    for t in clean:
        if t["side"] == "buy":
            balances[t["asset"]] = balances.get(t["asset"], Decimal(0)) + t["amount"]
        else:
            held = balances.get(t["asset"], Decimal(0))
            if t["amount"] > held:
                issues.append({"code": "INSUFFICIENT_INVENTORY",
                               "trade_index": t["i"],
                               "detail": f"sell {_fmt(t['amount'])} {t['asset']} "
                                         f"but only {_fmt(held)} held at that "
                                         f"time (short {_fmt(t['amount'] - held)})"})
                balances[t["asset"]] = Decimal(0)
            else:
                balances[t["asset"]] = held - t["amount"]

    return {"result": {"ok": not issues,
                       "issues": issues,
                       "summary": {"trades": len(trades), "buys": buys,
                                   "sells": sells,
                                   "assets": sorted({t["asset"] for t in clean})}},
            "report": {"issue_count": len(issues),
                       "engine_version": ENGINE_VERSION}}


def project(out: dict, view: str) -> dict:
    """Narrow a full result to one focused view.

    Callers who only need a tax summary shouldn't have to receive (or pay to
    parse) lot-level detail, and a portfolio agent asking "what do I still
    hold" doesn't want the disposal log. Errors pass through untouched.
    """
    if "error" in out or view not in ("gains", "inventory"):
        return out
    r = out["result"]
    if view == "gains":
        result = {"method": r["method"],
                  "disposals": [{k: v for k, v in d.items()
                                 if k != "lots_consumed"}
                                for d in r["disposals"]],
                  "totals": r["totals"]}
    else:
        result = {"method": r["method"], "inventory": r["inventory"]}
    return {"result": result, "report": out["report"]}


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
            if method in POOLED_METHODS:
                # One running pool per asset: merge cost and quantity, then
                # re-derive the average unit cost. `time` stays null because
                # a pooled holding has no single acquisition date.
                if lots:
                    pool = lots[0]
                    total = pool["amount"] * pool["unit_cost"] + cost
                    pool["amount"] += t["amount"]
                    pool["unit_cost"] = total / pool["amount"]
                else:
                    lots.append({"time": None, "amount": t["amount"],
                                 "unit_cost": cost / t["amount"]})
            else:
                lots.append({"time": t["time"], "amount": t["amount"],
                             "unit_cost": cost / t["amount"]})
            continue

        available = sum((l["amount"] for l in lots), Decimal(0))
        if t["amount"] > available:
            raise _Typed("INSUFFICIENT_INVENTORY", f"raw.trades[{t['i']}]",
                         f"sell {_fmt(t['amount'])} {t['asset']} but only "
                         f"{_fmt(available)} held at that time")

        if method in POOLED_METHODS:
            order = list(range(len(lots)))   # a single pool
        elif method == "FIFO":
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
                          "lots_consumed": [] if method in POOLED_METHODS
                                            else consumed})
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
                   "pooled": method in POOLED_METHODS,
                   "engine_version": ENGINE_VERSION},
    }
