"""統合DB（SQLite）：人物・作品・クレジット・施設・展覧会・上映を1つのDBにまとめ、横断検索する。

- 入力はすべて既存の台帳（読み取りのみ）。元データは変更しない。
- DBは作り直し可能な派生物。一時ファイルに書いてから置き換えるため、途中で失敗しても既存のDBは壊れない。
- 各行に data_kind（実データ／テスト用架空データ）と出典URLを持たせ、実データとテストデータを混ぜない。
"""
import datetime
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import film_watch as FW  # noqa: E402
import screenings as S  # noqa: E402
import exhibition_watch as E  # noqa: E402
import portal_preview as P  # noqa: E402
import venue_master as V  # noqa: E402

SCHEMA = """
CREATE TABLE people (id TEXT PRIMARY KEY, name TEXT NOT NULL, external_id TEXT, data_kind TEXT, source_url TEXT,
  disambiguator TEXT, checked_on TEXT);
CREATE TABLE works (id TEXT PRIMARY KEY, title TEXT NOT NULL, release_year TEXT, external_id TEXT, data_kind TEXT,
  source_url TEXT, release_date TEXT, release_status TEXT, checked_on TEXT, release_source_url TEXT);
CREATE TABLE credits (work_id TEXT, person_id TEXT, role TEXT, source_url TEXT, checked_on TEXT,
  PRIMARY KEY (work_id, person_id, role));
CREATE TABLE sales (work_id TEXT, kind TEXT, status TEXT, starts_at TEXT, precision TEXT, url TEXT,
  source_url TEXT, checked_on TEXT, data_kind TEXT, PRIMARY KEY (work_id, kind, url));
CREATE TABLE release_history (work_id TEXT, before_date TEXT, after_date TEXT, checked_on TEXT, source_url TEXT,
  data_kind TEXT);
CREATE TABLE facilities (id TEXT PRIMARY KEY, name TEXT NOT NULL, exhibitions INTEGER);
CREATE TABLE exhibitions (id TEXT PRIMARY KEY, title TEXT NOT NULL, venue TEXT, facility_id TEXT,
  period_text TEXT, start_date TEXT, end_date TEXT, official_url TEXT, state TEXT, flags TEXT,
  data_kind TEXT, source_url TEXT, checked_at TEXT, missing_reason TEXT);
CREATE TABLE screenings (key TEXT PRIMARY KEY, title TEXT NOT NULL, theater TEXT, screen_date TEXT,
  start_time TEXT, runtime_min TEXT, screening_type TEXT, program_title TEXT, release_year TEXT,
  evidence_url TEXT, data_kind TEXT);
"""


def missing_reason(period, end):
    """会期の欠損理由（推測で埋めず、理由だけを分類する）。終了日が判明していれば空。"""
    if end is not None:
        return ""
    if not (period or "").strip():
        return "掲載に会期の記載なし"
    if "取得困難" in period:
        return "掲載元が取得困難（要アクセス）"
    if not re.search(r"\d{4}年", period):
        return "年の記載なし（推測しない）"
    return "終了の記載なし（開始のみ）"


def exhibition_id(row):
    """展覧会の安定ID：同じ会場・題名・公式URLなら同じID（出典URLが同じでも題名が違えば別）。"""
    import hashlib
    key = "|".join([str(row[1]), str(row[2]), str(row[6])])
    return "exh_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def external_id(url):
    """出典URLから外部IDを取る（Wikidata の URL なら Q番号）。それ以外は空（推測しない）。"""
    if url.startswith("https://www.wikidata.org/wiki/Q"):
        return url.rsplit("/", 1)[-1]
    return ""


def _rows_people_works(film_dir):
    """台帳 → 行。公開日・販売は、台帳に無いものを「未確認」として入れる（日付は補わない）。"""
    st = FW.Store(film_dir)
    people, works = st.load("people"), st.load("works")
    p_rows = [(pid, p["name"], external_id(p.get("source_url", "")), p.get("data_kind", ""), p.get("source_url", ""),
               p.get("disambiguator", ""), p.get("checked_on", ""))
              for pid, p in people.items()]
    w_rows, c_rows, s_rows = [], [], []
    for wid, w in works.items():
        rel = w.get("release") or {}
        w_rows.append((wid, w["title"], w.get("release_year", ""), external_id(w.get("source_url", "")),
                       w.get("data_kind", ""), w.get("source_url", ""), rel.get("date"),
                       rel.get("status", "未確認"), rel.get("checked_on", ""), rel.get("source_url", "")))
        for c in w.get("credits", []):
            if c["person_id"] in people:
                c_rows.append((wid, c["person_id"], c["role"], c.get("source_url", ""), c.get("checked_on", "")))
        for s in w.get("sales", []):
            s_rows.append((wid, s["kind"], s["status"], s.get("starts_at"), s.get("precision", "unknown"),
                           s.get("url", ""), s.get("source_url", ""), s.get("checked_on", ""), w.get("data_kind", "")))
    h_rows = [(h["work_id"], h["before"], h["after"], h["at"], h["source_url"], works.get(h["work_id"], {}).get("data_kind", ""))
              for h in st.load("history") if h.get("field") == "公開日" and h["work_id"] in works]
    return p_rows, w_rows, c_rows, s_rows, h_rows


