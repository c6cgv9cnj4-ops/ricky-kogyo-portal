"""映画・人物の公開HP（静的ページ）をローカルで生成する。一括登録→DB構築→ページ生成を1コマンドで行う。

- 出力先の既定は docs/film/（生成まで。公開・コミット・プッシュは行わない）
- 既存の docs/ のトップや他ページには触れない。出力先は docs/film だけ（または docs/ 配下の film 名のフォルダ）
- 出力先が既存でも、この生成で作られたもの（マーカーあり）以外は上書きしない
- 未確認の情報は「未確認」「未発表」と表示し、日付や人物を推測で補わない
例:
  python3 film_site.py --dir film_watch_data --db portal_data/portal.db --batch my_films.json
"""
import argparse
import datetime
import hashlib
import json
import html
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import film_entry as FE  # noqa: E402
import film_watch as FW  # noqa: E402
import film_web as W  # noqa: E402
import exhibition_watch as E  # noqa: E402
import portal_db as D  # noqa: E402
import portal_preview as P  # noqa: E402
import portal_search as Q  # noqa: E402
import screenings as S  # noqa: E402

DOCS_ROOT = os.path.join(HERE, "docs")
DEFAULT_OUT = os.path.join(DOCS_ROOT, "film")
MARKER = ".generated_by_film_site"


def _e(v):
    return html.escape(str(v or ""))


def _name(wid):
    return "w_" + hashlib.sha1(wid.encode("utf-8")).hexdigest()[:12]


def check_out(out):
    """出力先は docs/ の中で、docs/ そのものではないこと。既存の別物は上書きしない。"""
    absout = os.path.abspath(out)
    if absout == os.path.abspath(DOCS_ROOT) or not absout.startswith(os.path.abspath(DOCS_ROOT) + os.sep):
        raise ValueError(f"出力先は docs/ の中のフォルダにしてください（docs/ 直下や外は不可）: {out}")
    if os.path.isdir(absout) and os.listdir(absout) and not os.path.exists(os.path.join(absout, MARKER)):
        raise ValueError(f"既存のフォルダで、この生成のものではないため上書きしません: {out}")


def _page_person(db, pid, today, screening_dir):
    prof = Q.person_profile(db, pid, today)
    body = "<p><a href='../index.html'>← 一覧へ</a></p>" + P.person_card(prof)
    shows = []
    for w in prof["works"]:
        rows = Q.work_screenings(db, w["title"])
        if rows:
            shows += [f"<li>{_e(w['title'])}：{_e(r['screen_date'])} {_e(r['start_time'])} ｜ {_e(r['theater'])} ｜ "
                      f"<a href='{_e(r['evidence_url'])}'>出典</a></li>" for r in rows]
    body += ("<h4>上映予定（登録済みの上映のみ）</h4><ul>" + "".join(shows) + "</ul>") if shows else \
        "<h4>上映予定（登録済みの上映のみ）</h4><p class='empty'>登録済みの上映はありません（未確認）。</p>"
    return W.page(prof["name"], body)


