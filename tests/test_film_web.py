"""映画選択→関係者表示→人物登録→再表示（登録済み）の一連の動作を、架空データで検証する。
外部通信なし。HTTP のテストは 127.0.0.1 の一時ポートだけを使う。書き込みは一時フォルダに限る。"""
import datetime
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import film_watch as FW  # noqa: E402
import film_web as W  # noqa: E402

TEST = "テスト用架空データ"
TODAY = datetime.date(2026, 10, 21)
CHK = "2026-10-21"
SRC = "https://example.invalid/film"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="film_web_")
        self.film = os.path.join(self.tmp, "film")
        self.db = os.path.join(self.tmp, "db", "portal.db")
        self.exh = os.path.join(self.tmp, "exh.html")
        with open(self.exh, "w", encoding="utf-8") as f:
            f.write("<html><body></body></html>")
        self.scr = os.path.join(self.tmp, "no_scr")
        self.store = FW.Store(self.film)
        self.wid = FW.register_work(self.store, "架空映画X", TEST, SRC, None)
        FW.add_staff(self.store, self.wid, "架空 監督", "監督", SRC, CHK, disambiguator="1975年生")
        FW.add_staff(self.store, self.wid, "架空 脚本", "脚本", SRC, CHK)
        FW.add_staff(self.store, self.wid, "架空 出演", "出演", SRC, CHK, disambiguator="1990年生")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class StaffStatusTest(Base):
    def test_all_unregistered_at_first(self):
        self.assertEqual([W.staff_status(self.store, s)[0] for s in self.store.load("works")[self.wid]["staff"]],
                         [W.STATUS_UNREGISTERED] * 3)

    def test_homonym_without_disambiguator_needs_check_not_auto_linked(self):
        FW.register_person(self.store, "架空 脚本", [], TEST, SRC)          # 区別情報なしで既に登録されている
        staff = self.store.load("works")[self.wid]["staff"][1]
        self.assertEqual(W.staff_status(self.store, staff)[0], W.STATUS_CHECK)
        with self.assertRaises(ValueError):
            W.track(self.store, self.wid, 1)                                 # 要確認は自動で登録しない

    def test_same_name_with_different_disambiguator_is_a_different_person(self):
        FW.register_person(self.store, "架空 出演", [], TEST, SRC, disambiguator="1985年生")
        staff = self.store.load("works")[self.wid]["staff"][2]               # 区別情報 1990年生
        self.assertEqual(W.staff_status(self.store, staff)[0], W.STATUS_UNREGISTERED)


class FlowTest(Base):
    """映画を選ぶ → 関係者が出る → 未登録を追加 → 再表示で登録済みになる。"""

    def test_select_film_show_staff_track_and_redisplay(self):
        # 1) 映画一覧に出る
        self.assertIn("架空映画X", W.index_html(self.store))
        # 2) 映画を選ぶと関係者が出る。未登録には追加ボタン
        before = W.film_html(self.store, self.wid)
        self.assertIn("架空 監督", before)
        self.assertIn("追跡対象に追加", before)
        self.assertEqual(before.count("追跡対象に追加"), 3)
        # 3) 未登録の関係者を追加する（人物台帳に入り、作品と役割が結ばれる）
        status, pid = W.track(self.store, self.wid, 0)
        self.assertEqual(status, W.STATUS_REGISTERED)
        people = self.store.load("people")
        self.assertEqual(people[pid]["disambiguator"], "1975年生")
        self.assertEqual(people[pid]["checked_on"], CHK)
        self.assertEqual(self.store.load("works")[self.wid]["credits"][0]["person_id"], pid)
        # 4) 再表示で登録済みになり、追加ボタンは減る。人物ページへのリンクが出る
        after = W.film_html(self.store, self.wid)
        self.assertEqual(after.count("追跡対象に追加"), 2)
        self.assertIn(f"/person?id={pid}", after)

    def test_track_twice_does_not_duplicate_person_or_credit(self):
        W.track(self.store, self.wid, 0)
        W.track(self.store, self.wid, 0)
        self.assertEqual(len(self.store.load("people")), 1)
        self.assertEqual(len(self.store.load("works")[self.wid]["credits"]), 1)

    def test_person_page_from_db_shows_unknown_not_guessed(self):
        _, pid = W.track(self.store, self.wid, 0)
        html_out = W.person_html(self.db, pid, TODAY, self.exh, self.scr, self.film)
        self.assertIn("架空 監督", html_out)
        self.assertIn("関連作品", html_out)
        self.assertIn("架空映画X", html_out)
        self.assertIn("日本公開日：未確認", html_out)                       # 公開日の登録なし → 未確認
        self.assertIn("販売：未確認", html_out)                             # 販売の登録なし → 未確認
        self.assertIn("今後の公開予定は登録されていません", html_out)         # 日付を補わない

    def test_unknown_person_id_shows_not_found(self):
        html_out = W.person_html(self.db, "p_none", TODAY, self.exh, self.scr, self.film)
        self.assertIn("この人物は登録されていません", html_out)


class HttpFlowTest(Base):
    """127.0.0.1 の一時ポートで実際に HTTP 操作する（選択→表示→追加→再表示）。"""

    def setUp(self):
        super().setUp()
        handler = W.make_handler(self.film, self.db, self.exh, self.scr, today=TODAY)
        import http.server
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, r.read().decode("utf-8")

    def test_click_flow_over_loopback(self):
        status, page = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("架空映画X", page)
        status, page = self.get("/film?id=" + urllib.parse.quote(self.wid))
        self.assertIn("追跡対象に追加", page)
        # 追加（POST）。リダイレクト先の映画ページが返る
        data = urllib.parse.urlencode({"work_id": self.wid, "index": "0"}).encode("utf-8")
        with urllib.request.urlopen(urllib.request.Request(self.base + "/track", data=data), timeout=5) as r:
            page = r.read().decode("utf-8")
            self.assertEqual(r.status, 200)
        self.assertIn("登録済み", page)
        self.assertEqual(len(self.store.load("people")), 1)
        # 同じ操作をもう一度しても、登録は増えない（既に登録済みの関係者は追加対象外）
        status, page = self.get("/film?id=" + urllib.parse.quote(self.wid))
        self.assertEqual(page.count("追跡対象に追加"), 2)

    def test_invalid_track_returns_400_and_changes_nothing(self):
        data = urllib.parse.urlencode({"work_id": self.wid, "index": "9"}).encode("utf-8")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(urllib.request.Request(self.base + "/track", data=data), timeout=5)
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(self.store.load("people"), {})


if __name__ == "__main__":
    unittest.main()
