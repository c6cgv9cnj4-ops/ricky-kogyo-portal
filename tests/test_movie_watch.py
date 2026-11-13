"""映画の発見・公開日の追跡の検証（テスト用の架空の登録人物・架空の作品。本番データではない）。"""
import datetime
import os
import sys
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import movie_watch as M  # noqa: E402

TODAY = datetime.date(2026, 10, 18)
NOW = "2026-10-18T06:00:00+09:00"
PREV_NOW = "2026-10-10T06:00:00+09:00"
REG = [{"id": "test_person_a", "names": ["テスト太郎"]},
       {"id": "test_person_b", "names": ["テスト太郎"]},     # 同名（テスト用）
       {"id": "test_person_c", "names": ["テスト花子", "T.Hanako"]}]   # 表記揺れ・別名（テスト用）


def film(fid, title, release=None, credits=None, url=None):
    return {"id": M.movie_key("testsrc", fid, title), "title": title,
            "release_date": release, "credits": credits or {}, "source_url": url or f"https://example.test/{fid}"}


class MatchTest(unittest.TestCase):
    def test_normalized_match_hits_registered_person(self):
        matches, held = M.match_people({"cast": ["ﾃｽﾄ花子"]}, REG)
        self.assertEqual([m["person_id"] for m in matches], ["test_person_c"])
        self.assertEqual(held, [])

    def test_alias_matches(self):
        matches, _ = M.match_people({"directors": ["T. Hanako"]}, REG)
        self.assertEqual(matches[0]["person_id"], "test_person_c")

    def test_same_name_for_two_people_is_held_not_matched(self):
        matches, held = M.match_people({"cast": ["テスト太郎"]}, REG)
        self.assertEqual(matches, [])
        self.assertEqual(held[0]["candidates"], ["test_person_a", "test_person_b"])

    def test_unregistered_name_is_not_matched(self):
        matches, held = M.match_people({"cast": ["どこにもない人"]}, REG)
        self.assertEqual((matches, held), ([], []))

    def test_no_registry_means_no_matches(self):
        matches, _ = M.match_people({"cast": ["テスト花子"]}, [])
        self.assertEqual(matches, [])


class IdentityTest(unittest.TestCase):
    def test_official_id_is_preferred_over_title(self):
        self.assertEqual(M.movie_key("s", "123", "作品名変更前"), "s|123")

    def test_title_variants_map_to_same_key_without_official_id(self):
        self.assertEqual(M.movie_key("s", None, "テスト・ムービー"), M.movie_key("s", None, "テストムービー"))


class StateTest(unittest.TestCase):
    def test_new_film_is_registered_with_detection_time(self):
        recs, state = M.apply_source_result([], [film("1", "新作A")], True, NOW, TODAY)
        self.assertTrue(state["ok"])
        self.assertEqual(recs[0]["first_detected_at"], NOW)
        self.assertIsNone(recs[0]["release_date"])

    def test_unknown_release_becomes_known_once_and_keeps_time(self):
        recs, _ = M.apply_source_result([], [film("1", "新作A")], True, PREV_NOW, TODAY)
        recs, _ = M.apply_source_result(recs, [film("1", "新作A", datetime.date(2026, 11, 20))], True, NOW, TODAY)
        self.assertEqual(recs[0]["release_first_known_at"], NOW)
        self.assertEqual(recs[0]["release_date"], datetime.date(2026, 11, 20))
        self.assertEqual(recs[0]["history"][-1]["field"], "公開日")
        self.assertIsNone(recs[0]["history"][-1]["old"])

    def test_release_change_is_detected_and_logged(self):
        recs, _ = M.apply_source_result([], [film("1", "新作A", datetime.date(2026, 11, 20))], True, PREV_NOW, TODAY)
        recs, _ = M.apply_source_result(recs, [film("1", "新作A", datetime.date(2026, 12, 4))], True, NOW, TODAY)
        last = recs[0]["history"][-1]
        self.assertEqual((last["old"], last["new"]), ("2026-11-20", "2026-12-04"))

    def test_staff_filled_later_without_deleting_existing(self):
        recs, _ = M.apply_source_result([], [film("1", "新作A")], True, PREV_NOW, TODAY)
        recs, _ = M.apply_source_result(recs, [film("1", "新作A", credits={"directors": ["監督X"]})], True, NOW, TODAY)
        self.assertEqual(recs[0]["credits"]["directors"], ["監督X"])
        recs, _ = M.apply_source_result(recs, [film("1", "新作A", credits={"cast": ["出演Y"]})], True, NOW, TODAY)
        self.assertEqual(recs[0]["credits"]["directors"], ["監督X"])     # 消えていない
        self.assertEqual(recs[0]["credits"]["cast"], ["出演Y"])

    def test_same_film_is_not_duplicated_across_runs(self):
        recs, _ = M.apply_source_result([], [film("1", "新作A")], True, PREV_NOW, TODAY)
        recs, _ = M.apply_source_result(recs, [film("1", "新作A")], True, NOW, TODAY)
        self.assertEqual(len(recs), 1)


