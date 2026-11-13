"""公開HP（静的ページ）のローカル生成：一括登録→DB→ページ生成→出力先の保護を、架空データで検証する。
出力先は一時フォルダの docs/ に差し替えるので、実際の docs/ には一切書かない。ネットワーク不要。"""
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
SRC = "https://example.invalid/film"
CHK = "2026-10-21"


def batch():
    return {
        "people": [{"ref": "dir", "name": "架空 監督", "disambiguator": "1975年生", "source_url": SRC, "checked_on": CHK}],
        "works": [
            {"ref": "w1", "title": "架空映画Z", "source_url": SRC, "checked_on": CHK,
             "staff": [{"name": "架空 出演", "role": "出演", "disambiguator": "1990年生", "source_url": SRC, "checked_on": CHK},
                       {"name": "架空 監督", "role": "監督", "disambiguator": "1975年生", "source_url": SRC, "checked_on": CHK}],
             "release": {"status": "公開日確定", "date": "2026-12-05", "source_url": SRC, "checked_on": CHK},
             "sales": [{"kind": "先行", "status": "発売前", "starts_at": "2026-10-26T10:00:00+09:00",
                        "url": "https://example.invalid/presale", "source_url": SRC, "checked_on": CHK}]},
            {"ref": "w2", "title": "架空映画N（次回作）", "source_url": SRC, "checked_on": CHK,
             "release": {"status": "未発表", "source_url": SRC, "checked_on": CHK}},
            {"ref": "w3", "title": "架空映画U（情報なし）", "source_url": SRC, "checked_on": CHK}],
        "credits": [
            {"work": "w1", "person": "dir", "role": "監督", "source_url": SRC, "checked_on": CHK},
            {"work": "w2", "person": "dir", "role": "監督", "source_url": SRC, "checked_on": CHK}],
    }


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="film_site_t_")
        self.repo_docs = os.path.join(self.tmp, "docs")          # 実際の docs/ の代わり
        os.makedirs(self.repo_docs)
        self.saved_docs = FS.DOCS_ROOT
        FS.DOCS_ROOT = self.repo_docs
        self.film = os.path.join(self.tmp, "film")
        self.db = os.path.join(self.tmp, "db", "portal.db")
        self.batch = os.path.join(self.tmp, "batch.json")
        with open(self.batch, "w", encoding="utf-8") as f:
            json.dump(batch(), f, ensure_ascii=False)
        self.exh = os.path.join(self.tmp, "exh.html")
        with open(self.exh, "w", encoding="utf-8") as f:
            f.write("<html><body></body></html>")
        self.out = os.path.join(self.repo_docs, "film")

    def tearDown(self):
        FS.DOCS_ROOT = self.saved_docs
        shutil.rmtree(self.tmp, ignore_errors=True)

    def generate(self, batch_path=None):
        return FS.build_site(self.film, self.db, self.out, batch=batch_path, data_kind=TEST,
                             exhibition_source=self.exh, screenings_dir=os.path.join(self.tmp, "no_scr"), today=TODAY)

    def read(self, *parts):
        with open(os.path.join(self.out, *parts), encoding="utf-8") as f:
            return f.read()


