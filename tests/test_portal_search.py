"""統合DBの検索（portal_search）と、DBからプレビューまでの経路のテスト。架空データのみ。"""
import datetime
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import film_watch as FW  # noqa: E402
import portal_db as D  # noqa: E402
import portal_preview as P  # noqa: E402
import portal_search as Q  # noqa: E402
import screenings as S  # noqa: E402

TODAY = datetime.date(2026, 10, 20)
NOW = datetime.datetime(2026, 10, 20, 9, 0, tzinfo=S.AW.JST)
TEST = "テスト用架空データ"


def card(title, venue, period, state="確認済み", link=None):
    return (f'<a class="card" href="{link or "https://example.invalid/" + title}" data-venue="{venue}" '
            f'data-state="{state}"><h3 class="title">{title}<span class="x">展</span></h3>'
            f"<div class='period'>{period}</div></a>")


class SearchFixture(unittest.TestCase):
    """架空の統合DBを作る共通の準備（検索テストと、HTMLテストで共有）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="portal_search_")
        self.film = os.path.join(self.tmp, "film")
        st = FW.Store(self.film)
        d1 = FW.register_person(st, "架空監督R（テスト）", data_kind=TEST, now=NOW)
        a1 = FW.register_person(st, "架空俳優S（テスト）", data_kind=TEST, now=NOW)
        w = FW.register_work(st, "架空映画T（テスト）", data_kind=TEST, source_url="https://example.invalid/t", now=NOW)
        FW.link_credit(st, w, d1, "監督")
        FW.link_credit(st, w, a1, "出演")
        self.scr = os.path.join(self.tmp, "scr")
        S.ingest(self.scr, [{"title": "架空映画T（テスト）", "theater": "架空シネマU（テスト）",
                             "screen_date": "2026-10-25", "start_time": "18:00",
                             "evidence_url": "https://example.invalid/tshow"}], NOW)
        body = "".join([
            card("架空展開催中（テスト）", "架空美術館V（テスト）", "2026年10月1日（木）～2026年10月25日（日）"),
            card("架空展終了（テスト）", "架空美術館V（テスト）", "2026年9月1日（火）～2026年10月10日（土）"),
            card("架空展会期不明（テスト）", "架空美術館V（テスト）", "", state="未確認"),
            card("架空展予定（テスト）", "架空ギャラリーW（テスト）", "2026年11月1日（日）～2026年12月30日（水）"),
        ])
        self.exh = os.path.join(self.tmp, "exh.html")
        with open(self.exh, "w", encoding="utf-8") as f:
            f.write("<html><body>" + body + "</body></html>")
        self.db = os.path.join(self.tmp, "t.sqlite")
        D.build(self.db, self.film, self.exh, self.scr, today=TODAY, data_kind=TEST)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class SearchTest(SearchFixture):
    def test_person_to_works_with_roles(self):
        r = Q.person_works(self.db, "架空監督R（テスト）")
        self.assertEqual(len(r), 1)
        self.assertEqual([(w["title"], w["role"]) for w in r[0]["works"]], [("架空映画T（テスト）", "監督")])
        self.assertEqual(r[0]["works"][0]["source_url"], "https://example.invalid/t")

    def test_work_to_people_and_screenings(self):
        people = Q.work_people(self.db, "架空映画T（テスト）")[0]["people"]
        self.assertEqual(sorted((p["name"], p["role"]) for p in people),
                         sorted([("架空監督R（テスト）", "監督"), ("架空俳優S（テスト）", "出演")]))
        shows = Q.work_screenings(self.db, "架空映画T（テスト）")
        self.assertEqual([(s["screen_date"], s["theater"]) for s in shows], [("2026-10-25", "架空シネマU（テスト）")])

    def test_no_screening_gives_empty_list_not_error(self):
        self.assertEqual(Q.work_screenings(self.db, "存在しない作品（テスト）"), [])
        self.assertEqual(Q.person_works(self.db, "存在しない人（テスト）"), [])

    def test_facility_exhibitions_status_and_tags(self):
        hits = {h["title"]: h for h in Q.facility_exhibitions(self.db, "架空美術館V（テスト）", TODAY)}
        self.assertEqual(hits["架空展開催中（テスト）"]["status"], "開催中")
        self.assertEqual(hits["架空展終了（テスト）"]["status"], "終了")
        self.assertEqual(hits["架空展会期不明（テスト）"]["status"], "会期不明")
        self.assertIn("出典未確認", hits["架空展会期不明（テスト）"]["tags"])
        self.assertIn("会期不明", hits["架空展会期不明（テスト）"]["tags"])

    def test_non_official_and_invalid_source_tagged(self):
        from portal_search import _exhibition_view
        base = ("架空展X", "架空館", "2026-10-01", "2026-12-31", "確認済み", "", "%s", "2026-10-20", "テスト用架空データ", "")
        g = _exhibition_view(base[:6] + ("https://news.google.com/x",) + base[7:], TODAY)
        h = _exhibition_view(base[:6] + ("http://example.invalid/x",) + base[7:], TODAY)
        self.assertIn("非公式出典", g["tags"])
        self.assertIn("出典URL不正", h["tags"])
        ok = _exhibition_view(base[:6] + ("https://example.invalid/x",) + base[7:], TODAY)
        self.assertNotIn("非公式出典", ok["tags"])

    def test_end_soon_only_for_current_within_30_days(self):
        ends = {h["title"]: h["tags"] for h in Q.exhibitions_by_end(self.db, TODAY)}
        self.assertIn("終了間近", ends["架空展開催中（テスト）"])     # 10/25 は今日から5日後
        self.assertNotIn("終了間近", ends["架空展予定（テスト）"])   # 開催予定は終了間近に入れない

    def test_preview_route_from_db_shows_end_soon(self):
        out = os.path.join(self.tmp, "p.html")
        P.build(self.exh, os.path.join(self.tmp, "no_scr"), out, today=TODAY, db_path=self.db)
        page = open(out, encoding="utf-8").read()
        self.assertIn("統合DB（検索用）", page)
        self.assertIn("架空展開催中（テスト）", page)   # 終了間近の一覧に表示される


class SearchHtmlTest(SearchFixture):
    """検索関数 → HTML までの統合テスト（架空データ）。"""

    def _page(self, **kw):
        out = os.path.join(self.tmp, "page.html")
        P.build(self.exh, os.path.join(self.tmp, "no_scr"), out, today=TODAY, db_path=self.db, search=kw)
        return open(out, encoding="utf-8").read()

    def test_person_work_role_and_source_in_html(self):
        page = self._page(person="架空監督R（テスト）")
        self.assertIn("人物検索：架空監督R（テスト）", page)
        self.assertIn("架空映画T（テスト）（監督）", page)
        self.assertIn("href='https://example.invalid/t'", page)

    def test_work_section_shows_people_year_and_screenings(self):
        page = self._page(work="架空映画T（テスト）")
        self.assertIn("出演：架空俳優S（テスト）", page)
        self.assertIn("公開年：未登録", page)               # 公開年が無い場合は推測せず「未登録」
        self.assertIn("2026-10-25 18:00", page)
        self.assertIn("架空シネマU（テスト）", page)

    def test_work_without_screening_says_none_registered(self):
        db = os.path.join(self.tmp, "nos.sqlite")
        D.build(db, self.film, self.exh, os.path.join(self.tmp, "no_scr"), today=TODAY, data_kind=TEST)
        out = os.path.join(self.tmp, "nos.html")
        P.build(self.exh, os.path.join(self.tmp, "no_scr"), out, today=TODAY, db_path=db,
                search={"work": "架空映画T（テスト）"})
        self.assertIn("登録済み上映情報なし", open(out, encoding="utf-8").read())

    def test_facility_section_shows_status_and_source(self):
        page = self._page(facility="架空美術館V（テスト）")
        self.assertIn("施設検索：架空美術館V（テスト）", page)
        self.assertIn("開催中", page)
        self.assertIn("会期不明", page)

    def test_exhibition_list_groups_and_tags(self):
        page = self._page()
        self.assertIn("展覧会一覧（会期・状態）", page)
        self.assertIn("終了間近", page)
        self.assertIn("出典未確認", page)

    def test_search_terms_are_escaped(self):
        page = self._page(person="<script>alert(1)</script>")
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("該当する人物は登録されていません", page)

    def test_non_https_source_not_linked(self):
        # 出典が https でない項目は、リンクにせず「URL不正」と示す
        self.assertEqual(P._link("http://example.invalid/x", "出典"), "出典（URL不正）")
        self.assertEqual(P._link("", "出典"), "出典未確認")   # 出典が無い情報は未確認と明示
        self.assertIn("<a href='https://example.invalid/y'", P._link("https://example.invalid/y", "出典"))


if __name__ == "__main__":
    unittest.main()
