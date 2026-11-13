"""一括入力の「更新」（登録済みの作品への公開日変更・販売情報追加）と、パイプラインの内部リンク検査。
隔離した架空データ・一時フォルダのみ。外部通信なし。"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import film_entry as FE  # noqa: E402
import film_site as FS  # noqa: E402
import film_watch as FW  # noqa: E402

TEST = "テスト用架空データ"
SRC = "https://example.invalid/src"
CHK = "2026-10-21"


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="batch_upd_")
        self.film = os.path.join(self.tmp, "film")
        self.path = os.path.join(self.tmp, "base.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"works": [{"ref": "w", "title": "架空作品U"}]}, f, ensure_ascii=False)
        FE.import_file(self.film, self.path, TEST)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def batch(self, body, name):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False)
        return p

    def snapshot(self):
        s = FW.Store(self.film)
        return s.load("works"), s.load("history")

    def test_release_change_by_title_keeps_before_and_after_in_history(self):
        p1 = self.batch({"updates": [{"title": "架空作品U", "release": {"status": "公開日確定", "date": "2027-03-01",
                                                                    "source_url": SRC, "checked_on": CHK}}]}, "u1.json")
        FE.import_file(self.film, p1, TEST)
        p2 = self.batch({"updates": [{"title": "架空作品U", "release": {"status": "公開日確定", "date": "2027-04-10",
                                                                    "source_url": SRC, "checked_on": "2026-11-01"}}]}, "u2.json")
        FE.import_file(self.film, p2, TEST)
        works, history = self.snapshot()
        wid = list(works)[0]
        self.assertEqual(works[wid]["release"]["date"], "2027-04-10")
        self.assertEqual([(h["before"], h["after"]) for h in history if h["work_id"] == wid],
                         [(None, "2027-03-01"), ("2027-03-01", "2027-04-10")])

    def test_sale_added_by_work_id_and_rerun_does_not_duplicate(self):
        wid = list(self.snapshot()[0])[0]
        body = {"updates": [{"work_id": wid, "sales": [{"kind": "先行", "status": "未確認", "url": "https://example.invalid/t",
                                                       "source_url": SRC, "checked_on": CHK}]}]}
        FE.import_file(self.film, self.batch(body, "s1.json"), TEST)
        FE.import_file(self.film, self.batch(body, "s2.json"), TEST)               # 同じ更新を再実行
        sales = self.snapshot()[0][wid]["sales"]
        self.assertEqual(len(sales), 1)

    def test_ambiguous_or_unknown_title_is_refused_and_nothing_changes(self):
        before = self.snapshot()
        with self.assertRaises(ValueError):
            FE.import_file(self.film, self.batch({"updates": [{"title": "存在しない作品",
                                                               "release": {"status": "未発表"}}]}, "x.json"), TEST)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_source_in_update_changes_nothing(self):
        before = self.snapshot()
        wid = list(before[0])[0]
        with self.assertRaises(ValueError):
            FE.import_file(self.film, self.batch({"updates": [{"work_id": wid, "release": {
                "status": "公開日確定", "date": "2027-03-01", "source_url": "http://bad", "checked_on": CHK}}]}, "y.json"), TEST)
        self.assertEqual(self.snapshot(), before)


class LinkCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="linkchk_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_broken_internal_link_is_reported_and_valid_one_is_not(self):
        os.makedirs(os.path.join(self.tmp, "work"))
        with open(os.path.join(self.tmp, "index.html"), "w", encoding="utf-8") as f:
            f.write("<a href='work/ok.html'>a</a><a href='work/missing.html'>b</a><a href='https://example.invalid/x'>c</a>")
        with open(os.path.join(self.tmp, "work", "ok.html"), "w", encoding="utf-8") as f:
            f.write("<a href='../index.html'>home</a>")
        broken = FS.check_internal_links(self.tmp)
        self.assertEqual(broken, ["index.html -> work/missing.html"])

    def test_pipeline_keeps_old_output_when_links_break(self):
        docs = os.path.join(self.tmp, "docs")
        os.makedirs(docs)
        old = os.path.join(docs, "film")
        os.makedirs(old)
        with open(os.path.join(old, "index.html"), "w", encoding="utf-8") as f:
            f.write("旧ページ")
        saved = FS.DOCS_ROOT
        FS.DOCS_ROOT = docs
        try:
            real_check = FS.check_internal_links
            FS.check_internal_links = lambda site: ["index.html -> missing.html"]
            try:
                with self.assertRaises(ValueError):
                    FS.build_site(os.path.join(self.tmp, "film_data"), os.path.join(self.tmp, "db.sqlite"), old,
                                  exhibition_source=os.path.join(self.tmp, "none.html"),
                                  screenings_dir=os.path.join(self.tmp, "ns"))
            finally:
                FS.check_internal_links = real_check
        finally:
            FS.DOCS_ROOT = saved
        with open(os.path.join(old, "index.html"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "旧ページ")                              # 出力は置き換えない


    def test_failed_build_restores_ledger_and_db_not_only_output(self):
        import json as _json
        docs = os.path.join(self.tmp, "docs")
        os.makedirs(docs)
        saved = FS.DOCS_ROOT
        FS.DOCS_ROOT = docs                                  # 出力先の検査は、生成の前に差し替える
        film = os.path.join(self.tmp, "film")
        db = os.path.join(self.tmp, "p.db")
        exh = os.path.join(self.tmp, "exh.html")
        with open(exh, "w", encoding="utf-8") as f:
            f.write("<html><body></body></html>")
        base = os.path.join(self.tmp, "base.json")
        with open(base, "w", encoding="utf-8") as f:
            _json.dump({"works": [{"ref": "w", "title": "架空作品R", "release": {"status": "未発表"}}]}, f, ensure_ascii=False)
        FE.import_file(film, base, TEST)
        FS.build_site(film, db, os.path.join(docs, "film"), exhibition_source=exh,
                      screenings_dir=os.path.join(self.tmp, "ns"), data_kind=TEST)
        ledger_before = open(os.path.join(film, "works.json"), encoding="utf-8").read()
        db_before = open(db, "rb").read()
        upd = os.path.join(self.tmp, "upd.json")
        with open(upd, "w", encoding="utf-8") as f:
            _json.dump({"updates": [{"title": "架空作品R", "release": {"status": "公開日確定", "date": "2099-01-01",
                                                                    "source_url": SRC, "checked_on": CHK}}]}, f, ensure_ascii=False)
        real = FS.check_internal_links
        FS.check_internal_links = lambda site: ["index.html -> missing.html"]   # リンク切れを模擬
        try:
            with self.assertRaises(ValueError):
                FS.build_site(film, db, os.path.join(docs, "film"), batch=upd, exhibition_source=exh,
                              screenings_dir=os.path.join(self.tmp, "ns"), data_kind=TEST)
        finally:
            FS.check_internal_links = real
            FS.DOCS_ROOT = saved
        self.assertEqual(open(os.path.join(film, "works.json"), encoding="utf-8").read(), ledger_before)
        self.assertEqual(open(db, "rb").read(), db_before)                       # DB も元に戻る


if __name__ == "__main__":
    unittest.main()
