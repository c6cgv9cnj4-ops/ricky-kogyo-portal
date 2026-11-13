"""人物登録→作品→役割→公開日・販売→DB構築→検索→プレビュー表示の一連のテスト。
架空データ専用（data_kind は テスト用架空データ）。ネットワーク不要。書き込みは一時フォルダに限る。"""
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
import film_watch as FW  # noqa: E402
import portal_db as D  # noqa: E402
import portal_preview as P  # noqa: E402
import portal_search as Q  # noqa: E402

TEST = "テスト用架空データ"
TODAY = datetime.date(2026, 10, 21)
SRC = "https://example.invalid/source"
CHK = "2026-10-21"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="film_entry_")
        self.film = os.path.join(self.tmp, "film")
        self.db = os.path.join(self.tmp, "db", "portal.db")
        self.exh = os.path.join(self.tmp, "exh.html")
        with open(self.exh, "w", encoding="utf-8") as f:
            f.write("<html><body></body></html>")          # 展覧会は空（この検証の対象外）
        self.scr = os.path.join(self.tmp, "no_scr")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args):
        """CLI を実行する。戻り値は (終了コード, 標準出力, 標準エラー)。"""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = FE.main(["--dir", self.film, "--data-kind", TEST, *args])
        return rc, out.getvalue(), err.getvalue()

    def store(self):
        return FW.Store(self.film)


class PersonRegistrationTest(Base):
    def test_same_name_without_disambiguator_is_refused(self):
        self.assertEqual(self.cli("person-add", "--name", "架空 次郎", "--source-url", SRC, "--checked-on", CHK)[0], 0)
        rc, _, err = self.cli("person-add", "--name", "架空 次郎", "--source-url", SRC, "--checked-on", CHK)
        self.assertEqual(rc, 2)
        self.assertIn("同一人物の可能性", err)
        self.assertEqual(len(self.store().load("people")), 1)      # 台帳は変わらない

    def test_same_name_with_different_disambiguator_is_allowed(self):
        a = self.cli("person-add", "--name", "架空 次郎", "--disambiguator", "1970年生",
                     "--source-url", SRC, "--checked-on", CHK)
        b = self.cli("person-add", "--name", "架空 次郎", "--disambiguator", "1985年生",
                     "--source-url", SRC, "--checked-on", CHK)
        self.assertEqual((a[0], b[0]), (0, 0))
        people = self.store().load("people")
        self.assertEqual(len(people), 2)
        self.assertEqual({p["disambiguator"] for p in people.values()}, {"1970年生", "1985年生"})

    def test_same_disambiguator_is_still_refused(self):
        self.cli("person-add", "--name", "架空 三郎", "--disambiguator", "1970年生", "--source-url", SRC, "--checked-on", CHK)
        rc, _, _ = self.cli("person-add", "--name", "架空 三郎", "--disambiguator", "1970年生",
                            "--source-url", SRC, "--checked-on", CHK)
        self.assertEqual(rc, 2)

    def test_missing_disambiguator_on_existing_homonym_is_refused(self):
        self.cli("person-add", "--name", "架空 四郎", "--disambiguator", "1970年生", "--source-url", SRC, "--checked-on", CHK)
        rc, _, _ = self.cli("person-add", "--name", "架空 四郎", "--source-url", SRC, "--checked-on", CHK)
        self.assertEqual(rc, 2)

    def test_http_source_is_refused_and_nothing_is_written(self):
        rc, _, err = self.cli("person-add", "--name", "架空 五郎", "--source-url", "http://example.invalid/x",
                              "--checked-on", CHK)
        self.assertEqual(rc, 2)
        self.assertIn("https://", err)
        self.assertFalse(os.path.exists(os.path.join(self.film, "people.json")))

    def test_bad_checked_on_is_refused(self):
        for bad in ("2026/10/21", "2026-02-30", "2026-10-21T10:00:00+09:00"):   # 空欄は任意になったので対象外
            rc, _, _ = self.cli("person-add", "--name", "架空 六郎", "--source-url", SRC, "--checked-on", bad)
            self.assertEqual(rc, 2, bad)
        self.assertFalse(os.path.exists(os.path.join(self.film, "people.json")))