class ProtectionTest(unittest.TestCase):
    def setUp(self):
        self.prev, _ = M.apply_source_result([], [film(str(i), f"作品{i}", datetime.date(2026, 11, 1)) for i in range(5)],
                                             True, PREV_NOW, TODAY)

    def test_fetch_failure_keeps_previous(self):
        recs, state = M.apply_source_result(self.prev, None, False, NOW, TODAY)
        self.assertFalse(state["ok"])
        self.assertEqual(len(recs), 5)
        self.assertTrue(all("取得失敗" in r["fetch_state"] for r in recs))
        self.assertTrue(all(r["release_date"] for r in recs))

    def test_zero_results_is_not_treated_as_no_films(self):
        recs, state = M.apply_source_result(self.prev, [], True, NOW, TODAY)
        self.assertEqual(state["reason"], "0件取得")
        self.assertEqual(len(recs), 5)

    def test_sudden_drop_keeps_previous(self):
        recs, state = M.apply_source_result(self.prev, [film("0", "作品0")], True, NOW, TODAY)
        self.assertEqual(state["reason"], "件数急減")
        self.assertEqual(len(recs), 5)

    def test_partial_missing_is_not_deleted_and_marked(self):
        fresh = [film(str(i), f"作品{i}", datetime.date(2026, 11, 1)) for i in range(4)]
        recs, state = M.apply_source_result(self.prev, fresh, True, NOW, TODAY)
        self.assertTrue(state["ok"])
        missing = [r for r in recs if r["id"] == self.prev[4]["id"]][0]
        self.assertIn("掲載確認できず", missing["fetch_state"])
        self.assertEqual(missing["release_date"], datetime.date(2026, 11, 1))

    def test_release_change_is_kept_on_recovery(self):
        recs, _ = M.apply_source_result(self.prev, None, False, NOW, TODAY)
        self.assertEqual(recs[0]["release_date"], datetime.date(2026, 11, 1))


class HomeTest(unittest.TestCase):
    def test_priority_order_and_content(self):
        recs, _ = M.apply_source_result([], [
            film("1", "未定の注目作", None),
            film("2", "判明した注目作", datetime.date(2026, 11, 20)),
            film("3", "一般の作品", datetime.date(2026, 11, 10)),
        ], True, NOW, TODAY)
        matches = {recs[0]["id"]: [{"name": "テスト花子", "role": "cast", "person_id": "test_person_c"}],
                   recs[1]["id"]: [{"name": "テスト花子", "role": "cast", "person_id": "test_person_c"}]}
        rows = M.home_rows(recs, matches, TODAY)
        labels = [(r["rec"]["title"], r["label"]) for r in rows]
        self.assertEqual(labels[0], ("判明した注目作", M.PRIORITY_LABELS[0]))
        self.assertIn(("未定の注目作", M.PRIORITY_LABELS[0]), labels)
        # 一般の作品は、注目人物ではないため、注目人物の作品より後ろに出る（公開日が初めて判明したので、2番目の区分）
        self.assertEqual(labels[-1], ("一般の作品", M.PRIORITY_LABELS[1]))

    def test_html_shows_link_person_release_and_unknown(self):
        recs, _ = M.apply_source_result([], [film("1", "未定の注目作", None, url="https://example.test/1")], True, NOW, TODAY)
        rows = M.home_rows(recs, {recs[0]["id"]: [{"name": "テスト花子"}]}, TODAY)
        html_text = M.render_home(rows)
        self.assertIn("href='https://example.test/1'", html_text)
        self.assertIn("注目人物：テスト花子", html_text)
        self.assertIn("公開日：未定", html_text)
        self.assertIn("初回検知：2026-10-18", html_text)
        self.assertNotIn("None", html_text)

    def test_empty_home_says_so(self):
        self.assertIn("ありません", M.render_home([]))


class RegistryLoadTest(unittest.TestCase):
    def test_missing_registry_loads_empty(self):
        self.assertEqual(M.load_registry(os.path.join(tempfile.gettempdir(), "no_such_watch_file.json")), [])


if __name__ == "__main__":
    unittest.main()
