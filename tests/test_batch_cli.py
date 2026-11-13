"""ローカルの JSON・CSV 取り込みを、実際のコマンド（main）から通す検証。すべて合成データ。"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import artist_watch as AW  # noqa: E402
import artist_watch_run as RUN  # noqa: E402

CONFIG = {"artists": [{"name": "SYNTH ARTIST（合成）"}],
          "sources": [{"id": "src", "name": "合成", "url": "https://example.invalid/s", "kind": "主催者", "terms_status": "確認済み"}]}
CSV_HEADER = "artist_name,venue,name,event_date,status,evidence_url,source_performance_id,sale_type,sale_start,sale_end\n"


class BatchCliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cfg = os.path.join(self.dir, "config.json")
        with open(self.cfg, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, ensure_ascii=False)
        self.ledger_dir = os.path.join(self.dir, "ledger")

    def write(self, name, text):
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return p

    def run_cli(self, batch):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = RUN.main(["--dir", self.ledger_dir, "--config", self.cfg, "--source", "src", "--batch", batch])
        return code, json.loads(buf.getvalue())

    def perfs(self):
        return [e for e in AW.Store(self.ledger_dir).load("events").values() if e.get("kind") == "performance"]

    def test_C01_csv_new_performances_end_to_end(self):
        csv_text = CSV_HEADER + (
            "SYNTH ARTIST（合成）,会場A,Tour A,2027-03-01,公演発表,https://example.invalid/a,K-A,一般発売,2026-11-10T10:00:00+09:00,2026-11-11T23:59:00+09:00\n"
            "SYNTH ARTIST（合成）,会場B,Tour B,2027-04-01,公演発表,https://example.invalid/b,K-B,,,\n")
        code, out = self.run_cli(self.write("in.csv", csv_text))
        self.assertEqual(code, 0)
        self.assertEqual(out["ingest"]["new"], 2)
        self.assertEqual(out["rejected_rows"], [])
        self.assertEqual(len(self.perfs()), 2)

    def test_C02_invalid_rows_rejected_and_reported_not_registered(self):
        csv_text = CSV_HEADER + (
            "SYNTH ARTIST（合成）,会場A,Tour A,2027-03-01,公演発表,https://example.invalid/a,K-A,,,\n"
            ",会場B,Tour B,2027-04-01,公演発表,,,,,\n"                                           # 必須（artist_name）欠損
            "SYNTH ARTIST（合成）,会場C,Tour C,2027/05/01,公演発表,,,,,\n"                          # 日付形式が不正
            "SYNTH ARTIST（合成）,会場D,Tour D,2027-06-01,公演発表,,,一般発売,2026-11-10 10:00,2026-11-11T23:59:00+09:00\n"  # 時刻にタイムゾーンなし
            "SYNTH ARTIST（合成）,会場E,Tour E,2027-07-01,公演発表,,,不明な種類,2026-11-10T10:00:00+09:00,\n")  # 発売の種類が不正
        code, out = self.run_cli(self.write("bad.csv", csv_text))
        self.assertEqual(code, 0)
        self.assertEqual(len(out["rejected_rows"]), 4)                  # 不正な行は4件、理由つきで報告
        self.assertEqual(out["ingest"]["new"], 1)                       # 正常な1件だけ登録
        self.assertEqual(len(self.perfs()), 1)

    def test_C03_json_same_path_same_validation(self):
        rows = [{"artist_name": "SYNTH ARTIST（合成）", "venue": "会場A", "name": "Tour A", "event_date": "2027-03-01"},
                {"artist_name": "SYNTH ARTIST（合成）", "venue": "会場B"}]     # name 欠損
        code, out = self.run_cli(self.write("in.json", json.dumps(rows, ensure_ascii=False)))
        self.assertEqual(out["ingest"]["new"], 1)
        self.assertEqual(len(out["rejected_rows"]), 1)
        self.assertIn("name", out["rejected_rows"][0]["理由"])

    def test_C04_duplicate_rows_in_one_file_are_one_performance(self):
        line = "SYNTH ARTIST（合成）,会場A,Tour A,2027-03-01,公演発表,,K-A,,,\n"
        code, out = self.run_cli(self.write("dup.csv", CSV_HEADER + line + line))
        self.assertEqual(len(self.perfs()), 1)
        self.assertEqual(out["ingest"]["new"] + out["ingest"]["same"], 2)

    def test_C05_rerun_same_input_is_stable(self):
        csv_text = CSV_HEADER + "SYNTH ARTIST（合成）,会場A,Tour A,2027-03-01,公演発表,,K-A,,,\n"
        p = self.write("in.csv", csv_text)
        self.run_cli(p)
        before = len(AW.Store(self.ledger_dir).load("history"))
        code, out = self.run_cli(p)
        self.assertEqual((out["ingest"]["new"], out["ingest"]["changed"]), (0, 0))
        self.assertEqual(out["candidates"], [])
        self.assertEqual(len(AW.Store(self.ledger_dir).load("history")), before)   # 履歴は増えない

    def test_C06_csv_change_detected_as_changed_with_history(self):
        p1 = self.write("day1.csv", CSV_HEADER + "SYNTH ARTIST（合成）,会場A,Tour A,2027-03-01,公演発表,,K-A,,,\n")
        self.run_cli(p1)
        p2 = self.write("day2.csv", CSV_HEADER + "SYNTH ARTIST（合成）,会場A,Tour A,2027-03-08,公演発表,,K-A,,,\n")
        code, out = self.run_cli(p2)
        self.assertEqual(out["ingest"]["changed"], 1)
        self.assertTrue(any(c["reason"] == "変更: event_date" for c in out["candidates"]))

    def test_C07_missing_csv_header_rejected_without_crash(self):
        with self.assertRaises(ValueError):
            RUN.read_batch(self.write("nohead.csv", "foo,bar\n1,2\n"))

    def test_C08_no_real_data_in_project(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        names = [f.lower() for f in os.listdir(here)]
        self.assertFalse(any(("concert" in n or "artist" in n or "overseas" in n) and n.endswith((".csv", ".json"))
                             for n in names))   # 公演の実データは置いていない（入力は利用者が配置する）


if __name__ == "__main__":
    unittest.main()
