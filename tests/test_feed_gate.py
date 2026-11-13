"""外部取得方式の許可ゲート・取得履歴・失敗の記録。外部通信は行わない（取得関数は合成の関数に差し替える）。
確認すること：許可のない情報源は、通信（取得関数の呼び出し）の前に拒否される／許可があれば既存の取り込みに届く／
取得の失敗は履歴に残り、台帳は変わらない。"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import feed_adapters as FA  # noqa: E402
import film_pipeline as FP  # noqa: E402
import film_site as FS  # noqa: E402
import film_watch as FW  # noqa: E402
import source_registry as SR  # noqa: E402

TEST = "テスト用架空データ"
TODAY = __import__("datetime").date(2026, 10, 21)
HOST = "example.invalid"
TEXT = ("作品名,監督・人物名,人物の区別情報,役割,公開状態,公開日,公開日の出典URL,確認日,販売区分,販売状態,販売開始日時,販売URL,販売の出典URL\n"
        "架空作品G,架空 監督G,1977年生,監督,公開日確定,2027-07-01,https://example.invalid/r,2026-10-21,,,,,\n")


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gate_")
        self.film = os.path.join(self.tmp, "film")
        self.db = os.path.join(self.tmp, "p.db")
        self.exh = os.path.join(self.tmp, "exh.html")
        with open(self.exh, "w", encoding="utf-8") as f:
            f.write("<html><body></body></html>")
        self.ns = os.path.join(self.tmp, "ns")
        self.out = os.path.join(self.tmp, "docs", "film")
        self.notice = os.path.join(self.tmp, "n.txt")
        self.saved_docs = FS.DOCS_ROOT
        FS.DOCS_ROOT = os.path.join(self.tmp, "docs")
        os.makedirs(FS.DOCS_ROOT)
        self.fetch_calls = []

    def tearDown(self):
        FS.DOCS_ROOT = self.saved_docs
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fetch(self):                       # 合成の取得関数（実際の通信はしない）
        self.fetch_calls.append(1)
        return TEXT

    def register(self, host):
        FA.FETCH_ADAPTERS["synthetic"] = {"host": host, "fetch": self.fetch, "parse_text": self.parse}

    def parse(self, text):
        import csv, io
        return list(csv.DictReader(io.StringIO(text)))

    def ledger(self):
        s = FW.Store(self.film)
        return s.load("works"), s.load("people")

    def run_pipeline(self):
        return FP.run("synthetic", None, self.film, self.db, self.out, self.notice, TEST,
                      exhibition_source=self.exh, screenings_dir=self.ns, today=TODAY)

    def tearDown_registry(self):
        FA.FETCH_ADAPTERS.pop("synthetic", None)

    def test_unapproved_source_is_refused_before_any_fetch(self):
        self.register(HOST)                                           # 規約未確認の情報源（台帳に無い）
        try:
            with self.assertRaises(PermissionError):
                self.run_pipeline()
        finally:
            self.tearDown_registry()
        self.assertEqual(self.fetch_calls, [])                        # 通信（取得関数）は呼ばれていない
        self.assertFalse(os.path.exists(os.path.join(self.film, "works.json")))
        self.assertEqual(FA.last_run(self.film, "synthetic")["status"].split("：")[0], "失敗")

    def test_toho_film_site_is_not_treated_as_approved(self):
        self.assertFalse(FA._terms_approved("godzilla-movie2023.toho.co.jp"))

    def test_approved_source_flows_to_ledger_html_diff_and_notice(self):
        self.register(HOST)
        approved = [dict(r, auto_allowed=True) for r in SR.rows()] + [
            {"host": HOST, "auto_allowed": True, "evidence_url": "https://example.invalid/terms", "checked_on": "2026-10-21"}]
        try:
            with mock.patch.object(FA.SR, "rows", return_value=approved):
                result = self.run_pipeline()
        finally:
            self.tearDown_registry()
        self.assertEqual(self.fetch_calls, [1])
        self.assertIn("[作品追加] 架空作品G", result["diff"])
        self.assertEqual(FA.last_run(self.film, "synthetic")["status"], "成功")
        self.assertEqual(FA.last_run(self.film, "synthetic")["input"], "（外部取得）")

    def test_fetch_failure_is_recorded_and_ledger_is_unchanged(self):
        def broken():
            raise OSError("テスト用の通信失敗")
        FA.FETCH_ADAPTERS["synthetic"] = {"host": HOST, "fetch": broken, "parse_text": self.parse}
        approved = SR.rows() + [{"host": HOST, "auto_allowed": True, "evidence_url": "https://example.invalid/t",
                                 "checked_on": "2026-10-21"}]
        try:
            with mock.patch.object(FA.SR, "rows", return_value=approved):
                with self.assertRaises(OSError):
                    self.run_pipeline()
        finally:
            self.tearDown_registry()
        self.assertFalse(os.path.exists(os.path.join(self.film, "works.json")))
        self.assertTrue(FA.last_run(self.film, "synthetic")["status"].startswith("失敗"))

    def test_unknown_source_name_is_refused(self):
        with self.assertRaises(ValueError):
            FA.run_adapter("no_such_source")


if __name__ == "__main__":
    unittest.main()
