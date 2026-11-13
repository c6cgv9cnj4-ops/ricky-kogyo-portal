"""指示文の表示ページ（ローカル）。指示ごとに「コピー」ボタンを付け、指示本文だけを一括コピーできるようにする。

入力：テキストファイル。指示は次の区切り行で分ける（区切りの外の文字は、どの指示にも含めない）。
    === 指示のタイトル ===
    指示本文（改行・空白はそのまま）
    === 次の指示 ===
    ...
区切りが1つも無い場合は、ファイル全体を1つの指示として扱う。
出力：HTML（既定は instruction_board/index.html）。docs/ 配下には出力しない（公開を防ぐため）。
外部通信はしない。コピーはブラウザのクリップボード機能を使い、使えない場合は本文欄を選択して手動でコピーできる。
"""
import argparse
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS_ROOT = os.path.join(HERE, "docs")
MARKER = re.compile(r"^=== (.+?) ===\s*$")


def parse_blocks(text):
    """区切りで指示を分ける。戻り値：[(タイトル, 本文), ...]。本文は前後の空行だけを除き、中の改行は保つ。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    marks = [i for i, line in enumerate(lines) if MARKER.match(line)]
    if not marks:
        body = _trim(lines)
        return [("指示", body)] if body else []
    blocks = []
    for n, i in enumerate(marks):
        title = MARKER.match(lines[i]).group(1).strip()
        end = marks[n + 1] if n + 1 < len(marks) else len(lines)
        body = _trim(lines[i + 1:end])
        blocks.append((title, body))
    return blocks


def _trim(lines):
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[start:end])


def _json_for_script(value):
    """HTMLのscriptに安全に埋め込めるJSON（< > & を \\uXXXX にする。</script> で壊れない）。"""
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>指示文</title>
<style>
body{font-family:-apple-system,sans-serif;margin:16px;line-height:1.6;background:#fff;color:#111}
section{border:1px solid #ccc;border-radius:6px;padding:12px;margin:12px 0}
h2{font-size:1.05em;margin:0 0 8px}
pre{white-space:pre-wrap;word-break:break-word;background:#f6f6f6;padding:8px;border-radius:4px;margin:0 0 8px}
textarea{width:100%;min-height:8em;font:inherit;box-sizing:border-box}
button{font:inherit;padding:6px 14px;cursor:pointer}
.status{margin-left:8px;font-weight:bold}
.ok{color:#0a7d2c}.ng{color:#b00020}
</style></head><body>
<h1>指示文</h1>
<p>「コピー」は、各指示の本文だけをクリップボードへ入れます（見出しと説明は含みません）。</p>
__SECTIONS__
<script type="application/json" id="instructions">__DATA__</script>
<script>
(function () {
  var items = JSON.parse(document.getElementById("instructions").textContent);
  function show(i, ok, msg) {
    var s = document.getElementById("status-" + i);
    s.textContent = msg;
    s.className = "status " + (ok ? "ok" : "ng");
  }
  function selectAll(i) {
    var ta = document.getElementById("body-" + i);
    ta.focus();
    ta.select();
  }
  function fallbackCopy(i) {
    selectAll(i);
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    return ok;
  }
  document.querySelectorAll("textarea[data-index]").forEach(function (ta) {
    ta.value = items[Number(ta.getAttribute("data-index"))].body;
  });
  document.querySelectorAll("button[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var i = Number(btn.getAttribute("data-copy"));
      var text = items[i].body;
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(function () {
          show(i, true, "コピーしました（" + items[i].title + "）");
        }, function () {
          if (fallbackCopy(i)) { show(i, true, "コピーしました"); }
          else { show(i, false, "コピーできませんでした。選択された本文欄を手動でコピーしてください"); }
        });
      } else {
        if (fallbackCopy(i)) { show(i, true, "コピーしました"); }
        else { show(i, false, "コピーできませんでした。選択された本文欄を手動でコピーしてください"); }
      }
    });
  });
})();
</script>
</body></html>
"""


def render(blocks):
    sections = []
    for i, (title, body) in enumerate(blocks):
        sections.append(
            f"<section>\n<h2>{html.escape(title)}</h2>\n"
            f"<textarea id=\"body-{i}\" data-index=\"{i}\" readonly></textarea>\n"
            f"<div><button type=\"button\" data-copy=\"{i}\">コピー</button>"
            f"<span class=\"status\" id=\"status-{i}\" role=\"status\"></span></div>\n</section>")
    data = [{"title": t, "body": b} for t, b in blocks]
    return PAGE.replace("__SECTIONS__", "\n".join(sections)).replace("__DATA__", _json_for_script(data))


def write(input_path, out_path):
    """入力を読み、HTMLを書く。docs/ 配下は拒否する（公開フォルダへ出さない）。"""
    absout = os.path.abspath(out_path)
    if absout == DOCS_ROOT or absout.startswith(DOCS_ROOT + os.sep):
        raise ValueError(f"docs/ 配下には出力しません: {out_path}")
    with open(input_path, encoding="utf-8") as f:
        blocks = parse_blocks(f.read())
    if not blocks:
        raise ValueError("指示文がありません（空の入力）")
    os.makedirs(os.path.dirname(absout), exist_ok=True)
    tmp = absout + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(render(blocks))
    os.replace(tmp, absout)
    return {"out": absout, "blocks": len(blocks)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", required=True, help="指示文のテキストファイル（=== タイトル === で区切る）")
    ap.add_argument("--out", default=os.path.join(HERE, "instruction_board", "index.html"))
    a = ap.parse_args()
    try:
        print(write(a.input, a.out))
    except ValueError as e:
        sys.stderr.write(f"出力しません: {e}\n")
        sys.exit(2)
