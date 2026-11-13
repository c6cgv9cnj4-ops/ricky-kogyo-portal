"""経済ポータルの「今日」判定が JST で行われることの回帰テスト（外部通信なし）。

背景：GitHub Actions の実行環境は UTC。naive な datetime.now() を使うと、
23:00〜24:00 UTC（08:00〜09:00 JST）に JST の当日記事が「前日」扱いになり、日経が0件になっていた。
"""
import datetime
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import economic_portal_dashboard as EPD  # noqa: E402

UTC = datetime.timezone.utc


def runner_clock(utc_year, utc_month, utc_day, utc_hour, utc_minute):
    """UTC の実行環境を模した datetime 代替。naive now() は UTC（Actions と同じ）を返す。"""
    instant = datetime.datetime(utc_year, utc_month, utc_day, utc_hour, utc_minute, 0, tzinfo=UTC)

    class FakeDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    return FakeDatetime


class TestNikkeiDateFilter(unittest.TestCase):
    def entries(self):
        # 2026-10-22 08:30 JST の記事（UTC では 10-21 23:30）
        return [{"date": "2026-10-22T08:30:00+09:00", "title": "今朝の記事", "link": "https://example.test/a"}]

    def test_morning_jst_articles_are_kept_when_runner_is_still_previous_utc_day(self):
        # 2026-10-21 23:59 UTC = 2026-10-22 08:59 JST
        feed = types.SimpleNamespace(entries=self.entries(), status=200)
        with mock.patch.object(EPD.datetime, "datetime", runner_clock(2026, 10, 21, 23, 59)), \
             mock.patch.object(EPD.feedparser, "parse", return_value=feed):
            items = EPD.fetch_nikkei_today()
        self.assertEqual([i["title"] for i in items], ["今朝の記事"])
        self.assertEqual(items[0]["time"], "08:30")

    def test_articles_from_other_jst_day_are_still_excluded(self):
        feed = types.SimpleNamespace(entries=[{"date": "2026-10-21T20:00:00+09:00", "title": "前日の記事", "link": "x"}],
                                     status=200)
        with mock.patch.object(EPD.datetime, "datetime", runner_clock(2026, 10, 21, 23, 59)), \
             mock.patch.object(EPD.feedparser, "parse", return_value=feed):
            items = EPD.fetch_nikkei_today()
        self.assertEqual(items, [])

    def test_daytime_run_unchanged(self):
        # 2026-10-15 04:41 UTC = 13:41 JST（従来から正しく動いていた時間帯）
        feed = types.SimpleNamespace(entries=[{"date": "2026-10-15T12:11:00+09:00", "title": "昼の記事", "link": "y"}],
                                     status=200)
        with mock.patch.object(EPD.datetime, "datetime", runner_clock(2026, 10, 15, 4, 41)), \
             mock.patch.object(EPD.feedparser, "parse", return_value=feed):
            items = EPD.fetch_nikkei_today()
        self.assertEqual([i["title"] for i in items], ["昼の記事"])


class TestStampIsJst(unittest.TestCase):
    def test_now_jst_label_shows_jst_wall_clock(self):
        # 2026-10-21 23:59:02 UTC は JST では 2026-10-22 08:59:02
        with mock.patch.object(EPD.datetime, "datetime", runner_clock(2026, 10, 21, 23, 59)):
            self.assertEqual(EPD.now_jst(), "2026-10-22 08:59:00 JST")


YAHOO_HTML = """<html><body>
<a href="/articles/a1">今日の見出し 10/22(木) 08:30</a>
<a href="/articles/a2">前日の見出し 10/21(水) 20:00</a>
</body></html>"""


class TestYahooMediaDateFilter(unittest.TestCase):
    def fetch(self, utc_clock):
        resp = types.SimpleNamespace(text=YAHOO_HTML, raise_for_status=lambda: None)
        with mock.patch.object(EPD.datetime, "datetime", utc_clock), \
             mock.patch.object(EPD.requests, "get", return_value=resp):
            return EPD.fetch_yahoo_media_today("読売新聞", "yomiuri")

    def test_only_current_jst_day_is_kept_in_utc_morning_window(self):
        # 2026-10-21 23:59 UTC = 2026-10-22 08:59 JST。見出しは 10/22 のみ残る
        items = self.fetch(runner_clock(2026, 10, 21, 23, 59))
        self.assertEqual([i["title"] for i in items], ["今日の見出し"])

    def test_just_after_jst_midnight_uses_new_jst_day(self):
        # 2026-10-21 15:30 UTC = 2026-10-22 00:30 JST。UTC 基準だと 10/21 扱いになる境界
        items = self.fetch(runner_clock(2026, 10, 21, 15, 30))
        self.assertEqual([i["title"] for i in items], ["今日の見出し"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
