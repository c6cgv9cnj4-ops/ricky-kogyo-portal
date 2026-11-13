"""施設マスター（venue_master）のテスト。架空データのみ。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import venue_master as V  # noqa: E402


class VenueMasterTest(unittest.TestCase):
    def test_same_name_same_id_across_notation(self):
        self.assertEqual(V.facility_id("架空美術館（テスト）"), V.facility_id("架空美術館(テスト)"))
        self.assertEqual(V.facility_id("ＡＢＣ館"), V.facility_id("ABC館"))   # 全角・半角は同じ施設

    def test_different_facilities_different_ids(self):
        self.assertNotEqual(V.facility_id("架空美術館A（テスト）"), V.facility_id("架空美術館B（テスト）"))

    def test_master_counts_exhibitions_and_skips_blank(self):
        rows = [{"venue": "架空美術館A（テスト）"}, {"venue": "架空美術館A（テスト）"},
                {"venue": "架空美術館B（テスト）"}, {"venue": ""}]
        m = V.build_master(rows)
        counts = {x["name"]: x["exhibitions"] for x in m}
        self.assertEqual(counts, {"架空美術館A（テスト）": 2, "架空美術館B（テスト）": 1})
        self.assertEqual(len(m), 2)


if __name__ == "__main__":
    import unittest as _u
    _u.main()
