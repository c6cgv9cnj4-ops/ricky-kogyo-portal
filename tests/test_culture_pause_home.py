"""文化機能の一時停止（CULTURE_PAUSED）がホームに反映されることを確認する（一時停止の解除で元に戻ること含む）。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import auto_fetch_all as A  # noqa: E402


class CulturePauseHomeTest(unittest.TestCase):
    def test_event_files_exclude_culture_while_paused(self):
        self.assertTrue(A.CULTURE_PAUSED)
        self.assertNotIn(A.CULTURE_EVENTS_JSON, A.EVENT_JSON_FILES)
        self.assertIn(A.MUSIC_EVENTS_JSON, A.EVENT_JSON_FILES)

    def test_culture_card_removed_and_others_kept(self):
        items = [{"path": "culture-exhibition/", "title": "文化"},
                 {"path": "photo-spot/", "title": "撮影"},
                 {"path": "badminton/", "title": "バド"}]
        kept = A.visible_artifacts(items)
        self.assertEqual([i["title"] for i in kept], ["撮影", "バド"])

    def test_culture_card_kept_when_unpaused(self):
        items = [{"path": "culture-exhibition/", "title": "文化"}]
        with mock.patch.object(A, "CULTURE_PAUSED", False):
            self.assertEqual(A.visible_artifacts(items), items)

    def test_home_news_reads_music_only(self):
        # 停止中は、文化のファイルを読まずに音楽のデータだけを連結する
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            music = os.path.join(d, "events.json")
            with open(music, "w", encoding="utf-8") as f:
                json.dump({"records": [{"title": "音楽A"}], "failed_venues": ["音楽会場"]}, f)
            with mock.patch.object(A, "EVENT_JSON_FILES", [music]):
                self.assertEqual(A.load_all_event_records(), [{"title": "音楽A"}])
                self.assertEqual(A.load_all_failed_venues(), ["音楽会場"])


if __name__ == "__main__":
    unittest.main()
