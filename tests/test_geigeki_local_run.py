"""geigeki_local_run：出力先の保護、ローカル実行、失敗時に書き換えないこと（ネットワーク不要。取得関数は差し替える）。"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))  # -I（隔離モード）でも同フォルダのテストを読めるように
import geigeki_local_run as L  # noqa: E402
import geigeki_events as G  # noqa: E402
from test_geigeki_events import LIST, DETAIL_A, DETAIL_B_YEAR, fetcher  # noqa: E402


class GuardTest(unittest.TestCase):
    def test_docs_path_refused(self):
        for p in [os.path.join(L.HERE, "docs", "music", "events.json"), os.path.join("docs", "music", "events.json")]:
            with self.assertRaises(ValueError):
                L.guard_output_path(p)

    def test_docs_prefix_sibling_is_allowed(self):
        # docs_backup のような別名フォルダは公開先ではない
        p = os.path.join(L.HERE, "docs_backup", "events.json")
        self.assertEqual(L.guard_output_path(p), os.path.abspath(p))

    def test_scratch_path_allowed(self):
        p = os.path.join(tempfile.gettempdir(), "x", "events.json")
        self.assertEqual(L.guard_output_path(p), os.path.abspath(p))


class RunTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="geigeki_run_")
        self.out = os.path.join(self.dir, "events.json")
        self.pages = {G.LIST_URL: LIST,
                      "https://www.geigeki.jp/performance/concert367/": DETAIL_A,
                      "https://www.geigeki.jp/performance/event348/": DETAIL_B_YEAR}
        self.sleep_calls = []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_first_run_writes_records_and_summary(self):
        s = L.run(self.out, fetch=fetcher(self.pages), sleep=self.sleep_calls.append, now=None)
        self.assertTrue(s["ok"])
        self.assertEqual(s["listed"], 2)
        self.assertEqual(s["records"], 2)
        self.assertEqual(s["failed"], [])
        self.assertTrue(os.path.exists(self.out))
        summ = json.load(open(os.path.join(self.dir, "run_summary.json"), encoding="utf-8"))
        self.assertEqual(summ["output_path"], os.path.abspath(self.out))
        self.assertIn("started_at_utc", summ)
        self.assertEqual(len(self.sleep_calls), 1)   # 公演ページの間隔（2件目の前に1回）

    def test_listing_failure_does_not_write(self):
        pages = {G.LIST_URL: None}
        s = L.run(self.out, fetch=fetcher({}, fail=(G.LIST_URL,)), sleep=lambda _: None)
        self.assertFalse(s["ok"])
        self.assertFalse(os.path.exists(self.out))

    def test_detail_failure_keeps_previous_record(self):
        L.run(self.out, fetch=fetcher(self.pages), sleep=lambda _: None)
        before = {r["id"]: r for r in json.load(open(self.out, encoding="utf-8"))["records"]}
        s = L.run(self.out, fetch=fetcher(self.pages, fail=("https://www.geigeki.jp/performance/event348/",)), sleep=lambda _: None)
        self.assertTrue(s["ok"])
        self.assertIn("https://www.geigeki.jp/performance/event348/", s["failed"])
        after = {r["id"]: r for r in json.load(open(self.out, encoding="utf-8"))["records"]}
        self.assertEqual(set(before), set(after))   # 失敗した公演も前回の記録を保持して消えない

    def test_docs_output_refused_before_any_fetch(self):
        called = []
        def spy(url):
            called.append(url)
            return False, "should not be called"
        with self.assertRaises(ValueError):
            L.run(os.path.join(L.HERE, "docs", "music", "events.json"), fetch=spy, sleep=lambda _: None)
        self.assertEqual(called, [])   # 拒否は取得の前に行う


if __name__ == "__main__":
    unittest.main()