class WorkReleaseSalesTest(Base):
    def setUp(self):
        super().setUp()
        self.cli("person-add", "--name", "架空 監督", "--disambiguator", "1975年生", "--source-url", SRC, "--checked-on", CHK)
        self.pid = list(self.store().load("people"))[0]
        self.cli("work-add", "--title", "架空作品A", "--source-url", SRC, "--checked-on", CHK)
        self.wid = list(self.store().load("works"))[0]

    def test_credit_keeps_source_and_date(self):
        rc, _, _ = self.cli("credit-add", "--work-id", self.wid, "--person-id", self.pid, "--role", "監督",
                            "--source-url", SRC, "--checked-on", CHK)
        self.assertEqual(rc, 0)
        cred = self.store().load("works")[self.wid]["credits"][0]
        self.assertEqual(cred, {"person_id": self.pid, "role": "監督", "source_url": SRC, "checked_on": CHK})

    def test_release_date_only_when_confirmed(self):
        self.assertEqual(self.cli("release-set", "--work-id", self.wid, "--status", "公開日確定", "--date", "2027-03-01",
                                  "--source-url", SRC, "--checked-on", CHK)[0], 0)
        rel = self.store().load("works")[self.wid]["release"]
        self.assertEqual((rel["date"], rel["status"]), ("2027-03-01", "公開日確定"))
        # 未発表・未確認に日付を入れる → 拒否
        self.assertEqual(self.cli("release-set", "--work-id", self.wid, "--status", "未発表", "--date", "2027-03-01",
                                  "--source-url", SRC, "--checked-on", CHK)[0], 2)
        # 公開日確定なのに日付なし → 拒否
        self.assertEqual(self.cli("release-set", "--work-id", self.wid, "--status", "公開日確定",
                                  "--source-url", SRC, "--checked-on", CHK)[0], 2)

    def test_sale_before_sale_needs_date_and_same_kind_url_is_updated_not_duplicated(self):
        url = "https://example.invalid/tickets"
        self.assertEqual(self.cli("sale-set", "--work-id", self.wid, "--kind", "先行", "--status", "発売前",
                                  "--source-url", SRC, "--checked-on", CHK)[0], 2)          # 日時なし → 拒否
        self.assertEqual(self.cli("sale-set", "--work-id", self.wid, "--kind", "先行", "--status", "発売前",
                                  "--starts-at", "2026-11-01T10:00:00", "--url", url,
                                  "--source-url", SRC, "--checked-on", CHK)[0], 2)          # タイムゾーンなし → 拒否
        self.cli("sale-set", "--work-id", self.wid, "--kind", "先行", "--status", "発売前", "--starts-at",
                 "2026-11-01T10:00:00+09:00", "--url", url, "--source-url", SRC, "--checked-on", CHK)
        self.cli("sale-set", "--work-id", self.wid, "--kind", "先行", "--status", "発売中", "--url", url,
                 "--source-url", SRC, "--checked-on", CHK)                                  # 同じ区分・URLは更新
        self.cli("sale-set", "--work-id", self.wid, "--kind", "一般", "--status", "未確認",
                 "--source-url", SRC, "--checked-on", CHK)                                  # 日付なし・未確認は可
        sales = self.store().load("works")[self.wid]["sales"]
        self.assertEqual(len(sales), 2)
        first = [s for s in sales if s["kind"] == "先行"][0]
        self.assertEqual((first["status"], first["starts_at"]), ("発売中", None))   # 更新後の状態。日付は推測しない


