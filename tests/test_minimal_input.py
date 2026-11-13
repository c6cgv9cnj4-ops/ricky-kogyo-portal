"""最小入力で登録を始められることと、出典のない情報を「確定」として出さないことを、架空データで検証する。
実データ・外部通信なし。書き込みは一時フォルダに限る。"""
import contextlib
import datetime
import io
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
import portal_db as D  # noqa: E402
import portal_preview as P  # noqa: E402
import portal_search as Q  # noqa: E402

TEST = "テスト用架空データ"
TODAY = datetime.date(2026, 10, 21)
SRC = "https://example.invalid/src"


class MinimalBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="minimal_")
        self.film = os.path.join(self.tmp, "film")
        self.db = os.path.join(self.tmp, "db", "portal.db")
        self.exh = os.path.join(self.tmp, "exh.html")
        with open(self.exh, "w", encoding="utf-8") as f:
            f.write("<html><body></body></html>")
        self.scr = os.path.join(self.tmp, "no_scr")
        self.docs = os.path.join(self.tmp, "docs")
        os.makedirs(self.docs)
        self.saved_docs = FS.DOCS_ROOT
        FS.DOCS_ROOT = self.docs

    def tearDown(self):
        FS.DOCS_ROOT = self.saved_docs
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = FE.main(["--dir", self.film, "--data-kind", TEST, *args])
        return rc, out.getvalue(), err.getvalue()

    def store(self):
        return FW.Store(self.film)

    def db_profile(self, pid):
        D.build(self.db, self.film, self.exh, self.scr, today=TODAY, data_kind=TEST)
        return Q.person_profile(self.db, pid, TODAY)


class MinimalCliTest(MinimalBase):
    def test_title_only_starts_a_work(self):
        rc, out, _ = self.cli("work-add", "--title", "架空映画ミニマル")
        self.assertEqual(rc, 0)
        wid = json.loads(out)["work_id"]
        w = self.store().load("works")[wid]
        self.assertEqual((w["title"], w["source_url"], w["checked_on"]), ("架空映画ミニマル", "", ""))

    def test_name_only_starts_a_person_and_is_unconfirmed(self):
        rc, _, _ = self.cli("person-add", "--name", "架空 ミニ監督")
        self.assertEqual(rc, 0)
        p = list(self.store().load("people").values())[0]
        self.assertEqual((p["source_url"], p["checked_on"], p["disambiguator"]), ("", "", ""))

    def test_bad_source_is_still_refused_when_given(self):
        rc, _, _ = self.cli("work-add", "--title", "架空映画B", "--source-url", "http://example.invalid/x")
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(os.path.join(self.film, "works.json")))

    def test_role_is_still_required_for_a_credit(self):
        self.cli("person-add", "--name", "架空 人物R")
        self.cli("work-add", "--title", "架空映画R")
        pid = list(self.store().load("people"))[0]
        wid = list(self.store().load("works"))[0]
        with self.assertRaises(SystemExit):
            FE.main(["--dir", self.film, "credit-add", "--work-id", wid, "--person-id", pid])


