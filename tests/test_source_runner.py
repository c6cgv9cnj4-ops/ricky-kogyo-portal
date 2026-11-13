"""情報源ごとの取得経路（未接続・未実行・成功・失敗）と、ローカル入力の検証。すべて合成データ・取得関数は偽物。"""
import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import artist_watch as AW  # noqa: E402
import artist_watch_run as RUN  # noqa: E402
import artist_watch_site as SITE  # noqa: E402

NOW = datetime.datetime(2026, 10, 19, 9, 0, tzinfo=AW.JST)


def rows_json(date="2027-03-01"):
    return json.dumps([{"artist_name": "SYNTH ARTIST（合成）", "venue": "会場A", "name": "Tour A", "event_date": date,
                        "status": "公演発表", "evidence_url": "https://example.invalid/a", "source_performance_id": "K-A"}],
                      ensure_ascii=False)


class Fake:
    def __init__(self, result=None, raise_exc=None):
        self.calls, self.result, self.raise_exc = [], result, raise_exc

    def __call__(self, url):
        self.calls.append(url)
        if self.raise_exc:
            raise self.raise_exc
        return self.result


class SourceRunnerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = AW.Store(self.dir)
        self.sid = f"src_t{id(self)}"
        self.added = []
        RUN.apply_config(self.store, {"artists": [{"name": "SYNTH ARTIST（合成）"}]}, now=NOW)   # 合成アーティストを台帳に登録

    def tearDown(self):
        for k in self.added:
            RUN.FETCHERS.pop(k, None)

    def source(self, terms="確認済み", kind="主催者"):
        AW.register_source(self.store, self.sid, "テスト情報源", "https://example.invalid/t", kind, terms_status=terms)

    def fetcher(self, fake):
        RUN.register_fetcher(self.sid, fake, "json_rows")
        self.added.append(self.sid)

    def events(self):
        return [e for e in self.store.load("events").values() if e.get("kind") == "performance"]

    def meta_status(self):
        return self.store.load("meta").get("last_status", {}).get(self.sid, {}).get("status")

    def test_R01_no_fetcher_is_not_connected_and_ledger_untouched(self):
        self.source()
        r = RUN.run_source(self.store, self.sid, now=NOW)
        self.assertEqual(r["state"], "未接続")
        self.assertEqual(self.events(), [])

    def test_R02_not_allowed_never_calls_fetch(self):
        self.source(terms="未確認")
        fake = Fake(("ok", rows_json()))
        self.fetcher(fake)
        r = RUN.run_source(self.store, self.sid, now=NOW)
        self.assertEqual(r["state"], "未実行")
        self.assertEqual(fake.calls, [])                                  # 許可が無ければ取得しない

    def test_R03_allowed_success_registers_performance(self):
        self.source()
        self.fetcher(Fake(("ok", rows_json())))
        r = RUN.run_source(self.store, self.sid, now=NOW)
        self.assertEqual(r["state"], "成功")
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.meta_status(), "ok")

    def test_R04_timeout_keeps_data_and_history(self):
        self.source()
        fake = Fake(("ok", rows_json()))
        self.fetcher(fake)
        RUN.run_source(self.store, self.sid, now=NOW)
        before = (self.store.load("events"), len(self.store.load("history")))
        fake.result = ("timeout", None)
        r = RUN.run_source(self.store, self.sid, now=NOW + datetime.timedelta(hours=1))
        self.assertEqual(r["state"], "失敗")
        self.assertEqual((self.store.load("events"), len(self.store.load("history"))), before)
        self.assertEqual(self.meta_status(), "timeout")

    def test_R05_exception_in_fetch_is_failure_not_success(self):
        self.source()
        self.fetcher(Fake(raise_exc=ConnectionError("偽の通信エラー")))
        r = RUN.run_source(self.store, self.sid, now=NOW)
        self.assertEqual(r["state"], "失敗")
        self.assertEqual(self.events(), [])
        self.assertEqual(self.meta_status(), "http_error")

    def test_R06_zero_rows_is_empty_failure_and_keeps_data(self):
        self.source()
        fake = Fake(("ok", rows_json()))
        self.fetcher(fake)
        RUN.run_source(self.store, self.sid, now=NOW)
        fake.result = ("ok", "[]")
        r = RUN.run_source(self.store, self.sid, now=NOW + datetime.timedelta(hours=1))
        self.assertEqual(r["state"], "失敗")
        self.assertEqual(len(self.events()), 1)                          # 0件で消さない
        self.assertEqual(self.meta_status(), "empty")

    def test_R07_unparseable_content_is_failure_and_ledger_untouched(self):
        self.source()
        fake = Fake(("ok", rows_json()))
        self.fetcher(fake)
        RUN.run_source(self.store, self.sid, now=NOW)
        before = self.store.load("events")
        fake.result = ("ok", '[{"artist_name": ')                        # 壊れたJSON
        r = RUN.run_source(self.store, self.sid, now=NOW + datetime.timedelta(hours=1))
        self.assertEqual(r["state"], "失敗")
        self.assertEqual(self.store.load("events"), before)
        self.assertEqual(self.meta_status(), "parse_error")

    def test_R08_local_file_source_goes_through_same_path_and_homepage(self):
        AW.register_source(self.store, "src_local", "ローカル入力（テスト）", "local://batch", "その他", terms_status="未確認")
        csv_path = os.path.join(self.dir, "in.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("artist_name,venue,name,event_date,status,evidence_url,source_performance_id,sale_type,sale_start,sale_end\n"
                    "SYNTH ARTIST（合成）,会場A,Tour L,2027-03-01,公演発表,,L1,,,\n")
        batch, rejected = RUN.read_batch(csv_path)
        result = RUN.run(self.store, "src_local", batch, now=NOW)
        self.assertEqual(result["ingest"]["new"], 1)
        out = os.path.join(self.dir, "site")
        SITE.render_site(self.store, out, data_label="ローカル入力（テスト）", now=NOW)
        self.assertIn("Tour L", open(os.path.join(out, "index.html"), encoding="utf-8").read())

    def test_R09_homepage_reflects_success_then_failure_state(self):
        self.source()
        fake = Fake(("ok", rows_json()))
        self.fetcher(fake)
        RUN.run_source(self.store, self.sid, now=NOW)
        fake.result = ("timeout", None)
        RUN.run_source(self.store, self.sid, now=NOW + datetime.timedelta(hours=1))
        out = os.path.join(self.dir, "site")
        SITE.render_site(self.store, out, data_label="合成データ", now=NOW + datetime.timedelta(hours=2))
        self.assertIn("Tour A", open(os.path.join(out, "index.html"), encoding="utf-8").read())
        self.assertIn("最終取得は失敗（timeout）", open(os.path.join(out, "sources.html"), encoding="utf-8").read())

    def test_R10_repeated_success_is_not_duplicated(self):
        self.source()
        self.fetcher(Fake(("ok", rows_json())))
        RUN.run_source(self.store, self.sid, now=NOW)
        r = RUN.run_source(self.store, self.sid, now=NOW + datetime.timedelta(hours=1))
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(r["ingest"]["same"], 1)


if __name__ == "__main__":
    unittest.main()
