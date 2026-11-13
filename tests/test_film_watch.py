"""映画・芸術の機会損失防止（film_watch）のテスト。
架空データは data_kind='テスト用架空データ'。手動確認フィクスチャは data_kind='手動確認フィクスチャ'（自動取得した結果ではない）。
実在する人物は、社長の指定がないため登録しない（人物は架空名のみ）。"""
import datetime
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
import film_watch as F  # noqa: E402
import movie_watch as MW  # noqa: E402

CHECKED = "2026-10-18T09:00:00+09:00"
FIXTURE_KIND = "手動確認フィクスチャ"
FAKE_KIND = "テスト用架空データ"
TODAY = datetime.date(2026, 10, 18)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.st = F.Store(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class T01_Person(Base):
    def test_register_person_and_refuse_duplicate(self):
        pid = F.register_person(self.st, "テスト監督A", ["テストA監督"], FAKE_KIND)
        self.assertEqual(self.st.load("people")[pid]["data_kind"], FAKE_KIND)
        with self.assertRaises(ValueError):
            F.register_person(self.st, "テスト監督Ａ", [], FAKE_KIND)   # 表記揺れ


class T02_Work(Base):
    def test_register_work(self):
        wid = F.register_work(self.st, "テスト作品X", FAKE_KIND, "https://example.test/w", None)
        self.assertEqual(self.st.load("works")[wid]["title"], "テスト作品X")


class T03_Link(Base):
    def test_link_person_to_work_with_role(self):
        pid = F.register_person(self.st, "テスト脚本家B", [], FAKE_KIND)
        wid = F.register_work(self.st, "テスト作品Y", FAKE_KIND, "", None)
        F.link_credit(self.st, wid, pid, "脚本")
        self.assertEqual(self.st.load("works")[wid]["credits"], [{"person_id": pid, "role": "脚本"}])
        with self.assertRaises(ValueError):
            F.link_credit(self.st, wid, pid, "不明な役割")


class T04_NewEvent(Base):
    def test_new_event_recorded_with_source_and_time(self):
        wid = F.register_work(self.st, "テスト作品Z", FAKE_KIND, "", None)
        eid, res = F.record_event(self.st, wid, "企画発表", "テスト発表", "https://example.test/z", CHECKED, FAKE_KIND)
        self.assertEqual(res, "new")
        e = self.st.load("events")[eid]
        self.assertEqual((e["source_url"], e["checked_at"]), ("https://example.test/z", CHECKED))


class T05_ReleaseChange(Base):
    def test_release_change_keeps_before_and_after(self):
        # 手動確認フィクスチャ：公式ページに記載された公開日（2027-04-29）の文言から抽出
        text = "映画『沖縄コンフィデンシャル』映画化＆2027年4月29日（木・祝）公開決定!!"
        self.assertEqual(F.extract_release_phrase(text), "2027-04-29")
        wid = F.register_work(self.st, "沖縄コンフィデンシャル（テスト照合）", FIXTURE_KIND, "https://www.toei.co.jp/entertainment/news/detail/1250019_3483.html", None)
        F.record_event(self.st, wid, "公開決定", "2027-04-29", "https://www.toei.co.jp/entertainment/news/detail/1250019_3483.html", CHECKED, FIXTURE_KIND)
        F.record_event(self.st, wid, "公開日変更", "2027-05-06", "https://example.test/change", "2026-11-01T09:00:00+09:00", FIXTURE_KIND)
        hist = [h for h in self.st.load("history") if h["field"] == "公開日"]
        self.assertEqual([(h["before"], h["after"]) for h in hist], [(None, "2027-04-29"), ("2027-04-29", "2027-05-06")])
        self.assertEqual(len(self.st.load("works")), 1)   # 新規作品として重複登録していない


class T06_NoDuplicate(Base):
    def test_same_event_twice_is_not_duplicated(self):
        wid = F.register_work(self.st, "テスト作品D", FAKE_KIND, "", None)
        a, r1 = F.record_event(self.st, wid, "公開決定", "2027-01-10", "u", CHECKED, FAKE_KIND)
        b, r2 = F.record_event(self.st, wid, "公開決定", "2027-01-10", "u", "2026-10-19T09:00:00+09:00", FAKE_KIND)
        self.assertEqual((r1, r2), ("new", "duplicate"))
        self.assertEqual(len(self.st.load("events")), 1)
        self.assertEqual(len(self.st.load("history")), 1)


class T07_FailureKeeps(Base):
    def test_failure_and_empty_do_not_remove(self):
        wid = F.register_work(self.st, "テスト作品E", FAKE_KIND, "", None)
        F.record_event(self.st, wid, "公開決定", "2027-02-01", "u", CHECKED, FAKE_KIND)
        before = self.st.load("events")
        self.assertFalse(F.safe_ingest(self.st, False, [{"title": "テスト作品E", "type": "公開決定", "value": "2027-03-01"}], CHECKED, FAKE_KIND, "u")["applied"])
        self.assertFalse(F.safe_ingest(self.st, True, [], CHECKED, FAKE_KIND, "u")["applied"])
        self.assertEqual(self.st.load("events"), before)


class T08_DateTimezone(Base):
    def test_naive_datetime_refused_aware_kept(self):
        with self.assertRaises(ValueError):
            F.parse_when("2026-10-20T10:00:00")
        v, prec = F.parse_when("2026-10-20T10:00:00+09:00")
        self.assertEqual((v, prec), ("2026-10-20T10:00:00+09:00", "datetime"))
        self.assertEqual(F.parse_when("2026-10-20"), ("2026-10-20", "day"))
        self.assertEqual(F.parse_when(None), (None, "unknown"))


class T09_Deadlines(Base):
    def test_ticket_deadline_extracted_and_window_applied(self):
        wid = F.register_work(self.st, "テスト作品F", FAKE_KIND, "", None)
        F.record_event(self.st, wid, "チケット締切", "2026-10-25", "u", CHECKED, FAKE_KIND)
        F.record_event(self.st, wid, "チケット締切", "2026-12-25", "u2", CHECKED, FAKE_KIND)
        items = F.deadline_items(self.st, TODAY)
        self.assertEqual([i["date"] for i in items], ["2026-10-25"])
        self.assertEqual(items[0]["days_left"], 7)


class T10_CorruptRefused(Base):
    def test_broken_json_is_refused_not_overwritten(self):
        os.makedirs(self.dir, exist_ok=True)
        p = self.st.path("events")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{壊れた")
        with self.assertRaises(F.CorruptState):
            self.st.load("events")
        with open(p, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{壊れた")   # 上書きされていない


class T11_SourceAndTime(Base):
    def test_source_url_and_checked_time_saved(self):
        wid = F.register_work(self.st, "テスト作品G", FIXTURE_KIND, "https://www.toei.co.jp/x", None)
        eid, _ = F.record_event(self.st, wid, "公開決定", "2027-04-29", "https://www.toei.co.jp/x", CHECKED, FIXTURE_KIND)
        e = self.st.load("events")[eid]
        self.assertEqual(e["source_url"], "https://www.toei.co.jp/x")
        self.assertEqual(e["checked_at"], CHECKED)
        self.assertEqual(e["data_kind"], FIXTURE_KIND)


class T12_FakeNotMarkedReal(Base):
    def test_invalid_kind_refused_and_fake_kind_recorded(self):
        with self.assertRaises(ValueError):
            F.register_person(self.st, "テスト人物", [], "実データ?")
        pid = F.register_person(self.st, "テスト人物", [], FAKE_KIND)
        self.assertEqual(self.st.load("people")[pid]["data_kind"], FAKE_KIND)


class T13_NotificationLedger(Base):
    def test_candidates_once_and_ledger_dedup(self):
        wid = F.register_work(self.st, "テスト作品H", FAKE_KIND, "", None)
        F.record_event(self.st, wid, "公開決定", "2027-01-10", "u", CHECKED, FAKE_KIND)
        F.record_event(self.st, wid, "公開日変更", "2027-01-20", "u", "2026-10-19T09:00:00+09:00", FAKE_KIND)
        c = F.notification_candidates(self.st, "2000-01-01")
        self.assertEqual(len(c), 2)          # 公開決定（初回の記録）と公開日変更の2件
        F.mark_candidates_seen(self.st, c)
        self.assertEqual(F.notification_candidates(self.st, "2000-01-01"), [])


class T14_ExistingMatchingReused(Base):
    def test_existing_matcher_matches_registered_person_only(self):
        F.register_person(self.st, "テスト俳優C", ["テストC"], FAKE_KIND)
        reg = {"people": [{"id": p["id"], "names": [p["name"]] + p["aliases"]} for p in self.st.load("people").values()]}
        m, held = MW.match_people({"cast": ["テスト俳優C"]}, reg["people"])
        self.assertEqual(m[0]["person_id"], list(self.st.load("people"))[0])
        m2, _ = MW.match_people({"cast": ["未登録の人"]}, reg["people"])
        self.assertEqual(m2, [])


class T15_ExtractRelease(unittest.TestCase):
    def test_phrases(self):
        self.assertEqual(F.extract_release_phrase("2026年11月20日（金）公開決定！"), "2026-11-20")
        self.assertEqual(F.extract_release_phrase("2027年10月15日（金）公開決定！！"), "2027-10-15")
        self.assertIsNone(F.extract_release_phrase("公開日は未定です"))


if __name__ == "__main__":
    unittest.main()
