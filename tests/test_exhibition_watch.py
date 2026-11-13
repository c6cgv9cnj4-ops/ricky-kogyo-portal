"""美術展の見逃し防止のテスト。架空のテスト専用データ（実在の作家は登録しない）と、既存の展示ページの読取のみを使う。"""
import datetime
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
import exhibition_watch as X  # noqa: E402

TODAY = datetime.date(2026, 10, 18)
NOW = "2026-10-18T09:00:00+09:00"
PER = "2026年9月1日～2026年12月1日"


def card(title, venue="テスト美術館", period=PER, link="https://example.test/ex/1"):
    return {"venue": venue, "title": title, "period": period, "link": link, "state": "確認済み"}


REG = {"artists": [{"id": "test_artist_a", "name": "テストアーティストA", "aliases": ["テスA"], "works": ["テスト作品B"]}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.state = X.State(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class T01_EmptyRegistry(Base):
    def test_empty_registry_runs(self):
        self.state.save("registry", {"artists": []})
        cs = [card("テスト展1")]
        self.assertEqual(X.match_card(cs[0], self.state.load("registry")), [])
        self.assertTrue(X.run(self.state, cs, True, TODAY, NOW)["applied"])


class T02_NameAndAlias(Base):
    def test_full_name_and_alias_detected(self):
        m = X.match_card(card("テストアーティストA 展"), REG)
        self.assertEqual(m[0]["matched"], "テストアーティストA")
        self.assertEqual(m[0]["confidence"], "確定")
        m2 = X.match_card(card("テスA 回顧展"), REG)
        self.assertEqual(m2[0]["matched"], "テスA")


class T03_NoFalseMatch(Base):
    def test_unregistered_and_substring_are_not_confirmed(self):
        self.assertEqual(X.match_card(card("まったく別の展覧会"), REG), [])
        m = X.match_card(card("テストアーティストAXYZ 展"), REG)   # 部分一致は要確認
        self.assertTrue(all(x["confidence"] == "要確認" for x in m))


class T04_WorkTitle(Base):
    def test_work_title_exact_match(self):
        m = X.match_card(card("テスト作品B"), REG)
        self.assertEqual(m[0]["field"], "作品名")
        self.assertEqual(m[0]["method"], "完全一致")
        self.assertEqual(m[0]["confidence"], "確定")


class T05_NewDetected(Base):
    def test_new_exhibition(self):
        r = X.run(self.state, [card("新展1")], True, TODAY, NOW)
        self.assertEqual(r["events"][0]["event"], "新規")


class T06_PeriodChange(Base):
    def test_period_change_detected(self):
        X.run(self.state, [card("展A")], True, TODAY, NOW)
        r = X.run(self.state, [card("展A", period="2026年9月1日～2027年1月10日")], True, TODAY, NOW)
        self.assertEqual(r["events"][0]["event"], "更新")
        self.assertEqual(r["events"][0]["before"], PER)


class T07_EndDetected(Base):
    def test_ended_when_end_passed_and_missing(self):
        X.run(self.state, [card("展B", period="2026年7月8日 〜 8月30日"), card("展C")], True, TODAY, NOW)
        r = X.run(self.state, [card("展C")], True, TODAY, NOW)   # 展Bは掲載されず、会期終了日を過ぎている
        self.assertEqual([e["event"] for e in r["events"] if e["title"] == "展B"], ["終了"])


class T08_NoDuplicateCandidates(Base):
    def test_same_input_twice_no_new_candidates(self):
        r1 = X.run(self.state, [card("展D")], True, TODAY, NOW)
        cands = X.candidates(self.state, r1["events"], REG)
        X.mark_dry_run(self.state, cands)
        r2 = X.run(self.state, [card("展D")], True, TODAY, NOW)
        self.assertEqual(r2["events"], [])
        self.assertEqual(len(self.state.load("exhibitions")), 1)


class T09_FailureKeepsData(Base):
    def test_failure_and_empty_keep_everything(self):
        X.run(self.state, [card("展E", period="2026年7月8日 〜 8月30日")], True, TODAY, NOW)
        before = self.state.load("exhibitions")
        self.assertFalse(X.run(self.state, [], True, TODAY, NOW)["applied"])
        self.assertFalse(X.run(self.state, [card("展E")], False, TODAY, NOW)["applied"])
        self.assertEqual(self.state.load("exhibitions"), before)


class T10_UrlAndTimeKept(Base):
    def test_official_url_and_checked_time_stored(self):
        X.run(self.state, [card("展F", link="https://example.test/ex/9#top")], True, TODAY, NOW)
        rec = list(self.state.load("exhibitions").values())[0]
        self.assertEqual(rec["official_url"], "https://example.test/ex/9")
        self.assertEqual(rec["checked_at"], NOW)
        self.assertIsNone(rec["deadline"])
        self.assertIsNone(rec["reservation"])


class T11_RealPageReuse(unittest.TestCase):
    """既存の展示ページ（読み取りのみ）を入力にできること。"""
    def test_existing_page_adapter(self):
        cards = X.load_cards(os.path.join(ROOT, X.DEFAULT_SOURCE))
        self.assertEqual(len(cards), 103)
        self.assertTrue(all(c["link"].startswith("http") for c in cards))
        self.assertTrue(all(c["title"] for c in cards))


class T12_DateParsing(unittest.TestCase):
    def test_date_forms(self):
        self.assertEqual(X.parse_dates("2026年7月8日 〜 8月30日"), (datetime.date(2026, 7, 8), datetime.date(2026, 8, 30)))
        self.assertEqual(X.parse_dates("2026年9月 9日（水）～2026年12月13日（日）"), (datetime.date(2026, 9, 9), datetime.date(2026, 12, 13)))
        self.assertEqual(X.parse_dates("取得困難（要アクセス）"), (None, None))


if __name__ == "__main__":
    unittest.main()
