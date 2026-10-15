"""カルチャー生成の件数・無料の急減ガードの判定テスト（ネットワーク不要）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from culture_exhibition_dashboard import guard_decision  # noqa: E402


class GuardDecisionTest(unittest.TestCase):
    def test_floor_is_not_released_by_force(self):
        ok, msg = guard_decision(30, 70, 68, 30, force=True)  # 50%未満
        self.assertFalse(ok)
        self.assertIn("下限ガード", msg)

    def test_count_drop_blocked_without_force(self):
        ok, _ = guard_decision(50, 70, 0, 0, force=False)  # 80%未満
        self.assertFalse(ok)

    def test_count_drop_allowed_with_force(self):
        ok, _ = guard_decision(50, 70, 0, 0, force=True)
        self.assertTrue(ok)

    def test_free_drop_68_to_18_blocked_without_force(self):
        ok, msg = guard_decision(100, 100, 68, 18, force=False)
        self.assertFalse(ok)
        self.assertIn("無料急減ガード", msg)

    def test_free_drop_68_to_18_allowed_only_with_force(self):
        ok, _ = guard_decision(100, 100, 68, 18, force=True)
        self.assertTrue(ok)

    def test_normal_update_passes(self):
        ok, _ = guard_decision(100, 100, 68, 66, force=False)
        self.assertTrue(ok)

    def test_first_run_without_previous_passes(self):
        ok, _ = guard_decision(10, 0, 0, 0, force=False)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
