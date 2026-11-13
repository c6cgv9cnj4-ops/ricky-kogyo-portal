"""JSONの一括登録（film_entry import）の検証。架空データ専用。ネットワーク不要。書き込みは一時フォルダに限る。
確認すること：全件が成功したときだけ反映される／途中で失敗したら台帳は変わらない／同じ一括は二重登録されない／
人物ページ（DB→検索→プレビュー）まで届く。"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import film_entry as FE  # noqa: E402
import film_watch as FW  # noqa: E402
import portal_db as D  # noqa: E402
import portal_preview as P  # noqa: E402
import portal_search as Q  # noqa: E402

TEST = "テスト用架空データ"
TODAY = datetime.date(2026, 10, 21)
SRC = "https://example.invalid/film"
CHK = "2026-10-21"


def good_batch():
    return {
        "people": [
            {"ref": "dir", "name": "架空 監督", "disambiguator": "1975年生",
             "source_url": SRC, "checked_on": CHK},
            {"ref": "wri", "name": "架空 脚本", "source_url": SRC, "checked_on": CHK},
        ],
        "works": [
            {"ref": "w1", "title": "架空映画Z", "source_url": SRC, "checked_on": CHK,
             "staff": [{"name": "架空 出演", "role": "出演", "disambiguator": "1990年生",
                        "source_url": SRC, "checked_on": CHK}],
             "release": {"status": "公開日確定", "date": "2026-12-05", "source_url": SRC, "checked_on": CHK},
             "sales": [{"kind": "先行", "status": "発売前", "starts_at": "2026-10-26T10:00:00+09:00",
                        "url": "https://example.invalid/presale", "source_url": SRC, "checked_on": CHK}]},
        ],
        "credits": [
            {"work": "w1", "person": "dir", "role": "監督", "source_url": SRC, "checked_on": CHK},
            {"work": "w1", "person": "wri", "role": "脚本", "source_url": SRC, "checked_on": CHK},
        ],
    }


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="film_import_t_")
        self.film = os.path.join(self.tmp, "film")
        self.json = os.path.join(self.tmp, "batch.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, batch):
        with open(self.json, "w", encoding="utf-8") as f:
            json.dump(batch, f, ensure_ascii=False)

    def run_import(self, batch=None):
        if batch is not None:
            self.write(batch)
        return FE.import_file(self.film, self.json, TEST)

    def snapshot(self):
        store = FW.Store(self.film)
        return {k: (store.load(k) if os.path.exists(store.path(k)) else None) for k in ("people", "works", "history")}


class ImportSuccessTest(Base):
    def test_whole_batch_is_applied_with_refs_resolved(self):
        summary = self.run_import(good_batch())
        self.assertEqual(summary, {"people": 2, "works": 1, "credits": 2})
        store = FW.Store(self.film)
        people, works = store.load("people"), store.load("works")
        self.assertEqual(len(people), 2)
        wid = list(works)[0]
        self.assertEqual(sorted(c["role"] for c in works[wid]["credits"]), ["監督", "脚本"])
        self.assertEqual(works[wid]["staff"][0]["name"], "架空 出演")
        self.assertEqual(works[wid]["release"]["date"], "2026-12-05")
        self.assertEqual(works[wid]["sales"][0]["status"], "発売前")

    def test_person_page_after_import_shows_release_and_near_sale(self):
        self.run_import(good_batch())
        exh = os.path.join(self.tmp, "exh.html")
        with open(exh, "w", encoding="utf-8") as f:
            f.write("<html><body></body></html>")
        db = os.path.join(self.tmp, "db.sqlite")
        D.build(db, self.film, exh, os.path.join(self.tmp, "no_scr"), today=TODAY, data_kind=TEST)
        pid = [p for p in FW.Store(self.film).load("people").values() if p["disambiguator"] == "1975年生"][0]["id"]
        prof = Q.person_profile(db, pid, TODAY)
        self.assertEqual(prof["works"][0]["title"], "架空映画Z")
        self.assertEqual(prof["works"][0]["release_date"], "2026-12-05")
        self.assertEqual([n["kind"] for n in prof["near_sales"]], ["先行"])
        html = P.person_card(prof)
        self.assertIn("日本公開日 2026-12-05（確定）", html)
        self.assertIn("あと5日", html)


class ImportSafetyTest(Base):
    def test_failure_in_any_item_changes_nothing(self):
        batch = good_batch()
        batch["works"][0]["release"]["source_url"] = "http://example.invalid/bad"   # 2件目相当の失敗
        before = None
        with self.assertRaises(ValueError):
            self.run_import(batch)
        self.assertFalse(os.path.exists(os.path.join(self.film, "people.json")))   # 1件目の人物も入らない
        self.assertEqual(before, None)

    def test_running_the_same_batch_twice_is_refused_and_keeps_one_copy(self):
        self.run_import(good_batch())
        after_first = self.snapshot()
        with self.assertRaises(ValueError):
            self.run_import()
        self.assertEqual(self.snapshot(), after_first)

    def test_unknown_key_is_refused(self):
        batch = good_batch()
        batch["ticket_guess"] = []
        with self.assertRaises(ValueError):
            self.run_import(batch)
        self.assertFalse(os.path.exists(os.path.join(self.film, "people.json")))

    def test_duplicate_or_unknown_ref_is_refused(self):
        batch = good_batch()
        batch["credits"][0]["person"] = "存在しない参照"
        with self.assertRaises(ValueError) as cm:
            self.run_import(batch)
        self.assertIn("参照名", str(cm.exception))

    def test_homonym_without_disambiguator_is_refused_and_nothing_written(self):
        batch = good_batch()
        batch["people"].append({"ref": "dup", "name": "架空 監督", "source_url": SRC, "checked_on": CHK})
        with self.assertRaises(ValueError):
            self.run_import(batch)
        self.assertFalse(os.path.exists(os.path.join(self.film, "people.json")))

    def test_missing_required_field_is_refused(self):
        batch = good_batch()
        del batch["works"][0]["title"]                       # 作品名は必須。出典や確認日は任意になった
        with self.assertRaises(ValueError):
            self.run_import(batch)

    def test_existing_ledger_is_kept_when_later_batch_fails(self):
        self.run_import(good_batch())
        before = self.snapshot()
        second = {"people": [{"ref": "x", "name": "架空 別人", "source_url": "ftp://bad", "checked_on": CHK}]}
        with self.assertRaises(ValueError):
            self.run_import(second)
        self.assertEqual(self.snapshot(), before)


class CliImportTest(Base):
    def test_cli_import_prints_summary_and_returns_zero(self):
        self.write(good_batch())
        rc = FE.main(["--dir", self.film, "--data-kind", TEST, "import", "--file", self.json])
        self.assertEqual(rc, 0)

    def test_cli_import_failure_returns_two(self):
        bad = good_batch()
        bad["people"][0]["source_url"] = "http://no"
        self.write(bad)
        rc = FE.main(["--dir", self.film, "--data-kind", TEST, "import", "--file", self.json])
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(os.path.join(self.film, "people.json")))


if __name__ == "__main__":
    unittest.main()