class GenerateTest(Base):
    def test_one_command_builds_db_and_pages(self):
        result = self.generate(self.batch)
        self.assertEqual(result["import"], {"people": 1, "works": 3, "credits": 2})
        self.assertEqual(result["db"]["people"], 1)
        self.assertTrue(os.path.exists(os.path.join(self.out, FS.MARKER)))
        index = self.read("index.html")
        self.assertIn("架空映画Z", index)
        self.assertIn("架空 監督（1975年生）", index)

    def test_person_page_shows_next_work_release_and_near_sale(self):
        self.generate(self.batch)
        person_files = os.listdir(os.path.join(self.out, "person"))
        self.assertEqual(len(person_files), 1)
        page = self.read("person", person_files[0])
        self.assertIn("次回作・新作", page)
        self.assertIn("架空映画N（次回作）", page)
        self.assertIn("日本公開日：未発表（新作）", page)
        self.assertIn("日本公開日 2026-12-05（確定）", page)
        self.assertIn("あと5日", page)                                     # 発売前・14日以内
        self.assertIn("上映予定（登録済みの上映のみ）", page)
        self.assertIn("登録済みの上映はありません（未確認）", page)

    def test_work_page_shows_sale_unknown_and_unregistered_staff_not_linked(self):
        self.generate(self.batch)
        files = {f: self.read("work", f) for f in os.listdir(os.path.join(self.out, "work"))}
        z = [v for v in files.values() if "架空映画Z" in v][0]
        self.assertIn("先行販売：発売前（開始 2026-10-26T10:00:00+09:00）", z)
        self.assertIn("公式販売ページ", z)
        self.assertIn("架空 出演", z)
        self.assertNotIn("../person/p_", z.split("架空 出演")[1].split("</li>")[0])  # 未登録の出演は人物ページへリンクしない
        self.assertIn("../person/p_", z)                                     # 登録済みの監督はリンクする
        u = [v for v in files.values() if "架空映画U" in v][0]
        self.assertIn("日本公開日：未確認", u)                              # 登録なし → 未確認
        self.assertIn("販売：未確認", u)

    def test_regeneration_replaces_output_without_leftovers(self):
        self.generate(self.batch)
        self.generate()                                                     # 2回目（一括なし）
        self.assertTrue(os.path.exists(os.path.join(self.out, "index.html")))
        self.assertEqual(len(os.listdir(os.path.join(self.out, "person"))), 1)


class LinkCheckTest(Base):
    """生成HTMLの機械検査：内部リンクがすべて実在するファイルを指すこと、出典リンクがあること。"""

    def test_all_internal_links_resolve_and_sources_are_linked(self):
        import html.parser
        self.generate(self.batch)

        class Links(html.parser.HTMLParser):
            def __init__(self):
                super().__init__()
                self.hrefs = []

            def handle_starttag(self, tag, attrs):
                if tag == "a":
                    self.hrefs.append(dict(attrs).get("href", ""))

        pages = []
        for root, _, files in os.walk(self.out):
            for name in files:
                if name.endswith(".html"):
                    pages.append(os.path.join(root, name))
        self.assertGreaterEqual(len(pages), 4)
        broken = []
        for page in pages:
            parser = Links()
            with open(page, encoding="utf-8") as f:
                parser.feed(f.read())
            for href in parser.hrefs:
                if href.startswith("https://"):
                    continue                                   # 外部の出典リンクは検査対象外（通信しない）
                target = os.path.normpath(os.path.join(os.path.dirname(page), href))
                if not os.path.exists(target):
                    broken.append((os.path.relpath(page, self.out), href))
        self.assertEqual(broken, [])
        # 出典リンクが付いていること（作品・人物ページ）
        for page in pages:
            if os.path.basename(page) != "index.html":
                with open(page, encoding="utf-8") as f:
                    self.assertIn("https://example.invalid/film", f.read())


class SafetyTest(Base):
    def test_failed_batch_keeps_previous_output_and_ledger(self):
        self.generate(self.batch)
        before = self.read("index.html")
        bad = os.path.join(self.tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            json.dump({"people": [{"ref": "x", "name": "架空 別人", "source_url": "ftp://bad", "checked_on": CHK}]}, f)
        with self.assertRaises(ValueError):
            self.generate(bad)
        self.assertEqual(self.read("index.html"), before)

    def test_refuses_docs_root_and_outside_docs(self):
        with self.assertRaises(ValueError):
            FS.check_out(self.repo_docs)
        with self.assertRaises(ValueError):
            FS.check_out(os.path.join(self.tmp, "elsewhere"))

    def test_refuses_to_overwrite_foreign_folder(self):
        os.makedirs(self.out)
        with open(os.path.join(self.out, "keep.html"), "w", encoding="utf-8") as f:
            f.write("他の人のページ")
        with self.assertRaises(ValueError):
            self.generate()
        with open(os.path.join(self.out, "keep.html"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "他の人のページ")

    def test_real_docs_root_is_not_touched_by_tests(self):
        real = os.path.join(FS.HERE, "docs")
        before = sorted(os.listdir(real)) if os.path.isdir(real) else []
        self.generate(self.batch)
        after = sorted(os.listdir(real)) if os.path.isdir(real) else []
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
