"""統合DB（portal_db が作る SQLite）の検索。読み取り専用。

- person_works：人物名 → その人物が関わる作品と役割（出典つき）
- work_people：作品名 → 関連する人物と役割
- work_screenings：作品名 → 上映回（上映が0件なら空の一覧を返す）
- facility_exhibitions：施設名 → その施設の展覧会（開催中・開催予定・終了、会期不明を区別）
- exhibitions_by_end：展覧会を終了日順に並べ、終了間近・会期不明・出典未確認を区別する
どの検索も、データが0件・欠損でも例外にせず、空の結果と理由を返す。
"""
import datetime
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import artist_watch as AW  # noqa: E402
import film_watch as FW  # noqa: E402  発売日の窓（SALE_NEAR_DAYS）を共有する

END_SOON_DAYS = 30


def _con(db_path):
    return sqlite3.connect(db_path)


def _norm(s):
    return AW.norm(s or "")


def person_works(db_path, name):
    """人物名（表記ゆれは norm で吸収）→ 作品と役割。同名の人物が複数あれば、それぞれ別に返す。"""
    con = _con(db_path)
    try:
        people = con.execute("SELECT id, name, source_url, data_kind FROM people").fetchall()
        out = []
        for pid, pname, purl, pkind in people:
            if _norm(pname) != _norm(name):
                continue
            works = con.execute(
                "SELECT w.title, c.role, w.source_url, w.release_year FROM credits c "
                "JOIN works w ON w.id = c.work_id WHERE c.person_id = ? ORDER BY w.title, c.role", (pid,)).fetchall()
            out.append({"person": pname, "source_url": purl, "data_kind": pkind,
                        "works": [{"title": t, "role": r, "source_url": u, "release_year": y} for t, r, u, y in works]})
        return out
    finally:
        con.close()


def person_candidates(db_path, name):
    """人物名（表記ゆれを吸収）に一致する人物を、区別情報つきで全部返す。名前だけで1人に決めない。"""
    con = _con(db_path)
    try:
        rows = con.execute("SELECT id, name, disambiguator, source_url, checked_on, data_kind FROM people").fetchall()
    finally:
        con.close()
    return [{"id": pid, "name": pname, "disambiguator": dis, "source_url": url, "checked_on": chk, "data_kind": kind}
            for pid, pname, dis, url, chk, kind in rows if _norm(pname) == _norm(name)]


def _sale_view(s):
    return {"kind": s[0], "status": s[1], "starts_at": s[2], "url": s[3], "source_url": s[4], "checked_on": s[5]}


def person_profile(db_path, person_id, today):
    """人物1人のプロフィール・関連作品（役割・公開日の状態・販売）・今後の公開予定。
    日付がない項目は「未確認」「未発表」の状態を保ち、日付を推測しない。"""
    con = _con(db_path)
    try:
        row = con.execute("SELECT id, name, disambiguator, source_url, checked_on, data_kind FROM people WHERE id = ?",
                          (person_id,)).fetchone()
        if row is None:
            return None
        works = []
        for wid, title, role, rdate, rstatus, wurl, curl, cchk, rurl, rchk in con.execute(
                "SELECT w.id, w.title, c.role, w.release_date, w.release_status, w.source_url, c.source_url, c.checked_on, "
                "w.release_source_url, w.checked_on "
                "FROM credits c JOIN works w ON w.id = c.work_id WHERE c.person_id = ? ORDER BY w.title, c.role",
                (person_id,)).fetchall():
            sales = [_sale_view(s) for s in con.execute(
                "SELECT kind, status, starts_at, url, source_url, checked_on FROM sales WHERE work_id = ? ORDER BY kind",
                (wid,)).fetchall()]
            history = [{"before": b, "after": a, "checked_on": c, "source_url": u} for b, a, c, u in con.execute(
                "SELECT before_date, after_date, checked_on, source_url FROM release_history WHERE work_id = ? "
                "ORDER BY rowid", (wid,)).fetchall()]
            works.append({"work_id": wid, "title": title, "role": role, "release_date": rdate,
                          "release_status": rstatus or "未確認", "release_source_url": rurl or "", "release_checked_on": rchk or "", "source_url": wurl, "credit_source_url": curl,
                          "credit_checked_on": cchk, "sales": sales, "release_history": history})
    finally:
        con.close()
    upcoming = []
    for w in works:
        if w["release_status"] == "公開日確定" and w["release_date"] and w["release_date"] >= today.isoformat() \
                and w["release_source_url"] and w["release_checked_on"]:   # 出典と確認日のない日付は「確定」として並べない
            upcoming.append({"title": w["title"], "kind": "公開日", "date": w["release_date"], "url": w["release_source_url"]})
        for s in w["sales"]:
            if s["status"] == "発売前" and s["starts_at"] and s["starts_at"][:10] >= today.isoformat():
                upcoming.append({"title": w["title"], "kind": f"{s['kind']}販売開始", "date": s["starts_at"],
                                 "url": s["url"] or s["source_url"]})
    upcoming.sort(key=lambda x: x["date"])
    # 発売開始が近い予定：登録された「発売前」の販売だけ（日付のある販売のみ。推測しない）
    near = []
    for w in works:
        for s in w["sales"]:
            if s["status"] == "発売前" and s["starts_at"]:
                left = (datetime.date.fromisoformat(s["starts_at"][:10]) - today).days
                if 0 <= left <= FW.SALE_NEAR_DAYS:
                    near.append({"title": w["title"], "kind": s["kind"], "starts_at": s["starts_at"],
                                 "days_left": left, "url": s["url"] or s["source_url"]})
    near.sort(key=lambda x: (x["days_left"], x["title"]))
    # 次回作：日本公開日が未発表として登録された作品、または確定日が今日以降の作品（推測しない）
    next_works = [{"title": w["title"], "role": w["role"], "release_status": w["release_status"],
                   "release_date": w["release_date"]} for w in works
                  if w["release_status"] == "未発表" or (w["release_status"] == "公開日確定" and w["release_date"]
                                                       and w["release_date"] >= today.isoformat() and w["release_source_url"] and w["release_checked_on"])]
    return {"id": row[0], "name": row[1], "disambiguator": row[2] or "", "source_url": row[3],
            "checked_on": row[4] or "", "data_kind": row[5], "works": works, "upcoming": upcoming,
            "near_sales": near, "next_works": next_works}


