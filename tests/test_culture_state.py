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
import fetch_meta as FM  # noqa: E402

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

    def test_missing_once_is_kept_as_not_confirmed(self):
        merged, labels = D.classify_and_merge(self.state_of(self.a, self.b), [self.a], set(), TODAY)
        key = D.keyed([self.b])
        bk = list(key)[0]
        self.assertIn(bk, merged)
        self.assertEqual(labels[bk], "掲載確認できず")
        self.assertEqual(merged[bk]["missed"], 1)

    def test_failed_venue_keeps_previous_item(self):
        merged, labels = D.classify_and_merge(self.state_of(self.a, self.b), [self.a], {"B館"}, TODAY)
        self.assertIn(D.item_id(self.b), merged)
        self.assertEqual(labels[D.item_id(self.b)], "保持（取得失敗）")

    def test_same_url_different_titles_are_separate_ids(self):
        x = item("A館", "展X", "https://a/same", "2026年9月1日～2026年12月1日")
        y = item("A館", "展Y", "https://a/same", "2026年9月1日～2026年12月1日")
        self.assertEqual(len(D.keyed([x, y])), 2)


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
        self.venues["A館"] = base[:8]   # 10件 → 8件（80%）は正常な減少。ただし1回の欠落では消失にしない
        active, diff = self.run_once()
        self.assertEqual(len([i for i in active if i["venue"] == "A館"]), 10)
        self.assertEqual(diff["counts"]["掲載確認できず"], 2)
        self.assertEqual(diff["counts"]["消失"], 0)

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


class MainHarness(unittest.TestCase):
    """main() を通して検証するための共通部分（テストは含まない）"""

    PER = "2026年9月1日～2026年12月1日"

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.page = os.path.join(self.dir, "docs", "culture-exhibition", "index.html")
        self.state = os.path.join(self.dir, "data", "culture_state.json")
        self.saved = (D.OUTPUT_DIR, D.OUTPUT_HTML, D.STATE_JSON, D.FETCHERS, D.fetch_ticket_info, FM.META_PATH)
        D.OUTPUT_DIR = os.path.join(self.dir, "docs", "culture-exhibition")
        D.OUTPUT_HTML = self.page
        D.STATE_JSON = self.state
        FM.META_PATH = os.path.join(self.dir, "data", "culture_fetch_meta.json")   # 実データの取得時刻を書かない
        D.fetch_ticket_info = lambda url: ("", "", "", False)
        self.venues = {}

    def tearDown(self):
        D.OUTPUT_DIR, D.OUTPUT_HTML, D.STATE_JSON, D.FETCHERS, D.fetch_ticket_info, FM.META_PATH = self.saved
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

class InitialRunSafetyTest(MainHarness):
    """初回移行の保護：状態JSONがない初回の、取得失敗と既存の公開展示の扱い"""

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


