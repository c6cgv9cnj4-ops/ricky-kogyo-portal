"""統合ポータル（プレビュー）のテスト。架空データは明示的に（テスト）と付け、ネットワークは使わない。
実データ（docs/culture-exhibition/index.html）は読み取りのみで、書き込みは一時フォルダに限る。"""
import datetime
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import portal_preview as P  # noqa: E402
import screenings as S  # noqa: E402

TODAY = datetime.date(2026, 10, 20)


def card(title, period, state="確認済み", link=None, venue="架空美術館（テスト）"):
    return {"title": title, "venue": venue, "period": period, "state": state,
            "link": link or f"https://example.invalid/{title}"}


class ExhibitionRowsTest(unittest.TestCase):
    def test_end_date_from_period(self):
        rows = P.exhibition_rows([card("架空展A（テスト）", "2026年10月1日（木）～2026年10月25日（日）")])
        self.assertEqual(rows[0]["end"], datetime.date(2026, 10, 25))

    def test_missing_end_is_none_not_guessed(self):
        rows = P.exhibition_rows([card("架空展B（テスト）", "")])
        self.assertIsNone(rows[0]["end"])
        self.assertEqual(P.exhibition_stats(rows)["missing_end"], 1)

    def test_end_soon_window(self):
        rows = P.exhibition_rows([
            card("架空展A（テスト）", "2026年10月1日（木）～2026年10月25日（日）"),   # 5日後に終了 → 対象
            card("架空展B（テスト）", "2026年9月1日（火）～2026年10月10日（土）"),    # 終了済み → 除外
            card("架空展C（テスト）", "2026年11月1日（日）～2026年12月30日（水）"),   # 40日以上先 → 除外
        ], TODAY)
        hits = P.end_soon(rows, TODAY)
        self.assertEqual([h["title"] for h in hits], ["架空展A（テスト）"])

    def test_stats_duplicates_and_states(self):
        link = "https://example.invalid/same"
        rows = P.exhibition_rows([card("架空展A（テスト）", "2026年10月1日（木）～2026年10月25日（日）", link=link),
                                  card("架空展A（テスト）", "2026年10月1日（木）～2026年10月25日（日）", link=link, state="未確認")])
        s = P.exhibition_stats(rows)
        self.assertEqual((s["total"], s["unique_links"], s["duplicate_links"]), (2, 1, 1))
        self.assertEqual(s["states"], {"確認済み": 1, "未確認": 1})
        self.assertEqual(s["sale_or_deadline_fields"], 0)


class BuildHtmlTest(unittest.TestCase):
    def test_empty_sections_say_zero_not_blank(self):
        page = P.build_html([], [], TODAY)
        self.assertIn("上映予定は0件", page)
        self.assertIn("映画の人物・作品の実データは未登録です", page)
        self.assertIn("予約期限が近い展覧会", page)
        self.assertIn("判定できません", page)

    def test_screening_and_end_items_rendered_with_source(self):
        rows = P.exhibition_rows([card("架空展A（テスト）", "2026年10月1日（木）～2026年10月25日（日）")], TODAY)
        shows =[{"screen_date": "2026-10-21", "start_time": "10:00", "title": "架空映画A（テスト）",
                  "theater": "架空シネマA（テスト）", "evidence_url": "https://example.invalid/s"}]
        page = P.build_html(rows, shows, TODAY)
        self.assertIn("架空映画A（テスト）", page)
        self.assertIn("2026-10-25", page)
        self.assertIn("https://example.invalid/s", page)

    def test_html_escaped(self):
        rows = P.exhibition_rows([card("<script>x</script>", "2026年10月1日（木）～2026年10月25日（日）")], TODAY)
        page = P.build_html(rows, [], TODAY)
        self.assertNotIn("<script>x</script>", page)
        self.assertIn("&lt;script&gt;", page)


