"""監視対象から雛形CSVを作る（社長の手作業を減らす）。架空データ・一時フォルダのみ。外部通信なし。
確認すること：雛形の人物・作品・役割が台帳と一致する／空欄の雛形は台帳を変えない／値を1つ入れると、その変更だけが出る。"""
import csv
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import feed_adapters as FA  # noqa: E402
import film_pipeline as FP  # noqa: E402
import film_site as FS  # noqa: E402
import film_watch as FW  # noqa: E402

TEST = "テスト用架空データ"
TODAY = __import__("datetime").date(2026, 10, 21)
SRC = "https://example.invalid/src"


class TemplateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tpl_")
        self.film = os.path.join(self.tmp, "film")
        self.db = os.path.join(self.tmp, "p.db")
        self.exh = os.path.join(self.tmp, "exh.html")
        with open(self.exh, "w", encoding="utf-8") as f:
            f.write("<html><body></body></html>")
        self.ns = os.path.join(self.tmp, "ns")
        self.saved = FS.DOCS_ROOT
        FS.DOCS_ROOT = os.path.join(self.tmp, "docs")
        os.makedirs(FS.DOCS_ROOT)
        self.notice = os.path.join(self.tmp, "n.txt")
        s = FW.Store(self.film)
        pid = FW.register_person(s, "架空 監督T", [], TEST, SRC, disambiguator="1968年生", checked_on="2026-10-21")
        wid = FW.register_work(s, "架空作品T", TEST, SRC)
        FW.link_credit(s, wid, pid, "監督", SRC, "2026-10-21")

    def tearDown(self):
        FS.DOCS_ROOT = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pipeline(self, path):
        return FP.run("csv_file", path, self.film, self.db, os.path.join(FS.DOCS_ROOT, "film"), self.notice, TEST,
                      exhibition_source=self.exh, screenings_dir=self.ns, today=TODAY)

    def test_template_is_prefilled_from_the_ledger(self):
        path = os.path.join(self.tmp, "tpl.csv")
        FA.export_template(self.film, path)
        rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["作品名"], rows[0]["監督・人物名"], rows[0]["人物の区別情報"], rows[0]["役割"]),
                         ("架空作品T", "架空 監督T", "1968年生", "監督"))
        self.assertEqual(rows[0]["公開状態"], "")                       # 新しい値だけ空欄

    def test_untouched_template_changes_nothing(self):
        path = os.path.join(self.tmp, "tpl.csv")
        FA.export_template(self.film, path)
        before = (FW.Store(self.film).load("works"), FW.Store(self.film).load("people"))
        result = self.pipeline(path)
        self.assertEqual(result["diff"], "変更なし")
        self.assertEqual((FW.Store(self.film).load("works"), FW.Store(self.film).load("people")), before)

    def test_one_filled_value_produces_only_that_change(self):
        path = os.path.join(self.tmp, "tpl.csv")
        FA.export_template(self.film, path)
        rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
        rows[0].update({"公開状態": "公開日確定", "公開日": "2027-08-01", "公開日の出典URL": SRC, "確認日": "2026-10-21"})
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(FA.COLUMNS))
            w.writeheader()
            w.writerows(rows)
        result = self.pipeline(path)
        self.assertEqual(result["diff"].count("\n") + 1, 1)             # 差分は1件だけ
        self.assertIn("[公開日変更] 架空作品T", result["diff"])


if __name__ == "__main__":
    unittest.main()
