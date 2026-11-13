"""人物と作品の関係の問い合わせ（film_discovery）のテスト。架空データのみ（data_kind=テスト用架空データ）。ネットワーク不要。"""
import datetime
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import film_discovery as D  # noqa: E402
import film_watch as FW  # noqa: E402
import screenings as S  # noqa: E402

TEST = "テスト用架空データ"
NOW = datetime.datetime(2026, 10, 20, 9, 0, tzinfo=S.AW.JST)


class GraphTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="film_disc_")
        self.store = FW.Store(self.dir)
        self.dirA = FW.register_person(self.store, "架空監督A（テスト）", data_kind=TEST, now=NOW)
        self.wri = FW.register_person(self.store, "架空脚本B（テスト）", data_kind=TEST, now=NOW)
        self.act = FW.register_person(self.store, "架空俳優C（テスト）", data_kind=TEST, now=NOW)
        self.work1 = FW.register_work(self.store, "架空映画一（テスト）", data_kind=TEST,
                                      source_url="https://example.invalid/w1", now=NOW)
        self.work2 = FW.register_work(self.store, "架空映画二（テスト）", data_kind=TEST, now=NOW)  # 出典なし
        FW.link_credit(self.store, self.work1, self.dirA, "監督")
        FW.link_credit(self.store, self.work1, self.wri, "脚本")
        FW.link_credit(self.store, self.work2, self.wri, "脚本")
        FW.link_credit(self.store, self.work2, self.act, "出演")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_works_of_person_with_source(self):
        ws = D.works_of_person(self.store, self.dirA)
        self.assertEqual([w["title"] for w in ws], ["架空映画一（テスト）"])
        self.assertEqual(ws[0]["role"], "監督")
        self.assertEqual(ws[0]["source_url"], "https://example.invalid/w1")

    def test_shared_works_is_fact_with_roles(self):
        sh = D.shared_works(self.store, self.dirA, self.wri)
        self.assertEqual([s["title"] for s in sh], ["架空映画一（テスト）"])
        self.assertEqual(sh[0]["roles_a"], ["監督"])
        self.assertEqual(sh[0]["roles_b"], ["脚本"])
        self.assertIn("事実", sh[0]["basis"])

    def test_discover_is_inference_with_path(self):
        d = D.discover(self.store, self.dirA)
        # 本人（監督A）の作品一以外で、共同制作者（脚本B）が関わる作品二
        self.assertEqual([x["title"] for x in d], ["架空映画二（テスト）"])
        self.assertIn("推測", d[0]["basis"])
        self.assertEqual(d[0]["path"], ["架空脚本B（テスト）"])

    def test_discover_excludes_own_works(self):
        titles = [x["title"] for x in D.discover(self.store, self.wri)]
        self.assertNotIn("架空映画二（テスト）", titles)   # 本人の作品は推測候補に入れない

    def test_missing_source_is_visible_in_summary(self):
        s = D.summary(self.store)
        self.assertEqual((s["people"], s["works"], s["credits"]), (3, 2, 4))   # 人物3・作品2・クレジット4
        self.assertEqual(s["works_without_source"], 1)
        self.assertEqual(s["data_kind_counts"], {TEST: 5})   # 架空データは実データと分けて数える（人物3＋作品2）

    def test_read_only_does_not_write(self):
        before = {k: open(self.store.path(k), "rb").read() for k in ("people", "works")}
        D.works_of_person(self.store, self.dirA)
        D.shared_works(self.store, self.dirA, self.wri)
        D.discover(self.store, self.dirA)
        D.summary(self.store)
        for k, b in before.items():
            self.assertEqual(open(self.store.path(k), "rb").read(), b)


class ScreeningLinkTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="film_disc_scr_")
        self.store = FW.Store(os.path.join(self.dir, "film"))
        p = FW.register_person(self.store, "架空監督D（テスト）", data_kind=TEST, now=NOW)
        w = FW.register_work(self.store, "架空映画四（テスト）", data_kind=TEST, now=NOW)
        FW.link_credit(self.store, w, p, "監督")
        self.p = p
        self.scr = os.path.join(self.dir, "scr")
        rows = [
            {"title": "架空映画四（テスト）", "theater": "架空シネマX（テスト）", "screen_date": "2026-10-25",
             "start_time": "18:00", "evidence_url": "https://example.invalid/s1"},
            {"title": "架空映画四（テスト）", "theater": "架空シネマY（テスト）", "screen_date": "2026-10-22",
             "start_time": "10:00", "evidence_url": "https://example.invalid/s2"},
            {"title": "架空別作品（テスト）", "theater": "架空シネマX（テスト）", "screen_date": "2026-10-23",
             "start_time": "10:00", "evidence_url": "https://example.invalid/s3"},
        ]
        S.ingest(self.scr, rows, NOW)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_person_work_screenings_sorted_and_filtered(self):
        hits = D.screenings_for_person(self.store, self.p, self.scr)
        self.assertEqual([(h["screen_date"], h["theater"]) for h in hits],
                         [("2026-10-22", "架空シネマY（テスト）"), ("2026-10-25", "架空シネマX（テスト）")])

    def test_no_screenings_gives_empty(self):
        empty = os.path.join(self.dir, "none")
        self.assertEqual(D.screenings_for_person(self.store, self.p, empty), [])


class YearMatchTest(unittest.TestCase):
    """同名の作品を、公開年で区別する（作品に公開年がある場合）。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="film_year_")
        self.store = FW.Store(os.path.join(self.dir, "film"))
        self.p = FW.register_person(self.store, "架空監督E（テスト）", data_kind=TEST, now=NOW)
        self.w = FW.register_work(self.store, "架空同名作（テスト）", data_kind=TEST, now=NOW)
        FW.link_credit(self.store, self.w, self.p, "監督")
        works = self.store.load("works")
        works[self.w]["release_year"] = "1961"          # 作品台帳の公開年（テスト用の架空値）
        self.store.save("works", works)
        self.scr = os.path.join(self.dir, "scr")
        S.ingest(self.scr, [
            {"title": "架空同名作（テスト）", "theater": "架空シネマX（テスト）", "screen_date": "2026-10-25",
             "start_time": "18:00", "evidence_url": "https://example.invalid/y1", "release_year": "1961"},
            {"title": "架空同名作（テスト）", "theater": "架空シネマY（テスト）", "screen_date": "2026-10-26",
             "start_time": "18:00", "evidence_url": "https://example.invalid/y2", "release_year": "1990"},
            {"title": "架空同名作（テスト）", "theater": "架空シネマZ（テスト）", "screen_date": "2026-10-27",
             "start_time": "18:00", "evidence_url": "https://example.invalid/y3"},
        ], NOW)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_year_match_kept_mismatch_excluded_unknown_kept_labeled(self):
        hits = D.screenings_for_person(self.store, self.p, self.scr)
        by_theater = {h["theater"]: h["match"] for h in hits}
        self.assertEqual(by_theater["架空シネマX（テスト）"], "作品名＋公開年一致")
        self.assertNotIn("架空シネマY（テスト）", by_theater)          # 公開年が違う別作品として除外
        self.assertEqual(by_theater["架空シネマZ（テスト）"], "作品名のみ（公開年未確認）")

    def test_works_of_person_exposes_release_year(self):
        ws = D.works_of_person(self.store, self.p)
        self.assertEqual(ws[0]["release_year"], "1961")


if __name__ == "__main__":
    unittest.main()