def _page_work(store, db, wid, w, people_pid):
    rel = w.get("release") or {}
    if rel.get("status") == "公開日確定" and rel.get("date"):
        if rel.get("source_url") and rel.get("checked_on"):
            rel_text = f"日本公開日 {rel['date']}（確定）"
        else:
            rel_text = f"日本公開日 {rel['date']}（{'確認日未確認' if rel.get('source_url') else '出典未確認'}）"
    else:
        rel_text = "日本公開日：" + ("未発表" if rel.get("status") == "未発表" else "未確認")
    sales = "".join(f"<li>{_e(s['kind'])}販売：{_e(s['status'])}（開始 {_e(s.get('starts_at') or '未確認')}）"
                    + (f" ｜ <a href='{_e(s['url'])}'>公式販売ページ</a>" if s.get("url") else "")
                    + f" ｜ 出典 <a href='{_e(s['source_url'])}'>{_e(s['checked_on'])}確認</a></li>"
                    for s in w.get("sales", [])) or "<li>販売：未確認</li>"
    staff = []
    people = store.load("people")
    for c in w.get("credits", []):                  # 登録済みのクレジット（監督・脚本など）を先に出す
        if c["person_id"] in people and c["person_id"] in people_pid:
            who = f"<a href='../person/{_e(c['person_id'])}.html'>{_e(people[c['person_id']]['name'])}</a>"
            staff.append(f"<li>{who}（{_e(c['role'])}） ｜ 登録済みのクレジット</li>")
    for s in w.get("staff", []):
        status, pid = W.staff_status(store, s)
        who = (f"<a href='../person/{_e(people_pid[pid])}.html'>{_e(s['name'])}</a>"
               if status == W.STATUS_REGISTERED and pid in people_pid else _e(s["name"]))
        staff.append(f"<li>{who}（{_e(s['role'])}） ｜ 区別情報：{_e(s.get('disambiguator') or '未入力')}</li>")
    body = (f"<p><a href='../index.html'>← 一覧へ</a></p><h2>{_e(w['title'])}</h2>"
            f"<p>{_e(rel_text)} ｜ 作品の出典：" + (f"<a href='{_e(w['source_url'])}'>作品</a>（確認日 {_e(w.get('checked_on') or '未確認')}）" if w.get("source_url") else "出典未確認") + "</p>"
            f"<h4>チケット販売</h4><ul>{sales}</ul><h4>関係者（出典つきの候補）</h4><ul>{''.join(staff) or '<li>未登録</li>'}</ul>")
    return W.page(w["title"], body)


