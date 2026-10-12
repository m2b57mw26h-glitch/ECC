#!/usr/bin/env python3
"""Order guard: refuses an order unless every hard rule holds.

Usage: python3 order_guard.py <order.json>
Exit code 0 = allowed, 1 = refused (reasons on stdout), 2 = bad input.

Rules (set by the user, the agent never relaxes them):
  - paper trading only: executionMode must be "paper";
  - futures at exactly 5x leverage;
  - a stop-loss on the losing side, at most 9% from the reference price
    (at 5x, liquidation is ~10% away);
  - a take-profit, if given, on the winning side.

Paper orders no longer need the user's "да" (user decision 2026-10-12).
Real money stays refused here until the user explicitly allows it.

order.json:    {"asset", "side": "long"|"short", "leverage", "ref_price",
                "sl", "tp"?, "size", "orderType", "executionMode"}
"""
import hashlib, json, sys

REQUIRED_LEVERAGE = 5
MAX_STOP_PCT = 9.0
# Fields that identify an order in logs.
FINGERPRINT_FIELDS = ("asset", "side", "leverage", "size", "orderType", "sl", "tp", "executionMode")


def order_fingerprint(order):
    payload = {k: order.get(k) for k in FINGERPRINT_FIELDS}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def check_order(order):
    """Return a list of violations; an empty list means the order may go."""
    errors = []

    if order.get("executionMode") != "paper":
        errors.append("not paper mode: executionMode must be 'paper'")

    if order.get("leverage") != REQUIRED_LEVERAGE:
        errors.append(f"leverage {order.get('leverage')} != {REQUIRED_LEVERAGE}x")

    side = order.get("side")
    if side not in ("long", "short"):
        errors.append(f"unknown side {side!r}")

    try:
        ref = float(order["ref_price"])
        if ref <= 0:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        errors.append("ref_price missing or not a positive number")
        ref = None

    sl = order.get("sl")
    if sl is None:
        errors.append("no stop-loss")
    elif ref and side in ("long", "short"):
        sl = float(sl)
        if (side == "long" and sl >= ref) or (side == "short" and sl <= ref):
            errors.append(f"stop-loss {sl} is on the wrong side of {ref} for a {side}")
        else:
            stop_pct = abs(sl - ref) / ref * 100
            if stop_pct > MAX_STOP_PCT:
                errors.append(f"stop-loss {stop_pct:.2f}% away, max {MAX_STOP_PCT}%")

    tp = order.get("tp")
    if tp is not None and ref and side in ("long", "short"):
        tp = float(tp)
        if (side == "long" and tp <= ref) or (side == "short" and tp >= ref):
            errors.append(f"take-profit {tp} is on the wrong side of {ref} for a {side}")

    return errors


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    try:
        order = json.load(open(argv[1], encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"[order_guard] cannot read input: {e}")
        return 2
    errors = check_order(order)
    if errors:
        print("REFUSED")
        for e in errors:
            print(f"- {e}")
        return 1
    print(f"ALLOWED {order['side']} {order['asset']} {order['leverage']}x fingerprint={order_fingerprint(order)[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
