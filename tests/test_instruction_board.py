"""指示文の表示ページ（instruction_board）のテスト。一時フォルダだけを使う。
確認すること：コピー対象は指示本文だけ／改行と長文が壊れない／複数の指示が混ざらない／docs/ へ出さない。"""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import instruction_board as IB  # noqa: E402


def payload(page):
    m = re.search(r'<script type="application/json" id="instructions">(.*?)</script>', page, re.S)
    return json.loads(m.group(1))


class ParseTest(unittest.TestCase):
    def test_blocks_keep_only_bodies_and_titles(self):
        text = ("前置きの説明（どの指示にも含めない）\n"
                "=== 指示A ===\n1行目\n2行目\n=== 指示B ===\n別の指示\n最後の行\n")
        self.assertEqual(IB.parse_blocks(text), [("指示A", "1行目\n2行目"), ("指示B", "別の指示\n最後の行")])

    def test_no_markers_means_one_block_of_the_whole_text(self):
        self.assertEqual(IB.parse_blocks("\n\n全文\n\n"), [("指示", "全文")])

    def test_inner_blank_lines_and_spaces_are_preserved(self):
        text = "=== A ===\n\n  字下げ\n\n空行の後\n\t タブ\n"
        self.assertEqual(IB.parse_blocks(text), [("A", "  字下げ\n\n空行の後\n\t タブ")])

    def test_crlf_is_normalized_to_lf(self):
        self.assertEqual(IB.parse_blocks("=== A ===\r\n一行\r\n二行\r\n"), [("A", "一行\n二行")])

    def test_empty_block_is_kept_as_empty_body(self):
        self.assertEqual(IB.parse_blocks("=== A ===\n=== B ===\n本文\n"), [("A", ""), ("B", "本文")])


class RenderTest(unittest.TestCase):
    TRICKY = 'コードの例 </script><script>alert(1)</script> "引用" \'単引用\' `バッククォート` a & b <b>太字</b>\n改行\tタブ'

    def test_payload_round_trips_exact_body_including_tricky_text(self):
        page = IB.render([("A", self.TRICKY), ("B", "二つ目")])
        data = payload(page)
        self.assertEqual([d["body"] for d in data], [self.TRICKY, "二つ目"])
        self.assertNotIn("</script><script>alert", page)     # 埋め込みで壊れていないこと

    def test_long_text_round_trips(self):
        long = "".join(f"行{i}：本文の長い一文です。\n" for i in range(5000))
        page = IB.render([("長文", long)])
        self.assertEqual(payload(page)[0]["body"], long)

    def test_each_copy_button_maps_to_its_own_body_and_titles_are_not_copied(self):
        page = IB.render([("見出しX", "本文X"), ("見出しY", "本文Y")])
        self.assertIn('data-copy="0"', page)
        self.assertIn('data-copy="1"', page)
        data = payload(page)
        self.assertEqual(data[0], {"title": "見出しX", "body": "本文X"})
        self.assertEqual(data[1], {"title": "見出しY", "body": "本文Y"})
        self.assertNotIn("見出しX", json.dumps([d["body"] for d in data], ensure_ascii=False))

    def test_html_in_titles_is_escaped(self):
        page = IB.render([("<img src=x>", "本文")])
        self.assertIn("&lt;img src=x&gt;", page)
        self.assertNotIn("<img src=x>", page)

    def test_failure_message_points_to_manual_copy(self):
        page = IB.render([("A", "本文")])
        self.assertIn("手動でコピー", page)
        self.assertIn("execCommand(\"copy\")", page)          # クリップボードAPIが使えない時の代替


class WriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="instr_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_html_outside_docs(self):
        src = os.path.join(self.tmp, "in.txt")
        with open(src, "w", encoding="utf-8") as f:
            f.write("=== A ===\n本文A\n=== B ===\n本文B\n")
        out = os.path.join(self.tmp, "board", "index.html")
        result = IB.write(src, out)
        self.assertEqual(result["blocks"], 2)
        self.assertTrue(os.path.exists(out))
        self.assertFalse(os.path.exists(out + ".tmp"))

    def test_refuses_docs_output(self):
        src = os.path.join(self.tmp, "in.txt")
        with open(src, "w", encoding="utf-8") as f:
            f.write("本文")
        with self.assertRaises(ValueError):
            IB.write(src, os.path.join(IB.DOCS_ROOT, "x.html"))

    def test_refuses_empty_input(self):
        src = os.path.join(self.tmp, "empty.txt")
        with open(src, "w", encoding="utf-8") as f:
            f.write("\n  \n")
        with self.assertRaises(ValueError):
            IB.write(src, os.path.join(self.tmp, "o.html"))


if __name__ == "__main__":
    unittest.main()