class EndToEndTest(Base):
    """人物登録 → 作品 → クレジット → 公開日・販売 → DB → 検索 → プレビュー表示までを一連で確認する。"""

    def build_all(self):
        self.cli("person-add", "--name", "架空 監督", "--disambiguator", "1975年生", "--source-url", SRC, "--checked-on", CHK)
        self.cli("person-add", "--name", "架空 監督", "--disambiguator", "1988年生", "--source-url", SRC, "--checked-on", CHK)
        people = self.store().load("people")
        pid_a = [k for k, v in people.items() if v["disambiguator"] == "1975年生"][0]
        pid_b = [k for k, v in people.items() if v["disambiguator"] == "1988年生"][0]
        self.cli("work-add", "--title", "架空作品A", "--source-url", SRC, "--checked-on", CHK)
        self.cli("work-add", "--title", "架空作品B", "--source-url", SRC, "--checked-on", CHK)
        works = self.store().load("works")
        wa = [k for k, v in works.items() if v["title"] == "架空作品A"][0]
        wb = [k for k, v in works.items() if v["title"] == "架空作品B"][0]
        self.cli("credit-add", "--work-id", wa, "--person-id", pid_a, "--role", "監督", "--source-url", SRC, "--checked-on", CHK)
        self.cli("credit-add", "--work-id", wb, "--person-id", pid_b, "--role", "監督", "--source-url", SRC, "--checked-on", CHK)
        # 作品A：公開日確定（未来）・先行は発売前（日時あり）・一般は未確認
        self.cli("release-set", "--work-id", wa, "--status", "公開日確定", "--date", "2026-12-05",
                 "--source-url", SRC, "--checked-on", CHK)
        self.cli("sale-set", "--work-id", wa, "--kind", "先行", "--status", "発売前", "--starts-at",
                 "2026-11-10T10:00:00+09:00", "--url", "https://example.invalid/presale", "--source-url", SRC,
                 "--checked-on", CHK)
        self.cli("sale-set", "--work-id", wa, "--kind", "一般", "--status", "未確認", "--source-url", SRC,
                 "--checked-on", CHK)
        # 作品B：公開日未発表（日付なし）
        self.cli("release-set", "--work-id", wb, "--status", "未発表", "--source-url", SRC, "--checked-on", CHK)
        return pid_a, pid_b

    def test_full_chain_db_search_preview(self):
        pid_a, pid_b = self.build_all()
        # DB構築
        counts = D.build(self.db, self.film, self.exh, self.scr, today=TODAY, data_kind=TEST)
        self.assertEqual((counts["people"], counts["works"], counts["credits"], counts["sales"]), (2, 2, 2, 2))
        # 検索：同名2人は別々に返り、名前だけで統合しない
        cands = Q.person_candidates(self.db, "架空 監督")
        self.assertEqual(sorted(c["disambiguator"] for c in cands), ["1975年生", "1988年生"])
        prof_a = Q.person_profile(self.db, pid_a, TODAY)
        prof_b = Q.person_profile(self.db, pid_b, TODAY)
        self.assertEqual([w["title"] for w in prof_a["works"]], ["架空作品A"])     # 1975年生の作品だけ
        self.assertEqual([w["title"] for w in prof_b["works"]], ["架空作品B"])     # 1988年生の作品だけ
        # 今後の公開予定：確定日の公開日と、発売前の先行販売だけ。未発表・未確認は含めない
        kinds = [(u["kind"], u["date"]) for u in prof_a["upcoming"]]
        self.assertIn(("公開日", "2026-12-05"), kinds)
        self.assertIn(("先行販売開始", "2026-11-10T10:00:00+09:00"), kinds)
        self.assertEqual(prof_b["upcoming"], [])
        # 出典と確認日
        self.assertEqual(prof_a["works"][0]["credit_source_url"], SRC)
        self.assertEqual(prof_a["works"][0]["credit_checked_on"], CHK)
        # プレビュー表示（人物検索）
        out = os.path.join(self.tmp, "preview", "index.html")
        P.build(self.exh, self.scr, out, today=TODAY, film_dir=self.film, db_path=self.db,
                search={"person": "架空 監督"}, meta_path=os.path.join(self.tmp, "none.json"))
        page = open(out, encoding="utf-8").read()
        self.assertIn("1975年生", page)
        self.assertIn("1988年生", page)
        self.assertIn("同名の人物が複数あります", page)
        self.assertIn("日本公開日 2026-12-05（確定）", page)
        self.assertIn("先行販売：発売前（開始 2026-11-10T10:00:00+09:00）", page)
        self.assertIn("一般販売：未確認", page)
        self.assertIn("日本公開日：未発表", page)                                  # 作品B
        self.assertIn("今後の公開予定（確定日のみ）", page)

    def test_unregistered_person_shows_not_registered_message(self):
        self.build_all()
        D.build(self.db, self.film, self.exh, self.scr, today=TODAY, data_kind=TEST)
        out = os.path.join(self.tmp, "p2.html")
        P.build(self.exh, self.scr, out, today=TODAY, db_path=self.db, search={"person": "架空 存在しない"},
                meta_path=os.path.join(self.tmp, "none.json"))
        self.assertIn("該当する人物は登録されていません", open(out, encoding="utf-8").read())

    def test_list_command_shows_ids(self):
        self.build_all()
        rc, out, _ = self.cli("list")
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(len(data["people"]), 2)
        self.assertEqual(len(data["works"]), 2)

    def test_work_without_release_or_sale_is_unconfirmed_not_guessed(self):
        self.cli("person-add", "--name", "架空 脚本家", "--disambiguator", "X", "--source-url", SRC, "--checked-on", CHK)
        pid = list(self.store().load("people"))[0]
        self.cli("work-add", "--title", "架空作品C", "--source-url", SRC, "--checked-on", CHK)
        wid = list(self.store().load("works"))[0]
        self.cli("credit-add", "--work-id", wid, "--person-id", pid, "--role", "脚本", "--source-url", SRC, "--checked-on", CHK)
        D.build(self.db, self.film, self.exh, self.scr, today=TODAY, data_kind=TEST)
        prof = Q.person_profile(self.db, pid, TODAY)
        self.assertEqual(prof["works"][0]["release_status"], "未確認")
        self.assertIsNone(prof["works"][0]["release_date"])
        self.assertEqual(prof["works"][0]["sales"], [])
        self.assertEqual(prof["upcoming"], [])

    def test_old_ledger_without_new_fields_still_builds(self):
        # 既存の台帳（公開日・販売・出典の新しい項目がない形）でも DB が作れる
        os.makedirs(self.film, exist_ok=True)
        with open(os.path.join(self.film, "people.json"), "w", encoding="utf-8") as f:
            json.dump({"p_old": {"id": "p_old", "name": "架空 旧", "aliases": [], "data_kind": TEST,
                                 "source_url": SRC, "registered_at": "2026-10-01T00:00:00+09:00"}}, f)
        with open(os.path.join(self.film, "works.json"), "w", encoding="utf-8") as f:
            json.dump({"w_old": {"id": "w_old", "title": "架空旧作", "data_kind": TEST, "source_url": SRC,
                                 "credits": [{"person_id": "p_old", "role": "出演"}]}}, f)
        counts = D.build(self.db, self.film, self.exh, self.scr, today=TODAY, data_kind=TEST)
        self.assertEqual((counts["people"], counts["works"], counts["credits"], counts["sales"]), (1, 1, 1, 0))
        prof = Q.person_profile(self.db, "p_old", TODAY)
        self.assertEqual(prof["works"][0]["release_status"], "未確認")


if __name__ == "__main__":
    unittest.main()