def check_internal_links(site):
    """生成HTMLの内部リンク（外部URL以外）が、実在するファイルを指しているかを確かめる。壊れたものを返す。"""
    import re
    broken = []
    for root, _, files in os.walk(site):
        for name in files:
            if not name.endswith(".html"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as f:
                for href in re.findall(r"href='([^']*)'", f.read()):
                    if href.startswith("https://"):
                        continue
                    if not os.path.exists(os.path.normpath(os.path.join(root, href))):
                        broken.append(f"{os.path.relpath(path, site)} -> {href}")
    return broken


def _confirm_label(source_url, checked_on):
    return "確認済み" if source_url and checked_on else "未確認（出典または確認日なし）"


def ledger_diff(old_dir, new_dir):
    """台帳の更新前後の差分（作品追加・公開日変更・販売情報の追加や変更）を、読める文章の一覧にする。
    公開日変更は既存の変更履歴（history）をそのまま使う。履歴は同じ変更を1件にしているので、再実行しても重複しない。
    old_dir が無い（初回）場合は、すべてを追加として出す。変更が無ければ「変更なし」。"""
    new = FW.Store(new_dir)
    old = FW.Store(old_dir) if old_dir and os.path.isdir(old_dir) else None
    old_works = old.load("works") if old else {}
    old_hist = old.load("history") if old else []
    new_works, new_people, new_hist = new.load("works"), new.load("people"), new.load("history")
    names = lambda w: "、".join(f"{new_people[c['person_id']]['name']}（{c['role']}）"
                               for c in w.get("credits", []) if c["person_id"] in new_people) or "関係者：未登録"
    lines = []
    for wid, w in new_works.items():
        if wid not in old_works:
            rel = w.get("release") or {}
            lines.append(f"[作品追加] {w['title']} ｜ 関係者：{names(w)} ｜ 公開日：{rel.get('status', '未確認')}"
                         f"{'（' + rel['date'] + '）' if rel.get('date') else ''} ｜ "
                         f"{_confirm_label(w.get('source_url'), w.get('checked_on'))} ｜ 出典：{w.get('source_url') or 'なし'}")
    seen = {(h["work_id"], h["before"], h["after"], h["at"]) for h in old_hist}
    for h in new_hist:
        key = (h["work_id"], h["before"], h["after"], h["at"])
        if h.get("field") == "公開日" and key not in seen and h["work_id"] in new_works:
            w = new_works[h["work_id"]]
            lines.append(f"[公開日変更] {w['title']} ｜ 関係者：{names(w)} ｜ 変更前：{h['before'] or '不明'} → "
                         f"変更後：{h['after']} ｜ 確認日：{h['at'] or '未確認'} ｜ "
                         f"{_confirm_label(h['source_url'], h['at'])} ｜ 出典：{h['source_url'] or 'なし'}")
    for wid, w in new_works.items():
        old_sales = {(x["kind"], x["url"]): x for x in old_works.get(wid, {}).get("sales", [])}
        for x in w.get("sales", []):
            prev = old_sales.get((x["kind"], x["url"]))
            if prev == x:
                continue
            what = "販売情報追加" if prev is None else "販売情報変更"
            before = f"{prev['status']}（開始 {prev.get('starts_at') or '未確認'}）" if prev else "なし"
            lines.append(f"[{what}] {w['title']} ｜ 関係者：{names(w)} ｜ {x['kind']}販売：{before} → "
                         f"{x['status']}（開始 {x.get('starts_at') or '未確認'}） ｜ "
                         f"{_confirm_label(x.get('source_url'), x.get('checked_on'))} ｜ 販売URL：{x.get('url') or 'なし'}")
    return "\n".join(lines) if lines else "変更なし"


NOTICE_STATE = "notified.json"   # 通知済みの差分の一覧（台帳フォルダ内。台帳そのものではない）


def write_notice(film_dir, diff, out_path):
    """差分を、ローカルの通知ファイルに書く。既に通知した行は、もう一度書かない（重複通知しない）。
    外部（メール・チャット）へは送らない。戻り値：新しく通知した件数と、書いたファイル。"""
    lines = [] if diff == "変更なし" else [l for l in diff.split("\n") if l.strip()]
    state_path = os.path.join(film_dir, NOTICE_STATE)
    seen = set(json.load(open(state_path, encoding="utf-8"))) if os.path.exists(state_path) else set()
    new = [(hashlib.sha256(l.encode("utf-8")).hexdigest(), l) for l in lines]
    new = [(k, l) for k, l in new if k not in seen]
    if not new:
        return {"new": 0, "file": None}
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("文化情報HP：更新通知（ローカル。外部には送信していません）\n")
        f.write("\n".join(l for _, l in new) + "\n")
    os.makedirs(film_dir, exist_ok=True)
    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(seen | {k for k, _ in new}), f)
    os.replace(tmp, state_path)
    return {"new": len(new), "file": out_path}


def build_site(film_dir, db_path, out_dir, batch=None, data_kind="実データ",
               exhibition_source=E.DEFAULT_SOURCE, screenings_dir="screenings_data", today=None):
    """一括登録（任意）→DB構築→ページ生成を行う。途中で失敗したら、台帳・DB・既存の出力を元に戻す。"""
    check_out(out_dir)
    backup = tempfile.mkdtemp(prefix="film_backup_")
    had_film = os.path.isdir(film_dir)
    had_db = os.path.exists(db_path)
    if had_film:
        shutil.copytree(film_dir, os.path.join(backup, "film"))
    if had_db:
        shutil.copy2(db_path, os.path.join(backup, "db"))
    try:
        result = _build_site(film_dir, db_path, out_dir, batch, data_kind, exhibition_source, screenings_dir, today)
        result["diff"] = ledger_diff(os.path.join(backup, "film") if had_film else None, film_dir)
        return result
    except BaseException:
        # 台帳と DB を、実行前の状態に戻す（一括登録が済んでいても、出力が旧版のままにならないように）
        if os.path.isdir(film_dir):
            shutil.rmtree(film_dir)
        if had_film:
            shutil.copytree(os.path.join(backup, "film"), film_dir)
        if had_db:
            shutil.copy2(os.path.join(backup, "db"), db_path)
        elif os.path.exists(db_path):
            os.remove(db_path)
        raise
    finally:
        shutil.rmtree(backup, ignore_errors=True)


