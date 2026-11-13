"""東京芸術劇場の公演情報の変換・差分・失敗時の扱い（ネットワーク不要。取得関数は差し替える）。"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import event_records as ER  # noqa: E402
import geigeki_events as G  # noqa: E402
import auto_fetch_all as H  # noqa: E402

LIST = '<a href="https://www.geigeki.jp/performance/">一覧</a><a href="https://www.geigeki.jp/performance/concert367/">a</a>' \
       '<a href="https://www.geigeki.jp/performance/event348/">b</a><a href="https://www.geigeki.jp/performance/concert367/">a2</a>'
DETAIL_A = ('<meta property="og:title" content="芸劇リサイタル・シリーズ［2026年度］"><h1>重要なお知らせ</h1>'
            '<div>日程 2026年4月8日(水) 2027年1月23日(土) 2027年2月19日(金) 会場 コンサートホール 主催 東京芸術劇場 料金 4,000円</div>')
DETAIL_B = ('<meta property="og:title" content="「のはらカレッジ 2026」受講生募集"><div>日程 2026年8月29日(土)〜2027年2月28日(日) '
            '会場 東京芸術劇場 応募期間 5月1日(金）～7月25日（土）まで 受講料 12,000円</div>')
DETAIL_B_YEAR = DETAIL_B.replace("5月1日(金）～7月25日（土）まで", "2026年5月1日(金）～2026年7月25日（土）まで")
DETAIL_NODATE = '<meta property="og:title" content="日程未掲載の公演"><div>会場 東京芸術劇場 主催 東京芸術劇場</div>'
TS = "2026-10-15T00:00:00+00:00"
TS2 = "2026-10-16T00:00:00+00:00"


def fetcher(pages, fail=()):
    def fetch(url):
        if url in fail:
            return False, "boom"
        if url in pages:
            return True, pages[url]
        return False, "missing"
    return fetch


class ParseTest(unittest.TestCase):
    def test_listing_links_unique_and_exclude_listing(self):
        urls = G.parse_listing_links(LIST)
        self.assertEqual(urls, ["https://www.geigeki.jp/performance/concert367/", "https://www.geigeki.jp/performance/event348/"])

    def test_detail_dates_and_title(self):
        d = G.parse_detail(DETAIL_A, "https://www.geigeki.jp/performance/concert367/")
        self.assertEqual(d["title"], "芸劇リサイタル・シリーズ［2026年度］")
        self.assertEqual(d["event_start"], datetime.date(2026, 4, 8))
        self.assertEqual(d["event_end"], datetime.date(2027, 2, 19))
        self.assertEqual(d["venue"], "コンサートホール")

    def test_deadline_without_year_is_not_guessed(self):
        d = G.parse_detail(DETAIL_B, "https://x/event348/")
        self.assertIsNone(d["application_deadline"])
        rec = G.to_record(d, "通常", TS)
        self.assertEqual(rec["dates"]["application_deadline"]["precision"], "unknown")
        self.assertEqual(ER.validate_record(rec), [])

    def test_deadline_with_year_is_day_precision(self):
        d = G.parse_detail(DETAIL_B_YEAR, "https://x/event348/")
        self.assertEqual(d["application_deadline"], datetime.date(2026, 7, 25))
        rec = G.to_record(d, "通常", TS)
        self.assertEqual(rec["dates"]["application_deadline"]["precision"], "day")

    def test_missing_schedule_stays_unknown(self):
        d = G.parse_detail(DETAIL_NODATE, "https://x/nodate/")
        rec = G.to_record(d, "通常", TS)
        self.assertEqual(rec["dates"]["event_start"]["precision"], "unknown")
        self.assertEqual(rec["confidence"], "未確認")
        self.assertEqual(ER.validate_record(rec), [])

    def test_sale_and_announce_dates_are_never_invented(self):
        rec = G.to_record(G.parse_detail(DETAIL_A, "https://x/concert367/"), "通常", TS)
        for key in ("ticket_sale_start", "general_sale_start", "announced_at"):
            self.assertEqual(rec["dates"][key]["precision"], "unknown")
            self.assertIsNone(rec["dates"][key]["value"])
        self.assertEqual(rec["source_url"], "https://x/concert367/")


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "music", "events.json")
        self.pages = {G.LIST_URL: LIST, "https://www.geigeki.jp/performance/concert367/": DETAIL_A,
                      "https://www.geigeki.jp/performance/event348/": DETAIL_B}

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_collect(self, pages=None, fail=(), ts=TS):
        return G.collect(fetch=fetcher(pages or self.pages, fail), sleep=lambda s: None, checked_at=ts, path=self.path)

    def load(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)["records"]

    def test_baseline_has_no_new_labels(self):
        self.assertTrue(self.run_collect()["ok"])
        self.assertTrue(all(r["state"] == "通常" for r in self.load()))

    def test_new_and_changed_after_baseline(self):
        self.run_collect()
        pages = dict(self.pages)
        pages["https://www.geigeki.jp/performance/event348/"] = DETAIL_B_YEAR     # 応募締切が追加
        pages[LIST_EXTRA] = DETAIL_NODATE
        pages[G.LIST_URL] = LIST + f'<a href="{LIST_EXTRA}">c</a>'
        self.run_collect(pages, ts=TS2)
        states = {r["id"]: r["state"] for r in self.load()}
        self.assertEqual(states["music|geigeki|event348"], "変更")
        self.assertEqual(states["music|geigeki|nodate"], "新着")
        self.assertEqual(states["music|geigeki|concert367"], "通常")

    def test_disappeared_is_kept_as_not_confirmed(self):
        self.run_collect()
        pages = dict(self.pages)
        pages[G.LIST_URL] = '<a href="https://www.geigeki.jp/performance/concert367/">a</a>'
        self.run_collect(pages, ts=TS2)
        states = {r["id"]: r["state"] for r in self.load()}
        self.assertEqual(states["music|geigeki|event348"], "掲載未確認")
        self.assertEqual(len(states), 2)

    def test_listing_failure_writes_nothing(self):
        self.run_collect()
        with open(self.path, "rb") as f:
            before = f.read()
        result = self.run_collect(fail=(G.LIST_URL,), ts=TS2)
        self.assertFalse(result["ok"])
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), before)

    def test_detail_failure_keeps_previous_record_and_is_reported(self):
        self.run_collect()
        result = self.run_collect(fail=("https://www.geigeki.jp/performance/event348/",), ts=TS2)
        self.assertIn("https://www.geigeki.jp/performance/event348/", result["failed"])
        ids = {r["id"] for r in self.load()}
        self.assertIn("music|geigeki|event348", ids)
        self.assertEqual(json.load(open(self.path, encoding="utf-8"))["failed_venues"],
                         ["https://www.geigeki.jp/performance/event348/"])

    def test_same_input_twice_writes_nothing(self):
        self.run_collect()
        with open(self.path, "rb") as f:
            first = f.read()
        result = self.run_collect(ts=TS2)
        self.assertFalse(result["changed"])
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), first)

    def test_ids_are_unique(self):
        self.run_collect()
        ids = [r["id"] for r in self.load()]
        self.assertEqual(len(ids), len(set(ids)))


LIST_EXTRA = "https://www.geigeki.jp/performance/nodate/"


class ScheduleTest(unittest.TestCase):
    def test_schedule_sorted_and_unknown_excluded(self):
        later = G.to_record(G.parse_detail(DETAIL_A, "https://x/concert367/"), "通常", TS)
        early = G.to_record(G.parse_detail(DETAIL_B_YEAR, "https://x/event348/"), "通常", TS)
        nodate = G.to_record(G.parse_detail(DETAIL_NODATE, "https://x/nodate/"), "通常", TS)
        rows = ER.schedule_rows([later, nodate, early])
        self.assertEqual([r["id"] for r in rows], ["music|geigeki|concert367", "music|geigeki|event348"])   # 開始日の早い順（2026-04-08 → 2026-08-29）

    def test_schedule_shows_deadline_only_when_known(self):
        early = G.to_record(G.parse_detail(DETAIL_B_YEAR, "https://x/event348/"), "通常", TS)
        html_text = ER.render_schedule_html([early])
        self.assertIn("応募締切：2026-07-25", html_text)
        later = G.to_record(G.parse_detail(DETAIL_B, "https://x/event348/"), "通常", TS)
        self.assertIn("応募締切：未確認", ER.render_schedule_html([later]))


class HomeIntegrationTest(unittest.TestCase):
    def test_home_shows_music_records_with_official_link(self):
        rec = G.to_record(G.parse_detail(DETAIL_A, "https://www.geigeki.jp/performance/concert367/"), "新着", TS)
        html_text = H.news_section_html([rec])
        self.assertIn("href='https://www.geigeki.jp/performance/concert367/'", html_text)
        self.assertIn("公演・イベント", html_text)
        self.assertIn("新着</span>", html_text)
        self.assertIn("開催日一覧（美術展・公演・イベント）", html_text)
        self.assertNotIn("None", html_text)


class ProtectionTest(unittest.TestCase):
    """取得失敗・構造変化・件数急減で、既存の有効な公演情報を消さないこと"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "music", "events.json")
        self.pages = {G.LIST_URL: LIST, "https://www.geigeki.jp/performance/concert367/": DETAIL_A,
                      "https://www.geigeki.jp/performance/event348/": DETAIL_B_YEAR}
        G.collect(fetch=fetcher(self.pages), sleep=lambda s: None, checked_at=TS, path=self.path)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def snapshot(self):
        with open(self.path, "rb") as f:
            return f.read()

    def run_fetch(self, fetch):
        return G.collect(fetch=fetch, sleep=lambda s: None, checked_at=TS2, path=self.path)

    def test_listing_http_error_writes_nothing(self):
        before = self.snapshot()
        result = self.run_fetch(lambda u: (False, "HTTP 503") if u == G.LIST_URL else (True, ""))
        self.assertFalse(result["ok"])
        self.assertIn("HTTP 503", result["reason"])
        self.assertEqual(self.snapshot(), before)

    def test_listing_without_links_is_structure_change_and_writes_nothing(self):
        before = self.snapshot()
        result = self.run_fetch(fetcher({G.LIST_URL: "<html><body>メンテナンス中</body></html>"}))
        self.assertFalse(result["ok"])
        self.assertEqual(self.snapshot(), before)

    def test_sudden_listing_drop_writes_nothing(self):
        many_links = "".join(f'<a href="https://www.geigeki.jp/performance/p{i}/">p</a>' for i in range(6))
        many = {f"https://www.geigeki.jp/performance/p{i}/": DETAIL_A for i in range(6)}
        self.run_fetch(fetcher(dict(self.pages, **many, **{G.LIST_URL: many_links})))   # 前回 8 件（2 + 6）
        before = self.snapshot()
        small = {G.LIST_URL: '<a href="https://www.geigeki.jp/performance/concert367/">a</a>'}
        result = self.run_fetch(fetcher(dict(self.pages, **small)))
        self.assertFalse(result["ok"])
        self.assertIn("急減", result["reason"])
        self.assertEqual(self.snapshot(), before)

    def test_detail_http_error_keeps_previous_record(self):
        url = "https://www.geigeki.jp/performance/event348/"
        result = self.run_fetch(lambda u: (False, "HTTP 500") if u == url else fetcher(self.pages)(u))
        self.assertTrue(result["ok"])
        self.assertIn(url, result["failed"])
        self.assertIn("HTTP 500", result["failed_reasons"][url])
        with open(self.path, encoding="utf-8") as f:
            recs = {r["id"]: r for r in json.load(f)["records"]}
        self.assertEqual(recs["music|geigeki|event348"]["dates"]["event_start"]["value"], "2026-08-29")

    def test_unparseable_detail_keeps_previous_record(self):
        url = "https://www.geigeki.jp/performance/event348/"
        pages = dict(self.pages, **{url: "<html><body>別の構造</body></html>"})
        result = self.run_fetch(fetcher(pages))
        self.assertIn(url, result["failed"])
        self.assertIn("解析不能", result["failed_reasons"][url])
        with open(self.path, encoding="utf-8") as f:
            recs = {r["id"]: r for r in json.load(f)["records"]}
        self.assertEqual(recs["music|geigeki|event348"]["title"], "「のはらカレッジ 2026」受講生募集")

    def test_lost_date_keeps_previous_record(self):
        url = "https://www.geigeki.jp/performance/concert367/"
        no_date = '<meta property="og:title" content="芸劇リサイタル・シリーズ［2026年度］"><div>会場 コンサートホール</div>'
        result = self.run_fetch(fetcher(dict(self.pages, **{url: no_date})))
        self.assertIn("日付が消えた", result["failed_reasons"][url])
        with open(self.path, encoding="utf-8") as f:
            recs = {r["id"]: r for r in json.load(f)["records"]}
        self.assertEqual(recs["music|geigeki|concert367"]["dates"]["event_start"]["value"], "2026-04-08")

    def test_stored_records_have_no_copied_body_text(self):
        with open(self.path, encoding="utf-8") as f:
            for rec in json.load(f)["records"]:
                self.assertEqual(rec["genre_extra"], {})
                self.assertNotIn("organizer", json.dumps(rec, ensure_ascii=False))

    def test_duplicate_listing_links_are_one_record(self):
        dup = LIST + '<a href="https://www.geigeki.jp/performance/concert367/">again</a>'
        G.collect(fetch=fetcher(dict(self.pages, **{G.LIST_URL: dup})), sleep=lambda s: None, checked_at=TS2, path=self.path)
        with open(self.path, encoding="utf-8") as f:
            ids = [r["id"] for r in json.load(f)["records"]]
        self.assertEqual(len(ids), len(set(ids)))



if __name__ == "__main__":
    unittest.main()
