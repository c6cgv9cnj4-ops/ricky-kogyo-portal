"""カルチャー生成の期限解析（P0）・状態JSON（P1）・施設単位の失敗保持（P2）・施設単位の欠落ガード（P3）の検証。
ネットワーク不要。取得関数と券売情報の取得は差し替えて、架空のデータで動かす。"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import culture_exhibition_dashboard as D  # noqa: E402

TODAY = datetime.date(2026, 10, 15)


def item(venue, title, link, period, status="開催中"):
    return {"venue": venue, "title": title, "link": link, "period": period, "status": status, "discount": ""}


class ParseEndDateTest(unittest.TestCase):
    """P0：会期の期限解析"""

    def test_single_date_with_made_is_end_date(self):
        self.assertEqual(D.parse_end_date("2026年10月20日（火）まで"), datetime.date(2026, 10, 20))

    def test_single_date_with_shuryo_is_end_date(self):
        self.assertEqual(D.parse_end_date("2026年10月20日 終了"), datetime.date(2026, 10, 20))

    def test_start_only_stays_none(self):
        self.assertIsNone(D.parse_end_date("2026年10月20日（火）から"))

    def test_range_with_short_end(self):
        self.assertEqual(D.parse_end_date("2026年7月8日 〜 8月30日"), datetime.date(2026, 8, 30))

    def test_full_range(self):
        self.assertEqual(D.parse_end_date("2026年9月 9日（水）～2026年12月13日（日）"), datetime.date(2026, 12, 13))

    def test_unknown_stays_none(self):
        self.assertIsNone(D.parse_end_date("取得困難（要アクセス）"))
        self.assertIsNone(D.parse_end_date(""))


class VenueDropGuardTest(unittest.TestCase):
    """P3：施設単位の件数減少"""

    def test_half_or_less_is_failure(self):
        self.assertEqual(D.venue_drop_failures({"A": 10}, {"A": 4}), {"A"})

    def test_mild_drop_passes(self):
        self.assertEqual(D.venue_drop_failures({"A": 10}, {"A": 6}), set())

    def test_below_minimum_is_not_judged(self):
        self.assertEqual(D.venue_drop_failures({"A": 2}, {"A": 0}), set())

    def test_absent_venue_is_failure_when_known(self):
        self.assertEqual(D.venue_drop_failures({"A": 5}, {}), {"A"})


class ClassifyAndMergeTest(unittest.TestCase):
    """P1・P2：新着・変更・消失・終了・保持の判定（純粋な関数）"""

    def setUp(self):
        self.a = item("A館", "展1", "https://a/1", "2026年9月1日～2026年12月1日")
        self.b = item("B館", "展2", "https://b/1", "2026年9月1日～2026年12月1日")

    def state_of(self, *items):
        return {"items": {D.item_id(i): dict(i) for i in items}}

    def test_baseline_has_no_new_labels(self):
        merged, labels = D.classify_and_merge(None, [self.a, self.b], set(), TODAY)
        self.assertEqual(len(merged), 2)
        self.assertNotIn("新着", labels.values())

    def test_new_item_is_labelled_new(self):
        merged, labels = D.classify_and_merge(self.state_of(self.a), [self.a, self.b], set(), TODAY)
        self.assertEqual(labels[D.item_id(self.b)], "新着")
        self.assertEqual(labels[D.item_id(self.a)], "変更なし")

    def test_period_change_is_labelled_changed(self):
        changed = dict(self.a, period="2026年9月1日～2026年12月31日")
        _, labels = D.classify_and_merge(self.state_of(self.a), [changed], set(), TODAY)
        self.assertEqual(labels[D.item_id(self.a)], "変更")

    def test_identical_second_run_has_no_new_or_changed(self):
        _, labels = D.classify_and_merge(self.state_of(self.a, self.b), [self.a, self.b], set(), TODAY)
        self.assertNotIn("新着", labels.values())
        self.assertNotIn("変更", labels.values())

    def test_ended_item_is_labelled_ended(self):
        old = item("A館", "展0", "https://a/0", "2026年7月8日 〜 8月30日")
        _, labels = D.classify_and_merge(self.state_of(old), [old], set(), TODAY)
        self.assertEqual(labels[D.item_id(old)], "終了")

    def test_missing_from_successful_venue_is_removed_and_labelled(self):
        merged, labels = D.classify_and_merge(self.state_of(self.a, self.b), [self.a], set(), TODAY)
        self.assertNotIn(D.item_id(self.b), merged)
        self.assertEqual(labels[D.item_id(self.b)], "消失")

    def test_failed_venue_keeps_previous_item(self):
        merged, labels = D.classify_and_merge(self.state_of(self.a, self.b), [self.a], {"B館"}, TODAY)
        self.assertIn(D.item_id(self.b), merged)
        self.assertEqual(labels[D.item_id(self.b)], "保持（取得失敗）")

    def test_same_url_different_titles_are_separate_ids(self):
        x = item("A館", "展X", "https://a/same", "2026年9月1日～2026年12月1日")
        y = item("A館", "展Y", "https://a/same", "2026年9月1日～2026年12月1日")
        self.assertNotEqual(D.item_id(x), D.item_id(y))


class BuildDatasetTest(unittest.TestCase):
    """P1〜P3 の統合：取得関数を差し替え、状態JSONを一時ディレクトリに保存して、連続する実行を再現する"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.state_path = os.path.join(self.dir, "culture_state.json")
        self.saved = (D.STATE_JSON, D.FETCHERS, D.fetch_ticket_info)
        D.STATE_JSON = self.state_path
        D.fetch_ticket_info = lambda url: ("", "", "", False)
        self.venues = {}          # 施設名 -> 次回の取得結果（生データ）または None（失敗）

    def tearDown(self):
        D.STATE_JSON, D.FETCHERS, D.fetch_ticket_info = self.saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def _fetcher(self, name):
        def fetch():
            result = self.venues[name]
            if result is None:
                D.log_skip(name, "取得エラー (テスト)")
                return []
            return [dict(i) for i in result]
        fetch.__name__ = f"fetch_{name}"
        return fetch

    def run_once(self, today=TODAY):
        D.FETCHERS = [self._fetcher(name) for name in self.venues]
        active, ended, open_count = D.build_dataset(today=today)
        diff = D.LAST_DIFF
        D.save_state({"version": 1, "venue_counts": diff["venue_counts"], "items": diff["merged"]})
        return active, diff

    def test_first_run_is_baseline_not_new(self):
        self.venues = {"A館": [item("A館", "展1", "https://a/1", "2026年9月1日～2026年12月1日")]}
        _, diff = self.run_once()
        self.assertTrue(diff["baseline"])
        self.assertEqual(diff["counts"]["新着"], 0)
        self.assertTrue(os.path.exists(self.state_path))

    def test_second_identical_run_has_no_new_or_changed(self):
        self.venues = {"A館": [item("A館", "展1", "https://a/1", "2026年9月1日～2026年12月1日")],
                       "B館": [item("B館", "展2", "https://b/1", "2026年9月1日～2026年12月1日")]}
        self.run_once()
        _, diff = self.run_once()
        self.assertFalse(diff["baseline"])
        self.assertEqual(diff["counts"]["新着"], 0)
        self.assertEqual(diff["counts"]["変更"], 0)
        self.assertEqual(diff["counts"]["変更なし"], 2)

    def test_failed_venue_keeps_previous_items_and_page_data(self):
        a = item("A館", "展1", "https://a/1", "2026年9月1日～2026年12月1日")
        self.venues = {"A館": [a], "B館": [item("B館", "展2", "https://b/1", "2026年9月1日～2026年12月1日")]}
        self.run_once()
        self.venues["B館"] = None   # B館の取得失敗
        active, diff = self.run_once()
        names = {i["title"] for i in active}
        self.assertIn("展2", names)
        self.assertEqual(diff["counts"]["保持（取得失敗）"], 1)
        self.assertIn("B館", diff["failed"])

    def test_sudden_venue_drop_keeps_previous_items(self):
        base = [item("A館", f"展{i}", f"https://a/{i}", "2026年9月1日～2026年12月1日") for i in range(10)]
        self.venues = {"A館": base}
        self.run_once()
        self.venues["A館"] = base[:3]   # 10件 → 3件（30%）
        active, diff = self.run_once()
        self.assertEqual(len([i for i in active if i["venue"] == "A館"]), 10)
        self.assertIn("A館", diff["guard_dropped"])

    def test_mild_venue_drop_is_applied_and_removed_item_labelled(self):
        base = [item("A館", f"展{i}", f"https://a/{i}", "2026年9月1日～2026年12月1日") for i in range(10)]
        self.venues = {"A館": base}
        self.run_once()
        self.venues["A館"] = base[:8]   # 10件 → 8件（80%）は正常な減少として扱う
        active, diff = self.run_once()
        self.assertEqual(len([i for i in active if i["venue"] == "A館"]), 8)
        self.assertEqual(diff["counts"]["消失"], 2)

    def test_ended_item_is_hidden_and_counted(self):
        old = item("A館", "展0", "https://a/0", "2026年7月8日 〜 8月30日")
        self.venues = {"A館": [old, item("A館", "展1", "https://a/1", "2026年9月1日～2026年12月1日")]}
        active, diff = self.run_once()
        self.assertEqual([i["title"] for i in active], ["展1"])
        self.assertEqual(diff["counts"]["終了"], 1)

    def test_state_json_round_trip_keeps_fingerprint(self):
        self.venues = {"A館": [item("A館", "展1", "https://a/1", "2026年9月1日～2026年12月1日")]}
        self.run_once()
        with open(self.state_path, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(len(saved["items"]), 1)
        _, diff = self.run_once()
        self.assertEqual(diff["counts"]["変更"], 0)



class StableIdTest(unittest.TestCase):
    """IDの安定性：URLの#の有無、保存済み値のID規則の変更で、同じ展覧会を別物と判定しない"""

    def test_fragment_does_not_change_id(self):
        a = item("五島美術館", "展覧会・イベント", "https://www.gotoh-museum.or.jp/", "")
        b = item("五島美術館", "展覧会・イベント", "https://www.gotoh-museum.or.jp/#", "")
        self.assertEqual(D.item_id(a), D.item_id(b))

    def test_stored_state_is_rekeyed_by_current_rule(self):
        old = item("五島美術館", "展覧会・イベント", "https://www.gotoh-museum.or.jp/#", "2026年9月1日～2026年12月1日")
        state = {"items": {"五島美術館|展覧会・イベント|https://www.gotoh-museum.or.jp/#": old}}
        fresh = [item("五島美術館", "展覧会・イベント", "https://www.gotoh-museum.or.jp/", "2026年9月1日～2026年12月1日")]
        _, labels = D.classify_and_merge(state, fresh, set(), TODAY)
        self.assertEqual(list(labels.values()), ["変更なし"])


class InitialRunSafetyTest(unittest.TestCase):
    """初回移行の保護：状態JSONがない初回の、取得失敗と既存の公開展示の扱い（main() を通して検証する）"""

    PER = "2026年9月1日～2026年12月1日"

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.page = os.path.join(self.dir, "docs", "culture-exhibition", "index.html")
        self.state = os.path.join(self.dir, "data", "culture_state.json")
        self.saved = (D.OUTPUT_DIR, D.OUTPUT_HTML, D.STATE_JSON, D.FETCHERS, D.fetch_ticket_info)
        D.OUTPUT_DIR = os.path.join(self.dir, "docs", "culture-exhibition")
        D.OUTPUT_HTML = self.page
        D.STATE_JSON = self.state
        D.fetch_ticket_info = lambda url: ("", "", "", False)
        self.venues = {}

    def tearDown(self):
        D.OUTPUT_DIR, D.OUTPUT_HTML, D.STATE_JSON, D.FETCHERS, D.fetch_ticket_info = self.saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def _fetcher(self, name):
        def fetch():
            result = self.venues[name]
            if result is None:
                D.log_skip(name, "取得エラー (テスト)")
                return []
            return [dict(i) for i in result]
        fetch.__name__ = f"fetch_{name}"
        return fetch

    def write_published(self, venue):
        os.makedirs(os.path.dirname(self.page), exist_ok=True)
        with open(self.page, "w", encoding="utf-8") as f:
            f.write(f'<html><a class="card" href="https://p.example/1" data-venue="{venue}">公開中の展示</a></html>')

    def run_main(self):
        D.FETCHERS = [self._fetcher(name) for name in self.venues]
        D.skip_log.clear()
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()) as out:
            try:
                D.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue()

    def test_1_initial_failure_with_published_venue_stops_without_change(self):
        self.write_published("松岡美術館")
        with open(self.page, "rb") as f:
            before = f.read()
        self.venues = {"松岡美術館": None, "A館": [item("A館", "展1", "https://a/1", self.PER)]}
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("停止します", out)
        with open(self.page, "rb") as f:
            self.assertEqual(f.read(), before)          # 公開HTML不変
        self.assertFalse(os.path.exists(self.state))    # 状態JSON未作成

    def test_1b_initial_failure_without_published_venue_proceeds(self):
        self.write_published("別の施設")
        self.venues = {"松岡美術館": None, "A館": [item("A館", "展1", "https://a/1", self.PER)]}
        code, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(self.state))

    def test_2_initial_all_success_creates_baseline(self):
        self.venues = {"A館": [item("A館", "展1", "https://a/1", self.PER)],
                       "B館": [item("B館", "展2", "https://b/1", self.PER)]}
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(self.page))
        self.assertTrue(os.path.exists(self.state))
        self.assertIn("初回", out)
        self.assertTrue(D.LAST_DIFF["baseline"])
        self.assertEqual(D.LAST_DIFF["counts"]["新着"], 0)

    def test_3_with_state_one_venue_failure_keeps_previous_and_updates_others(self):
        self.venues = {"A館": [item("A館", "展1", "https://a/1", self.PER)],
                       "B館": [item("B館", "展2", "https://b/1", self.PER)]}
        self.assertEqual(self.run_main()[0], 0)
        self.venues = {"A館": None, "B館": [item("B館", "展2", "https://b/1", "2026年9月1日～2026年12月31日")]}
        code, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("A館", D.LAST_DIFF["failed"])
        self.assertEqual(D.LAST_DIFF["counts"]["保持（取得失敗）"], 1)
        self.assertEqual(D.LAST_DIFF["counts"]["変更"], 1)      # B館の変更は反映
        with open(self.page, encoding="utf-8") as f:
            html_text = f.read()
        self.assertIn('data-venue="A館"', html_text)            # A館の展示は保持
        self.assertIn("2026年12月31日", html_text)              # B館の変更は公開

    def test_4_with_state_all_success_runs_diff(self):
        self.venues = {"A館": [item("A館", "展1", "https://a/1", self.PER)]}
        self.assertEqual(self.run_main()[0], 0)
        self.venues = {"A館": [item("A館", "展1", "https://a/1", self.PER),
                               item("A館", "展新", "https://a/2", self.PER)]}
        self.assertEqual(self.run_main()[0], 0)
        self.assertFalse(D.LAST_DIFF["baseline"])
        self.assertEqual(D.LAST_DIFF["counts"]["新着"], 1)
        self.assertEqual(D.LAST_DIFF["counts"]["変更なし"], 1)

    def test_blocker_function_rules(self):
        self.assertEqual(D.initial_run_blockers({"baseline": True, "failed": ["A"]}, ["A", "B"]), {"A"})
        self.assertEqual(D.initial_run_blockers({"baseline": True, "failed": ["A"]}, ["B"]), set())
        self.assertEqual(D.initial_run_blockers({"baseline": False, "failed": ["A"]}, ["A"]), set())
        self.assertEqual(D.initial_run_blockers({"baseline": True, "failed": []}, ["A"]), set())


if __name__ == "__main__":
    unittest.main()
