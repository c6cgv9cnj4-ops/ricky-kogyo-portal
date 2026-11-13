"""映画の上映情報のリンク中心表示（未接続の準備）と、取得失敗時の前回データ保持の検証。ネットワーク不要。"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import cinema_link_view as C  # noqa: E402

FILMS = [
    {"title": "作品A", "days": [{"date": datetime.date(2026, 10, 18), "times": ["10:00", "13:00"]}]},
    {"title": "作品B", "days": [{"date": datetime.date(2026, 10, 20), "times": ["15:00"]}]},
]
NOW = "2026-10-18T06:00:00+09:00"


class LinkViewTest(unittest.TestCase):
    def test_view_has_no_copied_content(self):
        view = C.link_view(FILMS)
        text = repr(view)
        for forbidden in ["作品A", "作品B", "10:00", "監督", "出演", "synopsis"]:
            self.assertNotIn(forbidden, text)
        self.assertEqual(view["film_count"], 2)
        self.assertEqual(view["date_from"], "2026-10-18")
        self.assertEqual(view["date_to"], "2026-10-20")

    def test_card_has_official_link_and_no_titles(self):
        html_text = C.render_link_card(C.apply_last_good(None, C.link_view(FILMS), NOW))
        self.assertIn(f"href='{C.SCHEDULE_URL}'", html_text)
        self.assertNotIn("作品A", html_text)
        self.assertIn("取得成功", html_text)
        self.assertIn("2026-10-18", html_text)


class LastGoodTest(unittest.TestCase):
    def setUp(self):
        self.previous = C.apply_last_good(None, C.link_view(FILMS), "2026-10-17T06:00:00+09:00")

    def test_success_replaces_with_new_time(self):
        state = C.apply_last_good(self.previous, C.link_view(FILMS[:1]), NOW)
        self.assertFalse(state["stale"])
        self.assertEqual(state["fetched_at"], NOW)
        self.assertEqual(state["film_count"], 1)

    def test_failure_keeps_previous_and_marks_stale(self):
        state = C.apply_last_good(self.previous, None, NOW)
        self.assertTrue(state["stale"])
        self.assertEqual(state["film_count"], 2)
        self.assertEqual(state["fetched_at"], "2026-10-17T06:00:00+09:00")
        self.assertIn("前回の取得日時", state["status"])

    def test_zero_result_is_not_a_valid_empty(self):
        state = C.apply_last_good(self.previous, C.link_view([]), NOW)
        self.assertTrue(state["stale"])
        self.assertEqual(state["film_count"], 2)

    def test_no_previous_and_failure_is_explicit_not_zero(self):
        state = C.apply_last_good(None, None, NOW)
        self.assertEqual(state["status"], "取得できていません")
        self.assertIsNone(state["fetched_at"])
        html_text = C.render_link_card(state)
        self.assertIn("取得できていません", html_text)
        self.assertIn("掲載期間：未確認", html_text)
        self.assertNotIn("None", html_text)

    def test_stale_card_shows_last_fetch_time(self):
        state = C.apply_last_good(self.previous, None, NOW)
        html_text = C.render_link_card(state)
        self.assertIn("2026-10-17T06:00:00+09:00", html_text)
        self.assertIn("前回の取得日時", html_text)


if __name__ == "__main__":
    unittest.main()