def _build_site(film_dir, db_path, out_dir, batch, data_kind, exhibition_source, screenings_dir, today):
    today = today or S.AW.now_jst().date()
    summary = FE.import_file(film_dir, batch, data_kind) if batch else None
    counts = D.build(db_path, film_dir, exhibition_source, screenings_dir, today=today, data_kind=data_kind)
    store = FW.Store(film_dir)
    people = store.load("people")
    people_pid = {pid: pid for pid in people}
    tmp = tempfile.mkdtemp(prefix="film_site_")
    try:
        site = os.path.join(tmp, "film")
        os.makedirs(os.path.join(site, "person"))
        os.makedirs(os.path.join(site, "work"))
        pages = 0
        for pid in people:
            with open(os.path.join(site, "person", f"{pid}.html"), "w", encoding="utf-8") as f:
                f.write(_page_person(db_path, pid, today, screenings_dir))
            pages += 1
        works = store.load("works")
        rows = []
        for wid, w in sorted(works.items(), key=lambda kv: kv[1]["title"]):
            fname = _name(wid)
            with open(os.path.join(site, "work", f"{fname}.html"), "w", encoding="utf-8") as f:
                f.write(_page_work(store, db_path, wid, w, people_pid))
            pages += 1
            rel = (w.get("release") or {}).get("status", "未確認")
            rows.append(f"<li><a href='work/{fname}.html'>{_e(w['title'])}</a> ｜ 公開日：{_e(rel)}</li>")
        prow = "".join(f"<li><a href='person/{_e(pid)}.html'>{_e(p['name'])}"
                       f"（{_e(p.get('disambiguator') or '区別情報：未入力')}）</a></li>"
                       for pid, p in sorted(people.items(), key=lambda kv: kv[1]["name"]))
        index = W.page("映画・人物", "<h1>映画・人物</h1><h2>映画</h2>"
                       + (f"<ul>{''.join(rows)}</ul>" if rows else "<p class='empty'>映画は未登録です。</p>")
                       + "<h2>人物</h2>" + (f"<ul>{prow}</ul>" if prow else "<p class='empty'>人物は未登録です。</p>"))
        with open(os.path.join(site, "index.html"), "w", encoding="utf-8") as f:
            f.write(index)
        with open(os.path.join(site, MARKER), "w", encoding="utf-8") as f:
            f.write("film_site.py が生成したフォルダです。手で編集しないでください。\n")
        broken = check_internal_links(site)
        if broken:
            raise ValueError("内部リンクが壊れているため、出力を置き換えません: " + ", ".join(broken[:5]))
        # 生成が全部できてから出力先を置き換える（途中失敗では既存の出力を残す）
        if os.path.isdir(out_dir):
            shutil.rmtree(out_dir)
        os.makedirs(os.path.dirname(out_dir), exist_ok=True)
        shutil.move(site, out_dir)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"import": summary, "db": counts, "pages": pages, "out": out_dir}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=FW.DEFAULT_DIR)
    ap.add_argument("--db", default=os.path.join(HERE, "portal_data", "portal.db"))
    ap.add_argument("--batch", default=None, help="一括登録のJSON（任意）")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--data-kind", default="実データ", choices=FW.DATA_KINDS)
    ap.add_argument("--notice-out", default=None, help="更新通知のファイル（任意）。既に通知した差分は書かない")
    a = ap.parse_args()
    try:
        result = build_site(a.dir, a.db, a.out, a.batch, a.data_kind)
    except ValueError as e:
        sys.stderr.write(f"生成しません: {e}\n")
        sys.exit(2)
    diff = result.pop("diff")
    print(diff)                                  # 更新差分（変更なしなら「変更なし」）
    if a.notice_out:
        print(write_notice(a.dir, diff, a.notice_out))
    print(result)
