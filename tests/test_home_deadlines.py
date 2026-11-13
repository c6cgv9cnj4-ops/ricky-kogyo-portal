import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import event_records as ER  # noqa: E402
import geigeki_events as G  # noqa: E402
import auto_fetch_all as AF  # noqa: E402

TODAY = datetime.date(2026, 10, 18)
URL = "https://www.geigeki.jp/performance/t/"


def page(schedule, sales, venue="コンサートホール", title="テスト公演"):
    return ('<html><head><meta property="og:title" content="%s"></head><body>'
            '<div>日程 %s 会場 %s 主催 テスト主催</div>'
            '<div>発売日 %s チケット取扱 東京芸術劇場ボックスオフィス</div></body></html>') % (title, schedule, venue, sales)


def rec(schedule, sales, title="テスト公演", url=URL):
    return G.to_record(G.parse_detail(page(schedule, sales, title=title), url), "新着", "2026-10-18T13:00:00+09:00")


class HomeDeadlineTest(unittest.TestCase):
    def test_H01_fourteen_day_sales_shown_with_kind(self):
        r = rec("2026年11月5日(木)", "先行(WEB先着)：10月25日(日)10:00～ 一般発売：11月1日(日)10:00～")
        html = AF.news_section_html([r], today=TODAY)
        self.assertIn("今後14日以内の発売・締切", html)
        self.assertIn("2026-10-25T10:00+09:00", html)
        self.assertIn("先行受付", html)
        self.assertIn("一般発売", html)
        self.assertIn("テスト公演", html)

    def test_H02_past_and_undated_not_shown(self):
        past = rec("2026年5月20日(水)", "一般発売：2月28日(土)10:00～")          # 過去（年は曜日で推定）
        undated = rec("2027年2月10日(水)", "一般発売：2月29日(月)10:00～", title="年不明")
        html = ER.render_deadline_html(ER.upcoming_deadlines([past, undated], TODAY, days=14))
        self.assertIn("今後14日以内の発売・締切はありません", html)
        self.assertNotIn("年不明", html)

    def test_H03_same_sale_not_listed_twice(self):
        r = rec("2026年11月5日(木)", "一般発売：10月20日(火)10:00～")
        r["dates"]["general_sale_start"] = ER.datetime_date("2026-10-20T10:00+09:00", "Asia/Tokyo", URL)
        html = AF.news_section_html([r], today=TODAY)
        self.assertEqual(html.count("2026-10-20T10:00+09:00"), 1)

    def test_H04_status_labels_distinguished_in_schedule(self):
        has = rec("2026年11月5日(木)", "一般発売：11月20日(金)10:00～", title="A")
        none = G.to_record(G.parse_detail(page("2026年11月5日(木)", "").replace("発売日", "x"), URL), "通常", "2026-10-18T13:00:00+09:00")
        unk = G.to_record(G.parse_detail(page("2026年11月5日(木)", "後日（区分なし）", title="C"), URL), "通常", "2026-10-18T13:00:00+09:00")
        html = AF.news_section_html([has, none, unk], today=TODAY)
        self.assertIn("発売：一般発売 2026-11-20T10:00+09:00", html)
        self.assertIn("発売：ページに発売の記載なし", html)
        self.assertIn("発売：未確認", html)

    def test_H05_old_json_without_sales_still_renders(self):
        old = {"id": "music|geigeki|old", "genre": "公演・イベント", "title": "旧データ", "source_url": URL,
               "state": "通常", "confidence": "確認済み", "last_checked_at": "2026-10-01T00:00:00+09:00", "venue": "会場",
               "dates": {k: ER.unknown_date() for k in ER.DATE_KEYS}, "genre_extra": {}}
        old["dates"]["event_start"] = ER.day_date("2026-11-05", URL)
        html = AF.news_section_html([old], today=TODAY)
        self.assertIn("旧データ", html)
        self.assertIn("2026-11-05", html)
        self.assertIn("発売：未確認", html)         # sales_status なし＝未確認として表示（発売なしとは書かない）

    def test_H06_application_deadline_display_unchanged(self):
        r = rec("2026年11月5日(木)", "一般発売：11月20日(金)10:00～")
        r["dates"]["application_deadline"] = ER.day_date("2026-10-30", URL)
        html = AF.news_section_html([r], today=TODAY)
        self.assertIn("応募締切：2026-10-30", html)

    def test_H07_titles_are_escaped(self):
        r = rec("2026年11月5日(木)", "一般発売：10月20日(火)10:00～", title="<script>x</script>")
        html = AF.news_section_html([r], today=TODAY)
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
