"""巨大情報収集のホームページ（静的HTML）の検証。すべて合成データ・一時フォルダ。"""
import contextlib
import datetime
import io
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
CFG = {"artists": [{"name": "SYNTH ARTIST（合成）"}],
       "sources": [{"id": "src", "name": "合成情報源", "url": "https://example.invalid/s", "kind": "主催者", "terms_status": "確認済み"}]}


def row(venue="会場A", name="Tour A", date="2027-03-01", status="公演発表", url="https://example.invalid/a", key="K-A"):
    return {"artist_name": "SYNTH ARTIST（合成）", "venue": venue, "name": name, "event_date": date,
            "sale_windows": [{"type": "一般発売", "start": "2026-11-10T10:00:00+09:00", "end": "2026-11-11T23:59:00+09:00"}],
            "status": status, "evidence_url": url, "source_performance_id": key}


class SiteTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = AW.Store(os.path.join(self.dir, "ledger"))
        self.out = os.path.join(self.dir, "site")

    def read(self, name):
        with open(os.path.join(self.out, name), encoding="utf-8") as f:
            return f.read()

    def test_S01_list_and_detail_from_ledger_with_label(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src", [row()], now=NOW)
        n = SITE.render_site(self.store, self.out, data_label="合成データ", now=NOW)
        idx = self.read("index.html")
        self.assertIn("Tour A", idx)
        self.assertIn("合成データ", idx)                                  # 出所を明示
        detail = self.read(SITE.detail_file(next(iter(self.store.load("events")))))
        self.assertIn("https://example.invalid/a", detail)                 # 出典
        self.assertIn("一般発売", detail)                                  # 発売区分
        self.assertEqual(n, 3)                                             # index・sources・詳細1件

    def test_S02_empty_states_are_explicit(self):
        SITE.render_site(self.store, self.out, data_label="合成データ", now=NOW)
        self.assertIn("データなし", self.read("index.html"))
        self.assertIn("情報源が登録されていません", self.read("sources.html"))

    def test_S03_failed_last_fetch_is_shown_and_data_kept(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src", [row()], now=NOW)
        RUN.run(self.store, "src", [], now=NOW, source_status="timeout")
        SITE.render_site(self.store, self.out, data_label="合成データ", now=NOW)
        src = self.read("sources.html")
        self.assertIn("最終取得は失敗（timeout）", src)
        self.assertIn("既存のデータは保持", src)
        self.assertIn("Tour A", self.read("index.html"))                  # 失敗しても既存の公演は残る

    def test_S04_html_is_escaped(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src", [row(name="<script>x</script>")], now=NOW)
        SITE.render_site(self.store, self.out, data_label="合成データ", now=NOW)
        self.assertNotIn("<script>x</script>", self.read("index.html"))
        self.assertIn("&lt;script&gt;", self.read("index.html"))

    def test_S05_duplicates_show_once(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src", [row()], now=NOW)
        RUN.run(self.store, "src", [row()], now=NOW + datetime.timedelta(hours=1))
        SITE.render_site(self.store, self.out, data_label="合成データ", now=NOW)
        self.assertEqual(self.read("index.html").count("Tour A</a>"), 1)
        self.assertEqual(len([f for f in os.listdir(self.out) if f.startswith("p_")]), 1)

    def test_S06_change_reflected_in_detail_history(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src", [row()], now=NOW)
        RUN.run(self.store, "src", [row(date="2027-03-08")], now=NOW + datetime.timedelta(hours=1))
        SITE.render_site(self.store, self.out, data_label="合成データ", now=NOW)
        pid = next(e["id"] for e in self.store.load("events").values() if e.get("kind") == "performance")
        detail = self.read(SITE.detail_file(pid))
        self.assertIn("event_date", detail)                                # 変更履歴に項目名
        self.assertIn("2027-03-08", detail)                                # 変更後の値

    def test_S07_bad_rows_never_appear_as_performances(self):
        csv_path = os.path.join(self.dir, "in.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("artist_name,venue,name,event_date,status,evidence_url,source_performance_id,sale_type,sale_start,sale_end\n"
                    "SYNTH ARTIST（合成）,会場A,Tour A,2027-03-01,公演発表,,K-A,,,\n"
                    ",会場B,Tour B,2027-03-01,公演発表,,,,,\n")
        RUN.apply_config(self.store, CFG, now=NOW)
        cfg_path = os.path.join(self.dir, "c.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(CFG, f, ensure_ascii=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            RUN.main(["--dir", os.path.join(self.dir, "ledger"), "--config", cfg_path, "--source", "src",
                      "--batch", csv_path, "--site-out", self.out, "--data-label", "合成データ"])
        idx = self.read("index.html")
        self.assertIn("Tour A", idx)
        self.assertNotIn("Tour B", idx)                                    # 不正行は表示されない

    def test_S08_refuses_to_write_into_docs(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        target = os.path.join(here, "docs", "artist-watch-test")
        cfg_path = os.path.join(self.dir, "c.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(CFG, f, ensure_ascii=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = RUN.main(["--dir", os.path.join(self.dir, "ledger"), "--config", cfg_path, "--site-out", target])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(target))

    def test_S09_banner_shows_location_label_empty_and_unconnected_schedule(self):
        SITE.render_site(self.store, self.out, data_label="合成データ（確認）", now=NOW, ledger_dir="/x/ledger")
        idx = self.read("index.html")
        self.assertIn("/x/ledger", idx)                                    # データの場所
        self.assertIn("合成データ（確認）", idx)                            # 出所
        self.assertIn("データなし", idx)
        self.assertIn("定期実行：未接続", idx)                             # 未接続を明示

    def test_S10_banner_shows_failed_fetch_and_keeps_data(self):
        RUN.apply_config(self.store, CFG, now=NOW)
        RUN.run(self.store, "src", [row()], now=NOW)
        RUN.run(self.store, "src", [], now=NOW, source_status="timeout")
        SITE.render_site(self.store, self.out, data_label="合成データ（確認）", now=NOW, ledger_dir="/x/ledger")
        idx = self.read("index.html")
        self.assertIn("最終の取得：失敗（timeout", idx)
        self.assertIn("表示は前回の正常データ", idx)
        self.assertIn("Tour A", idx)


if __name__ == "__main__":
    unittest.main()