class BuildFromRealSourceTest(unittest.TestCase):
    """実データ（既存の掲載ページ）を読むだけの経路。出力は一時フォルダに限る。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="portal_preview_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_real_exhibition_page_counts(self):
        out = os.path.join(self.tmp, "preview.html")
        r = P.build(P.E.DEFAULT_SOURCE, os.path.join(self.tmp, "no_screenings"), out, today=TODAY)
        self.assertEqual(r["exhibitions"], 103)     # 掲載ページの件数（読み取り時点）
        self.assertEqual(r["screenings"], 0)        # 上映の実データは未投入
        self.assertTrue(os.path.exists(out))

    def test_docs_output_refused(self):
        docs_out = os.path.join(P.DOCS_DIR, "portal_preview.html")
        with self.assertRaises(ValueError):
            P.build(P.E.DEFAULT_SOURCE, self.tmp, docs_out, today=TODAY)
        self.assertFalse(os.path.exists(docs_out))


class DataQualityFlagTest(unittest.TestCase):
    """実データの品質の区分（架空データで確認）。区分は削除せず付け、終了が近い一覧からは外す。"""

    P10 = "2026年10月1日（木）～2026年10月25日（日）"   # 終了 10/25（TODAY 10/20 から5日後）

    def test_non_official_source_flagged_and_excluded_from_end_soon(self):
        rows = P.exhibition_rows([card("架空展G（テスト）", self.P10, link="https://news.google.com/x")], TODAY)
        self.assertIn("非公式出典", rows[0]["flags"])
        self.assertEqual(P.end_soon(rows, TODAY), [])

    def test_non_exhibition_title_flagged_and_excluded(self):
        rows = P.exhibition_rows([card("1/8-3/8 冬の周遊キャンペーン（テスト）", self.P10)], TODAY)
        self.assertIn("展覧会以外の可能性", rows[0]["flags"])
        self.assertEqual(P.end_soon(rows, TODAY), [])

    def test_ended_flagged_not_listed_as_end_soon(self):
        rows = P.exhibition_rows([card("架空展H（テスト）", "2026年9月1日（火）～2026年10月10日（土）")], TODAY)
        self.assertIn("終了済み", rows[0]["flags"])
        self.assertEqual(P.end_soon(rows, TODAY), [])

    def test_http_and_duplicate_link_flags_kept_in_list(self):
        same = "https://example.invalid/dup"
        rows = P.exhibition_rows([card("架空展I（テスト）", self.P10, link="http://example.invalid/i"),
                                  card("架空展J（テスト）", self.P10, link=same),
                                  card("架空展K（テスト）", self.P10, link=same)], TODAY)
        self.assertIn("https でない", rows[0]["flags"])
        self.assertIn("リンク重複", rows[1]["flags"])
        self.assertEqual(len(P.end_soon(rows, TODAY)), 3)   # https でない・リンク重複は、表示区分として残し、一覧からは外さない

    def test_missing_end_flagged(self):
        rows = P.exhibition_rows([card("架空展L（テスト）", "")], TODAY)
        self.assertIn("会期不明", rows[0]["flags"])

    def test_stats_counts_flags(self):
        rows = P.exhibition_rows([card("架空展M（テスト）", self.P10, link="https://news.google.com/m"),
                                  card("架空展N（テスト）", self.P10)], TODAY)
        self.assertEqual(P.exhibition_stats(rows)["flag_counts"], {"非公式出典": 1})


class FilmSectionTest(unittest.TestCase):
    """映画の人物・作品の台帳を、プレビューの一覧に接続する（架空データのみ）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="portal_film_")
        self.film_dir = os.path.join(self.tmp, "film")
        st = P.FW.Store(self.film_dir)
        p1 = P.FW.register_person(st, "架空監督F（テスト）", data_kind="テスト用架空データ")
        p2 = P.FW.register_person(st, "架空俳優G（テスト）", data_kind="テスト用架空データ")
        w = P.FW.register_work(st, "架空映画五（テスト）", data_kind="テスト用架空データ",
                               source_url="https://example.invalid/film5")
        P.FW.link_credit(st, w, p1, "監督")
        P.FW.link_credit(st, w, p2, "出演")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_film_rows_roles_and_source(self):
        rows = P.film_rows(self.film_dir)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["credits"], "監督：架空監督F（テスト）、出演：架空俳優G（テスト）")
        self.assertEqual(rows[0]["source_url"], "https://example.invalid/film5")

    def test_film_section_rendered_and_escaped(self):
        page = P.build_html([], [], TODAY, P.film_rows(self.film_dir))
        self.assertIn("架空映画五（テスト）", page)
        self.assertIn("出演：架空俳優G（テスト）", page)
        self.assertNotIn("映画の人物・作品の実データは未登録です", page)

    def test_build_with_film_dir_counts_films(self):
        out = os.path.join(self.tmp, "preview.html")
        r = P.build(P.E.DEFAULT_SOURCE, os.path.join(self.tmp, "no_scr"), out, today=TODAY, film_dir=self.film_dir)
        self.assertEqual(r["films"], 1)


class SnapshotNoticeTest(unittest.TestCase):
    """展覧会の掲載時点の注記（架空の掲載ファイル）。取得停止後に、古い情報を最新と誤認させない。"""

    def test_notice_shows_snapshot_time_in_jst(self):
        tmp = tempfile.mkdtemp(prefix="portal_snap_")
        try:
            src = os.path.join(tmp, "src.html")
            with open(src, "w", encoding="utf-8") as f:
                f.write("<html><body></body></html>")
            t = datetime.datetime(2026, 10, 15, 12, 0, tzinfo=datetime.timezone.utc).timestamp()
            os.utime(src, (t, t))
            out = os.path.join(tmp, "p.html")
            P.build(src, os.path.join(tmp, "no_scr"), out, today=TODAY, meta_path=os.path.join(tmp, "meta.json"))
            page = open(out, encoding="utf-8").read()
            # 取得時刻はメタデータが無いので「不明」。ファイルの更新時刻は取得時刻とは別に示す（12:00 UTC = 21:00 JST）
            self.assertIn("展覧会の取得時刻（最終成功）：不明", page)
            self.assertIn("掲載ファイルの更新時刻：2026-10-15 21:00 JST", page)
            self.assertIn("自動取得は一時停止中", page)
            self.assertLess(page.index("展覧会の取得時刻"), page.index("近いうちの上映"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class DbConnectionTest(unittest.TestCase):
    """統合DBをプレビューから参照する（架空データ）。DBを指定しない場合の表示は変わらない。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="portal_dbconn_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_db_block_appended_only_when_db_given(self):
        import portal_db as D
        html_src = os.path.join(self.tmp, "src.html")
        with open(html_src, "w", encoding="utf-8") as f:
            f.write("<html><body></body></html>")
        db = os.path.join(self.tmp, "x.sqlite")
        D.build(db, os.path.join(self.tmp, "no_film"), html_src, os.path.join(self.tmp, "no_scr"),
                today=TODAY, data_kind="テスト用架空データ")
        with_db = os.path.join(self.tmp, "with.html")
        without = os.path.join(self.tmp, "without.html")
        P.build(html_src, os.path.join(self.tmp, "no_scr"), with_db, today=TODAY, db_path=db)
        P.build(html_src, os.path.join(self.tmp, "no_scr"), without, today=TODAY)
        self.assertIn("統合DB（検索用）", open(with_db, encoding="utf-8").read())
        self.assertNotIn("統合DB（検索用）", open(without, encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