def _rows_exhibitions(exhibition_source, today, data_kind):
    cards = E.load_cards(exhibition_source)
    rows = P.exhibition_rows(cards, today)
    fac = V.build_master(rows)
    # 確認日時：掲載ファイルの最終更新時刻（この掲載がいつの状態かを示す。取得した時刻ではない）
    checked = datetime.datetime.fromtimestamp(os.path.getmtime(exhibition_source), S.AW.JST).isoformat(timespec="seconds")
    e_rows = []
    for r in rows:
        start, end = E.parse_dates(r["period"])
        e_rows.append((V.facility_id(r["venue"]) if r["venue"] else None, r["title"], r["venue"], r["period"],
                       start.isoformat() if start else None, end.isoformat() if end else None,
                       r["link"], r["state"], ",".join(r["flags"]), data_kind, r["link"], checked,
                       missing_reason(r["period"], end)))
    f_rows = [(f["facility_id"], f["name"], f["exhibitions"]) for f in fac]
    return f_rows, e_rows


def _rows_screenings(screenings_dir, data_kind):
    _, data = S.load_store(screenings_dir)
    out = []
    for k, r in data["screenings"].items():
        out.append((k, r["title"], r.get("theater", ""), r["screen_date"], r["start_time"], r.get("runtime_min", ""),
                    r.get("screening_type", ""), r.get("program_title", ""), r.get("release_year", ""),
                    r.get("evidence_url", ""), data_kind))
    return out


def build(db_path, film_dir, exhibition_source, screenings_dir, today=None, data_kind="実データ"):
    """DBを一時ファイルに作り、完成したら置き換える。戻り値は各表の件数。
    data_kind は展覧会と上映の入力の種別（実データ／テスト用架空データ）。人物・作品は台帳の値を使う。"""
    today = today or S.AW.now_jst().date()
    p_rows, w_rows, c_rows, s_rows_sales, h_rows = _rows_people_works(film_dir)
    f_rows, e_rows = _rows_exhibitions(exhibition_source, today, data_kind)
    s_rows = _rows_screenings(screenings_dir, data_kind)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    tmp = db_path + ".building"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    try:
        con.executescript(SCHEMA)
        con.executemany("INSERT INTO people VALUES (?,?,?,?,?,?,?)", p_rows)
        con.executemany("INSERT INTO works VALUES (?,?,?,?,?,?,?,?,?,?)", w_rows)
        con.executemany("INSERT INTO credits VALUES (?,?,?,?,?)", c_rows)
        con.executemany("INSERT INTO sales VALUES (?,?,?,?,?,?,?,?,?)", s_rows_sales)
        con.executemany("INSERT INTO release_history VALUES (?,?,?,?,?,?)", h_rows)
        con.executemany("INSERT INTO facilities VALUES (?,?,?)", f_rows)
        # e_rows の列：(facility_id, title, venue, period_text, start, end, official_url, state, flags, data_kind, source_url)
        seen, ex_rows = set(), []
        for r in e_rows:
            eid = exhibition_id(r)
            if eid in seen:          # 同じ展覧会が掲載で重複している場合は1件にまとめる（件数はDBに入れない）
                continue
            seen.add(eid)
            ex_rows.append((eid, r[1], r[2], r[0], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11], r[12]))
        con.executemany("INSERT INTO exhibitions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ex_rows)
        con.executemany("INSERT INTO screenings VALUES (?,?,?,?,?,?,?,?,?,?,?)", s_rows)
        con.commit()
    finally:
        con.close()
    os.replace(tmp, db_path)
    return counts(db_path)   # DBに実際に入った件数


def search(db_path, q):
    """横断検索：人物・作品・施設・展覧会・上映の名前・題名に q を含むものを、出典つきで返す。"""
    con = sqlite3.connect(db_path)
    like = f"%{q}%"
    out = []
    for kind, sql in [
        ("人物", "SELECT name, source_url, data_kind FROM people WHERE name LIKE ?"),
        ("作品", "SELECT title, source_url, data_kind FROM works WHERE title LIKE ?"),
        ("施設", "SELECT name, '', '' FROM facilities WHERE name LIKE ?"),
        ("展覧会", "SELECT title, official_url, data_kind FROM exhibitions WHERE title LIKE ?"),
        ("上映", "SELECT title, evidence_url, data_kind FROM screenings WHERE title LIKE ?"),
    ]:
        for name, url, kindtag in con.execute(sql, (like,)):
            out.append({"type": kind, "title": name, "source_url": url, "data_kind": kindtag})
    con.close()
    return out


def counts(db_path):
    con = sqlite3.connect(db_path)
    res = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
           for t in ("people", "works", "credits", "sales", "facilities", "exhibitions", "screenings")}
    con.close()
    return res


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--film-dir", required=True)
    ap.add_argument("--screenings-dir", required=True)
    ap.add_argument("--exhibition-source", default=E.DEFAULT_SOURCE)
    args = ap.parse_args()
    print(json.dumps(build(args.db, args.film_dir, args.exhibition_source, args.screenings_dir), ensure_ascii=False))
