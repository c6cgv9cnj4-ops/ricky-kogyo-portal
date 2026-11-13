"""通知候補の送信状態（生成済み／送信待ち／送信成功／送信失敗）の検証。実送信はしない（偽の送信関数）。"""
import datetime
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import artist_watch as AW  # noqa: E402
import artist_watch_run as RUN  # noqa: E402

NOW = datetime.datetime(2026, 10, 18, 12, 0, tzinfo=AW.JST)


def cand(i):
    return {"key": f"change|art|performance|v{i}|{i}", "reason": "変更: status", "id": f"p{i}",
            "detail": {"before": "公演発表", "after": "中止", "count": 1, "first_at": "x", "last_at": "x"}, "evidence_url": ""}


class FakeSender:
    """送る代わりに記録する。fail_on は失敗させる key の集合、raise_on は例外にする key の集合。"""
    def __init__(self, fail_on=(), raise_on=()):
        self.calls, self.fail_on, self.raise_on = [], set(fail_on), set(raise_on)

    def __call__(self, payload):
        self.calls.append(payload["key"])
        if payload["key"] in self.raise_on:
            raise ConnectionError("偽の通信エラー")
        return payload["key"] not in self.fail_on


class NotifyLedgerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = AW.Store(self.dir)

    def states(self):
        return {k: v["status"] for k, v in self.store.load("ledger").items()}

    def test_N01_generated_then_restart_still_pending(self):
        AW.mark_dry_run_seen(self.store, [cand(1), cand(2)], now=NOW)
        restarted = AW.Store(self.dir)                                     # 再起動
        self.assertEqual(AW.pending_sends(restarted), sorted([cand(1)["key"], cand(2)["key"]]))
        self.assertEqual(set(self.states().values()), {"生成済み"})
        sender = FakeSender()
        AW.dispatch(restarted, sender, now=NOW)
        self.assertEqual(len(sender.calls), 2)                             # 未送信は取りこぼさない

    def test_N02_success_recorded_and_not_resent(self):
        AW.mark_dry_run_seen(self.store, [cand(1)], now=NOW)
        c1 = AW.dispatch(self.store, FakeSender(), now=NOW)
        self.assertEqual(c1["sent"], 1)
        self.assertEqual(self.states()[cand(1)["key"]], "送信成功")
        sender = FakeSender()
        c2 = AW.dispatch(AW.Store(self.dir), sender, now=NOW)              # 再実行・再起動後
        self.assertEqual(sender.calls, [])                                 # 成功済みは再送しない
        self.assertEqual(c2["attempted"], 0)

    def test_N03_failure_then_retry_succeeds(self):
        AW.mark_dry_run_seen(self.store, [cand(1)], now=NOW)
        c1 = AW.dispatch(self.store, FakeSender(fail_on={cand(1)["key"]}), now=NOW)
        self.assertEqual((c1["failed"], c1["sent"]), (1, 0))
        rec = self.store.load("ledger")[cand(1)["key"]]
        self.assertEqual((rec["status"], rec["attempts"]), ("送信失敗", 1))
        c2 = AW.dispatch(AW.Store(self.dir), FakeSender(), now=NOW)        # 再試行（再起動後）
        self.assertEqual(c2["sent"], 1)
        self.assertEqual(self.store.load("ledger")[cand(1)["key"]]["status"], "送信成功")

    def test_N04_midway_failure_keeps_other_candidates_pending(self):
        cs = [cand(i) for i in range(1, 6)]
        AW.mark_dry_run_seen(self.store, cs, now=NOW)
        sender = FakeSender(fail_on={cs[1]["key"]}, raise_on={cs[2]["key"]})
        c = AW.dispatch(self.store, sender, now=NOW)
        self.assertEqual((c["sent"], c["failed"]), (3, 2))                 # 1,4,5 は成功。2は失敗、3は例外
        st = self.states()
        self.assertEqual(st[cs[1]["key"]], "送信失敗")
        self.assertEqual(st[cs[2]["key"]], "送信失敗")
        self.assertIn("偽の通信エラー", self.store.load("ledger")[cs[2]["key"]]["last_error"])
        self.assertEqual(sorted(AW.pending_sends(self.store)), sorted([cs[1]["key"], cs[2]["key"]]))

    def test_N05_duplicate_registration_is_one_entry(self):
        AW.mark_dry_run_seen(self.store, [cand(1)], now=NOW)
        AW.mark_dry_run_seen(self.store, [cand(1)], now=NOW + datetime.timedelta(minutes=5))
        self.assertEqual(len(self.store.load("ledger")), 1)
        self.assertEqual(self.store.load("ledger")[cand(1)["key"]]["generated_at"], NOW.isoformat(timespec="seconds"))

    def test_N06_crash_during_send_is_retried_not_lost(self):
        AW.mark_dry_run_seen(self.store, [cand(1)], now=NOW)
        led = self.store.load("ledger"); led[cand(1)["key"]]["status"] = "送信待ち"   # 送信中に停止した状態
        self.store.save("ledger", led)
        self.assertIn(cand(1)["key"], AW.pending_sends(AW.Store(self.dir)))            # 失わない（再送対象）
        sender = FakeSender()
        AW.dispatch(AW.Store(self.dir), sender, now=NOW)
        self.assertEqual(sender.calls, [cand(1)["key"]])                              # 送信の可能性がある分は再送（重複の可能性はある）

    def test_N07_max_attempts_then_stops_and_is_reported(self):
        AW.mark_dry_run_seen(self.store, [cand(1)], now=NOW)
        for _ in range(3):
            AW.dispatch(self.store, FakeSender(fail_on={cand(1)["key"]}), now=NOW)
        sender = FakeSender()
        c = AW.dispatch(self.store, sender, now=NOW)
        self.assertEqual(sender.calls, [])                                            # 上限後は送らない
        self.assertEqual(c["skipped_limit"], 0)                                       # 一覧から外れて停止
        self.assertEqual(self.states()[cand(1)["key"]], "送信失敗")                    # 状態は残す（要対応）

    def test_N08_run_integration_generation_then_dispatch_then_no_regeneration(self):
        cfg = {"artists": [{"name": "SYNTH（合成）"}],
               "sources": [{"id": "s", "name": "s", "url": "https://example.invalid", "kind": "主催者", "terms_status": "確認済み"}]}
        RUN.apply_config(self.store, cfg, now=NOW)
        row = {"artist_name": "SYNTH（合成）", "venue": "会場A", "name": "Tour", "event_date": "2027-03-01",
               "sale_windows": [], "status": "公演発表", "evidence_url": "https://example.invalid/a"}
        RUN.run(self.store, "s", [row], now=NOW)
        row2 = dict(row, status="中止")
        r = RUN.run(self.store, "s", [row2], now=NOW + datetime.timedelta(hours=1))
        self.assertTrue(any(c["reason"] == "変更: status" for c in r["candidates"]))  # 生成
        d1 = AW.dispatch(self.store, FakeSender(), now=NOW)
        self.assertEqual(d1["sent"], len(r["candidates"]))
        r2 = RUN.run(self.store, "s", [row2], now=NOW + datetime.timedelta(hours=2))
        self.assertEqual(r2["candidates"], [])                                        # 再生成されない
        self.assertEqual(AW.dispatch(self.store, FakeSender(), now=NOW)["attempted"], 0)

    # ---- 監査で追加：壊れた台帳・旧形式・要対応・再開・例外・重複の境界 ----
    def test_N09_corrupt_ledger_fails_closed(self):
        import json as _json  # noqa: F401
        with open(os.path.join(self.dir, "ledger.json"), "w", encoding="utf-8") as f:
            f.write('{"x": {"status": "dry_run"')                      # 途中で壊れた
        sender = FakeSender()
        with self.assertRaises(AW.CorruptStateError):
            AW.dispatch(self.store, sender, now=NOW)
        self.assertEqual(sender.calls, [])                             # 空扱いで全件を送らない

    def test_N10_legacy_dry_run_entry_is_flagged_not_silently_dropped(self):
        self.store.save("ledger", {"old": {"status": "dry_run"}})
        self.assertIn("old", AW.pending_sends(self.store))
        AW.dispatch(self.store, FakeSender(), now=NOW)
        na = AW.needs_attention(self.store)
        self.assertEqual([x["key"] for x in na], ["old"])
        self.assertIn("旧形式", na[0]["last_error"])
        self.assertEqual(AW.requeue_failed(self.store, ["old"]), 0)    # 送信内容が無いものは戻せない

    def test_N11_three_failures_need_attention_and_explicit_requeue(self):
        AW.mark_dry_run_seen(self.store, [cand(1), cand(2)], now=NOW)
        for _ in range(3):
            AW.dispatch(self.store, FakeSender(fail_on={cand(1)["key"]}), now=NOW)
        na = AW.needs_attention(self.store)
        self.assertEqual([x["key"] for x in na], [cand(1)["key"]])     # 要対応として識別できる
        self.assertNotIn(cand(1)["key"], AW.pending_sends(self.store))  # 自動では送らない
        self.assertEqual(self.states()[cand(2)["key"]], "送信成功")
        self.assertEqual(AW.requeue_failed(self.store, [cand(1)["key"], cand(2)["key"]]), 1)   # 送信済みは戻さない
        sender = FakeSender()
        AW.dispatch(self.store, sender, now=NOW)
        self.assertEqual(sender.calls, [cand(1)["key"]])
        self.assertEqual(self.states()[cand(1)["key"]], "送信成功")
        self.assertEqual(AW.needs_attention(self.store), [])

    def test_N12_keyboard_interrupt_mid_send_keeps_rest_pending(self):
        cs = [cand(i) for i in range(1, 4)]
        AW.mark_dry_run_seen(self.store, cs, now=NOW)
        class Stop(FakeSender):
            def __call__(self, payload):
                if payload["key"] == cs[1]["key"]:
                    raise KeyboardInterrupt()
                return super().__call__(payload)
        with self.assertRaises(KeyboardInterrupt):
            AW.dispatch(self.store, Stop(), now=NOW)
        st = self.states()
        self.assertEqual(st[cs[0]["key"]], "送信成功")
        self.assertEqual(st[cs[1]["key"]], "送信待ち")                # 送信中の停止は「待ち」のまま残る
        self.assertEqual(st[cs[2]["key"]], "生成済み")                # 残りは失われない
        sender = FakeSender()
        AW.dispatch(AW.Store(self.dir), sender, now=NOW)
        self.assertEqual(sender.calls, [cs[1]["key"], cs[2]["key"]])

    def test_N13_mixed_legacy_and_new_in_one_ledger(self):
        led = {"old": {"status": "dry_run"}}
        self.store.save("ledger", led)
        AW.mark_dry_run_seen(self.store, [cand(1)], now=NOW)
        sender = FakeSender()
        c = AW.dispatch(self.store, sender, now=NOW)
        self.assertEqual(sender.calls, [cand(1)["key"]])               # 新形式は送る
        self.assertEqual([x["key"] for x in AW.needs_attention(self.store)], ["old"])   # 旧形式は要対応

    def test_N14_leftover_tmp_file_does_not_affect_load(self):
        AW.mark_dry_run_seen(self.store, [cand(1)], now=NOW)
        with open(os.path.join(self.dir, "ledger.json.tmp"), "w", encoding="utf-8") as f:
            f.write('{"half": ')                                          # 保存の途中で止まった痕跡
        sender = FakeSender()
        AW.dispatch(AW.Store(self.dir), sender, now=NOW)
        self.assertEqual(sender.calls, [cand(1)["key"]])

    def test_N15_crash_after_receipt_before_save_is_resent_documented(self):
        """送信先が受信した直後・成功保存の前に停止した場合：現在は送信済みと判定できず、再送される（重複の可能性）。"""
        AW.mark_dry_run_seen(self.store, [cand(1)], now=NOW)
        received = []
        class Crash(FakeSender):
            def __call__(self, payload):
                received.append(payload["key"])          # 受信は成立した
                raise KeyboardInterrupt()                # 保存前に停止
        with self.assertRaises(KeyboardInterrupt):
            AW.dispatch(self.store, Crash(), now=NOW)
        sender = FakeSender()
        AW.dispatch(AW.Store(self.dir), sender, now=NOW)
        self.assertEqual(received + sender.calls, [cand(1)["key"], cand(1)["key"]])   # 2回届く（重複）
        self.assertEqual(self.states()[cand(1)["key"]], "送信成功")

    def test_N16_failed_candidate_is_not_regenerated_by_generation(self):
        AW.mark_dry_run_seen(self.store, [cand(1)], now=NOW)
        AW.dispatch(self.store, FakeSender(fail_on={cand(1)["key"]}), now=NOW)
        ledger = self.store.load("ledger")
        self.assertIn(cand(1)["key"], ledger)                          # 台帳に残るため、生成で重複しない
        self.assertEqual(ledger[cand(1)["key"]]["status"], "送信失敗")


if __name__ == "__main__":
    unittest.main()
