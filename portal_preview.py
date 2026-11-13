"""映画・展覧会の統合ポータル（プレビュー）。読み取り専用で、公開先（docs/）には書かない。

- 展覧会：既存の掲載ページ（docs/culture-exhibition/index.html）を exhibition_watch.load_cards で読む（ネットワークなし）
- 上映：screenings.py の台帳（手動CSVから取り込んだもの）を screenings.upcoming で読む
- 人物から映画を探す・作品の発見：未実装（人物の登録と作品の関係のデータがまだ無い）。画面には「0件」と明示する
- 出力先は --out で指定する。docs/ 配下は拒否する
"""
import argparse
import collections
import datetime
import html
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import exhibition_watch as E  # noqa: E402
import screenings as S  # noqa: E402
import film_watch as FW  # noqa: E402
import portal_search as Q  # noqa: E402
import fetch_meta as FM  # noqa: E402
import film_watch as FW  # noqa: E402

DOCS_DIR = os.path.abspath(os.path.join(HERE, "docs"))
END_SOON_DAYS = 30


def guard_output_path(path):
    absolute = os.path.abspath(path)
    if absolute == DOCS_DIR or absolute.startswith(DOCS_DIR + os.sep):
        raise ValueError(f"公開先（docs/）への書き込みは拒否します: {absolute}")
    return absolute


NON_OFFICIAL_HOSTS = ("news.google.com",)            # 公式の出典ではない（集約サイト）
NON_EXHIBITION_WORDS = ("キャンペーン", "スタンプラリー", "閉廊", "ワークショップ", "フィルムライブラリー")


def exhibition_rows(cards, today=None):
    """カードを表示用の行にする。終了日は掲載の会期から取る（推測しない）。
    区分（flags）は削除せず付ける：非公式出典／展覧会以外の可能性／終了済み／https でない／会期不明／リンク重複。"""
    today = today or S.AW.now_jst().date()
    link_count = collections.Counter(c.get("link", "") for c in cards)
    rows = []
    for c in cards:
        _, end = E.parse_dates(c.get("period", ""))   # 戻り値は (開始日, 終了日)。判明しなければ None
        link = c.get("link", "")
        title = c.get("title", "")
        flags = []
        if any(h in link for h in NON_OFFICIAL_HOSTS):
            flags.append("非公式出典")
        if any(w in title for w in NON_EXHIBITION_WORDS):
            flags.append("展覧会以外の可能性")
        if end is None:
            flags.append("会期不明")
        elif end < today:
            flags.append("終了済み")
        if link and not link.startswith("https://"):
            flags.append("https でない")
        if link_count[link] > 1:
            flags.append("リンク重複")
        rows.append({"title": title, "venue": c.get("venue", ""), "period": c.get("period", ""),
                     "link": link, "state": c.get("state", "未確認"), "end": end, "flags": flags})
    return rows


def exhibition_stats(rows):
    links = [r["link"] for r in rows]
    states = {}
    for r in rows:
        states[r["state"]] = states.get(r["state"], 0) + 1
    return {"total": len(rows), "unique_links": len(set(links)), "duplicate_links": len(links) - len(set(links)),
            "missing_end": sum(1 for r in rows if r["end"] is None), "states": states,
            "flag_counts": dict(collections.Counter(f for r in rows for f in r.get("flags", []))),
            "sale_or_deadline_fields": 0}   # 掲載カードには発売日・予約期限の項目がない


def end_soon(rows, today, days=END_SOON_DAYS):
    """終了が近い展覧会。非公式出典・展覧会以外の可能性・終了済みは、一覧から外す（区分は残す）。"""
    excluded = {"非公式出典", "展覧会以外の可能性", "終了済み"}
    hits = [r for r in rows if r["end"] and today <= r["end"] <= today + datetime.timedelta(days=days)
            and not (excluded & set(r.get("flags", [])))]
    return sorted(hits, key=lambda r: r["end"])


def _e(v):
    return html.escape(str(v or ""))


