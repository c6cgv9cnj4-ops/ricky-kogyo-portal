"""情報源台帳（source_registry）の整合性テスト。ネットワーク不要。"""
import os
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import source_registry as R  # noqa: E402


class SourceRegistryTest(unittest.TestCase):
    def test_statuses_are_known_values(self):
        for r in R.rows():
            self.assertIn(r["terms_status"], R.STATUSES, r["host"])

    def test_no_source_is_auto_allowed_without_explicit_permission(self):
        # 規約で自動取得を明示的に許可した情報源は現時点で0件。許可済みとして扱わない。
        self.assertEqual(sum(1 for r in R.rows() if r["auto_allowed"]), 0)

    def test_every_row_has_evidence_url_and_reason(self):
        for r in R.rows():
            self.assertTrue(r["evidence_url"], r["host"])
            self.assertTrue(r["reason"], r["host"])

    def test_evidence_urls_are_http_or_https(self):
        for r in R.rows():
            self.assertTrue(r["evidence_url"].startswith(("https://", "http://")), r["host"])

    def test_counts_by_status(self):
        c = Counter(r["terms_status"] for r in R.rows())
        self.assertEqual(sum(c.values()), len(R.REGISTRY))
        self.assertEqual(c["許可確認"], 0)


if __name__ == "__main__":
    unittest.main()