class UnsourcedDisplayTest(MinimalBase):
    def test_confirmed_date_without_source_is_not_shown_as_confirmed(self):
        pid = FW.register_person(self.store(), "架空 監督U", [], TEST, "", checked_on="")
        wid = FW.register_work(self.store(), "架空映画U", TEST, "")
        FW.link_credit(self.store(), wid, pid, "監督")
        FW.set_release(self.store(), wid, "公開日確定", "", "", "2026-12-05")   # 日付はあるが出典なし
        prof = self.db_profile(pid)
        html = P.person_card(prof)
        self.assertIn("日本公開日 2026-12-05（出典未確認）", html)
        self.assertNotIn("（確定）", html)
        self.assertEqual(prof["upcoming"], [])                                  # 確定日の一覧には入れない
        self.assertEqual(prof["next_works"], [])                                # 次回作にも入れない

    def test_confirmed_date_with_source_is_shown_as_confirmed(self):
        pid = FW.register_person(self.store(), "架空 監督S", [], TEST, SRC, checked_on="2026-10-21")
        wid = FW.register_work(self.store(), "架空映画S", TEST, SRC)
        FW.link_credit(self.store(), wid, pid, "監督", SRC, "2026-10-21")
        FW.set_release(self.store(), wid, "公開日確定", SRC, "2026-10-21", "2026-12-05")
        prof = self.db_profile(pid)
        self.assertIn("日本公開日 2026-12-05（確定）", P.person_card(prof))
        self.assertEqual([u["date"] for u in prof["upcoming"]], ["2026-12-05"])

    def test_unknown_release_and_sale_show_unknown_not_a_date(self):
        pid = FW.register_person(self.store(), "架空 監督M", [], TEST, "", checked_on="")
        wid = FW.register_work(self.store(), "架空映画M", TEST, "")
        FW.link_credit(self.store(), wid, pid, "監督")
        html = P.person_card(self.db_profile(pid))
        self.assertIn("日本公開日：未確認", html)
        self.assertIn("販売：未確認", html)
        self.assertIn("出典未確認", html)                                       # 人物の出典も未確認と出す


    def test_source_without_checked_date_is_not_shown_as_confirmed(self):
        # 出典URLは候補として分かっているが、確認日が無い（内容を確認していない）場合は「確定」にしない
        pid = FW.register_person(self.store(), "架空 監督C", [], TEST, "", checked_on="")
        wid = FW.register_work(self.store(), "架空映画C", TEST, SRC)
        FW.link_credit(self.store(), wid, pid, "監督")
        FW.set_release(self.store(), wid, "公開日確定", SRC, "", "2023-11-03")
        prof = self.db_profile(pid)
        html = P.person_card(prof)
        self.assertIn("日本公開日 2023-11-03（確認日未確認）", html)
        self.assertNotIn("（確定）", html)
        self.assertEqual(prof["upcoming"], [])
        self.assertEqual(prof["next_works"], [])


class MinimalSiteTest(MinimalBase):
    def test_minimal_registration_generates_pages_without_crash(self):
        self.cli("person-add", "--name", "架空 ミニ監督")
        self.cli("work-add", "--title", "架空映画ミニマル")
        pid = list(self.store().load("people"))[0]
        wid = list(self.store().load("works"))[0]
        self.cli("credit-add", "--work-id", wid, "--person-id", pid, "--role", "監督")
        FS.build_site(self.film, self.db, os.path.join(self.docs, "film"), data_kind=TEST,
                      exhibition_source=self.exh, screenings_dir=self.scr, today=TODAY)
        out = os.path.join(self.docs, "film")
        index = open(os.path.join(out, "index.html"), encoding="utf-8").read()
        self.assertIn("架空映画ミニマル", index)
        self.assertIn("架空 ミニ監督", index)
        work_pages = [open(os.path.join(out, "work", f), encoding="utf-8").read()
                      for f in os.listdir(os.path.join(out, "work"))]
        self.assertTrue(any("日本公開日：未確認" in p and "出典未確認" in p for p in work_pages))

    def test_minimal_batch_with_only_titles_and_names(self):
        path = os.path.join(self.tmp, "min.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"people": [{"ref": "a", "name": "架空 ミニ脚本"}],
                       "works": [{"ref": "w", "title": "架空映画Q"}],
                       "credits": [{"work": "w", "person": "a", "role": "脚本"}]}, f, ensure_ascii=False)
        summary = FE.import_file(self.film, path, TEST)
        self.assertEqual(summary, {"people": 1, "works": 1, "credits": 1})

    def test_batch_error_names_the_missing_field(self):
        path = os.path.join(self.tmp, "bad.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"works": [{"ref": "w"}]}, f)                              # 作品名がない
        with self.assertRaises(ValueError) as cm:
            FE.import_file(self.film, path, TEST)
        self.assertIn("title", str(cm.exception))


    def test_placeholder_text_is_refused_so_template_cannot_register_by_accident(self):
        path = os.path.join(self.tmp, "tpl.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"people": [{"ref": "a", "name": "（監督名）"}],
                       "works": [{"ref": "w", "title": "（作品名）"}]}, f, ensure_ascii=False)
        with self.assertRaises(ValueError) as cm:
            FE.import_file(self.film, path, TEST)
        self.assertIn("未入力", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.film, "people.json")))


if __name__ == "__main__":
    unittest.main()
