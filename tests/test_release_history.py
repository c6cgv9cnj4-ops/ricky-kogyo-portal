"""発売開始が近い予定の表示（人物ページ）と、公開日の変更履歴（台帳→DB→表示）の検証。
架空データ専用。ネットワーク不要。書き込みは一時フォルダに限る。"""
import datetime
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import film_watch as FW  # noqa: E402
import portal_db as D  # noqa: E402
import portal_preview as P  # noqa: E402
import portal_search as Q  # noqa: E402

TEST = "テスト用架空データ"
TODAY = datetime.date(2026, 10, 21)
SRC = "https://example.invalid/src"
CHK = "2026-10-21"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="release_hist_")
        self.film = os.path.join(self.tmp, "film")
        self.db = os.path.join(self.tmp, "db", "portal.db")
        self.exh = os.path.join(self.tmp, "exh.html")
        with open(self.exh, "w", encoding="utf-8") as f:
            f.write("<html><body></body></html>")
        self.scr = os.path.join(self.tmp, "no_scr")
        self.store = FW.Store(self.film)
        self.pid = FW.register_person(self.store, "架空 監督", [], TEST, SRC, disambiguator="1975年生", checked_on=CHK)
        self.wid = FW.register_work(self.store, "架空作品M", TEST, SRC)
        FW.link_credit(self.store, self.wid, self.pid, "監督", SRC, CHK)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build_profile(self):
        D.build(self.db, self.film, self.exh, self.scr, today=TODAY, data_kind=TEST)
        return Q.person_profile(self.db, self.pid, TODAY)


class NearSalesTest(Base):
    def add(self, kind, status, starts_at, url):
        FW.add_sale(self.store, self.wid, kind, status, url, SRC, CHK, starts_at=starts_at)

    def test_near_window_includes_only_registered_upcoming_sales(self):
        self.add("先行", "発売前", "2026-10-26T10:00:00+09:00", "https://example.invalid/pre")   # 5日後 → 近い
        self.add("一般", "発売前", "2026-11-30T10:00:00+09:00", "https://example.invalid/gen")   # 40日後 → 近くない
        prof = self.build_profile()
        self.assertEqual([(n["kind"], n["days_left"]) for n in prof["near_sales"]], [("先行", 5)])
        self.assertEqual(prof["near_sales"][0]["url"], "https://example.invalid/pre")

    def test_sale_already_started_is_not_listed_as_near(self):
        self.add("先行", "発売中", "2026-10-15T10:00:00+09:00", "https://example.invalid/open")  # 開始済み
        self.add("一般", "販売終了", "2026-10-01T10:00:00+09:00", "https://example.invalid/end")
        self.add("一般", "未確認", None, "https://example.invalid/unk")                          # 日付なし
        prof = self.build_profile()
        self.assertEqual(prof["near_sales"], [])

    def test_no_sales_registered_means_no_near_items_not_a_guess(self):
        prof = self.build_profile()
        self.assertEqual(prof["near_sales"], [])
        html = P.person_card(prof)
        self.assertIn("発売開始が14日以内の予定はありません", html)

    def test_card_shows_near_and_started_states_distinctly(self):
        self.add("先行", "発売前", "2026-10-26T10:00:00+09:00", "https://example.invalid/pre")
        self.add("一般", "発売中", "2026-10-15T10:00:00+09:00", "https://example.invalid/open")
        self.add("一般", "未確認", None, "https://example.invalid/unk")
        html = P.person_card(self.build_profile())
        self.assertIn("あと5日：2026-10-26T10:00:00+09:00", html)
        self.assertIn("一般販売：発売中（開始 2026-10-15T10:00:00+09:00）", html)
        self.assertIn("一般販売：未確認（開始 未確認）", html)

    def test_deadline_window_is_shared_and_unchanged(self):
        self.assertEqual(FW.SALE_NEAR_DAYS, 14)
        FW.record_event(self.store, self.wid, "チケット発売", "2026-11-04", "https://example.invalid/e", CHK, TEST)  # 14日後
        FW.record_event(self.store, self.wid, "チケット締切", "2026-11-05", "https://example.invalid/f", CHK, TEST)  # 15日後
        items = FW.deadline_items(self.store, TODAY)
        self.assertEqual([i["type"] for i in items], ["チケット発売"])


class ReleaseHistoryTest(Base):
    def test_history_keeps_before_after_checked_on_and_source(self):
        FW.set_release(self.store, self.wid, "公開日確定", SRC, CHK, "2027-03-01")
        FW.set_release(self.store, self.wid, "公開日確定", "https://example.invalid/new", "2026-11-01", "2027-04-10")
        hist = FW.release_history(self.store, self.wid)
        self.assertEqual([(h["before"], h["after"]) for h in hist], [(None, "2027-03-01"), ("2027-03-01", "2027-04-10")])
        self.assertEqual(hist[1]["at"], "2026-11-01")                       # 確認日
        self.assertEqual(hist[1]["source_url"], "https://example.invalid/new")  # 出典

    def test_same_date_again_is_not_a_new_change(self):
        FW.set_release(self.store, self.wid, "公開日確定", SRC, CHK, "2027-03-01")
        FW.set_release(self.store, self.wid, "公開日確定", SRC, "2026-11-02", "2027-03-01")
        self.assertEqual(len(FW.release_history(self.store, self.wid)), 1)

    def test_unknown_in_between_makes_previous_date_unknown_not_guessed(self):
        FW.set_release(self.store, self.wid, "公開日確定", SRC, CHK, "2027-03-01")
        FW.set_release(self.store, self.wid, "未確認", SRC, "2026-11-02")
        FW.set_release(self.store, self.wid, "公開日確定", SRC, "2026-11-03", "2027-05-01")
        hist = FW.release_history(self.store, self.wid)
        self.assertEqual(hist[-1]["before"], None)                          # 直前は未確認なので「変更前は不明」
        self.assertEqual(hist[-1]["after"], "2027-05-01")

    def test_history_is_separate_per_work(self):
        other = FW.register_work(self.store, "架空作品N", TEST, SRC)
        FW.set_release(self.store, self.wid, "公開日確定", SRC, CHK, "2027-03-01")
        FW.set_release(self.store, other, "公開日確定", SRC, CHK, "2027-06-01")
        self.assertEqual([h["after"] for h in FW.release_history(self.store, self.wid)], ["2027-03-01"])

    def test_db_and_card_show_before_and_after(self):
        FW.set_release(self.store, self.wid, "公開日確定", SRC, CHK, "2027-03-01")
        FW.set_release(self.store, self.wid, "公開日確定", "https://example.invalid/new", "2026-11-01", "2027-04-10")
        prof = self.build_profile()
        hist = prof["works"][0]["release_history"]
        self.assertEqual([(h["before"], h["after"]) for h in hist], [(None, "2027-03-01"), ("2027-03-01", "2027-04-10")])
        html = P.person_card(prof)
        self.assertIn("変更：変更前は不明 → 2027-03-01（確認日 2026-10-21）", html)
        self.assertIn("変更：2027-03-01 → 2027-04-10（確認日 2026-11-01）", html)
        self.assertIn("日本公開日 2027-04-10（確定）", html)                  # 現在の公開日

    def test_work_without_release_has_no_history_and_says_unknown(self):
        prof = self.build_profile()
        self.assertEqual(prof["works"][0]["release_history"], [])
        self.assertIn("日本公開日：未確認", P.person_card(prof))


if __name__ == "__main__":
    unittest.main()
