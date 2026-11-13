"""手動投入の入口（テンプレート・架空サンプル・設定例）が、既存のCLIとホームに通ることの検証。実データは使わない。"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import artist_watch as AW  # noqa: E402
import artist_watch_home as HOME  # noqa: E402
import artist_watch_run as RUN  # noqa: E402

INPUT = os.path.join(HERE, "artist_watch_input")


class ManualInputTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ledger = os.path.join(self.tmp, "ledger")
        self.site = os.path.join(self.tmp, "site")
        self.cfg = os.path.join(INPUT, "設定_テンプレート.json")

    def cli(self, batch):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = RUN.main(["--dir", self.ledger, "--config", self.cfg, "--source", "src_manual", "--batch", batch])
        return code, json.loads(buf.getvalue())

    def copy(self, name, edit=None):
        dst = os.path.join(self.tmp, name)
        with open(os.path.join(INPUT, "サンプル_架空データ.csv"), encoding="utf-8") as f:
            text = f.read()
        if edit:
            text = edit(text)
        with open(dst, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return dst

    def perfs(self):
        return [e for e in AW.Store(self.ledger).load("events").values() if e.get("kind") == "performance"]

    def test_M1_template_header_matches_spec_and_is_empty(self):
        with open(os.path.join(INPUT, "テンプレート_空.csv"), encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        self.assertEqual(lines[0].split(","), list(RUN.CSV_COLUMNS))
        self.assertEqual(len(lines), 1)                                    # データ行なし

    def test_M2_sample_is_fictional_and_valid(self):
        valid, rejected = RUN.read_batch(os.path.join(INPUT, "サンプル_架空データ.csv"))
        self.assertEqual(rejected, [])
        self.assertEqual(len(valid), 3)
        self.assertTrue(all("架空" in r["artist_name"] for r in valid))    # 架空と明示

    def test_M3_config_example_registers_source(self):
        with open(self.cfg, encoding="utf-8") as f:
            cfg = json.load(f)
        store = AW.Store(self.ledger)
        RUN.apply_config(store, cfg, now=AW.now_jst())
        self.assertIn("src_manual", store.load("sources"))

    def test_M4_sample_ingest_reflects_on_home_and_rerun_has_no_duplicates(self):
        sample = self.copy("sample.csv")
        code, out = self.cli(sample)
        self.assertEqual(code, 0)
        self.assertEqual(out["ingest"]["new"], 3)
        self.assertEqual(out.get("unknown_artists"), [])                  # 設定にないアーティストの行は無い
        HOME.build(self.ledger, self.site, "データ出所：架空のサンプル（確認用）")
        idx = open(os.path.join(self.site, "index.html"), encoding="utf-8").read()
        self.assertIn("架空アーティストA（サンプル）", idx)
        self.assertIn("データ出所：架空のサンプル（確認用）", idx)
        self.assertIn("定期実行：未接続", idx)
        _, again = self.cli(sample)                                        # 同じ入力を再投入
        self.assertEqual((again["ingest"]["new"], again["ingest"]["changed"]), (0, 0))
        self.assertEqual(len(self.perfs()), 3)                             # 重複しない

    def test_M5_changed_date_is_in_history_and_home(self):
        self.cli(self.copy("v1.csv"))
        v2 = self.copy("v2.csv", edit=lambda t: t.replace("2027-03-01", "2027-03-15"))
        _, out = self.cli(v2)
        self.assertEqual(out["ingest"]["changed"], 1)
        hist = AW.Store(self.ledger).load("history")
        self.assertTrue(any(h["field"] == "event_date" and h["after"] == "2027-03-15" for h in hist))
        HOME.build(self.ledger, self.site, "データ出所：架空のサンプル（確認用）")
        self.assertIn("2027-03-15", open(os.path.join(self.site, "index.html"), encoding="utf-8").read())

    def test_M6_empty_template_is_safe_no_change(self):
        empty = os.path.join(INPUT, "テンプレート_空.csv")
        code, out = self.cli(empty)
        self.assertEqual(code, 0)
        self.assertFalse(out["ingest"]["applied"])                         # 取り込みなし。台帳は変えない
        self.assertEqual(self.perfs(), [])


if __name__ == "__main__":
    unittest.main()
