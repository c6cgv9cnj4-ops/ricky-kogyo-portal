"""指示文ページのコピー処理（埋め込みスクリプト）を、最小限のDOM代替で Node 上で実行して確認する。
実ブラウザの代わりではない（クリップボードの実機動作は未確認）。Node が無い環境では飛ばす。"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import instruction_board as IB  # noqa: E402

NODE = shutil.which("node")

STUB = r"""
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const mode = process.argv[3];            // "ok" | "reject" | "noapi"
const script = html.split('<script>')[1].split('</script>')[0];
const payload = JSON.parse(html.match(/id="instructions">([\s\S]*?)<\/script>/)[1]);
const nodes = {};
function el(id, attrs) {
  return { id, attrs: attrs || {}, value: "", textContent: "", className: "", handlers: {}, selected: false,
    getAttribute(k) { return this.attrs[k] === undefined ? null : this.attrs[k]; },
    addEventListener(t, f) { this.handlers[t] = f; }, focus() {}, select() { this.selected = true; } };
}
const textareas = [], buttons = [];
payload.forEach((_, i) => {
  textareas.push(el("body-" + i, { "data-index": String(i) }));
  buttons.push(el(null, { "data-copy": String(i) }));
  nodes["status-" + i] = el("status-" + i);
});
const copied = [];
const document = {
  getElementById(id) { if (id === "instructions") return { textContent: html.match(/id="instructions">([\s\S]*?)<\/script>/)[1] };
                       return nodes[id] || textareas.find(t => t.id === id); },
  querySelectorAll(sel) { return sel.startsWith("textarea") ? textareas : buttons; },
  execCommand() { return mode !== "noapi"; },
};
const navigator = mode === "noapi" ? {} : { clipboard: { writeText(t) { copied.push(t);
  return mode === "reject" ? Promise.reject(new Error("denied")) : Promise.resolve(); } } };
const window = { isSecureContext: true };
eval(script);
(async () => {
  const out = { values: textareas.map(t => t.value) };
  for (const b of buttons) { b.handlers.click(); }
  await new Promise(r => setTimeout(r, 10));
  out.copied = copied;
  out.status = payload.map((_, i) => ({ text: nodes["status-" + i].textContent, cls: nodes["status-" + i].className }));
  out.selected = textareas.map(t => t.selected);
  process.stdout.write(JSON.stringify(out));
})();
"""


@unittest.skipUnless(NODE, "node が無い環境では実行しない")
class CopyScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ib_js_")
        self.blocks = [("A", "全文A\n改行 \"引用\" </script> `x`\n\n最後"), ("B", "二つ目だけ")]
        self.page = os.path.join(self.tmp, "index.html")
        with open(self.page, "w", encoding="utf-8") as f:
            f.write(IB.render(self.blocks))
        self.stub = os.path.join(self.tmp, "stub.js")
        with open(self.stub, "w", encoding="utf-8") as f:
            f.write(STUB)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_mode(self, mode):
        out = subprocess.run([NODE, self.stub, self.page, mode], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_full_body_copied_exactly_per_block(self):
        r = self.run_mode("ok")
        self.assertEqual(r["copied"], [self.blocks[0][1], self.blocks[1][1]])   # 本文だけ、改行つき、混ざらない
        self.assertEqual(r["values"], [self.blocks[0][1], self.blocks[1][1]])   # 本文欄にも同じ内容

    def test_success_message_is_shown(self):
        r = self.run_mode("ok")
        self.assertIn("コピーしました", r["status"][0]["text"])
        self.assertEqual(r["status"][0]["cls"], "status ok")

    def test_clipboard_rejected_falls_back_to_selection_and_copy_command(self):
        r = self.run_mode("reject")
        self.assertIn("コピーしました", r["status"][0]["text"])                  # execCommand が成功した場合
        self.assertEqual(r["selected"], [True, True])                           # 本文欄を選択して代替

    def test_no_clipboard_api_and_no_command_shows_manual_message(self):
        r = self.run_mode("noapi")
        self.assertEqual(r["status"][0]["cls"], "status ng")
        self.assertIn("手動でコピー", r["status"][0]["text"])
        self.assertEqual(r["copied"], [])


if __name__ == "__main__":
    unittest.main()
