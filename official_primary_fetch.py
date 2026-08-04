import os
import datetime
from local_portal_fetch import (
    HEADERS,
    fetch_police_list,
    fetch_fire_dept_notices,
    fetch_earthquake_info,
)

# ============================================================
# 公式一次情報収集・分析ダッシュボード 自動生成スクリプト
#
# local_portal_fetch.py で実績のある「公式機関の一次情報」取得関数
# （埼玉県警／埼玉県央広域消防本部／気象庁）のみを対象に、憶測を挟まず
# 一覧化・簡易集計する。地域ニュースまとめ（号外NET等の二次情報）は
# 近隣ポータル側の役割のため、本ダッシュボードには含めない。
# ============================================================

OUTPUT_DIR = "docs/official-primary"
OUTPUT_HTML = os.path.join(OUTPUT_DIR, "index.html")
JST = datetime.timezone(datetime.timedelta(hours=9))

SOURCES = [
    {
        "key": "police_konosu",
        "name": "埼玉県警 鴻巣警察署 新着情報",
        "fetch": lambda: fetch_police_list(
            "鴻巣警察署 新着情報",
            "https://www.police.pref.saitama.lg.jp/kenke/kesatsusho/konosu/shinchaku/index.html",
            default_city="鴻巣市",
        ),
        "origin": "https://www.police.pref.saitama.lg.jp/kenke/kesatsusho/konosu/shinchaku/index.html",
    },
    {
        "key": "police_ageo",
        "name": "埼玉県警 上尾警察署 新着情報",
        "fetch": lambda: fetch_police_list(
            "上尾警察署 新着情報",
            "https://www.police.pref.saitama.lg.jp/kenke/kesatsusho/ageo/shinchaku/index.html",
            default_city="桶川市",
        ),
        "origin": "https://www.police.pref.saitama.lg.jp/kenke/kesatsusho/ageo/shinchaku/index.html",
    },
    {
        "key": "fire_kenou",
        "name": "埼玉県央広域消防本部 公式お知らせ",
        "fetch": lambda: fetch_fire_dept_notices(
            "埼玉県央広域消防本部 お知らせ",
            "https://www.ken-o.or.jp/firehead/",
        ),
        "origin": "https://www.ken-o.or.jp/firehead/",
    },
    {
        "key": "jma_eq",
        "name": "気象庁 地震情報（埼玉県該当分）",
        "fetch": fetch_earthquake_info,
        "origin": "https://www.data.jma.go.jp/developer/xml/feed/eqvol.xml",
    },
]


def build_dataset():
    results = []
    for src in SOURCES:
        try:
            items = src["fetch"]()
        except Exception as e:
            items = []
            print(f"⚠️ {src['name']} 取得エラー: {e}")
        results.append({**src, "items": items, "count": len(items)})
    return results


def render(results, now_str):
    total = sum(r["count"] for r in results)
    active_sources = sum(1 for r in results if r["count"] > 0)

    summary_cards = "".join(
        f"""
        <div class="stat">
          <div class="stat-num">{r['count']}</div>
          <div class="stat-label">{r['name']}</div>
        </div>"""
        for r in results
    )

    sections = ""
    for r in results:
        if r["count"] == 0:
            body = '<p class="empty">現在、対象となる新着情報はありません（正常にアクセスできています）。</p>'
        else:
            rows = ""
            for item in r["items"]:
                title = item.get("title", "")
                link = item.get("link", "#")
                date_display = item.get("date_display") or item.get("origin_time", "")
                rows += f"""
                <li>
                  <a href="{link}" target="_blank" rel="noopener">{title}</a>
                  <span class="date">{date_display}</span>
                </li>"""
            body = f'<ul class="list">{rows}</ul>'

        sections += f"""
        <section>
          <h2>{r['name']}</h2>
          <p class="origin">一次情報源: <a href="{r['origin']}" target="_blank" rel="noopener">{r['origin']}</a></p>
          {body}
        </section>"""

    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>公式一次情報収集・分析ダッシュボード｜AI部長</title>
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
  header, main {{ max-width:920px; margin:0 auto; }}
  header h1 {{ font-size:1.5rem; margin:0 0 6px; }}
  header p {{ color:var(--sub); font-size:0.9rem; margin:4px 0; }}
  .back {{ display:inline-block; margin-bottom:14px; color:var(--accent); text-decoration:none; font-size:0.85rem; }}
  .stats {{ display:flex; flex-wrap:wrap; gap:12px; margin:18px 0 30px; }}
  .stat {{ background:var(--card-bg); border:1px solid var(--border); border-radius:10px; padding:12px 16px; min-width:140px; }}
  .stat-num {{ font-size:1.4rem; font-weight:700; }}
  .stat-label {{ font-size:0.78rem; color:var(--sub); margin-top:2px; }}
  section {{ margin-bottom:26px; background:var(--card-bg); border:1px solid var(--border); border-radius:12px; padding:18px 20px; }}
  section h2 {{ font-size:1.02rem; margin:0 0 6px; border-left:4px solid var(--accent); padding-left:10px; }}
  .origin {{ font-size:0.76rem; color:var(--sub); margin:0 0 12px; word-break:break-all; }}
  .origin a {{ color:var(--sub); }}
  ul.list {{ list-style:none; margin:0; padding:0; }}
  ul.list li {{ padding:8px 0; border-top:1px dashed var(--border); display:flex; justify-content:space-between; gap:12px; font-size:0.9rem; }}
  ul.list li:first-child {{ border-top:none; }}
  ul.list a {{ color:var(--text); text-decoration:none; }}
  ul.list a:hover {{ text-decoration:underline; }}
  .date {{ color:var(--sub); font-size:0.76rem; white-space:nowrap; }}
  .empty {{ color:var(--sub); font-size:0.86rem; }}
  footer {{ max-width:920px; margin:24px auto 0; color:var(--sub); font-size:0.76rem; }}
</style>
</head>
<body>
<header>
  <a class="back" href="../">← 全成果物ポータルに戻る</a>
  <h1>🏛️ 公式一次情報収集・分析ダッシュボード</h1>
  <p>埼玉県警・埼玉県央広域消防本部・気象庁の公式一次情報のみを収集対象とし、二次情報や憶測は含めません。</p>
  <div class="stats">
    <div class="stat"><div class="stat-num">{total}</div><div class="stat-label">直近件数（全ソース合計）</div></div>
    <div class="stat"><div class="stat-num">{active_sources}/{len(results)}</div><div class="stat-label">新着ありのソース数</div></div>
  </div>
</header>
<main>
{sections}
</main>
<footer>
  <p>最終更新: {now_str}｜GitHub Actionsにより毎日自動更新（official_primary_fetch.py）</p>
  <p>本ページは各公式機関の一次情報のみを対象とし、要約・見出しの改変は行いません（原文タイトルをそのまま掲載）。</p>
</footer>
</body>
</html>
"""


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    results = build_dataset()
    now_str = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    html = render(results, now_str)
    temp_file = OUTPUT_HTML + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(temp_file, OUTPUT_HTML)
    total = sum(r["count"] for r in results)
    print(f"✅ 公式一次情報ダッシュボード生成完了: {OUTPUT_HTML}（合計{total}件）")


if __name__ == "__main__":
    main()
