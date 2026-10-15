"""共通データ契約（event_records）・美術展の変換・ホームの新着欄の検証。ネットワーク不要。"""
import datetime
import json
import os
import sys
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import event_records as ER  # noqa: E402
import culture_exhibition_dashboard as D  # noqa: E402
import auto_fetch_all as H  # noqa: E402

TODAY = datetime.date(2026, 10, 15)


def rec(rid, state="通常", confidence="確認済み", dates=None, title="展", url="https://x.example/1", venue="A館"):
    base = {k: ER.unknown_date() for k in ER.DATE_KEYS}
    base.update(dates or {})
    return {"id": rid, "genre": "美術展", "title": title, "venue": venue, "source_url": url,
            "evidence_url": "", "state": state, "confidence": confidence,
            "last_checked_at": "2026-10-15T00:00:00+00:00", "dates": base, "genre_extra": {}}


class ContractTest(unittest.TestCase):
    def test_valid_record_passes(self):
        self.assertEqual(ER.validate_record(rec("a")), [])

    def test_unknown_date_must_not_have_value(self):
        r = rec("a")
        r["dates"]["application_deadline"] = {"value": "2026-10-30", "precision": "unknown", "timezone": None, "source_url": ""}
        self.assertTrue(any("不明な日付に値" in e for e in ER.validate_record(r)))

    def test_datetime_requires_timezone(self):
        r = rec("a")
        r["dates"]["ticket_sale_start"] = {"value": "2026-10-20T10:00:00", "precision": "datetime", "timezone": None, "source_url": ""}
        self.assertTrue(any("timezone" in e for e in ER.validate_record(r)))

    def test_missing_source_and_bad_confidence_fail(self):
        r = rec("a", url="ftp://x", confidence="たぶん")
        errs = ER.validate_record(r)
        self.assertTrue(any("http" in e for e in errs))
        self.assertTrue(any("confidence" in e for e in errs))

    def test_day_and_datetime_are_distinguished(self):
        d = ER.day_date("2026-10-20")
        t = ER.datetime_date("2026-10-20T10:00:00+09:00", "Asia/Tokyo")
        self.assertEqual(d["precision"], "day")
        self.assertEqual(t["precision"], "datetime")
        self.assertEqual(t["timezone"], "Asia/Tokyo")
        self.assertEqual(ER.day_date(None)["precision"], "unknown")


class DeadlineTest(unittest.TestCase):
    def test_ticket_deadline_and_event_end_are_separate_fields(self):
        r = rec("a", dates={"event_end": ER.day_date("2026-12-01"), "application_deadline": ER.day_date("2026-10-20")})
        self.assertEqual(r["dates"]["event_end"]["value"], "2026-12-01")
        self.assertEqual(r["dates"]["application_deadline"]["value"], "2026-10-20")
        rows = ER.upcoming_deadlines([r], TODAY, days=14)
        self.assertEqual([x["kind"] for x in rows], ["application_deadline"])   # 終了日は締切として出さない

    def test_upcoming_window_and_unknown_excluded(self):
        near = rec("near", dates={"application_deadline": ER.day_date("2026-10-20")})
        far = rec("far", dates={"application_deadline": ER.day_date("2026-12-20")})
        unknown = rec("unk")
        rows = ER.upcoming_deadlines([near, far, unknown], TODAY, days=14)
        self.assertEqual([x["record"]["id"] for x in rows], ["near"])

    def test_datetime_deadline_is_included(self):
        r = rec("t", dates={"ticket_sale_start": ER.datetime_date("2026-10-18T10:00:00+09:00", "Asia/Tokyo")})
        self.assertEqual(len(ER.upcoming_deadlines([r], TODAY, days=14)), 1)


class NewsTest(unittest.TestCase):
    def test_priority_and_exclusions(self):
        records = [rec("c", "通常"), rec("e", "終了"), rec("n", "新着", title="B"), rec("h", "変更", title="A"),
                   rec("k", "取得失敗・前回データ", title="C"), rec("m", "掲載未確認", title="D")]
        states = [r["state"] for r in ER.news_rows(records)]
        self.assertEqual(states, ["新着", "変更", "取得失敗・前回データ", "掲載未確認"])

    def test_no_news_has_clear_empty_text(self):
        self.assertIn("差分なし", ER.render_news_html([], "差分なし"))

    def test_news_html_escapes_and_keeps_links_and_confidence(self):
        r = rec("x", "新着", title="<script>x</script>", url="https://ok.example/p")
        html_text = ER.render_news_html(ER.news_rows([r]), "なし")
        self.assertNotIn("<script>", html_text)
        self.assertIn("href='https://ok.example/p'", html_text)
        self.assertIn("確度：確認済み", html_text)
        self.assertIn("公式発表日：未確認", html_text)


