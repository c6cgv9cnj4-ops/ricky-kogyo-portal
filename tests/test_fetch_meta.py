"""取得時刻メタデータ（fetch_meta）とプレビュー注記のテスト。
ネットワークは使わず、書き込みは一時フォルダに限る。架空データは（テスト）と付ける。"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fetch_meta as FM  # noqa: E402
import portal_preview as P  # noqa: E402

JST = FM.JST


class TempMetaCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "meta.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class FetchMetaTest(TempMetaCase):
    def test_missing_file_is_unknown(self):
        self.assertIsNone(FM.load(self.path))
        self.assertEqual(FM.display_success_time(FM.load(self.path)), "不明")

    def test_success_is_timezone_aware_and_shown_in_jst(self):
        FM.record_success(self.path, when="2026-10-21T06:50:00+09:00")
        meta = FM.load(self.path)
        self.assertEqual(meta["last_success_at"], "2026-10-21T06:50:00+09:00")
        self.assertEqual(meta["last_attempt_status"], "成功")
        self.assertEqual(FM.display_success_time(meta), "2026-10-21 06:50 JST")

    def test_utc_success_is_converted_to_jst_for_display(self):
        FM.record_success(self.path, when="2026-10-20T21:50:00+00:00")
        self.assertEqual(FM.display_success_time(FM.load(self.path)), "2026-10-21 06:50 JST")

    def test_failure_keeps_previous_success_time(self):
        FM.record_success(self.path, when="2026-10-19T06:00:00+09:00")
        FM.record_failure("HTTP 503", self.path, when="2026-10-21T06:50:00+09:00")
        meta = FM.load(self.path)
        self.assertEqual(meta["last_success_at"], "2026-10-19T06:00:00+09:00")
        self.assertEqual(meta["last_attempt_status"], "失敗")
        self.assertEqual(meta["last_attempt_at"], "2026-10-21T06:50:00+09:00")
        self.assertEqual(meta["last_attempt_reason"], "HTTP 503")
        self.assertEqual(FM.display_success_time(meta), "2026-10-19 06:00 JST")

    def test_failure_without_prior_success_does_not_invent_time(self):
        FM.record_failure("取得エラー", self.path, when="2026-10-21T06:50:00+09:00")
        meta = FM.load(self.path)
        self.assertIsNone(meta["last_success_at"])
        self.assertEqual(FM.display_success_time(meta), "不明")

    def test_success_overwrites_previous_success_time(self):
        FM.record_success(self.path, when="2026-10-19T06:00:00+09:00")
        FM.record_success(self.path, when="2026-10-21T06:50:00+09:00")
        self.assertEqual(FM.display_success_time(FM.load(self.path)), "2026-10-21 06:50 JST")

    def test_naive_timestamp_is_unknown_not_guessed(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"last_success_at": "2026-10-21T06:50:00"}, f)
        self.assertEqual(FM.display_success_time(FM.load(self.path)), "不明")

    def test_corrupt_file_is_unknown(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ broken")
        self.assertIsNone(FM.load(self.path))
        FM.record_failure("テスト", self.path, when="2026-10-21T06:50:00+09:00")
        self.assertEqual(FM.display_success_time(FM.load(self.path)), "不明")

    def test_non_dict_json_is_unknown(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(["x"], f)
        self.assertIsNone(FM.load(self.path))

    def test_no_temp_file_left_behind(self):
        FM.record_success(self.path, when="2026-10-21T06:50:00+09:00")
        self.assertEqual(sorted(os.listdir(self.dir)), ["meta.json"])


class OutcomeTest(TempMetaCase):
    """全体の判定：部分失敗（失敗・件数急減・特定不能の施設）では成功時刻を更新しない。"""

    def test_all_venues_ok_records_success(self):
        FM.record_outcome([], self.path, when="2026-10-21T06:50:00+09:00")
        meta = FM.load(self.path)
        self.assertEqual(meta["last_attempt_status"], "成功")
        self.assertEqual(FM.display_success_time(meta), "2026-10-21 06:50 JST")

    def test_partial_failure_keeps_previous_success_time(self):
        FM.record_success(self.path, when="2026-10-19T06:00:00+09:00")
        FM.record_outcome(["施設A", "施設B", "施設A"], self.path, when="2026-10-21T06:50:00+09:00")
        meta = FM.load(self.path)
        self.assertEqual(meta["last_attempt_status"], "一部失敗")
        self.assertEqual(meta["last_success_at"], "2026-10-19T06:00:00+09:00")
        self.assertEqual(meta["last_attempt_reason"], "一部の施設が前回値: 施設A, 施設B")

    def test_unmapped_empty_counts_as_partial(self):
        FM.record_outcome(["未特定の取得関数"], self.path, when="2026-10-21T06:50:00+09:00")
        self.assertEqual(FM.load(self.path)["last_attempt_status"], "一部失敗")

    def test_partial_failure_without_prior_success_shows_unknown(self):
        FM.record_outcome(["施設A"], self.path, when="2026-10-21T06:50:00+09:00")
        self.assertEqual(FM.display_success_time(FM.load(self.path)), "不明")

    def test_success_after_partial_updates_time(self):
        FM.record_outcome(["施設A"], self.path, when="2026-10-20T06:00:00+09:00")
        FM.record_outcome([], self.path, when="2026-10-21T06:50:00+09:00")
        self.assertEqual(FM.display_success_time(FM.load(self.path)), "2026-10-21 06:50 JST")

    def test_meta_without_success_key_is_unknown(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"version": 1}, f)
        self.assertEqual(FM.display_success_time(FM.load(self.path)), "不明")

    def test_naive_previous_success_is_dropped_on_failure(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"last_success_at": "2026-10-19T06:00:00"}, f)
        FM.record_failure("テスト", self.path, when="2026-10-21T06:50:00+09:00")
        self.assertIsNone(FM.load(self.path)["last_success_at"])
        self.assertEqual(FM.display_success_time(FM.load(self.path)), "不明")

    def test_utc_offset_other_than_jst_is_shown_in_jst(self):
        FM.record_success(self.path, when="2026-10-21T00:00:00+00:00")
        self.assertEqual(FM.display_success_time(FM.load(self.path)), "2026-10-21 09:00 JST")


class SnapshotNoteTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.source = os.path.join(self.dir, "index.html")
        with open(self.source, "w", encoding="utf-8") as f:
            f.write("<html></html>")
        self.meta = os.path.join(self.dir, "meta.json")
        self.file_stamp = datetime.datetime.fromtimestamp(os.path.getmtime(self.source), JST).strftime("%Y-%m-%d %H:%M")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_no_meta_shows_unknown_and_file_time_is_labeled_separately(self):
        note = P.snapshot_note(self.source, self.meta)
        self.assertIn("展覧会の取得時刻（最終成功）：不明", note)
        self.assertIn(f"掲載ファイルの更新時刻：{self.file_stamp} JST", note)
        self.assertNotIn("取得時刻（最終成功）：" + self.file_stamp, note)

    def test_file_time_is_never_used_as_fetch_time(self):
        note = P.snapshot_note(self.source, self.meta)
        self.assertNotIn("取得時刻（最終成功）：" + self.file_stamp, note)

    def test_success_time_is_shown(self):
        FM.record_success(self.meta, when="2026-10-21T06:50:00+09:00")
        note = P.snapshot_note(self.source, self.meta)
        self.assertIn("展覧会の取得時刻（最終成功）：2026-10-21 06:50 JST", note)
        self.assertNotIn("失敗", note)

    def test_failure_after_success_shows_previous_time_with_warning(self):
        FM.record_success(self.meta, when="2026-10-19T06:00:00+09:00")
        FM.record_failure("HTTP 503", self.meta, when="2026-10-21T06:50:00+09:00")
        note = P.snapshot_note(self.source, self.meta)
        self.assertIn("2026-10-19 06:00 JST", note)
        self.assertIn("直近の取得は失敗", note)

    def test_default_path_without_metadata_file_shows_unknown(self):
        # 既定の保存先（実データの場所）を、存在しない一時パスに差し替えて確認する。偽の時刻は出ないこと
        saved = FM.META_PATH
        FM.META_PATH = os.path.join(self.dir, "absent", "culture_fetch_meta.json")
        try:
            note = P.snapshot_note(self.source)
        finally:
            FM.META_PATH = saved
        self.assertIn("展覧会の取得時刻（最終成功）：不明", note)
        self.assertNotIn("直近の取得は", note)

    def test_partial_failure_is_labeled_and_keeps_previous_time(self):
        FM.record_success(self.meta, when="2026-10-19T06:00:00+09:00")
        FM.record_outcome(["施設A"], self.meta, when="2026-10-21T06:50:00+09:00")
        note = P.snapshot_note(self.source, self.meta)
        self.assertIn("2026-10-19 06:00 JST", note)
        self.assertIn("直近の取得は一部失敗", note)

    def test_partial_failure_without_success_shows_unknown_and_status(self):
        FM.record_outcome(["施設A"], self.meta, when="2026-10-21T06:50:00+09:00")
        note = P.snapshot_note(self.source, self.meta)
        self.assertIn("展覧会の取得時刻（最終成功）：不明", note)
        self.assertIn("直近の取得は一部失敗", note)


if __name__ == "__main__":
    unittest.main()
