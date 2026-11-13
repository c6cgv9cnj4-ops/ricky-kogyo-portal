"""上映予定（screenings）のテスト。架空のテスト専用データのみ使用する（ネットワーク不要）。"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import screenings as S  # noqa: E402

TODAY = datetime.date(2026, 10, 20)
NOW = datetime.datetime(2026, 10, 20, 9, 0, tzinfo=S.AW.JST)


def row(**kw):
    base = {"title": "架空映画A（テスト）", "theater": "架空シネマA（テスト）", "screen_name": "1",
            "screen_date": "2026-10-25", "start_time": "10:30", "runtime_min": "120",
            "evidence_url": "https://example.invalid/screening-a", "source_kind": "会場"}
    base.update(kw)
    return base


class ValidateTest(unittest.TestCase):
    def test_valid_row(self):
        self.assertEqual(S.validate(row()), [])

    def test_blank_optional_allowed(self):
        self.assertEqual(S.validate(row(screen_name="", runtime_min="", source_kind="")), [])

    def test_required_missing_rejected(self):
        for k in S.REQUIRED:
            self.assertTrue(S.validate(row(**{k: ""})), k)

    def test_invalid_date_rejected(self):
        self.assertTrue(S.validate(row(screen_date="2026/10/25")))      # 形式違い
        self.assertTrue(S.validate(row(screen_date="2026-02-30")))      # 存在しない日付

    def test_invalid_time_rejected(self):
        for bad in ("25:00", "10:60", "1030", "10:3", "午前10時"):
            self.assertTrue(S.validate(row(start_time=bad)), bad)

    def test_runtime_range_rejected(self):
        for bad in ("0", "401", "1.5", "abc", "-10"):
            self.assertTrue(S.validate(row(runtime_min=bad)), bad)

    def test_non_https_rejected(self):
        self.assertTrue(S.validate(row(evidence_url="http://example.invalid/x")))

    def test_unknown_source_kind_rejected(self):
        self.assertTrue(S.validate(row(source_kind="不明")))


class IngestTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="screenings_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_new_then_same_no_duplicate(self):
        r1 = S.ingest(self.dir, [row(), row(start_time="14:00")], NOW)
        self.assertEqual((r1["new"], r1["same"]), (2, 0))
        r2 = S.ingest(self.dir, [row()], NOW)
        self.assertEqual((r2["new"], r2["same"], r2["updated"]), (0, 1, 0))
        self.assertEqual(S.summarize(self.dir)["total"], 2)

    def test_notation_variant_same_screening(self):
        S.ingest(self.dir, [row()], NOW)
        r = S.ingest(self.dir, [row(title="架空映画Ａ（テスト）")], NOW)   # 全角英字
        self.assertEqual(r["same"], 1)
        self.assertEqual(S.summarize(self.dir)["total"], 1)

    def test_runtime_change_updates_and_records_history(self):
        S.ingest(self.dir, [row(runtime_min="120")], NOW)
        r = S.ingest(self.dir, [row(runtime_min="118")], NOW)
        self.assertEqual(r["updated"], 1)
        data = json.load(open(os.path.join(self.dir, "screenings.json"), encoding="utf-8"))
        (rec,) = data["screenings"].values()
        self.assertEqual(rec["runtime_min"], "118")
        self.assertEqual(data["history"][-1]["field"], "runtime_min")
        self.assertEqual(data["history"][-1]["before"], "120")

    def test_rejected_rows_not_stored_and_reported(self):
        r = S.ingest(self.dir, [row(), row(start_time="25:00"), row(screen_date="2026-02-30")], NOW)
        self.assertEqual(r["new"], 1)
        self.assertEqual([x["row"] for x in r["rejected"]], [2, 3])
        self.assertEqual(S.summarize(self.dir)["total"], 1)

    def test_all_rejected_does_not_touch_file(self):
        S.ingest(self.dir, [row()], NOW)
        path = os.path.join(self.dir, "screenings.json")
        before = open(path, "rb").read()
        r = S.ingest(self.dir, [row(start_time="99:99")], NOW)
        self.assertEqual(r["new"], 0)
        self.assertEqual(open(path, "rb").read(), before)   # 有効行が無ければファイルは変えない

    def test_existing_records_never_deleted(self):
        S.ingest(self.dir, [row(), row(start_time="14:00")], NOW)
        S.ingest(self.dir, [row(start_time="18:00")], NOW)   # CSVに無い上映は消さない
        self.assertEqual(S.summarize(self.dir)["total"], 3)

    def test_corrupt_store_stops_without_overwrite(self):
        path = os.path.join(self.dir, "screenings.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{壊れた")
        with self.assertRaises(S.CorruptStoreError):
            S.ingest(self.dir, [row()], NOW)
        self.assertEqual(open(path, encoding="utf-8").read(), "{壊れた")   # 上書きしない

    def test_separate_file_only(self):
        S.ingest(self.dir, [row()], NOW)
        self.assertEqual(os.listdir(self.dir), ["screenings.json"])


class SummaryAndUpcomingTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="screenings_sum_")
        S.ingest(self.dir, [
            row(screen_date="2026-10-21", start_time="10:00", runtime_min="100", screen_name="", source_kind=""),
            row(screen_date="2026-10-25", start_time="14:00", theater="架空シネマB（テスト）", title="架空映画B（テスト）"),
            row(screen_date="2026-11-30", start_time="09:30", title="架空映画C（テスト）", runtime_min=""),
            row(screen_date="2026-10-01", start_time="20:00", title="架空映画D（テスト）"),   # 過去
        ], NOW)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_summary_counts_period_missing(self):
        s = S.summarize(self.dir)
        self.assertEqual(s["total"], 4)
        self.assertEqual(s["titles"], 4)
        self.assertEqual(s["theaters"], 2)
        self.assertEqual(s["period"], ["2026-10-01", "2026-11-30"])
        self.assertEqual(s["missing_rate"]["screen_name"], 0.25)   # 1件目が空欄
        self.assertEqual(s["missing_rate"]["runtime_min"], 0.25)   # 3件目が空欄

    def test_upcoming_window_excludes_past_and_far(self):
        hits = S.upcoming(self.dir, today=TODAY, days=14)
        self.assertEqual([h["screen_date"] for h in hits], ["2026-10-21", "2026-10-25"])

    def test_upcoming_sorted_by_time(self):
        hits = S.upcoming(self.dir, today=TODAY, days=60)
        keys = [(h["screen_date"], h["start_time"]) for h in hits]
        self.assertEqual(keys, sorted(keys))

    def test_empty_store_summary(self):
        empty = tempfile.mkdtemp(prefix="screenings_empty_")
        try:
            s = S.summarize(empty)
            self.assertEqual(s["total"], 0)
            self.assertIsNone(s["period"])
        finally:
            shutil.rmtree(empty, ignore_errors=True)


class CsvTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="screenings_csv_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, text):
        path = os.path.join(self.dir, "in.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            f.write(text)
        return path

    def test_load_csv_with_bom(self):
        p = self._write("title,theater,screen_date,start_time,evidence_url\n"
                        "架空映画A（テスト）,架空シネマA（テスト）,2026-10-25,10:30,https://example.invalid/a\n")
        rows = S.load_csv(p)
        self.assertEqual(rows[0]["start_time"], "10:30")

    def test_missing_required_column_raises(self):
        with self.assertRaises(ValueError):
            S.load_csv(self._write("title,theater\nX,Y\n"))


class OptionalFieldsTest(unittest.TestCase):
    """上映区分・企画名・公開年（任意）。空欄の既存CSVも取り込めること、規則外の値を拒否することを確かめる。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="screenings_opt_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_old_csv_without_new_columns_still_loads(self):
        p = os.path.join(self.dir, "old.csv")
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            f.write("title,theater,screen_date,start_time,evidence_url\n"
                    "架空映画A（テスト）,架空シネマA（テスト）,2026-10-25,10:30,https://example.invalid/a\n")
        r = S.ingest(self.dir, S.load_csv(p), NOW)
        self.assertEqual((r["new"], r["rejected"]), (1, []))

    def test_blank_new_fields_are_valid(self):
        self.assertEqual(S.validate(row(screening_type="", program_title="", release_year="")), [])

    def test_known_types_valid_unknown_rejected(self):
        for t in S.SCREENING_TYPES:
            self.assertEqual(S.validate(row(screening_type=t)), [], t)
        self.assertTrue(S.validate(row(screening_type="不明な区分")))

    def test_release_year_rules(self):
        self.assertEqual(S.validate(row(release_year="1961")), [])
        for bad in ("61", "19610", "19x1", "1800", "2100", "1961.0"):
            self.assertTrue(S.validate(row(release_year=bad)), bad)

    def test_program_title_length(self):
        self.assertEqual(S.validate(row(program_title="あ" * S.PROGRAM_TITLE_MAX)), [])
        self.assertTrue(S.validate(row(program_title="あ" * (S.PROGRAM_TITLE_MAX + 1))))

    def test_key_unchanged_multiple_screenings_kept(self):
        # 同一作品の別の上映（館・日時が違う）は別々に保持する。キーは従来どおり。
        r = S.ingest(self.dir, [row(screening_type="特集上映", program_title="架空特集（テスト）"),
                                row(theater="架空シネマB（テスト）", screening_type="特集上映"),
                                row(start_time="14:00", screening_type="特集上映")], NOW)
        self.assertEqual(r["new"], 3)
        self.assertEqual(S.summarize(self.dir)["total"], 3)

    def test_same_screening_new_program_title_updates_with_history(self):
        S.ingest(self.dir, [row(program_title="")], NOW)
        r = S.ingest(self.dir, [row(program_title="架空特集（テスト）")], NOW)
        self.assertEqual(r["updated"], 1)
        data = json.load(open(os.path.join(self.dir, "screenings.json"), encoding="utf-8"))
        self.assertEqual(data["history"][-1]["field"], "program_title")

    def test_summary_missing_rate_includes_new_fields(self):
        S.ingest(self.dir, [row(screening_type="新作"), row(start_time="14:00")], NOW)
        s = S.summarize(self.dir)
        self.assertEqual(s["missing_rate"]["screening_type"], 0.5)
        self.assertEqual(s["missing_rate"]["release_year"], 1.0)


if __name__ == "__main__":
    unittest.main()
