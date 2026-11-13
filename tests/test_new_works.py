"""登録済み人物への新作追加：既存の人物を参照して作品を足す、重複は作らない、公開日未確認は次回作にしない。
架空データ・一時フォルダのみ。外部通信なし。"""
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
SRC = "https://example.invalid/src"
CHK = "2026-10-21"


class NewWorkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="new_works_")
        self.film = os.path.join(self.tmp, "film")
        self.db = os.path.join(self.tmp, "db.sqlite")
        self.exh = os.path.join(self.tmp, "exh.html")
        with open(self.exh, "w", encoding="utf-8") as f:
            f.write("<html><body></body></html>")
        self.scr = os.path.join(self.tmp, "no_scr")
        first = os.path.join(self.tmp, "first.json")
        with open(first, "w", encoding="utf-8") as f:
            json.dump({"people": [{"ref": "d", "name": "架空 監督N", "disambiguator": "1970年生",
                                   "source_url": SRC, "checked_on": CHK}],
                       "works": [{"ref": "w1", "title": "架空作品one"}],
                       "credits": [{"work": "w1", "person": "d", "role": "監督"}]}, f, ensure_ascii=False)
        FE.import_file(self.film, first, TEST)
        self.pid = [k for k, v in FW.Store(self.film).load("people").items() if v["name"] == "架空 監督N"][0]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def new_work_batch(self, title="架空作品two（新作）", person_id=None, release=None):
        work = {"ref": "w2", "title": title}
        if release is not None:
            work["release"] = release
        path = os.path.join(self.tmp, f"new_{len(os.listdir(self.tmp))}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"works": [work],
                       "credits": [{"work": "w2", "person_id": person_id or self.pid, "role": "監督"}]},
                      f, ensure_ascii=False)
        return path

    def snapshot(self):
        s = FW.Store(self.film)
        return (s.load("people"), s.load("works"))

    def test_new_work_is_added_to_existing_person_without_recreating_them(self):
        before_people = self.snapshot()[0]
        FE.import_file(self.film, self.new_work_batch(release={"status": "未発表"}), TEST)
        people, works = self.snapshot()
        self.assertEqual(people, before_people)                                  # 人物は増えない
        self.assertEqual(len(works), 2)
        new = [w for w in works.values() if w["title"].startswith("架空作品two")][0]
        self.assertEqual(new["credits"], [{"person_id": self.pid, "role": "監督"}])

    def test_person_page_lists_new_work_and_next_work(self):
        FE.import_file(self.film, self.new_work_batch(release={"status": "未発表"}), TEST)
        D.build(self.db, self.film, self.exh, self.scr, today=TODAY, data_kind=TEST)
        prof = Q.person_profile(self.db, self.pid, TODAY)
        self.assertEqual(sorted(w["title"] for w in prof["works"]), ["架空作品one", "架空作品two（新作）"])
        self.assertEqual([n["title"] for n in prof["next_works"]], ["架空作品two（新作）"])
        self.assertIn("日本公開日：未発表（新作）", P.person_card(prof))

    def test_same_work_again_is_refused_and_ledger_unchanged(self):
        path = self.new_work_batch()
        FE.import_file(self.film, path, TEST)
        after_first = self.snapshot()
        with self.assertRaises(ValueError):
            FE.import_file(self.film, path, TEST)
        self.assertEqual(self.snapshot(), after_first)

    def test_unknown_person_id_is_refused_and_nothing_changes(self):
        before = self.snapshot()
        with self.assertRaises(ValueError) as cm:
            FE.import_file(self.film, self.new_work_batch(person_id="p_nonexistent"), TEST)
        self.assertIn("登録されていないID", str(cm.exception))
        self.assertEqual(self.snapshot(), before)

    def test_undated_new_work_is_unknown_and_not_listed_as_next_work(self):
        FE.import_file(self.film, self.new_work_batch(), TEST)                   # 公開状態は未入力
        D.build(self.db, self.film, self.exh, self.scr, today=TODAY, data_kind=TEST)
        prof = Q.person_profile(self.db, self.pid, TODAY)
        new = [w for w in prof["works"] if w["title"].startswith("架空作品two")][0]
        self.assertEqual(new["release_status"], "未確認")
        self.assertEqual(prof["next_works"], [])
        self.assertIn("日本公開日：未確認", P.person_card(prof))

    def test_homonym_registered_separately_is_not_merged_with_existing(self):
        path = os.path.join(self.tmp, "homonym.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"people": [{"ref": "x", "name": "架空 監督N", "disambiguator": "1988年生"}]}, f, ensure_ascii=False)
        FE.import_file(self.film, path, TEST)
        people = self.snapshot()[0]
        self.assertEqual(len([p for p in people.values() if p["name"] == "架空 監督N"]), 2)


if __name__ == "__main__":
    unittest.main()
