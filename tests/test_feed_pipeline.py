"""提供データ（CSV・JSON）→ 台帳 → DB → HTML → 差分 → 通知、の一連の検証。
隔離した架空データ・一時フォルダのみ。外部通信なし。"""
import csv
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import feed_adapters as FA  # noqa: E402
import film_pipeline as FP  # noqa: E402
import film_site as FS  # noqa: E402
import film_watch as FW  # noqa: E402

TEST = "テスト用架空データ"
TODAY = __import__("datetime").date(2026, 10, 21)
HEADER = list(FA.COLUMNS)
SRC = "https://example.invalid/src"


class FeedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="feed_")
        self.film = os.path.join(self.tmp, "film")
        self.db = os.path.join(self.tmp, "p.db")
        self.out = os.path.join(self.tmp, "docs", "film")
        self.exh = os.path.join(self.tmp, "exh.html")
        with open(self.exh, "w", encoding="utf-8") as f:
            f.write("<html><body></body></html>")
        self.ns = os.path.join(self.tmp, "ns")
        self.notice = os.path.join(self.tmp, "notice.txt")
        self.saved_docs = FS.DOCS_ROOT
        FS.DOCS_ROOT = os.path.join(self.tmp, "docs")
        os.makedirs(FS.DOCS_ROOT)

    def tearDown(self):
        FS.DOCS_ROOT = self.saved_docs
        shutil.rmtree(self.tmp, ignore_errors=True)

    def csv(self, rows, name="feed.csv"):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=HEADER)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in HEADER})
        return p

    def pipeline(self, path, source="csv_file"):
        return FP.run(source, path, self.film, self.db, self.out, self.notice, TEST,
                      exhibition_source=self.exh, screenings_dir=self.ns, today=TODAY)

    def ledger(self):
        s = FW.Store(self.film)
        return s.load("works"), s.load("people"), s.load("history")

    def base_row(self, **kw):
        row = {"作品名": "架空作品P", "監督・人物名": "架空 監督P", "人物の区別情報": "1975年生", "役割": "監督",
               "公開状態": "公開日確定", "公開日": "2027-05-01", "公開日の出典URL": SRC, "確認日": "2026-10-21"}
        row.update(kw)
        return row

    def test_new_work_person_release_and_sale_flow_to_html_and_notice(self):
        path = self.csv([self.base_row(**{"販売区分": "先行", "販売状態": "発売前", "販売開始日時": "2026-11-10T10:00:00+09:00",
                                          "販売URL": "https://example.invalid/t", "販売の出典URL": SRC,
                                          "販売の出典URL": SRC})])
        result = self.pipeline(path)
        self.assertIn("架空作品P", result["report"]["new_works"])
        self.assertIn("[作品追加] 架空作品P", result["diff"])
        self.assertIn("架空 監督P（監督）", result["diff"])
        works, people, _ = self.ledger()
        self.assertEqual(len(people), 1)
        self.assertEqual([w["title"] for w in works.values()], ["架空作品P"])
        self.assertTrue(os.path.exists(os.path.join(self.out, "index.html")))
        self.assertEqual(result["notice"]["new"], len([l for l in result["diff"].split("\n") if l.startswith("[")]))
        self.assertTrue(os.path.exists(self.notice))

    def test_rerun_same_file_adds_nothing_and_notifies_nothing_again(self):
        path = self.csv([self.base_row(**{"販売区分": "一般", "販売状態": "未確認"})])
        self.pipeline(path)
        before = self.ledger()
        second = self.pipeline(path)
        self.assertEqual(second["diff"], "変更なし")
        self.assertEqual(second["notice"], {"new": 0, "file": None})
        self.assertEqual(self.ledger(), before)                                     # 人物・作品・販売は増えない

    def test_changed_release_date_is_detected_and_noticed_once(self):
        self.pipeline(self.csv([self.base_row()]))
        changed = self.csv([self.base_row(**{"公開日": "2027-06-15"})], name="feed2.csv")
        result = self.pipeline(changed)
        self.assertIn("[公開日変更] 架空作品P", result["diff"])
        self.assertIn("変更後：2027-06-15", result["diff"])
        self.assertEqual(result["notice"]["new"], 1)
        again = self.pipeline(changed)
        self.assertEqual(again["notice"]["new"], 0)

    def test_unconfirmed_row_is_labelled_not_confirmed(self):
        result = self.pipeline(self.csv([self.base_row(**{"公開日の出典URL": "", "確認日": ""})]))
        self.assertIn("未確認（出典または確認日なし）", result["diff"])
        self.assertNotIn("（確定）", open(os.path.join(self.out, "work", os.listdir(os.path.join(self.out, "work"))[0]),
                                          encoding="utf-8").read())

    def test_same_name_without_disambiguator_is_reported_not_linked(self):
        self.pipeline(self.csv([self.base_row()]))
        result = self.pipeline(self.csv([self.base_row(**{"作品名": "架空作品Q", "人物の区別情報": ""})], name="q.csv"))
        self.assertTrue(any("同名の人物あり" in x for x in result["report"]["needs_check"]))
        _, people, _ = self.ledger()
        self.assertEqual(len(people), 1)                                            # 同名の人物を勝手に増やさない

    def test_unknown_column_is_refused_before_any_write(self):
        p = os.path.join(self.tmp, "bad.csv")
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            f.write("作品名,秘密の列\n架空作品B,値\n")
        with self.assertRaises(ValueError):
            self.pipeline(p)
        self.assertFalse(os.path.exists(os.path.join(self.film, "works.json")))

    def test_invalid_source_url_fails_and_ledger_is_unchanged(self):
        self.pipeline(self.csv([self.base_row()]))
        before = self.ledger()
        bad = self.csv([self.base_row(**{"作品名": "架空作品B", "公開日の出典URL": "http://bad", "公開状態": "未発表",
                                         "公開日": ""})], name="bad2.csv")
        with self.assertRaises(ValueError):
            self.pipeline(bad)
        self.assertEqual(self.ledger(), before)


    def test_json_file_input_works_the_same_way(self):
        p = os.path.join(self.tmp, "feed.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"rows": [self.base_row(**{"作品名": "架空作品J"})]}, f, ensure_ascii=False)
        result = self.pipeline(p, source="json_file")
        self.assertIn("架空作品J", result["report"]["new_works"])

    def test_run_log_records_input_fingerprint_and_counts(self):
        path = self.csv([self.base_row()])
        self.pipeline(path)
        log = [json.loads(l) for l in open(os.path.join(self.film, "feed_runs.jsonl"), encoding="utf-8")]
        self.assertEqual(log[-1]["status"], "成功")
        self.assertEqual(log[-1]["rows"], 1)
        self.assertEqual(log[-1]["input_sha256"], FA.sha256_file(path))


    def test_screening_in_the_screenings_ledger_appears_on_the_person_page(self):
        import screenings as S
        scr = os.path.join(self.tmp, "screenings")
        rows = [{"title": "架空作品P", "theater": "架空シネマP（テスト）", "screen_date": "2026-11-20", "start_time": "18:00",
                 "evidence_url": SRC}]
        S.ingest(scr, rows, now=None)
        self.pipeline(self.csv([self.base_row()]))
        FP.run("csv_file", self.csv([self.base_row()], name="again.csv"), self.film, self.db, self.out, self.notice, TEST,
               exhibition_source=self.exh, screenings_dir=scr, today=TODAY)
        person = [os.path.join(self.out, "person", f) for f in os.listdir(os.path.join(self.out, "person"))][0]
        text = open(person, encoding="utf-8").read()
        self.assertIn("架空シネマP（テスト）", text)
        self.assertIn("2026-11-20", text)


if __name__ == "__main__":
    unittest.main()