class CultureConversionTest(unittest.TestCase):
    def item(self, **over):
        judgment = {"無料表示": True, "割引表示": False, "手帳本人": "確認済み:無料", "確認日": "2026-10-15"}
        base = {"venue": "A館", "title": "展1", "link": "https://a.example/1#frag", "period": "2026年9月1日～2026年12月1日",
                "status": "開催中", "general_price": "1,000円", "discount": "無料（確認済み）",
                "discount_source_url": "https://a.example/evidence", "judgment": judgment}
        base.update(over)
        return base

    def test_no_fake_dates_for_unknown_deadlines(self):
        r = D.culture_record(self.item(), "変更", "2026-10-15T00:00:00+00:00")
        for key in ("ticket_sale_start", "general_sale_start", "application_deadline", "announced_at"):
            self.assertEqual(r["dates"][key]["precision"], "unknown")
            self.assertIsNone(r["dates"][key]["value"])
        self.assertEqual(r["dates"]["event_start"]["value"], "2026-09-01")
        self.assertEqual(r["dates"]["event_end"]["value"], "2026-12-01")
        self.assertEqual(ER.validate_record(r), [])

    def test_state_mapping_and_no_end_for_not_confirmed(self):
        r = D.culture_record(self.item(period="取得困難（要アクセス）"), "掲載確認できず（限定掲載・3回以上）", "t")
        self.assertEqual(r["state"], "掲載未確認")
        self.assertEqual(r["dates"]["event_end"]["precision"], "unknown")   # 会期終了と誤表示しない
        self.assertEqual(D.culture_record(self.item(), "保持（取得失敗）", "t")["state"], "取得失敗・前回データ")
        self.assertEqual(D.culture_record(self.item(), "復帰", "t")["state"], "再掲載")

    def test_confidence_and_evidence_kept(self):
        r = D.culture_record(self.item(), "変更なし", "t")
        self.assertEqual(r["confidence"], "確認済み")
        self.assertEqual(r["evidence_url"], "https://a.example/evidence")
        self.assertEqual(r["source_url"], "https://a.example/1#frag")
        est = self.item(judgment={"手帳本人": "推定:根拠URLなし", "無料表示": False, "割引表示": False})
        r2 = D.culture_record(est, "変更なし", "t")
        self.assertEqual(r2["confidence"], "推定")
        self.assertEqual(r2["evidence_url"], "")

    def test_same_url_different_exhibitions_are_not_merged(self):
        a = self.item(title="展X", link="https://a.example/same")
        b = self.item(title="展Y", link="https://a.example/same")
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "events.json")
            D.write_culture_events([a, b], {id(a): "変更なし", id(b): "変更なし"}, "t", path)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(len(json.load(f)["records"]), 2)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_same_item_has_one_stable_id(self):
        a = D.culture_record(self.item(), "変更なし", "t")
        b = D.culture_record(self.item(link="https://a.example/1"), "変更なし", "t")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(ER.dedupe_records([a, b])), 1)

    def test_write_events_skips_identical_content(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "events.json")
            labels = {}
            items = [self.item()]
            changed1 = D.write_culture_events(items, {id(items[0]): "変更なし"}, "2026-10-15T00:00:00+00:00", path)
            changed2 = D.write_culture_events(items, {id(items[0]): "変更なし"}, "2026-10-15T00:00:00+00:00", path)
            self.assertTrue(changed1)
            self.assertFalse(changed2)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["schema"], ER.SCHEMA_VERSION)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class EventsStabilityTest(unittest.TestCase):
    """events.json：内容が同じ日は書き込まない。記録の最終確認時刻は、その記録の内容が変わった時刻だけ動く"""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, "events.json")
        self.a = CultureConversionTest.item(CultureConversionTest(), title="展A", link="https://a.example/a")
        self.b = CultureConversionTest.item(CultureConversionTest(), title="展B", link="https://a.example/b")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_same_content_different_time_is_not_rewritten(self):
        D.write_culture_events([self.a, self.b], {}, "2026-10-15T00:00:00+00:00", self.path)
        with open(self.path, "rb") as f:
            first = f.read()
        changed = D.write_culture_events([self.a, self.b], {}, "2026-10-16T00:00:00+00:00", self.path)
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), first)
        self.assertFalse(changed)

    def test_only_changed_record_gets_new_time(self):
        D.write_culture_events([self.a, self.b], {}, "2026-10-15T00:00:00+00:00", self.path)
        b2 = dict(self.b, period="2026年9月1日～2026年12月31日")
        D.write_culture_events([self.a, b2], {}, "2026-10-16T00:00:00+00:00", self.path)
        with open(self.path, encoding="utf-8") as f:
            recs = {r["title"]: r for r in json.load(f)["records"]}
        self.assertEqual(recs["展A"]["last_checked_at"], "2026-10-15T00:00:00+00:00")
        self.assertEqual(recs["展B"]["last_checked_at"], "2026-10-16T00:00:00+00:00")


class HomeNewsTest(unittest.TestCase):
    def test_first_run_has_no_new_items_on_home(self):
        r = rec("b", "通常", title="基準")
        html_text = H.news_section_html([r])
        self.assertNotIn("新着</span>", html_text)
        self.assertIn("差分なし", html_text)

    def test_home_names_failed_venues_explicitly(self):
        html_text = H.news_section_html([rec("n", "新着")], failed_venues=["松岡美術館"])
        self.assertIn("今回取得できなかった施設", html_text)
        self.assertIn("松岡美術館", html_text)

    def test_home_shows_new_change_and_failure_with_links(self):
        records = [rec("n", "新着", title="新展", url="https://n.example/1"),
                   rec("k", "取得失敗・前回データ", title="保持展", url="https://k.example/1")]
        html_text = H.news_section_html(records)
        self.assertIn("href='https://n.example/1'", html_text)
        self.assertIn("取得失敗・前回データ</span>", html_text)
        self.assertNotIn("None", html_text)
        self.assertNotIn("NaN", html_text)
        self.assertEqual(html_text.count("<li class='news-row'>"), 2)


if __name__ == "__main__":
    unittest.main()
