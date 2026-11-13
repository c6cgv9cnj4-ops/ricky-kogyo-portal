"""来日実績台帳（tour_history）のテスト。架空のテスト専用データのみ使用する（ネットワーク不要）。"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tour_history as TH  # noqa: E402

TODAY = datetime.date(2026, 10, 20)
NOW = datetime.datetime(2026, 10, 20, 9, 0, tzinfo=TH.AW.JST)


def row(**kw):
    base = {"artist_name": "架空アーティストX（テスト）", "arrival_month": "2024-03", "venue": "架空ホールA（テスト）",
            "promoter": "架空主催社（テスト）", "evidence_url": "https://example.invalid/tour-x", "title": "架空ツアー2024",
            "source_kind": "主催者"}
    base.update(kw)
    return base


class ValidateTest(unittest.TestCase):
    def test_valid_past_row(self):
        self.assertEqual(TH.validate(row(), TODAY), [])

    def test_required_missing_rejected(self):
        for k in TH.REQUIRED:
            errs = TH.validate(row(**{k: ""}), TODAY)
            self.assertTrue(any(k in e for e in errs), k)

    def test_future_month_rejected(self):
        self.assertTrue(TH.validate(row(arrival_month="2026-11"), TODAY))

    def test_current_month_allowed(self):
        self.assertEqual(TH.validate(row(arrival_month="2026-10"), TODAY), [])

    def test_bad_month_format_rejected(self):
        self.assertTrue(TH.validate(row(arrival_month="2024/3"), TODAY))
        self.assertTrue(TH.validate(row(arrival_month="2024-13"), TODAY))

    def test_non_https_url_rejected(self):
        self.assertTrue(TH.validate(row(evidence_url="http://example.invalid/x"), TODAY))

    def test_unknown_source_kind_rejected(self):
        self.assertTrue(TH.validate(row(source_kind="不明な種類"), TODAY))


class IngestTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tour_history_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_new_then_same_no_duplicate(self):
        r1 = TH.ingest(self.dir, [row(), row(arrival_month="2025-01", venue="架空ホールB（テスト）")], TODAY, NOW)
        self.assertEqual((r1["new"], r1["same"]), (2, 0))
        r2 = TH.ingest(self.dir, [row()], TODAY, NOW)   # 同じ行の再取込
        self.assertEqual((r2["new"], r2["same"]), (0, 1))
        self.assertEqual(TH.summarize(self.dir)["total"], 2)

    def test_notation_variant_is_same_record(self):
        TH.ingest(self.dir, [row()], TODAY, NOW)
        r = TH.ingest(self.dir, [row(artist_name="架空アーティストＸ（テスト）")], TODAY, NOW)   # 全角英字
        self.assertEqual(r["same"], 1)
        self.assertEqual(TH.summarize(self.dir)["total"], 1)

    def test_promoter_conflict_keeps_original(self):
        TH.ingest(self.dir, [row()], TODAY, NOW)
        r = TH.ingest(self.dir, [row(promoter="別の架空主催（テスト）")], TODAY, NOW)
        self.assertEqual(r["conflict"], 1)
        _, data = TH.load_store(self.dir)
        (rec,) = data["records"].values()
        self.assertEqual(rec["promoter"], "架空主催社（テスト）")   # 上書きしない
        self.assertEqual(data["conflicts"][0]["incoming"], "別の架空主催（テスト）")

    def test_rejected_rows_not_stored_and_reported(self):
        r = TH.ingest(self.dir, [row(), row(arrival_month="2030-01"), row(promoter="")], TODAY, NOW)
        self.assertEqual(r["new"], 1)
        self.assertEqual([x["row"] for x in r["rejected"]], [2, 3])
        self.assertEqual(TH.summarize(self.dir)["total"], 1)

    def test_store_is_separate_file_and_valid_json(self):
        TH.ingest(self.dir, [row()], TODAY, NOW)
        self.assertEqual(sorted(os.listdir(self.dir)), ["tour_history.json"])
        with open(os.path.join(self.dir, "tour_history.json"), encoding="utf-8") as f:
            json.load(f)


class SummaryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tour_history_sum_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_counts_years_and_missing_rate(self):
        TH.ingest(self.dir, [
            row(),
            row(arrival_month="2025-01", venue="架空ホールB（テスト）", title=""),
            row(arrival_month="2025-02", venue="架空ホールC（テスト）", title="", source_kind=""),
            row(artist_name="架空アーティストY（テスト）", venue="架空ホールD（テスト）", title="", source_kind=""),
        ], TODAY, NOW)
        s = TH.summarize(self.dir)
        self.assertEqual(s["total"], 4)
        self.assertEqual(s["artists"], 2)
        self.assertEqual(s["by_year"], {"2024": 2, "2025": 2})   # 1件目と4件目は既定の2024-03
        self.assertEqual(s["missing_rate"]["title"], 0.75)   # 2〜4件目の公演名が空（3/4）
        self.assertEqual(s["missing_rate"]["source_kind"], 0.5)

    def test_empty_store_summary(self):
        s = TH.summarize(self.dir)
        self.assertEqual(s["total"], 0)
        self.assertIsNone(s["missing_rate"]["title"])


class CsvTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tour_csv_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, text):
        path = os.path.join(self.dir, "in.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            f.write(text)
        return path

    def test_load_csv_with_bom(self):
        p = self._write("artist_name,arrival_month,venue,promoter,evidence_url,title,source_kind\n"
                        "架空アーティストX（テスト）,2024-03,架空ホールA（テスト）,架空主催社（テスト）,https://example.invalid/x,架空ツアー,主催者\n")
        rows = TH.load_csv(p)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["arrival_month"], "2024-03")

    def test_missing_required_column_raises(self):
        p = self._write("artist_name,venue\nX,Y\n")
        with self.assertRaises(ValueError):
            TH.load_csv(p)


if __name__ == "__main__":
    unittest.main()