def _li(items, empty):
    if not items:
        return f"<p class='empty'>{_e(empty)}</p>"
    return "<ul>" + "".join(items) + "</ul>"


def film_rows(film_dir):
    """映画の人物・作品の台帳（film_watch）を一覧用の行にする。読み取りのみ。人物名は同名で統合しない。"""
    st = FW.Store(film_dir)
    people, works = st.load("people"), st.load("works")
    rows = []
    for w in sorted(works.values(), key=lambda w: w["title"]):
        parts = [f"{c['role']}：{people[c['person_id']]['name']}" for c in w.get("credits", []) if c["person_id"] in people]
        rows.append({"title": w["title"], "credits": "、".join(parts), "source_url": w.get("source_url", "")})
    return rows


def build_html(exh_rows, screening_items, today, film_items=()):
    stats = exhibition_stats(exh_rows)
    soon = end_soon(exh_rows, today)
    shows = [f"<li>{_e(s['screen_date'])} {_e(s['start_time'])} ｜ {_e(s['title'])} ｜ {_e(s['theater'])} "
             f"｜ <a href='{_e(s['evidence_url'])}'>出典</a></li>" for s in screening_items]
    ends = [f"<li>{_e(r['end'].isoformat())} まで ｜ {_e(r['title'])} ｜ {_e(r['venue'])} "
            f"｜ <a href='{_e(r['link'])}'>出典</a></li>" for r in soon]
    films = [f"<li><b>{_e(f['title'])}</b>（{_e(f['credits'])}） ｜ <a href='{_e(f['source_url'])}'>出典</a></li>"
             for f in film_items]
    states = " / ".join(f"{_e(k)} {v}件" for k, v in sorted(stats["states"].items()))
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>映画・展覧会 見逃し防止（プレビュー）</title>
<style>body{{font-family:-apple-system,sans-serif;margin:16px;line-height:1.6}}
h2{{font-size:1.1em;border-bottom:1px solid #ccc;padding-bottom:4px}}.empty{{color:#777}}</style></head>
<body>
<h1>映画・展覧会 見逃し防止（プレビュー）</h1>
<p>基準日：{_e(today.isoformat())}（日本時間）。このページは非公開のプレビューです。</p>

<h2>近いうちの上映（{S.UPCOMING_DAYS}日以内）</h2>
{_li(shows, "上映予定は0件です（上映の実データが未投入）。")}

<h2>映画の人物と作品（実データ・人物から発見）</h2>
{_li(films, "映画の人物・作品の実データは未登録です。")}

<h2>リバイバル・特集上映</h2>
<p class="empty">種別の実データが未投入のため、0件です。</p>

<h2>終了が近い展覧会（{END_SOON_DAYS}日以内）</h2>
{_li(ends, "終了が近い展覧会はありません。")}

<h2>予約期限が近い展覧会</h2>
<p class="empty">掲載ページに予約期限の項目がないため、判定できません（{stats['total']}件すべて欠損）。</p>

<h2>データの状態（展覧会）</h2>
<ul>
<li>掲載件数：{stats['total']}件（重複リンク：{stats['duplicate_links']}件）</li>
<li>会期の欠損：{stats['missing_end']}件</li>
<li>掲載の確認状態：{states or 'なし'}</li>
<li>区分の件数（削除せず表示のみ）：{_e(stats['flag_counts']) or 'なし'}</li>
</ul>
</body></html>
"""


def db_block(db_path):
    """統合DB（portal_db.py が作る SQLite）の件数と種別を、プレビューの末尾に出す。読み取りのみ。
    循環参照を避けるため、portal_db は import せず、表の件数を直接数える。"""
    import sqlite3
    con = sqlite3.connect(db_path)
    try:
        counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("people", "works", "credits", "facilities", "exhibitions", "screenings")}
        kinds = dict(con.execute(
            "SELECT k, COUNT(*) FROM (SELECT data_kind AS k FROM people UNION ALL SELECT data_kind FROM works "
            "UNION ALL SELECT data_kind FROM exhibitions UNION ALL SELECT data_kind FROM screenings) GROUP BY k"))
    finally:
        con.close()
    rows = "".join(f"<li>{_e(t)}：{n}件</li>" for t, n in counts.items())
    return ("<h2>統合DB（検索用）</h2><ul>" + rows + "</ul>"
            f"<p>データ種別：{_e(kinds)}（実データと架空データは別の値で区別）</p>")


def _link(url, label):
    """https の出典だけリンクにする。https でない・空の出典は、リンクにせず文字で示す。"""
    if url and url.startswith("https://"):
        return f"<a href='{_e(url)}' rel='noopener'>{_e(label)}</a>"
    if not url:
        return "出典未確認"                                  # 出典が登録されていない情報は、未確認と明示する
    return _e(label) + "（URL不正）"


def _exh_item(e):
    tags = "・".join(e["tags"]) or "区分なし"
    start = e["start_date"] or "不明"
    end = e["end_date"] or "不明"
    reason = f" ｜ 欠損理由：{_e(e['missing_reason'])}" if e.get("missing_reason") else ""
    return (f"<li><b>{_e(e['status'])}</b> ｜ {_e(e['title'])}（{_e(e['venue'])}） ｜ 会期 {_e(start)}〜{_e(end)} "
            f"｜ {_e(tags)}{reason} ｜ {_link(e['official_url'], '出典')}</li>")


def _release_text(w):
    """日本公開日の表示。出典のある日付だけ「確定」と出す。出典のない日付は「出典未確認」、それ以外は未発表・未確認。"""
    if w["release_status"] == "公開日確定" and w["release_date"]:
        if w.get("release_source_url") and w.get("release_checked_on"):
            return f"日本公開日 {w['release_date']}（確定）"
        return f"日本公開日 {w['release_date']}（{'確認日未確認' if w.get('release_source_url') else '出典未確認'}）"
    return "日本公開日：" + ("未発表" if w["release_status"] == "未発表" else "未確認")


def _sale_text(s):
    when = s["starts_at"] or "未確認"
    url = f" ｜ {_link(s['url'], '販売URL')}" if s["url"] else ""
    src = "" if s.get("source_url") or s.get("url") else "（出典未確認）"
    return f"{_e(s['kind'])}販売：{_e(s['status'])}（開始 {_e(when)}）{url}{src}"


def person_card(p):
    """人物1人のプロフィール・関連作品・今後の公開予定（確定日のみ）。区別情報が無ければその旨を出す。"""
    dis = p["disambiguator"] or "区別情報：未入力"
    head = (f"<h3>{_e(p['name'])}（{_e(dis)}）</h3>"
            f"<p>出典：{_link(p['source_url'], '出典')} ｜ 確認日：{_e(p['checked_on'] or '未確認')} ｜ 区分：{_e(p['data_kind'])}</p>")
    items = []
    for w in p["works"]:
        sales = " ｜ ".join(_sale_text(s) for s in w["sales"]) or "販売：未確認"
        items.append(f"<li>{_e(w['title'])}（{_e(w['role'])}） ｜ {_e(_release_text(w))} ｜ {sales} ｜ "
                     f"{_link(w['source_url'], '作品の出典')}{_history_text(w)}</li>")
    works = "<ul>" + "".join(items) + "</ul>" if items else "<p class='empty'>関連作品は登録されていません。</p>"
    ups = [f"<li>{_e(u['date'])} ｜ {_e(u['title'])} ｜ {_e(u['kind'])} ｜ {_link(u['url'], '出典')}</li>"
           for u in p["upcoming"]]
    up = "<ul>" + "".join(ups) + "</ul>" if ups else \
        "<p class='empty'>今後の公開予定は登録されていません（日付が確定していないものは含みません）。</p>"
    nears = [f"<li>あと{u['days_left']}日：{_e(u['starts_at'])} ｜ {_e(u['title'])} ｜ {_e(u['kind'])}販売開始 ｜ "
             f"{_link(u['url'], '販売ページ')}</li>" for u in p["near_sales"]]
    near = "<ul>" + "".join(nears) + "</ul>" if nears else \
        f"<p class='empty'>発売開始が{FW.SALE_NEAR_DAYS}日以内の予定はありません（登録済みの発売前の販売に限る）。</p>"
    nexts = [f"<li>{_e(n['title'])}（{_e(n['role'])}） ｜ "
             + (f"日本公開日 {_e(n['release_date'])}（確定）" if n["release_date"] else "日本公開日：未発表（新作）")
             + "</li>" for n in p["next_works"]]
    nxt = "<ul>" + "".join(nexts) + "</ul>" if nexts else "<p class='empty'>次回作・新作の登録はありません。</p>"
    return (f"<div class='person'>{head}<h4>次回作・新作</h4>{nxt}<h4>関連作品</h4>{works}"
            f"<h4>今後の公開予定（確定日のみ）</h4>{up}<h4>発売開始が近い予定</h4>{near}</div>")


def _history_text(w):
    """公開日の変更履歴。変更前が無い（台帳になし）は「不明」と出し、推測しない。"""
    if not w["release_history"]:
        return ""
    lines = "".join(
        f"<br>変更：{_e(h['before'] or '変更前は不明')} → {_e(h['after'])}（確認日 {_e(h['checked_on'] or '未確認')}）"
        f" ｜ {_link(h['source_url'], '出典')}" for h in w["release_history"])
    return lines


def search_html(db_path, today, person="", work="", facility=""):
    """統合DBの検索結果をHTMLにする。検索ロジックは portal_search に任せ、ここは表示だけ行う。"""
    out = []
    if person:
        cands = Q.person_candidates(db_path, person)
        if not cands:
            body = "<p class='empty'>該当する人物は登録されていません。</p>"
        else:
            note = ("<p class='notice'>同名の人物が複数あります。名前だけでは同一人物と判断しません（区別情報で見分けてください）。</p>"
                    if len(cands) > 1 else "")
            body = note + "".join(person_card(Q.person_profile(db_path, c["id"], today)) for c in cands)
        out.append(f"<h2>人物検索：{_e(person)}</h2>{body}")
    if work:
        people = Q.work_people(db_path, work)
        shows = Q.work_screenings(db_path, work)
        if not people:
            body = "<p class='empty'>該当する作品は登録されていません。</p>"
        else:
            w = people[0]
            year = w.get("release_year") or "公開年：未登録"
            cast = "".join(f"<li>{_e(p['role'])}：{_e(p['name'])} ｜ {_link(p['source_url'], '出典')}</li>"
                           for p in w["people"]) or "<li class='empty'>関連人物は登録されていません</li>"
            body = (f"<p><b>{_e(w['work'])}</b>（{_e(year)}） ｜ {_link(w['source_url'], '出典')}</p><ul>{cast}</ul>")
        if shows:
            body += "<h3>上映回</h3><ul>" + "".join(
                f"<li>{_e(s['screen_date'])} {_e(s['start_time'])} ｜ {_e(s['theater'])}"
                f"{'（' + _e(s['program_title']) + '）' if s['program_title'] else ''} ｜ "
                f"{_link(s['evidence_url'], '出典')}</li>" for s in shows) + "</ul>"
        else:
            body += "<p class='empty'><b>登録済み上映情報なし</b>（この作品の上映は、まだ1件も登録されていません）</p>"
        out.append(f"<h2>作品検索：{_e(work)}</h2>{body}")
    if facility:
        hits = Q.facility_exhibitions(db_path, facility, today)
        if not hits:
            body = "<p class='empty'>該当する施設の展覧会は登録されていません。</p>"
        else:
            body = "<ul>" + "".join(_exh_item(e) for e in hits) + "</ul>"
        out.append(f"<h2>施設検索：{_e(facility)}</h2>{body}")
    # 展覧会一覧（状態別）：検索語に関係なく、常に表示する
    all_ex = Q.exhibitions_by_end(db_path, today)
    groups = collections.OrderedDict((s, []) for s in ("開催中", "開催予定", "終了", "会期不明"))
    for e in all_ex:
        groups.setdefault(e["status"], []).append(e)
    counts = " / ".join(f"{k} {len(v)}件" for k, v in groups.items())
    body = f"<p>{_e(counts)}（終了間近：{sum(1 for e in all_ex if '終了間近' in e['tags'])}件）</p>"
    for status, items in groups.items():
        body += f"<h3>{_e(status)}（{len(items)}件）</h3>"
        body += ("<ul>" + "".join(_exh_item(e) for e in items) + "</ul>") if items else "<p class='empty'>0件</p>"
    out.append(f"<h2>展覧会一覧（会期・状態）</h2>{body}")
    return "".join(out)


def snapshot_note(exhibition_source, meta_path=None):
    """展覧会の鮮度を示す注記。取得時刻（最終成功）と掲載ファイルの更新時刻は別物として並べる。
    取得時刻が確認できなければ「不明」と出す。ファイルの更新時刻から取得時刻を推定しない。"""
    file_stamp = datetime.datetime.fromtimestamp(os.path.getmtime(exhibition_source), S.AW.JST).strftime("%Y-%m-%d %H:%M")
    meta = FM.load(meta_path or os.path.join(HERE, FM.META_PATH))
    fetched = FM.display_success_time(meta)
    attempt = (meta or {}).get("last_attempt_status")
    failure = ""
    if attempt in ("失敗", "一部失敗"):
        failure = (f"（直近の取得は{attempt}。上記は前回成功時の時刻です）" if fetched != FM.UNKNOWN
                   else f"（直近の取得は{attempt}）")
    return ("<p class='notice'><b>展覧会の取得時刻（最終成功）：" + _e(fetched) + "</b>" + _e(failure) +
            "<br>掲載ファイルの更新時刻：" + file_stamp + " JST（ファイルの更新日時であり、取得時刻とは別です）。"
            "自動取得は一時停止中のため、利用条件の確認が済むまで更新されません。終了・会期は、この時点の情報です。</p>")


def build(exhibition_source, screenings_dir, out_path, today=None, film_dir=None, db_path=None, search=None,
          meta_path=None):
    out_path = guard_output_path(out_path)
    today = today or S.AW.now_jst().date()
    cards = E.load_cards(exhibition_source)
    exh_rows = exhibition_rows(cards, today)
    items = S.upcoming(screenings_dir, today=today, days=S.UPCOMING_DAYS)
    films = film_rows(film_dir) if film_dir else []
    page = build_html(exh_rows, items, today, films)
    anchor = "<h2>近いうちの上映"
    page = page.replace(anchor, snapshot_note(exhibition_source, meta_path) + anchor, 1)   # 展覧会の鮮度を、上映の前に示す
    if db_path and os.path.exists(db_path):
        s = search or {}
        block = db_block(db_path) + search_html(db_path, today, s.get("person", ""), s.get("work", ""),
                                               s.get("facility", ""))
        page = page.replace("</body>", block + "</body>")   # 既存の表示は変えず、末尾に追加するだけ
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    return {"exhibitions": len(exh_rows), "screenings": len(items), "films": len(films), "output": out_path}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="出力HTMLのパス（docs/ 配下は不可）")
    ap.add_argument("--screenings-dir", default=os.path.join(HERE, "screenings_data"))
    ap.add_argument("--exhibition-source", default=E.DEFAULT_SOURCE)
    ap.add_argument("--db", default=None, help="統合DB（portal_db.py の出力）。指定すると人物検索を表示")
    ap.add_argument("--person", default="", help="人物名。同名は全件を区別情報つきで表示（名前だけで統合しない）")
    ap.add_argument("--film-dir", default=None, help="台帳フォルダ。指定すると人物・作品の一覧も表示")
    args = ap.parse_args()
    print(build(args.exhibition_source, args.screenings_dir, args.out, film_dir=args.film_dir,
                db_path=args.db, search={"person": args.person}))