def work_people(db_path, title):
    """作品名 → 関連人物と役割（監督・脚本・出演など）。"""
    con = _con(db_path)
    try:
        works = con.execute("SELECT id, title, source_url, data_kind, release_year FROM works").fetchall()
        out = []
        for wid, wtitle, wurl, wkind, wyear in works:
            if _norm(wtitle) != _norm(title):
                continue
            ppl = con.execute(
                "SELECT p.name, c.role, p.source_url FROM credits c JOIN people p ON p.id = c.person_id "
                "WHERE c.work_id = ? ORDER BY c.role, p.name", (wid,)).fetchall()
            out.append({"work": wtitle, "source_url": wurl, "data_kind": wkind, "release_year": wyear or "",
                        "people": [{"name": n, "role": r, "source_url": u} for n, r, u in ppl]})
        return out
    finally:
        con.close()


def work_screenings(db_path, title):
    """作品名 → 上映回（日時順）。上映が無ければ空の一覧。公開年は、両方が分かる時だけ照合に使う。"""
    con = _con(db_path)
    try:
        rows = con.execute("SELECT title, theater, screen_date, start_time, screening_type, program_title, "
                           "evidence_url, data_kind FROM screenings ORDER BY screen_date, start_time").fetchall()
        return [{"title": t, "theater": th, "screen_date": d, "start_time": st, "screening_type": ty,
                 "program_title": pg, "evidence_url": u, "data_kind": k}
                for t, th, d, st, ty, pg, u, k in rows if _norm(t) == _norm(title)]
    finally:
        con.close()


def status_of(start, end, today):
    if start is None and end is None:
        return "会期不明"
    if end is not None and end < today:
        return "終了"
    if start is not None and start > today:
        return "開催予定"
    return "開催中"


def _exhibition_view(row, today):
    title, venue, start, end, state, flags, official_url, checked_at, data_kind, reason = row
    s = datetime.date.fromisoformat(start) if start else None
    e = datetime.date.fromisoformat(end) if end else None
    tags = []
    if e is None:
        tags.append("会期不明")
    elif today <= e <= today + datetime.timedelta(days=END_SOON_DAYS) and (s is None or s <= today):
        tags.append("終了間近")
    if state != "確認済み":
        tags.append("出典未確認")
    if official_url and "news.google.com" in official_url:
        tags.append("非公式出典")          # 集約サイト経由。公式の出典ではない
    if official_url and not official_url.startswith("https://"):
        tags.append("出典URL不正")        # https でないURLは、リンクにしない（portal_preview 側）
    return {"title": title, "venue": venue, "start_date": start, "end_date": end,
            "status": status_of(s, e, today), "tags": tags, "official_url": official_url,
            "checked_at": checked_at, "data_kind": data_kind, "missing_reason": reason or ""}


def facility_exhibitions(db_path, facility_name, today):
    """施設名 → その施設の展覧会（会場名での一致）。会期不明は「会期不明」と表示する。"""
    con = _con(db_path)
    try:
        rows = con.execute("SELECT title, venue, start_date, end_date, state, flags, official_url, checked_at, "
                           "data_kind, missing_reason FROM exhibitions").fetchall()
        hits = [_exhibition_view(r, today) for r in rows if _norm(r[1]) == _norm(facility_name)]
        return sorted(hits, key=lambda x: (x["end_date"] is None, x["end_date"] or "", x["title"]))
    finally:
        con.close()


def exhibitions_by_end(db_path, today):
    """展覧会を終了日順に並べる。会期不明は最後にまとめる。0件でも空の一覧を返す。"""
    con = _con(db_path)
    try:
        rows = con.execute("SELECT title, venue, start_date, end_date, state, flags, official_url, checked_at, "
                           "data_kind, missing_reason FROM exhibitions").fetchall()
        views = [_exhibition_view(r, today) for r in rows]
        return sorted(views, key=lambda x: (x["end_date"] is None, x["end_date"] or "", x["title"]))
    finally:
        con.close()
