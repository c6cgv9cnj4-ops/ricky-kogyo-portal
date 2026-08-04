import os
import json
import datetime
import requests

# ============================================================
# 全成果物集約ポータル 自動生成スクリプト
#
# これまでにAI部長（統括エージェント）が作成した各成果物URLへ
# 定期的にアクセスし、生存確認（404ハレーション排除）を行った上で
# 一覧ダッシュボード（docs/index.html）を再生成する。
#
# 各成果物そのものの中身（元データ）は個別のパイプライン
# （例: local_portal_fetch.py）が別途更新するため、本スクリプトは
# 「リンク切れを絶対に放置しない」ことに専念する。
# ============================================================

OUTPUT_DIR = "docs"
OUTPUT_HTML = os.path.join(OUTPUT_DIR, "index.html")
HISTORY_JSON = os.path.join(OUTPUT_DIR, "status_history.json")
JST = datetime.timezone(datetime.timedelta(hours=9))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# category: 表示グループ名
ARTIFACTS = [
    {
        "title": "バドミントン代表・動向インテリジェンス",
        "url": "https://claude.ai/code/artifact/2bea51e1-2b48-4be1-8052-fce54cd8bee8",
        "category": "スポーツ",
        "team": "02_分析・提案チーム",
    },
    {
        "title": "都内 美術館・写真展データベース",
        "url": "https://claude.ai/code/artifact/147e2a38-c8a8-4bb8-813c-737161159ed1",
        "category": "カルチャー・撮影",
        "team": "01_情報収集チーム",
    },
    {
        "title": "経済ポータルダッシュボード",
        "url": "https://claude.ai/code/artifact/8b48e4d7-532d-4fce-a424-546f47bdfb56",
        "category": "経済",
        "team": "01_情報収集チーム",
    },
    {
        "title": "AI部長 朝刊（2026年8月1日）",
        "url": "https://claude.ai/code/artifact/56b4f217-5dc8-4184-bf8b-63125210cfc9",
        "category": "総合ダッシュボード",
        "team": "01_情報収集チーム",
    },
    {
        "title": "近隣3市 地域ポータル（北本・桶川・鴻巣）",
        "url": "https://claude.ai/code/artifact/de2fe804-00a9-4ec0-8b34-13f0cd27e9d5",
        "category": "地域・防災",
        "team": "01_情報収集チーム",
    },
    {
        "title": "4紙見出し比較ポータル",
        "url": "https://claude.ai/code/artifact/8e525709-0b6d-4fbb-8898-17781ba2b27b",
        "category": "ニュース比較",
        "team": "01_情報収集チーム",
    },
    {
        "title": "撮影スポット＆気象条件ダッシュボード",
        "url": "https://claude.ai/code/artifact/d08b5c33-0e3a-4049-89e0-0f23eb8e06a1",
        "category": "カルチャー・撮影",
        "team": "01_情報収集チーム",
    },
    {
        "title": "公式一次情報収集・分析ダッシュボード",
        "url": "https://claude.ai/code/artifact/d600feee-9de2-4380-9582-5c6abb20fa27",
        "category": "総合ダッシュボード",
        "team": "02_分析・提案チーム",
    },
    {
        "title": "近隣ポータル（北本・桶川・鴻巣／GitHub Actions自動更新）",
        "url": "https://c6cgv9cnj4-ops.github.io/kitamoto-okegawa-konosu-portal/",
        "category": "地域・防災",
        "team": "01_情報収集チーム",
        "note": "local_portal_fetch.py が15分おきに元データを再取得・自動更新中",
    },
]

CATEGORY_ORDER = [
    "総合ダッシュボード",
    "地域・防災",
    "ニュース比較",
    "経済",
    "スポーツ",
    "カルチャー・撮影",
]


