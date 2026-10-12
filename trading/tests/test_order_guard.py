import json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from order_guard import check_order, order_fingerprint  # noqa: E402


def short_order(**over):
    o = {"asset": "HYPE", "side": "short", "leverage": 5, "ref_price": 87.5, "sl": 94.0,
         "tp": 75.0, "size": 1340, "orderType": "market", "executionMode": "paper"}
    o.update(over)
    return o


class OrderGuardTest(unittest.TestCase):
    def assertRefused(self, order, fragment):
        errors = check_order(order)
        self.assertTrue(any(fragment in e for e in errors), errors)

    def test_valid_short_allowed_without_approval(self):
        self.assertEqual(check_order(short_order()), [])

    def test_valid_long_allowed(self):
        self.assertEqual(check_order(short_order(side="long", sl=80.0, tp=100.0)), [])

    def test_real_money_refused(self):
        self.assertRefused(short_order(executionMode="live"), "not paper")

    def test_wrong_leverage(self):
        self.assertRefused(short_order(leverage=2), "leverage")

    def test_no_stop(self):
        self.assertRefused(short_order(sl=None), "no stop-loss")

    def test_stop_too_far(self):
        self.assertRefused(short_order(sl=96.0), "max 9.0%")  # 9.7% above entry

    def test_stop_exactly_nine_percent_allowed(self):
        o = short_order(side="long", ref_price=100.0, sl=91.0, tp=120.0)
        self.assertEqual(check_order(o), [])

    def test_stop_wrong_side(self):
        self.assertRefused(short_order(sl=80.0), "wrong side")

    def test_tp_wrong_side(self):
        self.assertRefused(short_order(tp=90.0), "take-profit")

    def test_missing_ref_price(self):
        self.assertRefused(short_order(ref_price=None), "ref_price")

    def test_unknown_side(self):
        self.assertRefused(short_order(side="buy"), "unknown side")

    def test_fingerprint_changes_with_order(self):
        self.assertNotEqual(order_fingerprint(short_order()), order_fingerprint(short_order(size=2000)))

    def test_cli_exit_codes(self):
        script = os.path.join(os.path.dirname(HERE), "order_guard.py")
        with tempfile.TemporaryDirectory() as d:
            op = os.path.join(d, "o.json")
            json.dump(short_order(), open(op, "w"))
            ok = subprocess.run([sys.executable, script, op], capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stdout)
            json.dump(short_order(leverage=10), open(op, "w"))
            bad = subprocess.run([sys.executable, script, op], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1)
            self.assertIn("REFUSED", bad.stdout)


if __name__ == "__main__":
    unittest.main()
