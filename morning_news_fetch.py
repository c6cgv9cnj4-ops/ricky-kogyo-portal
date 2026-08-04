import os
import datetime
from bs4 import BeautifulSoup

# ============================================================
# AI部長 朝刊 自動生成スクリプト
#
# 他7ページ（公式一次情報／近隣3市地域ポータル／4紙見出し比較／
# 経済ポータル／美術館・写真展／撮影スポット／バドミントン代表）の
# 「生成済みHTML」から見出しのみを実データとして抽出し、1ページに
# ダイジェスト表示する。新規の外部スクレイピングは行わず、他スクリプト
# が実際に取得した内容のみを引用するため、捏造・憶測を含まない。
#
# ※ 本スクリプトは他の全fetchスクリプトが実行された「後」に
#    実行すること（ワークフロー内の実行順序に注意）。
# ============================================================

OUTPUT_DIR = "docs/morning-news"
OUTPUT_HTML = os.path.join(OUTPUT_DIR, "index.html")
DOCS_DIR = "docs"
JST = datetime.timezone(datetime.timedelta(hours=9))


def extract_texts(theme_dir, selector, limit, exclude_if_has_span=False):
    path = os.path.join(DOCS_DIR, theme_dir, "index.html")
    if not os.path.exists(path):
        return None  # ページ未生成（取得不可）
    with open(path, encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")
    texts = []
    for tag in soup.select(selector):
        if exclude_if_has_span and tag.find("span") is not None:
            continue
        text = tag.get_text(separator=" ", strip=True)
        if text:
            texts.append(text)
        if len(texts) >= limit:
            break
    return texts


SECTIONS = [
    {"key": "official-primary", "title": "🏛️ 公式一次情報", "selector": "ul.list li a", "limit": 3},
    {"key": "local-portal", "title": "🏘️ 近隣3市 地域トピック", "selector": ".topic-title", "limit": 3},
    {"key": "newspaper", "title": "📰 4紙見出し比較", "selector": ".topic-title", "limit": 3},
    {"key": "economic", "title": "💹 経済ニュース", "selector": "h3", "limit": 3, "exclude_if_has_span": True},
    {"key": "culture-exhibition", "title": "🎨 美術館・写真展", "selector": "h3.title", "limit": 3},
    {"key": "photo-spot", "title": "📷 撮影スポット", "selector": "h3", "limit": 3},
    {"key": "badminton", "title": "🏸 バドミントン代表", "selector": "h2", "limit": 3},
]


def build_dataset():
    results = []
    for sec in SECTIONS:
        texts = extract_texts(
            sec["key"], sec["selector"], sec["limit"], sec.get("exclude_if_has_span", False)
        )
        results.append({**sec, "texts": texts})
    return results


def render(results, now_str, date_str):
    sections_html = ""
    for r in results:
        if r["texts"] is None:
            body = '<p class="empty">⚠️ 元ページが未生成のため、今回は表示できません。</p>'
        elif not r["texts"]:
            body = '<p class="empty">現在、表示できる項目はありません。</p>'
        else:
            items = "".join(f"<li>{t}</li>" for t in r["texts"])
            body = f"<ul>{items}</ul>"
        sections_html += f"""
        <section>
          <div class="sec-head">
            <h2>{r['title']}</h2>
            <a class="more" href="../{r['key']}/">全文を見る →</a>
          </div>
          {body}
        </section>"""

    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI部長 朝刊（{date_str}）</title>
<style>
  :root {{
    --bg:#f4f6f8; --card-bg:#ffffff; --text:#1a1f24; --sub:#5b6570;
    --accent:#2a5bd7; --border:#e2e6ea;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#12161b; --card-bg:#1b2027; --text:#eef1f4; --sub:#9aa4af; --border:#2a313a; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin:0; background:var(--bg); color:var(--text);
    font-family:-apple-system,"Hiragino Sans","Yu Gothic",sans-serif; padding:32px 20px 60px;
  }}
  header, main {{ max-width:760px; margin:0 auto; }}
  .back {{ display:inline-block; margin-bottom:14px; color:var(--accent); text-decoration:none; font-size:0.85rem; }}
  header h1 {{ font-size:1.5rem; margin:0 0 6px; }}
  header p {{ color:var(--sub); font-size:0.88rem; margin:4px 0 22px; }}
  section {{ background:var(--card-bg); border:1px solid var(--border); border-radius:12px; padding:16px 20px; margin-bottom:16px; }}
  .sec-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }}
  .sec-head h2 {{ font-size:1rem; margin:0; }}
  .more {{ font-size:0.78rem; color:var(--accent); text-decoration:none; white-space:nowrap; }}
  ul {{ margin:0; padding-left:1.2em; }}
  li {{ font-size:0.92rem; line-height:1.6; margin-bottom:4px; }}
  .empty {{ color:var(--sub); font-size:0.86rem; margin:0; }}
  footer {{ max-width:760px; margin:20px auto 0; color:var(--sub); font-size:0.76rem; }}
</style>
</head>
<body>
<header>
  <a class="back" href="../">← 全成果物ポータルに戻る</a>
  <h1>☕ AI部長 朝刊（{date_str}）</h1>
  <p>他7ページから実際に取得済みの見出しのみを引用したダイジェストです。詳細は各セクションの「全文を見る」からご確認ください。</p>
</header>
<main>
{sections_html}
</main>
<footer>
  <p>最終更新: {now_str}｜GitHub Actionsにより毎日自動更新（morning_news_fetch.py）</p>
</footer>
</body>
</html>
"""


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    results = build_dataset()
    now = datetime.datetime.now(JST)
    now_str = now.strftime("%Y-%m-%d %H:%M JST")
    date_str = now.strftime("%Y年%m月%d日")
    html = render(results, now_str, date_str)
    temp_file = OUTPUT_HTML + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(temp_file, OUTPUT_HTML)
    missing = [r["key"] for r in results if r["texts"] is None]
    print(f"✅ AI部長朝刊 生成完了: {OUTPUT_HTML}" + (f"（未生成: {missing}）" if missing else ""))


if __name__ == "__main__":
    main()
