"""監視対象の自動生成（watch_targets）。架空データ・一時フォルダのみ。通信なし。
確認すること：情報源の状態（許可待ち・台帳に無い）を推定せず表示する／要確認を一覧にする／同名を結ばない。"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import film_watch as FW  # noqa: E402
import watch_targets as WT  # noqa: E402


class WatchTargetsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="watch_")
        self.film = os.path.join(self.tmp, "film")
        s = FW.Store(self.film)
        self.p1 = FW.register_person(s, "架空 監督W", [], "テスト用架空データ", "https://example.invalid/p",
                                     disambiguator="1966年生", checked_on="2026-10-21")
        self.p2 = FW.register_person(s, "架空 脚本W", [], "テスト用架空データ", "")
        self.w = FW.register_work(s, "架空作品W", "テスト用架空データ", "https://unknown-site.invalid/w")
        FW.link_credit(s, self.w, self.p1, "監督")
        FW.link_credit(s, self.w, self.p2, "脚本")
        FW.set_release(s, self.w, "公開日確定", "", "", "2027-09-01")          # 出典なしの確定日

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sources_are_listed_with_status_and_never_auto_allowed(self):
        data = WT.build(self.film)
        srcs = data["作品"][0]["情報源候補"]
        self.assertIn("unknown-site.invalid", srcs)
        self.assertEqual(srcs["unknown-site.invalid"]["status"], "台帳に無い（許可待ち）")
        self.assertTrue(all("許可確認" not in v["status"] for v in srcs.values()))

    def test_review_items_cover_missing_source_and_undated_confirmation(self):
        review = WT.build(self.film)["要確認"]
        self.assertTrue(any("公開日の出典または確認日が無い" in x for x in review))
        self.assertTrue(any("架空 脚本W：区別情報" in x for x in review))

    def test_targets_list_people_with_roles_and_identity_fields(self):
        target = WT.build(self.film)["作品"][0]
        names = sorted((c["名前"], c["役割"]) for c in target["関係者"])
        self.assertEqual(names, [("架空 監督W", "監督"), ("架空 脚本W", "脚本")])
        self.assertEqual([c["区別情報"] for c in target["関係者"] if c["名前"] == "架空 監督W"], ["1966年生"])


if __name__ == "__main__":
    unittest.main()
