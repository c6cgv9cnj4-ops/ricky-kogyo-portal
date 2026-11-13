"""詳細ページの並列取得（prefetch_ticket_info）のテスト。ネットワークは使わず、取得関数は架空の関数に差し替える。"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import culture_exhibition_dashboard as D  # noqa: E402


class PrefetchTest(unittest.TestCase):
    def setUp(self):
        self.saved = D.fetch_ticket_info
        self.calls = []
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def tearDown(self):
        D.fetch_ticket_info = self.saved

    def _fake(self, delay=0.0):
        def fetch(url):
            with self.lock:
                self.calls.append(url)
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(delay)
                return (f"{len(url)}円", "", "", True)   # 架空の結果（URLから決まる）
            finally:
                with self.lock:
                    self.active -= 1
        return fetch

    def test_parallel_result_equals_sequential(self):
        links = [f"https://example.invalid/{i}" for i in range(20)]
        D.fetch_ticket_info = self._fake()
        sequential = D.prefetch_ticket_info(links, workers=1)
        parallel = D.prefetch_ticket_info(links, workers=4)
        self.assertEqual(sequential, parallel)
        self.assertEqual(list(parallel), links)   # 順序も保たれる

    def test_duplicate_links_are_fetched_once(self):
        D.fetch_ticket_info = self._fake()
        result = D.prefetch_ticket_info(["https://example.invalid/a", "https://example.invalid/a",
                                         "https://example.invalid/b"], workers=4)
        self.assertEqual(sorted(self.calls), ["https://example.invalid/a", "https://example.invalid/b"])
        self.assertEqual(set(result), {"https://example.invalid/a", "https://example.invalid/b"})

    def test_empty_input(self):
        D.fetch_ticket_info = self._fake()
        self.assertEqual(D.prefetch_ticket_info([]), {})
        self.assertEqual(self.calls, [])

    def test_workers_one_is_sequential(self):
        D.fetch_ticket_info = self._fake(delay=0.01)
        D.prefetch_ticket_info([f"https://example.invalid/{i}" for i in range(6)], workers=1)
        self.assertEqual(self.max_active, 1)

    def test_concurrency_is_capped_at_workers(self):
        D.fetch_ticket_info = self._fake(delay=0.05)
        D.prefetch_ticket_info([f"https://example.invalid/{i}" for i in range(12)], workers=3)
        self.assertLessEqual(self.max_active, 3)
        self.assertGreater(self.max_active, 1)   # 並列になっていること

    def test_per_host_limit_and_total_limit_are_both_respected(self):
        # 架空の3ホスト（ネットワークなし）。全体4並列・同一ホスト2並列の上限を、モックの同時数で確認する
        import urllib.parse
        per_host_active = {}
        per_host_max = {}

        def fake(url):
            host = urllib.parse.urlsplit(url).netloc
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                per_host_active[host] = per_host_active.get(host, 0) + 1
                per_host_max[host] = max(per_host_max.get(host, 0), per_host_active[host])
            try:
                time.sleep(0.05)
                return ("", "", "", True)
            finally:
                with self.lock:
                    self.active -= 1
                    per_host_active[host] -= 1
        D.fetch_ticket_info = fake
        links = [f"https://host{h}.example.invalid/{i}" for h in range(3) for i in range(6)]
        result = D.prefetch_ticket_info(links, workers=4, per_host=2)
        self.assertEqual(len(result), len(links))
        self.assertLessEqual(self.max_active, 4)
        self.assertGreater(self.max_active, 2)                  # 複数ホストで並列になっていること
        for host, peak in per_host_max.items():
            self.assertLessEqual(peak, 2, host)
        self.assertEqual(sorted(per_host_max), [f"host{h}.example.invalid" for h in range(3)])

    def test_network_error_and_timeout_are_not_raised_in_parallel_mode(self):
        # 実際の取得元には接続しない。requests.get を差し替えて、タイムアウトと接続エラーを再現する
        import requests
        saved_get = D.requests.get

        def failing_get(url, **kwargs):
            if url.endswith("/timeout"):
                raise requests.Timeout("テスト用のタイムアウト")
            raise requests.ConnectionError("テスト用の接続エラー")
        D.requests.get = failing_get
        try:
            urls = ["http://127.0.0.1:9/timeout", "http://127.0.0.1:9/refused", "http://127.0.0.1:9/timeout"]
            result = D.prefetch_ticket_info(urls, workers=4)
        finally:
            D.requests.get = saved_get
        self.assertEqual(set(result), set(urls))
        for url in set(urls):
            self.assertEqual(result[url], (None, None, None, False))   # 読めなかった扱い。例外は外へ出ない

    def test_unexpected_worker_exception_propagates_same_as_sequential(self):
        def boom(url):
            if url.endswith("/bad"):
                raise ValueError("テスト用の想定外の例外")
            return ("", "", "", True)
        D.fetch_ticket_info = boom
        links = ["https://example.invalid/ok", "https://example.invalid/bad"]
        with self.assertRaises(ValueError):
            D.prefetch_ticket_info(links, workers=1)
        with self.assertRaises(ValueError):
            D.prefetch_ticket_info(links, workers=4)

    def test_uses_patched_fetch_function_so_existing_harness_still_works(self):
        D.fetch_ticket_info = lambda url: ("", "", "", False)
        result = D.prefetch_ticket_info(["https://example.invalid/x"])
        self.assertEqual(result["https://example.invalid/x"], ("", "", "", False))


if __name__ == "__main__":
    unittest.main()
