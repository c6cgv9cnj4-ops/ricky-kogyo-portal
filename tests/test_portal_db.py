"""統合DB（portal_db）のテスト。架空データのみ。失敗時に既存DBを壊さないことを確かめる。"""
import datetime
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import portal_db as D  # noqa: E402
import film_watch as FW  # noqa: E402
import screenings as S  # noqa: E402

TODAY = datetime.date(2026, 10, 20)
NOW = datetime.datetime(2026, 10, 20, 9, 0, tzinfo=S.AW.JST)
TEST = "テスト用架空データ"


class PortalDbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="portal_db_")
        self.film = os.path.join(self.tmp, "film")
        st = FW.Store(self.film)
        p = FW.register_person(st, "架空監督P（テスト）", data_kind=TEST, now=NOW)
        w = FW.register_work(st, "架空映画Q（テスト）", data_kind=TEST, source_url="https://example.invalid/q", now=NOW)
        FW.link_credit(st, w, p, "監督")
        self.scr = os.path.join(self.tmp, "scr")
        S.ingest(self.scr, [{"title": "架空映画Q（テスト）", "theater": "架空シネマ（テスト）", "screen_date": "2026-10-25",
                             "start_time": "18:00", "evidence_url": "https://example.invalid/s"}], NOW)
        # 展覧会の入力は、架空のカードを読める最小のHTMLにする
        self.exh = os.path.join(self.tmp, "exh.html")
        with open(self.exh, "w", encoding="utf-8") as f:
            f.write('<html><body>'
                    '<a class="card" href="https://example.invalid/e1" data-venue="架空美術館（テスト）" data-state="確認済み">'
                    '<h3 class="title">架空展一（テスト）<span class="x">展</span></h3>'
                    "<div class='period'>2026年10月1日（木）～2026年10月25日（日）</div></a>"
                    '</body></html>')
        self.db = os.path.join(self.tmp, "out", "portal.sqlite")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_counts_and_search(self):
        c = D.build(self.db, self.film, self.exh, self.scr, today=TODAY, data_kind=TEST)
        self.assertEqual((c["people"], c["works"], c["credits"], c["screenings"]), (1, 1, 1, 1))
        hits = D.search(self.db, "架空映画Q")
        self.assertEqual(sorted(h["type"] for h in hits), sorted(["作品", "上映"]))   # 同じ題名の作品と上映の両方に一致
        work = next(h for h in hits if h["type"] == "作品")
        self.assertEqual(work["source_url"], "https://example.invalid/q")
        self.assertEqual(c["exhibitions"], 1)

    def test_failed_rebuild_keeps_existing_db(self):
        D.build(self.db, self.film, self.exh, self.scr, today=TODAY)
        before = open(self.db, "rb").read()
        bad = os.path.join(self.tmp, "bad_film")
        os.makedirs(bad)
        with open(os.path.join(bad, "people.json"), "w", encoding="utf-8") as f:
            f.write("{壊れた")                               # 台帳が壊れている → 作成は失敗する
        with self.assertRaises(Exception):
            D.build(self.db, bad, self.exh, self.scr, today=TODAY)
        self.assertEqual(open(self.db, "rb").read(), before)   # 既存DBは変わらない

    def test_exhibition_rows_carry_flags_and_source(self):
        D.build(self.db, self.film, self.exh, self.scr, today=TODAY, data_kind=TEST)
        import sqlite3
        con = sqlite3.connect(self.db)
        row = con.execute("SELECT title, end_date, official_url, data_kind FROM exhibitions").fetchone()
        con.close()
        self.assertEqual(row, ("架空展一（テスト）", "2026-10-25", "https://example.invalid/e1", TEST))


class MissingReasonTest(unittest.TestCase):
    """欠損の理由は推測せず、分類だけを残す（架空データ）。"""

    def test_reasons(self):
        self.assertEqual(D.missing_reason("2026年10月1日（木）～2026年10月25日（日）",
                                          datetime.date(2026, 10, 25)), "")          # 終了が判明：理由なし
        self.assertEqual(D.missing_reason("", None), "掲載に会期の記載なし")
        self.assertEqual(D.missing_reason("取得困難（要アクセス）", None), "掲載元が取得困難（要アクセス）")
        self.assertEqual(D.missing_reason("10月1日（木）～12月13日（日）", None), "年の記載なし（推測しない）")
        self.assertEqual(D.missing_reason("2026年10月1日（木）", None), "終了の記載なし（開始のみ）")


class EdgeCaseTest(unittest.TestCase):
    """空データ・不正データ・欠損・同一展覧会の重複（架空データ）。"""

    CARD = ('<a class="card" href="https://example.invalid/d1" data-venue="架空美術館（テスト）" data-state="確認済み">'
            '<h3 class="title">架空展D（テスト）<span class="x">展</span></h3>'
            "<div class='period'>2026年10月1日（木）～2026年10月25日（日）</div></a>")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="portal_db_edge_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _html(self, body):
        p = os.path.join(self.tmp, "src.html")
        with open(p, "w", encoding="utf-8") as f:
            f.write("<html><body>" + body + "</body></html>")
        return p

    def test_all_empty_inputs_build_zero_counts(self):
        db = os.path.join(self.tmp, "empty.sqlite")
        c = D.build(db, os.path.join(self.tmp, "no_film"), self._html(""), os.path.join(self.tmp, "no_scr"),
                    today=TODAY, data_kind=TEST)
        self.assertEqual(c, {"people": 0, "works": 0, "credits": 0, "sales": 0, "facilities": 0, "exhibitions": 0,
                             "screenings": 0})

    def test_malformed_card_without_title_is_kept_as_empty_not_crash(self):
        db = os.path.join(self.tmp, "malformed.sqlite")
        bad = '<a class="card" href="https://example.invalid/x" data-venue="架空美術館（テスト）" data-state="未確認"></a>'
        c = D.build(db, os.path.join(self.tmp, "no_film"), self._html(bad), os.path.join(self.tmp, "no_scr"),
                    today=TODAY, data_kind=TEST)
        self.assertEqual(c["exhibitions"], 1)            # 題名が空でも落とさない（欠損として残す）

    def test_same_exhibition_twice_is_stored_once(self):
        db = os.path.join(self.tmp, "dup.sqlite")
        c = D.build(db, os.path.join(self.tmp, "no_film"), self._html(self.CARD + self.CARD),
                    os.path.join(self.tmp, "no_scr"), today=TODAY, data_kind=TEST)
        self.assertEqual(c["exhibitions"], 1)            # 同一展覧会の重複は1件に統合
        self.assertEqual(c["facilities"], 1)

    def test_rebuild_same_input_is_idempotent(self):
        db = os.path.join(self.tmp, "idem.sqlite")
        args = (db, os.path.join(self.tmp, "no_film"), self._html(self.CARD), os.path.join(self.tmp, "no_scr"))
        c1 = D.build(*args, today=TODAY, data_kind=TEST)
        c2 = D.build(*args, today=TODAY, data_kind=TEST)
        self.assertEqual(c1, c2)


if __name__ == "__main__":
    unittest.main()
