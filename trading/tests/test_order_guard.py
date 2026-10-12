import json, os, subprocess, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from order_guard import check_order, order_fingerprint  # noqa: E402

NOW = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)


def short_order(**over):
    o = {"asset": "HYPE", "side": "short", "leverage": 5, "ref_price": 87.5, "sl": 94.0,
         "tp": 75.0, "size": 1340, "orderType": "market", "executionMode": "paper"}
    o.update(over)
    return o


def approve(order, text="да", minutes_ago=5):
    return {"text": text, "order_fingerprint": order_fingerprint(order),
            "approved_at": (NOW - timedelta(minutes=minutes_ago)).isoformat()}


class OrderGuardTest(unittest.TestCase):
    def assertRefused(self, order, approval, fragment):
        errors = check_order(order, approval, now=NOW)
        self.assertTrue(any(fragment in e for e in errors), errors)

    def test_valid_short_allowed(self):
        o = short_order()
        self.assertEqual(check_order(o, approve(o), now=NOW), [])

    def test_valid_long_allowed(self):
        o = short_order(side="long", sl=80.0, tp=100.0)
        self.assertEqual(check_order(o, approve(o, text=" Да! "), now=NOW), [])

    def test_no_approval(self):
        self.assertRefused(short_order(), None, "no approval")

    def test_approval_not_da(self):
        o = short_order()
        self.assertRefused(o, approve(o, text="ок"), "not an explicit")

    def test_approval_for_other_order(self):
        o = short_order()
        a = approve(o)
        self.assertRefused(short_order(size=2000), a, "different order")

    def test_stale_approval(self):
        o = short_order()
        self.assertRefused(o, approve(o, minutes_ago=31), "stale")

    def test_future_approval(self):
        o = short_order()
        self.assertRefused(o, approve(o, minutes_ago=-5), "stale")

    def test_real_money_refused(self):
        o = short_order(executionMode="live")
        self.assertRefused(o, approve(o), "not paper")

    def test_wrong_leverage(self):
        o = short_order(leverage=2)
        self.assertRefused(o, approve(o), "leverage")

    def test_no_stop(self):
        o = short_order(sl=None)
        self.assertRefused(o, approve(o), "no stop-loss")

    def test_stop_too_far(self):
        o = short_order(sl=96.0)  # 9.7% above entry
        self.assertRefused(o, approve(o), "max 9.0%")

    def test_stop_exactly_nine_percent_allowed(self):
        o = short_order(side="long", ref_price=100.0, sl=91.0, tp=120.0)
        self.assertEqual(check_order(o, approve(o), now=NOW), [])

    def test_stop_wrong_side(self):
        o = short_order(sl=80.0)
        self.assertRefused(o, approve(o), "wrong side")

    def test_tp_wrong_side(self):
        o = short_order(tp=90.0)
        self.assertRefused(o, approve(o), "take-profit")

    def test_missing_ref_price(self):
        o = short_order(ref_price=None)
        self.assertRefused(o, approve(o), "ref_price")

    def test_cli_exit_codes(self):
        o = short_order()
        a = approve(o, minutes_ago=0)
        a["approved_at"] = datetime.now(timezone.utc).isoformat()
        with tempfile.TemporaryDirectory() as d:
            op, ap = os.path.join(d, "o.json"), os.path.join(d, "a.json")
            json.dump(o, open(op, "w"))
            json.dump(a, open(ap, "w"))
            script = os.path.join(os.path.dirname(HERE), "order_guard.py")
            ok = subprocess.run([sys.executable, script, op, ap], capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stdout)
            json.dump(short_order(leverage=10), open(op, "w"))
            bad = subprocess.run([sys.executable, script, op, ap], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1)
            self.assertIn("REFUSED", bad.stdout)


if __name__ == "__main__":
    unittest.main()
