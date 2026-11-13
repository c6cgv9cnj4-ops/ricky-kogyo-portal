"""海外アーティスト機会損失防止の最小構成のテスト。架空のテスト専用データのみ使用する（実在のアーティストは登録しない）。"""
import datetime
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import artist_watch as W  # noqa: E402

JST = W.JST
NOW = datetime.datetime(2026, 10, 18, 9, 0, tzinfo=JST)
TEST_ARTIST = "テストアーティストA"


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = W.Store(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class T01_EmptyStart(Base):
    def test_empty_registry_loads_and_has_no_items(self):
        self.assertEqual(self.store.load("artists"), {})
        self.assertEqual(W.deadline_items(self.store, NOW), [])
        self.assertEqual(W.notification_candidates(self.store, "2000-01-01T00:00:00+09:00", NOW), [])


class T02_RegisterArtist(Base):
    def test_register_and_persist(self):
        aid = W.register_artist(self.store, TEST_ARTIST, "https://example.test/a", ["テストA"], NOW)
        a = self.store.load("artists")[aid]
        self.assertEqual(a["name"], TEST_ARTIST)
        self.assertEqual(a["official_url"], "https://example.test/a")
        self.assertEqual(a["registered_at"], NOW.isoformat(timespec="seconds"))


class T03_DuplicateArtist(Base):
    def test_duplicate_is_refused_including_normalized_and_alias(self):
        W.register_artist(self.store, TEST_ARTIST, aliases=["テストA"], now=NOW)
        with self.assertRaises(ValueError):
            W.register_artist(self.store, "テスト  アーティストＡ", now=NOW)   # 表記揺れ
        with self.assertRaises(ValueError):
            W.register_artist(self.store, "テストA", now=NOW)                 # 別名と一致
        self.assertEqual(len(self.store.load("artists")), 1)


class T04_RegisterPerformance(Base):
    def test_new_performance_is_recorded_with_history(self):
        aid = W.register_artist(self.store, TEST_ARTIST, now=NOW)
        rid, res = W.upsert_performance(self.store, aid, "テスト会場", "テスト公演", event_date="2026-12-01",
                                        now=NOW, evidence_url="https://example.test/p1")
        self.assertEqual(res, "new")
        self.assertEqual(self.store.load("history")[-1]["field"], "新規")


class T05_NewDetected(Base):
    def test_new_item_via_batch(self):
        aid = W.register_artist(self.store, TEST_ARTIST, now=NOW)
        W.register_source(self.store, "src1", "テスト情報源", "https://example.test/s1", "公式サイト", terms_status="確認済み")
        r = W.ingest_batch(self.store, "src1", [{"artist_id": aid, "venue": "テスト会場", "name": "テスト公演", "event_date": "2026-12-01"}], True, NOW)
        self.assertEqual(r["new"], 1)


class T06_DateChangeDetected(Base):
    def test_date_and_venue_change_recorded_with_before_after(self):
        aid = W.register_artist(self.store, TEST_ARTIST, now=NOW)
        rid, _ = W.upsert_performance(self.store, aid, "テスト会場", "テスト公演", event_date="2026-12-01", now=NOW)
        _, res = W.upsert_performance(self.store, aid, "テスト会場", "テスト公演", event_date="2026-12-05", now=NOW, evidence_url="https://example.test/x")
        self.assertEqual(res, "changed")
        h = [x for x in self.store.load("history") if x["field"] == "event_date"][0]
        self.assertEqual((h["before"], h["after"]), ("2026-12-01", "2026-12-05"))
        self.assertEqual(h["evidence_url"], "https://example.test/x")


class T07_DeadlineStates(Base):
    def test_open_soon_and_overdue(self):
        aid = W.register_artist(self.store, TEST_ARTIST, now=NOW)
        W.upsert_performance(self.store, aid, "会場A", "公演A", sale_windows=[
            {"type": "先行受付", "start": "2026-10-19T10:00:00+09:00", "end": "2026-10-25T23:59:00+09:00"}], now=NOW)
        W.upsert_performance(self.store, aid, "会場B", "公演B", sale_windows=[
            {"type": "一般発売", "start": "2026-10-01T10:00:00+09:00", "end": "2026-10-10T23:59:00+09:00"}], now=NOW)
        reasons = {(d["name"], d["reason"]) for d in W.deadline_items(self.store, NOW)}
        self.assertIn(("公演A", "受付開始が近い"), reasons)
        self.assertIn(("公演B", "締切超過"), reasons)


class T08_NoticeIsSeparate(Base):
    def test_cancel_and_postpone_are_separate_records_linked_to_parent(self):
        aid = W.register_artist(self.store, TEST_ARTIST, now=NOW)
        rid, _ = W.upsert_performance(self.store, aid, "会場A", "公演A", event_date="2026-12-01", now=NOW)
        nid1, _ = W.add_notice(self.store, rid, "中止", {"reason": "テスト理由"}, now=NOW)
        nid2, _ = W.add_notice(self.store, rid, "延期", {"to": "2027-01-10"}, now=NOW)
        ev = self.store.load("events")
        self.assertNotEqual(nid1, nid2)
        self.assertEqual(ev[nid1]["kind"], "notice")
        self.assertEqual(ev[nid1]["parent_id"], rid)
        self.assertEqual(ev[rid]["kind"], "performance")   # 親は変わらない


class T09_NoDuplicateNotification(Base):
    def test_reprocessing_same_input_creates_no_new_changes_or_candidates(self):
        aid = W.register_artist(self.store, TEST_ARTIST, now=NOW)
        item = {"artist_id": aid, "venue": "会場A", "name": "公演A", "event_date": "2026-12-01",
                "sale_windows": [{"type": "一般発売", "start": "2026-10-20T10:00:00+09:00", "end": "2026-10-30T23:59:00+09:00"}]}
        self.store.save("sources", {"s": {"id": "s", "auto_fetch_allowed": True}})
        W.ingest_batch(self.store, "s", [item], True, NOW)
        before = len(self.store.load("history"))
        W.ingest_batch(self.store, "s", [item], True, NOW)
        self.assertEqual(len(self.store.load("history")), before)
        cands = W.notification_candidates(self.store, "2000-01-01T00:00:00+09:00", NOW)
        W.mark_dry_run_seen(self.store, cands)
        self.assertEqual(W.notification_candidates(self.store, "2000-01-01T00:00:00+09:00", NOW), [])


class T10_FailureKeepsData(Base):
    def test_failure_and_empty_batch_do_not_remove_or_change(self):
        aid = W.register_artist(self.store, TEST_ARTIST, now=NOW)
        self.store.save("sources", {"s": {"id": "s", "auto_fetch_allowed": True}})
        W.ingest_batch(self.store, "s", [{"artist_id": aid, "venue": "会場A", "name": "公演A", "event_date": "2026-12-01"}], True, NOW)
        before = self.store.load("events")
        r1 = W.ingest_batch(self.store, "s", [], True, NOW)            # 0件
        r2 = W.ingest_batch(self.store, "s", [{"artist_id": aid, "venue": "会場A", "name": "公演A", "event_date": "2026-12-09"}], False, NOW)  # 取得失敗
        self.assertFalse(r1["applied"])
        self.assertFalse(r2["applied"])
        self.assertEqual(self.store.load("events"), before)


class T11_HistoryKept(Base):
    def test_history_is_append_only_across_changes(self):
        aid = W.register_artist(self.store, TEST_ARTIST, now=NOW)
        W.upsert_performance(self.store, aid, "会場A", "公演A", event_date="2026-12-01", now=NOW)
        W.upsert_performance(self.store, aid, "会場A", "公演A", event_date="2026-12-02", now=NOW)
        W.upsert_performance(self.store, aid, "会場A", "公演A", event_date="2026-12-03", now=NOW)
        fields = [h["field"] for h in self.store.load("history")]
        self.assertEqual(fields.count("event_date"), 2)
        self.assertEqual(fields[0], "新規")


class T12_DryRunCandidates(Base):
    def test_candidate_has_reason_and_no_send(self):
        aid = W.register_artist(self.store, TEST_ARTIST, now=NOW)
        W.upsert_performance(self.store, aid, "会場A", "公演A", sale_windows=[
            {"type": "一般発売", "start": "2026-10-18T20:00:00+09:00", "end": "2026-10-30T23:59:00+09:00"}], now=NOW)
        cands = W.notification_candidates(self.store, "2000-01-01T00:00:00+09:00", NOW)
        self.assertTrue(any(c["reason"] == "受付開始が近い" for c in cands))
        self.assertTrue(all("key" in c and "reason" in c for c in cands))


class T_Validation(Base):
    def test_timezone_required_and_bad_types_refused(self):
        aid = W.register_artist(self.store, TEST_ARTIST, now=NOW)
        with self.assertRaises(ValueError):
            W.upsert_performance(self.store, aid, "会場A", "公演A", sale_windows=[{"type": "一般発売", "start": "2026-10-20T10:00:00"}], now=NOW)
        with self.assertRaises(ValueError):
            W.upsert_performance(self.store, aid, "会場A", "公演A", sale_windows=[{"type": "謎の受付", "start": "2026-10-20"}], now=NOW)
        with self.assertRaises(ValueError):
            W.add_notice(self.store, "なし", "中止", {}, now=NOW)

    def test_unknown_sale_dates_are_not_invented(self):
        aid = W.register_artist(self.store, TEST_ARTIST, now=NOW)
        W.upsert_performance(self.store, aid, "会場A", "公演A", sale_windows=[{"type": "先行受付", "start": None, "end": "2026-10-25"}], now=NOW)
        items = W.deadline_items(self.store, NOW)
        self.assertTrue(all(i["reason"] != "受付開始が近い" for i in items))

    def test_unregistered_source_not_auto_fetched(self):
        W.register_source(self.store, "s2", "テスト情報源", "https://example.test/s2", "公式サイト", terms_status="未確認")
        self.assertFalse(self.store.load("sources")["s2"]["auto_fetch_allowed"])


if __name__ == "__main__":
    unittest.main()
