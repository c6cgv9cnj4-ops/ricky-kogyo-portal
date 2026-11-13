import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import artist_watch as AW  # noqa: E402
import artist_watch_run as RUN  # noqa: E402

NOW = datetime.datetime(2026, 10, 18, 12, 0, tzinfo=AW.JST)
SINCE = (NOW - datetime.timedelta(hours=24)).isoformat(timespec="seconds")
CFG = {
    "artists": [{"name": "テスト・アーティスト", "official_url": "https://example.com/a", "aliases": ["TEST ARTIST"]}],
    "sources": [{"id": "src_official", "name": "公式テスト", "url": "https://example.com/tour", "kind": "公式サイト",
                 "terms_status": "確認済み"}],
}


def row(name="テスト・アーティスト", venue="東京会場", event="2026-11-20", window_end="2026-10-19T23:59:00+09:00",
        status="公演発表", url="https://example.com/tour/1"):
    return {"artist_name": name, "venue": venue, "name": "Tour 2026", "event_date": event,
            "sale_windows": [{"type": "先行受付", "start": "2026-10-01T10:00:00+09:00", "end": window_end}],
            "status": status, "evidence_url": url}


class ArtistRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AW.Store(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_T01_config_registers_once(self):
        first = RUN.apply_config(self.store, CFG, now=NOW)
        second = RUN.apply_config(self.store, CFG, now=NOW)
        self.assertEqual(first, {"artists": 1, "sources": 1})
        self.assertEqual(second, {"artists": 0, "sources": 0})
        self.assertTrue(self.store.load("sources")["src_official"]["auto_fetch_allowed"])

    def test_T02_new_batch_gives_deadline_candidate_not_change(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        r = RUN.run(self.store, "src_official", [row()], SINCE, now=NOW)
        self.assertTrue(r["ingest"]["applied"])
        self.assertEqual(r["ingest"]["new"], 1)
        reasons = [c["reason"] for c in r["candidates"]]
        self.assertIn("締切が近い", reasons)            # 締切 2026-10-19 23:59 は 36 時間後
        self.assertFalse(any(c["reason"].startswith("変更") for c in r["candidates"]))

    def test_T03_same_batch_twice_no_duplicate_candidates(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src_official", [row()], SINCE, now=NOW)
        again = RUN.run(self.store, "src_official", [row()], SINCE, now=NOW)
        self.assertEqual(again["ingest"]["same"], 1)
        self.assertEqual(again["candidates"], [])       # 台帳で既に記録済み

    def test_T04_changed_date_is_change_candidate(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src_official", [row()], SINCE, now=NOW)
        later = NOW + datetime.timedelta(minutes=5)
        r = RUN.run(self.store, "src_official", [row(event="2026-11-21")], SINCE, now=later)
        self.assertEqual(r["ingest"]["changed"], 1)
        changes = [c for c in r["candidates"] if c["reason"].startswith("変更")]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["detail"]["before"], "2026-11-20")
        self.assertEqual(changes[0]["detail"]["after"], "2026-11-21")

    def test_T05_empty_batch_keeps_existing(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src_official", [row()], SINCE, now=NOW)
        before = self.store.load("events")
        r = RUN.run(self.store, "src_official", [], SINCE, now=NOW)
        self.assertFalse(r["ingest"]["applied"])
        self.assertEqual(self.store.load("events"), before)

    def test_T06_unknown_artist_reported_not_ingested(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        r = RUN.run(self.store, "src_official", [row(name="未登録の人")], SINCE, now=NOW)
        self.assertEqual(r["unknown_artists"], ["未登録の人"])
        self.assertEqual(self.store.load("events"), {})

    def test_T07_naive_datetime_rejected(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        with self.assertRaises(ValueError):
            RUN.run(self.store, "src_official", [row(window_end="2026-10-19T23:59:00")], SINCE, now=NOW)

    def test_T08_no_args_does_nothing(self):
        d = tempfile.mkdtemp()
        self.assertEqual(RUN.main(["--dir", d]), 0)
        self.assertEqual(os.listdir(d), [])

    def test_T09_batch_without_source_refused(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "batch.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump([row()], f)
        self.assertEqual(RUN.main(["--dir", d, "--batch", p]), 1)

    def test_T10_failed_fetch_keeps_existing_and_no_cancel(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src_official", [row()], SINCE, now=NOW)
        before = self.store.load("events")
        r = RUN.run(self.store, "src_official", [], SINCE, now=NOW, source_status="timeout")
        self.assertFalse(r["ingest"]["applied"])
        self.assertEqual(self.store.load("events"), before)   # 削除も中止扱いもしない
        self.assertFalse(any(c["reason"] == "中止" for c in r["candidates"]))

    def test_T11_fetch_time_and_source_recorded(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src_official", [row()], SINCE, now=NOW)
        rec = next(iter(self.store.load("events").values()))
        self.assertEqual(rec["source_id"], "src_official")
        self.assertEqual(rec["fetched_at"], NOW.isoformat(timespec="seconds"))
        self.assertEqual(rec["evidence_url"], "https://example.com/tour/1")

    def test_T12_empty_source_failure_with_items_not_applied(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        r = RUN.run(self.store, "src_official", [row()], SINCE, now=NOW, source_status="timeout")
        self.assertFalse(r["ingest"]["applied"])
        self.assertEqual(self.store.load("events"), {})

    def test_T13_fetch_statuses_all_preserve_and_are_labelled(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src_official", [row()], SINCE, now=NOW)
        before = self.store.load("events")
        for st, label in (("timeout", "タイムアウト"), ("http_error", "HTTPエラー"),
                          ("empty", "空レスポンス"), ("parse_error", "解析失敗")):
            r = RUN.run(self.store, "src_official", [], SINCE, now=NOW, source_status=st)
            self.assertFalse(r["ingest"]["applied"])
            self.assertIn(label, r["ingest"]["reason"])
            self.assertEqual(self.store.load("events"), before)
        with self.assertRaises(ValueError):
            RUN.run(self.store, "src_official", [], SINCE, now=NOW, source_status="unknown")

    def test_T14_retry_after_failure_keeps_history_consistent(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src_official", [row()], SINCE, now=NOW)
        RUN.run(self.store, "src_official", [], SINCE, now=NOW, source_status="timeout")
        hist_before = len(self.store.load("history"))
        r = RUN.run(self.store, "src_official", [row()], SINCE, now=NOW + datetime.timedelta(minutes=10))
        self.assertEqual(r["ingest"]["same"], 1)
        self.assertEqual(len(self.store.load("history")), hist_before)  # 再取得で履歴を増やさない

    def test_T15_same_url_different_performance_not_merged(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        r = RUN.run(self.store, "src_official", [row(venue="東京会場"), row(venue="大阪会場")], SINCE, now=NOW)
        self.assertEqual(r["ingest"]["new"], 2)
        self.assertEqual(len(self.store.load("events")), 2)

    def test_T16_announced_at_separate_from_fetched_at(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        item = row(); item["announced_at"] = None
        RUN.run(self.store, "src_official", [item], SINCE, now=NOW)
        rec = next(iter(self.store.load("events").values()))
        self.assertIsNone(rec["announced_at"])               # 公表日時は不明のまま（推測しない）
        self.assertEqual(rec["fetched_at"], NOW.isoformat(timespec="seconds"))

    def test_T17_status_recovery_shows_real_change_only(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src_official", [row()], SINCE, now=NOW)
        RUN.run(self.store, "src_official", [], SINCE, now=NOW, source_status="http_error")
        later = NOW + datetime.timedelta(minutes=30)
        r = RUN.run(self.store, "src_official", [row(status="中止")], SINCE, now=later)
        self.assertEqual(r["ingest"]["changed"], 1)
        changes = [c for c in r["candidates"] if c["reason"].startswith("変更")]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["detail"]["after"], "中止")  # 実際に中止と書かれた時だけ変更になる

    # ---- 監査で追加：アダプタ・許可制の取得・訂正の区別 ----
    def test_T18_json_adapter_round_trip_and_new_entry(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        text = json.dumps([row()], ensure_ascii=False)
        r = RUN.ingest_source(self.store, "src_official", "json_rows", text, SINCE, now=NOW)
        self.assertEqual(r["ingest"]["new"], 1)
        self.assertIn("src_official", {x["source_id"] for x in self.store.load("events").values()})

    def test_T19_adapter_rejects_missing_required_field(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        bad = json.dumps([{"artist_name": "テスト・アーティスト", "venue": "東京会場"}])   # name がない
        with self.assertRaises(ValueError):
            RUN.ingest_source(self.store, "src_official", "json_rows", bad, SINCE, now=NOW)
        self.assertEqual(self.store.load("events"), {})

    def test_T20_unknown_adapter_refused(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        with self.assertRaises(ValueError):
            RUN.ingest_source(self.store, "src_official", "html_nonexistent", "[]", SINCE, now=NOW)

    def test_T21_fetch_refused_without_permission_and_fetch_not_called(self):
        cfg = json.loads(json.dumps(CFG))
        cfg["sources"][0]["terms_status"] = "未確認"
        RUN.apply_config(self.store, cfg, now=NOW)
        called = []
        def fake(url):
            called.append(url)
            return "ok", "[]"
        with self.assertRaises(PermissionError):
            RUN.fetch_source(self.store, "src_official", fake)
        self.assertEqual(called, [])          # 許可が無ければ、取得関数は呼ばれない

    def test_T22_fetch_allowed_timeout_keeps_data(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.ingest_source(self.store, "src_official", "json_rows", json.dumps([row()]), SINCE, now=NOW)
        before = self.store.load("events")
        status, _ = RUN.fetch_source(self.store, "src_official", lambda url: ("timeout", None))
        r = RUN.ingest_source(self.store, "src_official", "json_rows", "", SINCE, now=NOW, status=status)
        self.assertFalse(r["ingest"]["applied"])
        self.assertEqual(self.store.load("events"), before)

    def test_T23_correction_is_a_distinct_notice(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src_official", [row()], SINCE, now=NOW)
        parent = next(iter(self.store.load("events")))
        nid, res = AW.add_notice(self.store, parent, "訂正", {"field": "name", "after": "正しい表記"}, now=NOW)
        self.assertEqual(res, "new")
        self.assertEqual(self.store.load("events")[nid]["kind"], "notice")

    def test_T24_notice_in_batch_creates_candidate_in_same_run(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src_official", [row()], SINCE, now=NOW)
        item = row(); item["notices"] = [{"type": "延期", "detail": {"new_date": "2027-02-01"}, "evidence_url": "https://example.com/n"}]
        later = NOW + datetime.timedelta(minutes=5)
        r = RUN.run(self.store, "src_official", [item], SINCE, now=later)
        self.assertEqual(r["notices_added"], 1)
        self.assertTrue(any(c["reason"] == "変更: 延期" for c in r["candidates"]))

    def test_T25_notice_not_applied_on_failure(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src_official", [row()], SINCE, now=NOW)
        before = self.store.load("events")
        item = row(); item["notices"] = [{"type": "中止", "detail": {}}]
        r = RUN.run(self.store, "src_official", [item], SINCE, now=NOW, source_status="timeout")
        self.assertEqual(self.store.load("events"), before)
        self.assertNotIn("notices_added", r)

    # ---- 起点（since）の保存・復元 ----
    def _anchor_store(self):
        return AW.Store(self.tmp.name)

    def test_T26_anchor_survives_restart_and_reports_only_new_change(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src_official", [row()], now=NOW)              # 初回
        t1 = NOW + datetime.timedelta(minutes=30)
        b = row(); b["sale_windows"] = [{"type": "一般発売", "start": "2026-10-22T10:00:00+09:00", "end": "2026-10-23T23:59:00+09:00"}]
        RUN.run(self.store, "src_official", [b], now=t1)                   # 変更（起点は t1）
        restarted = self._anchor_store()                                   # 再起動を想定して読み直す
        self.assertEqual(RUN.get_anchor(restarted, "src_official"), t1.isoformat(timespec="seconds"))
        r = RUN.run(restarted, "src_official", [b], now=t1 + datetime.timedelta(minutes=5))   # 同一データ
        self.assertEqual(r["candidates"], [])                             # 既に通知済みの変更は出ない

    def test_T27_first_run_without_anchor_starts_from_beginning(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        self.assertEqual(RUN.effective_since(self.store), RUN.BEGINNING)   # 起点が無ければ取り逃がさない
        RUN.run(self.store, "src_official", [row()], now=NOW)
        self.assertEqual(RUN.effective_since(self.store), NOW.isoformat(timespec="seconds"))

    def test_T28_failed_fetch_does_not_advance_anchor(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src_official", [row()], now=NOW)
        anchor = RUN.get_anchor(self.store, "src_official")
        RUN.run(self.store, "src_official", [], now=NOW + datetime.timedelta(hours=1), source_status="timeout")
        self.assertEqual(RUN.get_anchor(self.store, "src_official"), anchor)

    def test_T29_change_during_failed_window_is_reported_after_recovery(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src_official", [row()], now=NOW)                  # 起点 = NOW
        b = row(); b["sale_windows"] = [{"type": "一般発売", "start": "2026-10-22T10:00:00+09:00", "end": "2026-10-23T23:59:00+09:00"}]
        RUN.run(self.store, "src_official", [], now=NOW + datetime.timedelta(minutes=10), source_status="http_error")
        r = RUN.run(self.store, "src_official", [b], now=NOW + datetime.timedelta(hours=2))   # 復旧後
        self.assertTrue(any(c["reason"] == "変更: sale_windows" for c in r["candidates"]))

    def test_T30_multi_source_uses_oldest_anchor(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        AW.register_source(self.store, "src_other", "別の情報源", "https://example.invalid/other", "主催者", terms_status="確認済み")
        RUN.run(self.store, "src_official", [row()], now=NOW)                  # src_other は未取込
        self.assertEqual(RUN.effective_since(self.store), RUN.BEGINNING)     # 一部だけ進めても止めない
        RUN.run(self.store, "src_other", [dict(row(), name="Other")], now=NOW + datetime.timedelta(hours=1))   # 公演名だけ変える
        self.assertEqual(RUN.effective_since(self.store), RUN.get_anchor(self.store, "src_official"))

    # ---- 停止後の大量候補：集約・重要の非破棄・上限と持ち越し・部分失敗 ----
    def _outage(self, days, n=50):
        RUN.apply_config(self.store, CFG, now=NOW)
        rows = [dict(row(), venue=f"会場{i}", name=f"Tour {i}", event_date="2027-03-01") for i in range(n)]
        RUN.run(self.store, "src_official", rows, now=NOW)
        aid = RUN.find_artist_id(self.store, "テスト・アーティスト")
        for day in range(1, days + 1):
            for i in range(n):
                AW.upsert_performance(self.store, aid, f"会場{i}", f"Tour {i}", event_date="2027-03-01",
                                      sale_windows=[{"type": "一般発売", "start": f"2026-11-{10 + day % 15:02d}T10:00:00+09:00",
                                                     "end": f"2026-11-{11 + day % 15:02d}T23:59:00+09:00"}],
                                      source_id="src_official", now=NOW + datetime.timedelta(days=day))
        return aid, n

    def test_T31_outage_grouped_to_final_state_and_expired_not_notified(self):
        self._outage(30)
        plan = AW.plan_candidates(self.store, RUN.BEGINNING, now=NOW + datetime.timedelta(days=31))
        self.assertEqual(len(plan["candidates"]), 50)            # 30日分の変更が、公演ごとの最終状態に集約される
        self.assertEqual(plan["overflow"], [])
        self.assertFalse(any(c["reason"] == "締切超過" for c in plan["candidates"]))

    def test_T32_important_never_capped(self):
        self._outage(1, n=60)
        plan = AW.plan_candidates(self.store, RUN.BEGINNING, now=NOW + datetime.timedelta(days=2), normal_cap=5)
        self.assertEqual(len(plan["candidates"]), 60)            # 上限があっても重要変更は全件
        self.assertEqual(plan["overflow"], [])

    def test_T33_normal_overflow_carried_to_next_runs_without_loss(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src_official", [row()], now=NOW)
        hist = self.store.load("history")
        for i in range(30):
            hist.append({"at": (NOW + datetime.timedelta(minutes=i + 1)).isoformat(timespec="seconds"), "id": f"x{i}", "field": "note",
                         "before": None, "after": i, "evidence_url": ""})
        self.store.save("history", hist)
        seen = []
        for k in range(3):
            r = RUN.run(self.store, "src_official", [row()], now=NOW + datetime.timedelta(hours=1 + k))
            seen += [c["key"] for c in r["candidates"] if c["reason"] == "変更: note"]
        self.assertEqual(len(seen), 30)
        self.assertEqual(len(set(seen)), 30)                   # 重複なし・取り逃がしなし

    def test_T34_partial_source_failure_keeps_other_source_changes(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        AW.register_source(self.store, "src_b", "B", "https://example.invalid/b", "主催者", terms_status="確認済み")
        RUN.run(self.store, "src_official", [row()], now=NOW)
        RUN.run(self.store, "src_b", [dict(row(), name="B")], now=NOW)
        RUN.run(self.store, "src_official", [row()], now=NOW + datetime.timedelta(hours=1))      # A は正常に進む
        RUN.run(self.store, "src_b", [], now=NOW + datetime.timedelta(hours=1), source_status="timeout")   # B は止まったまま
        b = dict(row(), name="B"); b["sale_windows"] = [{"type": "一般発売", "start": "2026-10-22T10:00:00+09:00", "end": "2026-10-23T23:59:00+09:00"}]
        r = RUN.run(self.store, "src_b", [b], now=NOW + datetime.timedelta(hours=3))                  # B が復旧し、停止中の変更を取り込む
        self.assertTrue(any(c["reason"] == "変更: sale_windows" for c in r["candidates"]))           # B の変更は取り逃がさない
        self.assertEqual(RUN.effective_since(self.store), RUN.get_anchor(self.store, "src_official"))   # 起点は最も古い情報源の時刻
    # ---- 公演同一性・持ち越しの検証（監査）----
    def test_T35_venue_change_separate_without_key_and_same_with_source_key(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src_official", [dict(row(), venue="会場A")], now=NOW)
        RUN.run(self.store, "src_official", [dict(row(), venue="会場B")], now=NOW + datetime.timedelta(hours=1))
        self.assertEqual(len([e for e in self.store.load("events").values() if e.get("kind") == "performance"]), 2)  # キー無し：統合しない
        tb = tempfile.mkdtemp(); st2 = AW.Store(tb)
        RUN.apply_config(st2, CFG, now=NOW)
        RUN.run(st2, "src_official", [dict(row(), venue="会場A", source_performance_id="K-1")], now=NOW)
        r = RUN.run(st2, "src_official", [dict(row(), venue="会場B", source_performance_id="K-1")], now=NOW + datetime.timedelta(hours=1))
        self.assertEqual(r["ingest"]["changed"], 1)                                               # キー有り：同一公演の変更
        self.assertEqual(len([e for e in st2.load("events").values() if e.get("kind") == "performance"]), 1)

    def test_T36_window_shift_does_not_renotify_delivered_change(self):
        self._outage(1, n=3)
        first = AW.plan_candidates(self.store, RUN.BEGINNING, now=NOW + datetime.timedelta(days=2))
        for c in first["candidates"]:
            AW.mark_dry_run_seen(self.store, [c])
        again = AW.plan_candidates(self.store, RUN.BEGINNING, now=NOW + datetime.timedelta(days=2))   # 起点を最初に戻しても
        self.assertEqual(again["candidates"], [])

    def test_T37_mixed_important_normal_partial_failure_and_restart(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        AW.register_source(self.store, "src_b", "B", "https://example.invalid/b", "主催者", terms_status="確認済み")
        self._outage(1, n=50)                                                                   # 重要：公演日・発売期限 50件
        hist = self.store.load("history")
        for i in range(30):                                                                     # 通常：note 30件（停止中）
            hist.append({"at": (NOW + datetime.timedelta(hours=2, minutes=i)).isoformat(timespec="seconds"),
                         "id": f"n{i}", "field": "note", "before": None, "after": i, "evidence_url": ""})
        self.store.save("history", hist)
        RUN.run(self.store, "src_b", [], now=NOW + datetime.timedelta(hours=3), source_status="timeout")   # B の失敗
        delivered = []
        for k in range(4):                                                                      # 再起動を想定して毎回読み直す
            st = AW.Store(self.tmp.name)
            r = RUN.run(st, "src_official", [dict(row(), venue=f"会場{i}", name=f"Tour {i}", event_date="2027-03-01") for i in range(50)],
                        now=NOW + datetime.timedelta(hours=4 + k))
            delivered += [c["key"] for c in r["candidates"]]
        important = [k for k in delivered if "sale_windows" in k or "event_date" in k]
        normal = [k for k in delivered if "|note|" in k]
        self.assertEqual(len(important), 50)           # 重要は全件（1回目でまとめて）
        self.assertEqual(len(normal), 30)              # 通常は持ち越しで取りこぼしなし
        self.assertEqual(len(set(delivered)), len(delivered))   # 重複なし

if __name__ == "__main__":
    unittest.main()
