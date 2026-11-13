import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import geigeki_events as G  # noqa: E402
import event_records as ER  # noqa: E402


def page(schedule, sales, venue="コンサートホール"):
    """公演ページの要点だけを組み立てる（実ページの文言を短く写したもの。本文の保存はしない）。"""
    return ('<html><head><meta property="og:title" content="テスト公演"></head><body>'
            f'<div>日程 {schedule} 会場 {venue} 主催 テスト主催</div>'
            f'<div>発売日 {sales} チケット取扱 東京芸術劇場ボックスオフィス</div></body></html>')


class SalesTest(unittest.TestCase):
    def test_S01_presale_and_general_year_inferred_by_weekday(self):
        # 実ページ（concert369 相当）：年の記載なし。2月23日は月曜＝2026年と一致
        html = page("2026年5月20日（水）、7月22日（水）", "芸劇先行(WEB先着)：2月23日(月・祝)10:00～2月27日(金)23:59 一般発売：2月28日(土)10:00～")
        d = G.parse_detail(html, "https://www.geigeki.jp/performance/concert369/")
        rec = G.to_record(d, "新着", "2026-10-18T13:00:00+09:00")
        self.assertEqual(rec["dates"]["ticket_sale_start"]["value"], "2026-02-23T10:00+09:00")
        self.assertEqual(rec["dates"]["ticket_sale_start"]["precision"], "datetime")
        self.assertEqual(rec["dates"]["general_sale_start"]["value"], "2026-02-28T10:00+09:00")
        self.assertEqual(ER.validate_record(rec), [])

    def test_S02_single_general_sale_with_weekday(self):
        # 実ページ（theater415 相当）：「一般発売：8月8日（土）10:00～」。8月8日は土曜＝2026年
        html = page("2026年10月9日(金)〜2026年11月3日(火)", "一般発売：8月8日（土）10:00～ 申込み 先着順にて受付。", venue="アトリエイースト")
        d = G.parse_detail(html, "https://www.geigeki.jp/performance/theater415-t415-19/")
        rec = G.to_record(d, "通常", "2026-10-18T13:00:00+09:00")
        self.assertEqual(rec["dates"]["general_sale_start"]["value"], "2026-08-08T10:00+09:00")
        self.assertEqual(rec["dates"]["ticket_sale_start"]["value"], None)   # 先行の記載なし
        self.assertEqual(ER.validate_record(rec), [])

    def test_S03_multiple_general_sales_stay_unknown(self):
        # 実ページ（lunchtime 系）：回ごとの発売。1件に決められないため unknown
        html = page("2026年7月23日(木)、2026年11月5日(木)", "Vol.161 一般発売：2026年3月12日(木)10:00～ Vol.162 一般発売：2026年9月17日(木)10:00～")
        d = G.parse_detail(html, "https://www.geigeki.jp/performance/lunchtime_r08/")
        rec = G.to_record(d, "通常", "2026-10-18T13:00:00+09:00")
        self.assertEqual(len(d["sales"]), 2)
        self.assertEqual(rec["dates"]["general_sale_start"]["precision"], "unknown")
        self.assertIsNone(rec["dates"]["general_sale_start"]["value"])

    def test_S04_no_sale_section_is_unknown_not_guessed(self):
        html = page("2014年〜", "")
        html = html.replace("発売日  チケット取扱", "")  # 発売日の欄がないページ
        d = G.parse_detail(html, "https://www.geigeki.jp/performance/goa/")
        self.assertEqual(d["sales"], [])
        rec = G.to_record(d, "通常", "2026-10-18T13:00:00+09:00")
        self.assertEqual(rec["dates"]["ticket_sale_start"]["precision"], "unknown")

    def test_S05_ambiguous_year_not_guessed(self):
        # 2月29日は閏年のみ。候補が1つに決まらない場合は年を推測しない
        html = page("2027年2月10日(水)", "一般発売：2月29日(月)10:00～")
        d = G.parse_detail(html, "https://www.geigeki.jp/performance/x/")
        self.assertEqual(d["sales"][0]["start"], None)
        self.assertEqual(d["sales"][0]["year_source"], "未確定")
        rec = G.to_record(d, "通常", "2026-10-18T13:00:00+09:00")
        self.assertEqual(rec["dates"]["general_sale_start"]["precision"], "unknown")

    def test_S06_end_date_without_year_is_kept(self):
        # P1：東京舞台芸術祭の終了日（年なし）。開始 2026-09-01、終了 11月3日 → 2026-11-03
        html = page("2026年9月1日（火）～11月3日（火・祝）", "一般発売：8月8日（土）10:00～")
        d = G.parse_detail(html, "https://www.geigeki.jp/performance/theater415-t415-0/")
        self.assertEqual(d["event_start"], datetime.date(2026, 9, 1))
        self.assertEqual(d["event_end"], datetime.date(2026, 11, 3))

    def test_S07_sale_fields_do_not_store_body_text(self):
        html = page("2026年5月20日（水）", "芸劇先行(WEB先着)：2月23日(月・祝)10:00～")
        d = G.parse_detail(html, "https://www.geigeki.jp/performance/concert369/")
        rec = G.to_record(d, "通常", "2026-10-18T13:00:00+09:00")
        self.assertEqual(rec["genre_extra"], {})

    # ---- 方式B：sales / sales_status（合成データ）----
    def _rec(self, schedule, sales_text, url="https://www.geigeki.jp/performance/x/"):
        html = page(schedule, sales_text)
        d = G.parse_detail(html, url)
        return G.to_record(d, "新着", "2026-10-18T13:00:00+09:00")

    def test_S08_multi_sales_all_kept_with_kind_and_precision(self):
        rec = self._rec("2026年7月23日(木)、2026年11月5日(木)",
                        "Vol.161 一般発売：2026年3月12日(木)10:00～ Vol.162 一般発売：2026年9月17日(木)10:00～ Vol.163 一般発売：2026年11月5日(木)10:00～")
        self.assertEqual([x["start"] for x in rec["sales"]],
                         ["2026-03-12T10:00+09:00", "2026-09-17T10:00+09:00", "2026-11-05T10:00+09:00"])
        self.assertEqual({x["kind"] for x in rec["sales"]}, {"一般発売"})
        self.assertTrue(all(x["precision"] == "datetime" and x["timezone"] == "Asia/Tokyo" for x in rec["sales"]))
        self.assertEqual(rec["sales_status"], "取得・解析済み（発売あり）")
        self.assertEqual(ER.validate_record(rec), [])

    def test_S09_presale_and_general_distinguished(self):
        rec = self._rec("2026年5月20日（水）", "芸劇先行(WEB先着)：2月23日(月・祝)10:00～ 一般発売：2月28日(土)10:00～")
        kinds = [(x["kind"], x["start"]) for x in rec["sales"]]
        self.assertEqual(kinds, [("先行受付", "2026-02-23T10:00+09:00"), ("一般発売", "2026-02-28T10:00+09:00")])
        self.assertEqual(rec["dates"]["ticket_sale_start"]["value"], "2026-02-23T10:00+09:00")   # 単発の従来動作
        self.assertEqual(rec["dates"]["general_sale_start"]["value"], "2026-02-28T10:00+09:00")

    def test_S10_single_sale_keeps_single_fields(self):
        rec = self._rec("2026年10月9日(金)", "一般発売：8月8日（土）10:00～")
        self.assertEqual(rec["dates"]["general_sale_start"]["value"], "2026-08-08T10:00+09:00")
        self.assertEqual(rec["dates"]["ticket_sale_start"]["precision"], "unknown")
        self.assertEqual(len(rec["sales"]), 1)

    def test_S11_sales_status_three_states(self):
        has = self._rec("2026年5月20日（水）", "一般発売：2月28日(土)10:00～")
        none = G.to_record(G.parse_detail(page("2014年〜", "").replace("発売日", "x"), "https://www.geigeki.jp/performance/goa/"),
                           "通常", "2026-10-18T13:00:00+09:00")
        unknown = G.to_record(G.parse_detail(page("2026年5月20日（水）", "発売は後日お知らせ（区分なし）"),
                                             "https://www.geigeki.jp/performance/y/"), "通常", "2026-10-18T13:00:00+09:00")
        self.assertEqual(has["sales_status"], "取得・解析済み（発売あり）")
        self.assertEqual(none["sales_status"], "ページ確認済み（発売欄なし）")
        self.assertEqual(none["sales"], [])
        self.assertEqual(unknown["sales_status"], "未確認")   # 発売欄はあるが解析できない → 発売なしとしない
        self.assertEqual(unknown["sales"], [])

    def test_S12_undated_year_kept_but_not_a_deadline(self):
        rec = self._rec("2027年2月10日(水)", "一般発売：2月29日(月)10:00～")
        item = rec["sales"][0]
        self.assertIsNone(item["start"]); self.assertEqual(item["precision"], "unknown"); self.assertEqual(item["year_source"], "未確定")
        self.assertEqual(ER.validate_record(rec), [])
        self.assertEqual(ER.upcoming_deadlines([rec], datetime.date(2027, 2, 1), days=14), [])

    def test_S13_upcoming_includes_only_future_dated_sales(self):
        rec = self._rec("2026年7月23日(木)、2026年11月5日(木)",
                        "Vol.161 一般発売：2026年3月12日(木)10:00～ Vol.162 一般発売：2026年10月25日(日)10:00～ Vol.163 一般発売：2026年12月1日(火)10:00～")
        rows = ER.upcoming_deadlines([rec], datetime.date(2026, 10, 18), days=14)
        self.assertEqual([(r["date"], r["kind"]) for r in rows], [("2026-10-25", "一般発売")])   # 過去(3/12)と14日超(12/1)は含まない

    def test_S14_no_double_listing_with_single_field(self):
        rec = self._rec("2026年5月20日（水）", "一般発売：10月20日(火)10:00～")
        rows = ER.upcoming_deadlines([rec], datetime.date(2026, 10, 18), days=14)
        self.assertEqual(len(rows), 1)   # 単一欄と sales に同じ発売機会が二重に出ない

    def test_S15_old_record_without_sales_still_valid_and_listed(self):
        old = {"id": "music|geigeki|old", "genre": "公演・イベント", "title": "旧", "source_url": "https://www.geigeki.jp/performance/old/",
               "state": "通常", "confidence": "確認済み", "last_checked_at": "2026-10-01T00:00:00+09:00",
               "dates": {k: ER.unknown_date() for k in ER.DATE_KEYS}, "genre_extra": {}}
        old["dates"]["general_sale_start"] = ER.datetime_date("2026-10-20T10:00+09:00", "Asia/Tokyo", old["source_url"])
        self.assertEqual(ER.validate_record(old), [])
        rows = ER.upcoming_deadlines([old], datetime.date(2026, 10, 18), days=14)
        self.assertEqual(len(rows), 1)

    def test_S16_schedule_html_shows_kind_and_status(self):
        has = self._rec("2026年7月23日(木)", "Vol.161 一般発売：2026年3月12日(木)10:00～ 芸劇先行(WEB先着)：2月23日(月・祝)10:00～")
        unk = G.to_record(G.parse_detail(page("2026年5月20日（水）", "発売は後日（区分なし）"), "https://www.geigeki.jp/performance/z/"),
                          "通常", "2026-10-18T13:00:00+09:00")
        html = ER.render_schedule_html([has, unk])
        self.assertIn("一般発売 2026-03-12T10:00+09:00", html)
        self.assertIn("先行受付 2026-02-23T10:00+09:00", html)
        self.assertIn("発売：未確認", html)

    def test_S17_genre_extra_stays_empty_with_sales(self):
        rec = self._rec("2026年7月23日(木)", "Vol.161 一般発売：2026年3月12日(木)10:00～")
        self.assertEqual(rec["genre_extra"], {})


if __name__ == "__main__":
    unittest.main()