def check_url(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        return r.status_code, (r.status_code < 400)
    except requests.RequestException as e:
        return f"ERR:{type(e).__name__}", False


def load_history():
    if os.path.exists(HISTORY_JSON):
        try:
            with open(HISTORY_JSON, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    history = load_history()
    now = datetime.datetime.now(JST)
    now_str = now.strftime("%Y-%m-%d %H:%M JST")

    results = []
    for item in ARTIFACTS:
        status_code, ok = check_url(item["url"])
        prev = history.get(item["url"], {})
        last_ok = now_str if ok else prev.get("last_ok", "未確認")
        results.append({
            **item,
            "status_code": status_code,
            "ok": ok,
            "checked_at": now_str,
            "last_ok": last_ok,
        })
        history[item["url"]] = {"last_ok": last_ok, "last_checked": now_str, "status_code": status_code}

    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    ok_count = sum(1 for r in results if r["ok"])
    total = len(results)

    grouped = {}
    for r in results:
        grouped.setdefault(r["category"], []).append(r)

    def category_key(c):
        return CATEGORY_ORDER.index(c) if c in CATEGORY_ORDER else len(CATEGORY_ORDER)

    sections_html = ""
    for cat in sorted(grouped.keys(), key=category_key):
        cards = ""
        for r in grouped[cat]:
            badge = (
                '<span class="badge ok">🟢 生存確認OK</span>'
                if r["ok"]
                else '<span class="badge ng">🔴 要確認（404の可能性）</span>'
            )
            note_html = f'<p class="note">{r["note"]}</p>' if r.get("note") else ""
            cards += f"""
            <a class="card" href="{r['url']}" target="_blank" rel="noopener">
              <div class="card-head">
                <span class="team">{r['team']}</span>
                {badge}
              </div>
              <h3>{r['title']}</h3>
              {note_html}
              <div class="card-foot">
                <span>最終正常確認: {r['last_ok']}</span>
                <span>HTTP: {r['status_code']}</span>
              </div>
            </a>
            """
        sections_html += f"""
        <section>
          <h2>{cat}</h2>
          <div class="grid">{cards}</div>
        </section>
        """

    html = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>全成果物ポータル｜一般社団法人りっきー興行 AI部長</title>
<style>
  :root {{
    --bg: #f4f6f8; --card-bg: #ffffff; --text: #1a1f24; --sub: #5b6570;
    --accent: #2a5bd7; --ok: #1e8e5a; --ng: #c53030; --border: #e2e6ea;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#12161b; --card-bg:#1b2027; --text:#eef1f4; --sub:#9aa4af; --border:#2a313a; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, "Hiragino Sans", "Yu Gothic", sans-serif;
    padding: 32px 20px 60px;
  }}
  header {{ max-width: 1080px; margin: 0 auto 28px; }}
  header h1 {{ font-size: 1.6rem; margin: 0 0 6px; }}
  header p {{ color: var(--sub); margin: 4px 0; font-size: 0.92rem; }}
  .summary {{
    display: inline-block; margin-top: 10px; padding: 6px 14px; border-radius: 20px;
    background: var(--card-bg); border: 1px solid var(--border); font-size: 0.88rem;
  }}
  main {{ max-width: 1080px; margin: 0 auto; }}
  section {{ margin-bottom: 30px; }}
  section h2 {{
    font-size: 1.05rem; border-left: 4px solid var(--accent); padding-left: 10px; margin-bottom: 14px;
  }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }}
  .card {{
    display: block; background: var(--card-bg); border: 1px solid var(--border);
    border-radius: 12px; padding: 16px; text-decoration: none; color: var(--text);
    transition: transform .15s, box-shadow .15s;
  }}
  .card:hover {{ transform: translateY(-2px); box-shadow: 0 6px 16px rgba(0,0,0,0.08); }}
  .card-head {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
  .team {{ font-size: 0.72rem; color: var(--sub); background: rgba(128,128,128,0.12); padding: 2px 8px; border-radius: 10px; }}
  .badge {{ font-size: 0.74rem; padding: 2px 8px; border-radius: 10px; white-space: nowrap; }}
  .badge.ok {{ color: var(--ok); background: rgba(30,142,90,0.12); }}
  .badge.ng {{ color: var(--ng); background: rgba(197,48,48,0.12); }}
  .card h3 {{ font-size: 1rem; margin: 4px 0 8px; line-height: 1.4; }}
  .note {{ font-size: 0.8rem; color: var(--sub); margin: 0 0 8px; }}
  .card-foot {{ display: flex; justify-content: space-between; font-size: 0.74rem; color: var(--sub); border-top: 1px dashed var(--border); padding-top: 8px; }}
  footer {{ max-width: 1080px; margin: 30px auto 0; color: var(--sub); font-size: 0.78rem; }}
</style>
</head>
<body>
<header>
  <h1>📁 全成果物ポータル</h1>
  <p>一般社団法人りっきー興行 AI情報収集・分析チーム｜AI部長 統括ダッシュボード</p>
  <div class="summary">✅ {ok_count} / {total} 件 生存確認OK ｜ 最終更新: {now_str}</div>
</header>
<main>
{sections_html}
</main>
<footer>
  <p>本ページはGitHub Actionsにより24時間ごとに自動巡回・更新されています（auto_fetch_all.py）。</p>
  <p>🔴表示のリンクは404等の可能性があります。手動での再確認をおすすめします（404ハレーション絶対排除ルールに基づく自動監視）。</p>
</footer>
</body>
</html>
"""

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    with open(os.path.join(OUTPUT_DIR, ".nojekyll"), "w") as f:
        pass

    print(f"Done: {ok_count}/{total} OK, wrote {OUTPUT_HTML}")


if __name__ == "__main__":
    main()