class StateChurnTest(unittest.TestCase):
    """状態JSONの不要な差分の抑制：内容が同じなら書き込まない、キー順序は固定、確認日は差分の原因にしない"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "data", "culture_state.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def state(self, title="展1", confirmed="2026-10-14", updated="2026-10-15T01:00:00+00:00"):
        judgment = {"無料表示": False, "割引表示": False, "手帳本人": "未確認", "確認日": confirmed}
        it = {"venue": "A館", "title": title, "link": "https://a/1", "period": "2026年9月1日～2026年12月1日",
              "status": "開催中", "judgment": judgment}
        return {"version": 1, "updated_at": updated, "venue_counts": {"A館": 1},
                "items": {D.item_id(it): it}}

    def read_bytes(self):
        with open(self.path, "rb") as f:
            return f.read()

    def test_first_write_then_identical_content_is_not_written(self):
        self.assertTrue(D.save_state(self.state(), self.path))
        before = self.read_bytes()
        self.assertFalse(D.save_state(self.state(updated="2026-10-16T01:00:00+00:00"), self.path))
        self.assertEqual(self.read_bytes(), before)

    def test_confirmation_date_only_change_is_not_written(self):
        D.save_state(self.state(confirmed="2026-10-14"), self.path)
        before = self.read_bytes()
        self.assertFalse(D.save_state(self.state(confirmed="2026-10-15"), self.path))
        self.assertEqual(self.read_bytes(), before)

    def test_real_content_change_is_written(self):
        D.save_state(self.state(title="展1"), self.path)
        self.assertTrue(D.save_state(self.state(title="展1改"), self.path))

    def test_key_order_does_not_change_bytes(self):
        a = self.state()
        b = {"venue_counts": {"A館": 1}, "items": a["items"], "updated_at": a["updated_at"], "version": 1}
        D.save_state(a, self.path)
        first = self.read_bytes()
        os.remove(self.path)
        D.save_state(b, self.path)
        self.assertEqual(self.read_bytes(), first)


class MainChurnTest(MainHarness):
    """main() を通して、同一入力の連続実行で状態JSONが変わらないこと"""

    def test_5_same_input_twice_does_not_rewrite_state(self):
        self.venues = {"A館": [item("A館", "展1", "https://a/1", self.PER)]}
        self.assertEqual(self.run_main()[0], 0)
        with open(self.state, "rb") as f:
            first = f.read()
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("内容に変更なし", out)
        with open(self.state, "rb") as f:
            self.assertEqual(f.read(), first)


class VolatilityHelpers:
    """掲載変動のテスト共通の補助（テストは含まない）"""

    PER = "2026年9月1日～2026年12月1日"

    def setUp(self):
        self.saved = (D.FETCHERS, D.fetch_ticket_info, set(D.FULL_LISTING_VENUES))
        D.fetch_ticket_info = lambda url: ("", "", "", False)
        self.today = TODAY

    def tearDown(self):
        D.FETCHERS, D.fetch_ticket_info = self.saved[0], self.saved[1]
        D.FULL_LISTING_VENUES.clear()
        D.FULL_LISTING_VENUES.update(self.saved[2])

    def run_round(self, state, listings, today=None):
        """listings: 施設名 -> 取得結果（リスト）または None（取得失敗）。状態は main と同じ形で作り直す。"""
        def make(name, result):
            def fetch():
                if result is None:
                    D.log_skip(name, "取得エラー (テスト)")
                    return []
                return [dict(i) for i in result]
            fetch.__name__ = f"fetch_{name}"
            return fetch
        D.FETCHERS = [make(n, r) for n, r in listings.items()]
        D.skip_log.clear()
        active, _, _ = D.build_dataset(today=today or self.today, state=state)
        diff = D.LAST_DIFF
        new_state = {"version": 1, "venue_counts": diff["venue_counts"], "items": diff["merged"]}
        return active, diff, new_state

    def ti(self, title, link="https://a/x", venue="A館"):
        return item(venue, title, link, self.PER)




class ListingVolatilityTest(VolatilityHelpers, unittest.TestCase):
    """掲載変動（入れ替わり・1回の欠落・取得失敗・復帰・会期終了）と、新着・消失の誤判定の抑制"""
    def test_1_single_miss_is_not_removal(self):
        a, b = self.ti("展1", "https://a/1"), self.ti("展2", "https://a/2")
        _, _, st = self.run_round(None, {"A館": [a, b]})
        active, diff, _ = self.run_round(st, {"A館": [a]})
        self.assertEqual(len(active), 2)
        self.assertEqual(diff["counts"]["消失"], 0)
        self.assertEqual(diff["counts"]["掲載確認できず"], 1)

    def test_2_two_consecutive_misses_are_not_removal(self):
        a, b = self.ti("展1", "https://a/1"), self.ti("展2", "https://a/2")
        _, _, st = self.run_round(None, {"A館": [a, b]})
        _, _, st = self.run_round(st, {"A館": [a]})
        active, diff, _ = self.run_round(st, {"A館": [a]})
        self.assertEqual(len(active), 2)
        self.assertEqual(diff["counts"]["消失"], 0)

    def test_3_three_misses_on_limited_listing_are_not_confirmed_removal(self):
        a, b = self.ti("展1", "https://a/1"), self.ti("展2", "https://a/2")
        _, _, st = self.run_round(None, {"A館": [a, b]})
        for _ in range(3):
            active, diff, st = self.run_round(st, {"A館": [a]})
        self.assertEqual(len(active), 2)
        self.assertEqual(diff["counts"]["消失"], 0)
        self.assertEqual(diff["counts"]["掲載確認できず"], 1)

    def test_3b_three_misses_on_full_listing_venue_is_removal(self):
        D.FULL_LISTING_VENUES.add("A館")
        a, b = self.ti("展1", "https://a/1"), self.ti("展2", "https://a/2")
        _, _, st = self.run_round(None, {"A館": [a, b]})
        for _ in range(3):
            active, diff, st = self.run_round(st, {"A館": [a]})
        self.assertEqual([i["title"] for i in active], ["展1"])
        self.assertEqual(diff["counts"]["消失"], 1)

    def test_4_failure_keeps_data_and_does_not_count_misses(self):
        a, b = self.ti("展1", "https://a/1"), self.ti("展2", "https://a/2")
        _, _, st = self.run_round(None, {"A館": [a, b]})
        for _ in range(4):
            active, diff, st = self.run_round(st, {"A館": None})
        self.assertEqual(len(active), 2)
        self.assertEqual(diff["counts"]["保持（取得失敗）"], 2)
        self.assertTrue(all(int(v.get("missed", 0)) == 0 for v in st["items"].values()))

    def test_5_reappearance_is_recovery_not_new(self):
        a, b = self.ti("展1", "https://a/1"), self.ti("展2", "https://a/2")
        _, _, st = self.run_round(None, {"A館": [a, b]})
        _, _, st = self.run_round(st, {"A館": [a]})
        _, diff, _ = self.run_round(st, {"A館": [a, b]})
        self.assertEqual(diff["counts"]["復帰"], 1)
        self.assertEqual(diff["counts"]["新着"], 0)

    def test_6_official_end_is_separate_from_listing_absence(self):
        ended = dict(self.ti("終了展", "https://a/9"), period="2026年7月8日 〜 8月30日")
        a = self.ti("展1", "https://a/1")
        _, _, st = self.run_round(None, {"A館": [a, ended]})
        active, diff, _ = self.run_round(st, {"A館": [a]})   # 掲載なし（会期終了の日付あり）
        self.assertEqual(diff["counts"]["終了"], 1)
        self.assertEqual([i["title"] for i in active], ["展1"])
        self.assertEqual(diff["counts"]["掲載確認できず"], 0)

    def test_7_tnm_style_rotation_causes_no_false_new_or_removal(self):
        pool = [self.ti(f"注目{i}", f"https://tnm/{i}", venue="東京国立博物館") for i in range(5)]
        pool[0] = self.ti("Living with Horses", "https://tnm/0", venue="東京国立博物館")  # 英語表記の回
        rotations = [[0, 1, 2], [1, 2, 3], [0, 2, 3], [2, 3, 4], [0, 1, 2], [1, 3, 4], [0, 2, 4], [1, 2, 3], [0, 3, 4], [1, 2, 4]]
        # 初回（基準）で全件を一度記録してから、掲載の入れ替わりだけを流す
        _, _, st = self.run_round(None, {"東京国立博物館": pool})
        news, removals = 0, 0
        for idx in rotations:
            listing = {"東京国立博物館": [pool[i] for i in idx]}
            _, diff, st = self.run_round(st, listing)
            news += diff["counts"]["新着"]
            removals += diff["counts"]["消失"]
        self.assertEqual(news, 0)
        self.assertEqual(removals, 0)
        self.assertEqual(len(st["items"]), 5)

    def test_8_identical_input_twice_does_not_change_state(self):
        a = self.ti("展1", "https://a/1")
        _, _, st = self.run_round(None, {"A館": [a]})
        _, _, st2 = self.run_round(st, {"A館": [a]})
        self.assertEqual(D.state_content_key(st), D.state_content_key(st2))
        self.assertFalse(D.state_content_key(st) != D.state_content_key(st2))


class IdStabilityTest(VolatilityHelpers, unittest.TestCase):
    """IDの安定性：名称変更、同一URLの複数展覧会、旧形式の状態JSON"""

    def test_rename_of_single_url_item_is_changed_not_new(self):
        a = self.ti("展1", "https://a/1")
        _, _, st = self.run_round(None, {"A館": [a]})
        renamed = self.ti("展1（改題）", "https://a/1")
        _, diff, _ = self.run_round(st, {"A館": [renamed]})
        self.assertEqual(diff["counts"]["新着"], 0)
        self.assertEqual(diff["counts"]["消失"], 0)
        self.assertEqual(diff["counts"]["変更なし"], 1)

    def test_same_url_two_exhibitions_are_both_kept_when_unchanged(self):
        x = self.ti("展X", "https://a/same")
        y = self.ti("展Y", "https://a/same")
        _, _, st = self.run_round(None, {"A館": [x, y]})
        active, diff, _ = self.run_round(st, {"A館": [x, y]})
        self.assertEqual(len(active), 2)
        self.assertEqual(diff["counts"]["変更なし"], 2)

    def test_rename_when_url_is_shared_is_known_limitation(self):
        # 同一URLを複数の展覧会が共有する場合、名称が変わるとIDも変わる（既知の制約）。
        # 旧IDは「掲載確認できず」として残り、消失にはならない（誤った消失は起きない）。
        x = self.ti("展X", "https://a/same")
        y = self.ti("展Y", "https://a/same")
        _, _, st = self.run_round(None, {"A館": [x, y]})
        renamed = self.ti("展X改", "https://a/same")
        active, diff, _ = self.run_round(st, {"A館": [renamed, y]})
        self.assertEqual(diff["counts"]["新着"], 1)
        self.assertEqual(diff["counts"]["消失"], 0)
        self.assertEqual(len(active), 3)

    def test_old_format_state_is_rekeyed_without_new(self):
        a = self.ti("展1", "https://a/1")
        old_key = "A館|展1|https://a/1"
        st = {"version": 1, "venue_counts": {"A館": 1},
              "items": {old_key: dict(a, judgment={})}}
        _, diff, _ = self.run_round(st, {"A館": [a]})
        self.assertEqual(diff["counts"]["新着"], 0)
        self.assertEqual(diff["counts"]["消失"], 0)


class RenderTest(unittest.TestCase):
    """ホームページ表示：差分バッジ、初回の扱い、取得失敗、会期終了日、リンクと確度の維持（メモリ内で描画する）"""

    def card(self, title="展1", label=None, period="2026年9月1日～2026年12月1日", state="確認済み", **extra):
        judgment = {"無料表示": False, "割引表示": False, "手帳本人": state, "確認日": "2026-10-15"}
        it = {"venue": "A館", "title": title, "link": "https://a.example/1", "period": period, "status": "開催中",
              "category": "art", "category_name": "美術展", "access": "", "general_price": "1,000円",
              "discount": "未確認", "judgment": judgment, "discount_source_url": extra.pop("src", "")}
        it.update(extra)
        return it, label

    def render(self, pairs, diff=None):
        items = [p[0] for p in pairs]
        labels = {id(p[0]): p[1] for p in pairs if p[1]}
        return D.render_html(items, labels, diff or {})

    def test_1_new_badge_only_for_new_label(self):
        new_it, _ = self.card("新着展", "新着")
        same_it, _ = self.card("既存展", "変更なし")
        page = self.render([(new_it, "新着"), (same_it, "変更なし")], {"baseline": False, "counts": {}})
        self.assertEqual(page.count("diff-badge"), 1)
        self.assertIn("新着</span>", page)

    def test_2_changed_badge_matches_changed_label(self):
        changed, _ = self.card("変更展", "変更")
        page = self.render([(changed, "変更")], {"baseline": False, "counts": {}})
        self.assertIn("変更あり</span>", page)
        unchanged, _ = self.card("同じ展", "変更なし")
        page2 = self.render([(unchanged, "変更なし")], {"baseline": False, "counts": {}})
        self.assertNotIn("変更あり</span>", page2)

    def test_3_baseline_shows_no_new_badges(self):
        it, _ = self.card("初回展", None)
        page = self.render([(it, None)], {"baseline": True, "counts": {}})
        self.assertNotIn("diff-badge", page)
        self.assertIn("初回の基準データを記録しました", page)

    def test_4_listing_absence_is_not_shown_as_end(self):
        it, _ = self.card("限定掲載展", "掲載確認できず（限定掲載・3回以上）", period="取得困難（要アクセス）")
        page = self.render([(it, "掲載確認できず（限定掲載・3回以上）")], {"baseline": False, "counts": {"掲載確認できず": 1}})
        self.assertIn("今回の掲載未確認</span>", page)
        self.assertIn("会期終了ではありません", page)
        self.assertNotIn("会期終了日", page)            # 期間が不明なので終了日は表示しない

    def test_5_failure_is_distinguished_from_normal(self):
        it, _ = self.card("保持展", "保持（取得失敗）")
        diff = {"baseline": False, "counts": {}, "failed": ["松岡美術館"]}
        page = self.render([(it, "保持（取得失敗）")], diff)
        self.assertIn("取得失敗・前回データ</span>", page)
        self.assertIn("今回取得できなかった施設（前回データがある場合は保持して表示）：松岡美術館", page)

    def test_6_end_date_shown_only_when_known(self):
        known, _ = self.card("期限展", None, period="2026年7月8日 〜 8月30日")
        unknown, _ = self.card("不明展", None, period="取得困難（要アクセス）")
        page = self.render([(known, None), (unknown, None)], {"baseline": False, "counts": {}})
        self.assertIn("会期終了日 2026-08-30", page)
        self.assertEqual(page.count("会期終了日"), 1)
        self.assertIsNotNone(D.parse_end_date("2026年7月8日 〜 8月30日"))
        self.assertLess(D.parse_end_date("2026年7月8日 〜 8月30日"), TODAY)   # 終了済み（非表示は build で判定）

    def test_7_official_link_and_evidence_kept(self):
        it, _ = self.card("根拠展", None, state="確認済み", src="https://a.example/evidence")
        page = self.render([(it, None)], {"baseline": False, "counts": {}})
        self.assertIn('href="https://a.example/1"', page)
        self.assertIn("href='https://a.example/evidence'", page)

    def test_8_confidence_state_kept(self):
        for state in ["確認済み", "推定", "未確認"]:
            it, _ = self.card("状態展", None, state=state)
            page = self.render([(it, None)], {"baseline": False, "counts": {}})
            self.assertIn(f'data-state="{state}"', page)


if __name__ == "__main__":
    unittest.main()
