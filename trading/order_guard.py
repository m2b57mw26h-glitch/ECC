#!/usr/bin/env python3
"""Order guard: refuses an order unless every hard rule holds.

Usage: python3 order_guard.py <order.json> <approval.json>
Exit code 0 = allowed, 1 = refused (reasons on stdout), 2 = bad input.

Rules (set by the user, the agent never relaxes them):
  - paper trading only: executionMode must be "paper";
  - futures at exactly 5x leverage;
  - a stop-loss on the losing side, at most 9% from the reference price
    (at 5x, liquidation is ~10% away);
  - a take-profit, if given, on the winning side;
  - the user's explicit "да" for this exact order (matched by fingerprint),
    given recently.

order.json:    {"asset", "side": "long"|"short", "leverage", "ref_price",
                "sl", "tp"?, "size", "orderType", "executionMode"}
approval.json: {"text", "order_fingerprint", "approved_at": ISO-8601 UTC}
"""
import hashlib, json, sys
from datetime import datetime, timedelta, timezone

REQUIRED_LEVERAGE = 5
MAX_STOP_PCT = 9.0
APPROVAL_WORDS = {"да"}
MAX_APPROVAL_AGE = timedelta(minutes=30)
# Fields the user approves; a change in any of them needs a new "да".
FINGERPRINT_FIELDS = ("asset", "side", "leverage", "size", "orderType", "sl", "tp", "executionMode")


def order_fingerprint(order):
    payload = {k: order.get(k) for k in FINGERPRINT_FIELDS}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_time(value):
    t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def check_order(order, approval, now=None):
    """Return a list of violations; an empty list means the order may go."""
    now = now or datetime.now(timezone.utc)
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

    if not approval:
        errors.append("no approval from the user")
        return errors
    if str(approval.get("text", "")).strip().lower().rstrip(".!") not in APPROVAL_WORDS:
        errors.append(f"approval text {approval.get('text')!r} is not an explicit 'да'")
    if approval.get("order_fingerprint") != order_fingerprint(order):
        errors.append("approval was given for a different order")
    try:
        age = now - _parse_time(approval["approved_at"])
        if age < timedelta(0) or age > MAX_APPROVAL_AGE:
            errors.append(f"approval is stale or from the future ({age})")
    except (KeyError, ValueError):
        errors.append("approval has no valid approved_at time")
    return errors


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    try:
        order = json.load(open(argv[1], encoding="utf-8"))
        approval = json.load(open(argv[2], encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"[order_guard] cannot read input: {e}")
        return 2
    errors = check_order(order, approval)
    if errors:
        print("REFUSED")
        for e in errors:
            print(f"- {e}")
        return 1
    print(f"ALLOWED {order['side']} {order['asset']} {order['leverage']}x fingerprint={order_fingerprint(order)[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
