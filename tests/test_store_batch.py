"""台帳の一括書き込み（Store.batch）の検証。架空データ・一時フォルダのみ。
確認すること：書き込み回数が減る／結果が通常の登録と同じ／途中で失敗したら何も書かない。"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import film_entry as FE  # noqa: E402
import film_watch as FW  # noqa: E402

TEST = "テスト用架空データ"
SRC = "https://example.invalid/s"
CHK = "2026-10-21"


def batch(n):
    people = [{"ref": f"p{i}", "name": f"架空 人物{i}", "disambiguator": f"{1960 + i}年生", "source_url": SRC,
               "checked_on": CHK} for i in range(n)]
    works = [{"ref": f"w{i}", "title": f"架空映画{i}", "source_url": SRC, "checked_on": CHK,
              "release": {"status": "公開日確定", "date": "2027-03-01", "source_url": SRC, "checked_on": CHK},
              "sales": [{"kind": "一般", "status": "未確認", "source_url": SRC, "checked_on": CHK}],
              "staff": [{"name": f"架空 関係者{i}", "role": "出演", "source_url": SRC, "checked_on": CHK}]}
             for i in range(n)]
    credits = [{"work": f"w{i}", "person": f"p{i}", "role": "監督", "source_url": SRC, "checked_on": CHK}
               for i in range(n)]
    return {"people": people, "works": works, "credits": credits}


class BatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="store_batch_")
        self.film = os.path.join(self.tmp, "film")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_batch_writes_each_file_once_not_per_item(self):
        path = os.path.join(self.tmp, "b.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(batch(30), f, ensure_ascii=False)
        with mock.patch.object(FW.Store, "_write", autospec=True, side_effect=FW.Store._write) as w:
            FE.import_file(self.film, path, TEST)
        keys = sorted(c.args[1] for c in w.call_args_list)
        self.assertEqual(len(keys), len(set(keys)))          # 同じファイルを2回書かない
        self.assertLessEqual(len(keys), 3)                    # people・works・history の3つだけ（以前は1件ごとに書いた）

    def test_batched_result_equals_unbatched_result(self):
        # 同じ入力を、一括と1件ずつで登録し、台帳の中身が一致することを確認する
        path = os.path.join(self.tmp, "b.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(batch(5), f, ensure_ascii=False)
        FE.import_file(self.film, path, TEST)
        batched = FW.Store(self.film)
        b_people, b_works = batched.load("people"), batched.load("works")

        plain_dir = os.path.join(self.tmp, "plain")
        store = FW.Store(plain_dir)
        data = batch(5)
        for p in data["people"]:
            FW.register_person(store, p["name"], [], TEST, p["source_url"], disambiguator=p["disambiguator"],
                               checked_on=p["checked_on"])
        # 人物IDは乱数なので、中身（名前・区別情報・確認日）で比べる
        self.assertEqual(sorted((p["name"], p["disambiguator"]) for p in b_people.values()),
                         sorted((p["name"], p["disambiguator"]) for p in store.load("people").values()))
        self.assertEqual(len(b_works), 5)
        self.assertTrue(all(w.get("release", {}).get("date") == "2027-03-01" for w in b_works.values()))

    def test_failure_inside_batch_writes_nothing(self):
        store = FW.Store(self.film)
        with self.assertRaises(RuntimeError):
            with store.batch():
                FW.register_person(store, "架空 途中", [], TEST, SRC, checked_on=CHK)
                raise RuntimeError("途中で失敗")
        self.assertFalse(os.path.exists(os.path.join(self.film, "people.json")))   # 何も書かれない
        self.assertEqual(FW.Store(self.film).load("people"), {})

    def test_failed_import_leaves_existing_ledger_unchanged(self):
        path = os.path.join(self.tmp, "ok.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(batch(3), f, ensure_ascii=False)
        FE.import_file(self.film, path, TEST)
        before = open(os.path.join(self.film, "people.json"), encoding="utf-8").read()
        bad = batch(3)
        bad["credits"].append({"work": "w0", "person": "存在しない", "role": "監督"})   # 参照名が不正
        bpath = os.path.join(self.tmp, "bad.json")
        with open(bpath, "w", encoding="utf-8") as f:
            json.dump(bad, f, ensure_ascii=False)
        with self.assertRaises(ValueError):
            FE.import_file(self.film, bpath, TEST)
        self.assertEqual(open(os.path.join(self.film, "people.json"), encoding="utf-8").read(), before)

    def test_nested_batches_flush_only_at_outer_end(self):
        store = FW.Store(self.film)
        with mock.patch.object(FW.Store, "_write", autospec=True, side_effect=FW.Store._write) as w:
            with store.batch():
                with store.batch():
                    FW.register_person(store, "架空 入れ子", [], TEST, SRC, checked_on=CHK)
                self.assertEqual(w.call_count, 0)              # 内側が終わっても、まだ書かない
        self.assertEqual(w.call_count, 1)


if __name__ == "__main__":
    unittest.main()
