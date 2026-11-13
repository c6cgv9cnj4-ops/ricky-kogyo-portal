"""情報源台帳の拡張項目（kind・lifecycle など）と検査関数のテスト。架空データのみ・通信なし。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import source_registry as R  # noqa: E402

APPROVED_TUPLE = ("example.invalid", "テスト", "関東", "許可確認", "https://example.invalid/terms",
                  "架空の根拠（テスト）", "取得", "架空")


class ExistingRowsTest(unittest.TestCase):
    def test_existing_rows_get_defaults_without_changing_values(self):
        rows = R.rows()
        self.assertEqual(len(rows), len(R.REGISTRY))
        for d in rows:
            self.assertEqual(d["kind"], "html")
            self.assertEqual(d["lifecycle"], "保留")
            self.assertEqual(d["license_text"], "未確認")
            self.assertFalse(d["auto_allowed"], d["host"])

    def test_no_existing_row_is_auto_allowed(self):
        self.assertEqual(sum(1 for d in R.rows() if d["auto_allowed"]), 0)


class ValidateTest(unittest.TestCase):
    def base(self, **kw):
        d = {"terms_status": "許可確認", "evidence_url": "https://example.invalid/t",
             "checked_on": "2026-10-26", "kind": "rss", "lifecycle": "有効"}
        d.update(kw)
        return d

    def test_complete_approved_row_passes(self):
        self.assertEqual(R.validate(self.base()), [])

    def test_approved_without_evidence_url_fails(self):
        self.assertTrue(R.validate(self.base(evidence_url="")))

    def test_approved_without_checked_on_fails(self):
        self.assertTrue(R.validate(self.base(checked_on="")))

    def test_approved_but_stopped_fails(self):
        self.assertTrue(R.validate(self.base(lifecycle="停止中")))

    def test_unknown_kind_fails(self):
        self.assertTrue(R.validate(self.base(kind="xls")))

    def test_unknown_terms_status_fails(self):
        self.assertTrue(R.validate(self.base(terms_status="たぶん可")))

    def test_non_approved_row_is_not_judged_by_approval_rules(self):
        self.assertEqual(R.validate({"terms_status": "判断不能", "kind": "html", "lifecycle": "保留"}), [])


class AutoAllowedGuardTest(unittest.TestCase):
    def test_approved_tuple_without_lifecycle_is_not_auto_allowed(self):
        # 許可確認の行でも、既定の状態（保留）のままなら自動取得しない
        with mock.patch.object(R, "REGISTRY", [APPROVED_TUPLE]):
            rows = R.rows()
        self.assertEqual(rows[0]["terms_status"], "許可確認")
        self.assertEqual(rows[0]["lifecycle"], "保留")
        self.assertFalse(rows[0]["auto_allowed"])

    def test_approved_tuple_without_evidence_is_not_auto_allowed(self):
        no_evidence = APPROVED_TUPLE[:4] + ("",) + APPROVED_TUPLE[5:]
        with mock.patch.object(R, "REGISTRY", [no_evidence]):
            self.assertFalse(R.rows()[0]["auto_allowed"])

    def test_non_approved_tuple_stays_not_auto_allowed(self):
        limited = ("example.invalid", "テスト", "関東", "制限あり", "https://example.invalid/t",
                   "架空", "手動確認", "架空")
        with mock.patch.object(R, "REGISTRY", [limited]):
            self.assertFalse(R.rows()[0]["auto_allowed"])


if __name__ == "__main__":
    unittest.main()
