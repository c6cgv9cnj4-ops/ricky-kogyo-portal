"""更新差分（作品追加・公開日変更・販売情報の追加と変更）の検証。隔離した架空データ・一時フォルダのみ。外部通信なし。
確認すること：変更が一覧に出る／変更前後と確認状態が出る／同じ更新の再実行では差分が出ない／失敗時は差分を返さない。"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import film_site as FS  # noqa: E402
import film_watch as FW  # noqa: E402

TEST = "テスト用架空データ"
TODAY = datetime.date(2026, 10, 21)
SRC = "https://example.invalid/src"
CHK = "2026-10-21"


class Env(unittest.TestCase):
    """隔離した環境（一時フォルダ・既定の出力先の差し替え・1回分の更新の実行）を、テストが共有する。"""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="update_diff_")
        self.docs = os.path.join(self.tmp, "docs")
        os.makedirs(self.docs)
        self.saved = FS.DOCS_ROOT
        FS.DOCS_ROOT = self.docs
        self.film = os.path.join(self.tmp, "film")
        self.db = os.path.join(self.tmp, "p.db")
        self.exh = os.path.join(self.tmp, "exh.html")
        with open(self.exh, "w", encoding="utf-8") as f:
            f.write("<html><body></body></html>")
        self.ns = os.path.join(self.tmp, "ns")

    def tearDown(self):
        FS.DOCS_ROOT = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_batch(self, body, name):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False)
        return FS.build_site(self.film, self.db, os.path.join(self.docs, "film"), batch=p, data_kind=TEST,
                             exhibition_source=self.exh, screenings_dir=self.ns, today=TODAY)["diff"]

class DiffTest(Env):
    def test_new_work_is_listed_with_person_and_unconfirmed_label(self):
        diff = self.run_batch({"people": [{"ref": "d", "name": "架空 監督D", "disambiguator": "1970年生"}],
                               "works": [{"ref": "w", "title": "架空作品D"}],
                               "credits": [{"work": "w", "person": "d", "role": "監督"}]}, "a.json")
        self.assertIn("[作品追加] 架空作品D", diff)
        self.assertIn("架空 監督D（監督）", diff)
        self.assertIn("未確認（出典または確認日なし）", diff)

    def test_release_change_shows_before_after_source_and_checked_date(self):
        self.run_batch({"people": [], "works": [{"ref": "w", "title": "架空作品E"}]}, "b1.json")
        self.run_batch({"updates": [{"title": "架空作品E", "release": {"status": "公開日確定", "date": "2027-03-01",
                                                                    "source_url": SRC, "checked_on": CHK}}]}, "b2.json")
        diff = self.run_batch({"updates": [{"title": "架空作品E", "release": {"status": "公開日確定", "date": "2027-04-10",
                                                                        "source_url": SRC, "checked_on": "2026-11-01"}}]}, "b3.json")
        self.assertIn("[公開日変更] 架空作品E", diff)
        self.assertIn("変更前：2027-03-01 → 変更後：2027-04-10", diff)
        self.assertIn("確認日：2026-11-01", diff)
        self.assertIn("確認済み", diff)
        self.assertIn(SRC, diff)

    def test_sale_addition_is_listed_and_rerun_is_no_change(self):
        self.run_batch({"works": [{"ref": "w", "title": "架空作品S"}]}, "c1.json")
        wid = [k for k, v in FW.Store(self.film).load("works").items() if v["title"] == "架空作品S"][0]
        body = {"updates": [{"work_id": wid, "sales": [{"kind": "先行", "status": "未確認", "url": "https://example.invalid/t",
                                                       "source_url": SRC, "checked_on": CHK}]}]}
        diff = self.run_batch(body, "c2.json")
        self.assertIn("[販売情報追加] 架空作品S", diff)
        self.assertIn("先行販売：なし → 未確認（開始 未確認）", diff)
        self.assertEqual(self.run_batch(body, "c3.json"), "変更なし")          # 同じ更新の再実行は差分なし

    def test_same_release_again_is_no_change(self):
        self.run_batch({"works": [{"ref": "w", "title": "架空作品R"}]}, "d1.json")
        upd = {"updates": [{"title": "架空作品R", "release": {"status": "公開日確定", "date": "2027-03-01",
                                                          "source_url": SRC, "checked_on": CHK}}]}
        self.run_batch(upd, "d2.json")
        self.assertEqual(self.run_batch(upd, "d3.json"), "変更なし")

    def test_failed_run_returns_no_diff_and_ledger_is_unchanged(self):
        self.run_batch({"works": [{"ref": "w", "title": "架空作品F"}]}, "e1.json")
        before = open(os.path.join(self.film, "works.json"), encoding="utf-8").read()
        with self.assertRaises(ValueError):
            self.run_batch({"updates": [{"title": "存在しない題名", "release": {"status": "未発表"}}]}, "e2.json")
        self.assertEqual(open(os.path.join(self.film, "works.json"), encoding="utf-8").read(), before)


class NoticeTest(Env):
    """差分を、ローカルの通知ファイルに一度だけ書く（重複通知しない）。外部へは送らない。"""

    def notice(self, diff):
        return FS.write_notice(self.film, diff, os.path.join(self.tmp, "notice.txt"))

    def test_new_work_release_and_sale_are_noticed_once(self):
        self.run_batch({"works": [{"ref": "w", "title": "架空作品N"}]}, "n1.json")
        wid = [k for k, v in FW.Store(self.film).load("works").items() if v["title"] == "架空作品N"][0]
        diff = self.run_batch({"updates": [{"work_id": wid,
                                            "release": {"status": "公開日確定", "date": "2027-03-01", "source_url": SRC, "checked_on": CHK},
                                            "sales": [{"kind": "一般", "status": "未確認", "url": "https://example.invalid/g",
                                                       "source_url": SRC, "checked_on": CHK}]}]}, "n2.json")
        self.assertEqual(self.notice(diff)["new"], 2)
        self.assertIn("[公開日変更]", open(os.path.join(self.tmp, "notice.txt"), encoding="utf-8").read())
        self.assertEqual(self.notice(diff)["new"], 0)                    # 同じ差分は重複通知しない

    def test_no_change_writes_no_notice(self):
        self.run_batch({"works": [{"ref": "w", "title": "架空作品M"}]}, "m1.json")
        diff = self.run_batch({"works": []}, "m2.json")
        self.assertEqual(diff, "変更なし")
        self.assertEqual(self.notice(diff), {"new": 0, "file": None})
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "notice.txt")))

    def test_only_the_new_change_is_noticed_on_a_later_run(self):
        self.run_batch({"works": [{"ref": "w", "title": "架空作品K"}]}, "k1.json")
        upd1 = {"updates": [{"title": "架空作品K", "release": {"status": "公開日確定", "date": "2027-03-01", "source_url": SRC, "checked_on": CHK}}]}
        self.notice(self.run_batch(upd1, "k2.json"))
        upd2 = {"updates": [{"title": "架空作品K", "release": {"status": "公開日確定", "date": "2027-04-10", "source_url": SRC, "checked_on": CHK}}]}
        diff = self.run_batch(upd2, "k3.json")
        self.assertEqual(self.notice(diff)["new"], 1)                    # 新しい変更の1件だけ
        text = open(os.path.join(self.tmp, "notice.txt"), encoding="utf-8").read()
        self.assertIn("変更後：2027-04-10", text)
        self.assertNotIn("変更後：2027-03-01", text)

    def test_failed_run_produces_no_notice(self):
        self.run_batch({"works": [{"ref": "w", "title": "架空作品X"}]}, "x1.json")
        with self.assertRaises(ValueError):
            self.run_batch({"updates": [{"title": "存在しない題名", "release": {"status": "未発表"}}]}, "x2.json")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "notice.txt")))


if __name__ == "__main__":
    unittest.main()
