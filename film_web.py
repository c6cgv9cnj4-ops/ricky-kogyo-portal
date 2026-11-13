"""映画→関係者→追跡登録の最小のローカル試作（127.0.0.1 のみ。外部通信・実取得なし）。

- 映画一覧 → 映画を選ぶ → 関係者（出典つき）と登録状態を表示
- 未登録の関係者は「追跡対象に追加」で、既存の register_person / link_credit により人物台帳へ登録
- 同姓同名は名前だけで同一人物と判断しない。区別情報が両方そろって一致したときだけ「登録済み」
- 登録済みの人物は、DB（portal_db → portal_search）から関連作品・公開予定・販売を表示する。
  未登録の情報は架空の値を出さず「未確認」と表示する
"""
import datetime
import html
import http.server
import os
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import film_watch as FW  # noqa: E402
import movie_watch as MW  # noqa: E402
import portal_db as D  # noqa: E402
import portal_preview as P  # noqa: E402
import portal_search as Q  # noqa: E402
import screenings as S  # noqa: E402
import exhibition_watch as E  # noqa: E402

STATUS_REGISTERED = "登録済み"
STATUS_UNREGISTERED = "未登録"
STATUS_CHECK = "要確認（同名の人物あり）"


def _e(v):
    return html.escape(str(v or ""))


def staff_status(store, staff):
    """関係者の登録状態と、対応する人物ID。名前だけでは一致と判断しない。
    - 登録済み：名前が同じで、区別情報の両方がそろって一致する人物がいる
    - 要確認：同名の人物がいるが、区別情報で別人と確定できない（自動では登録しない）
    - 未登録：同名の人物がいない、または区別情報で別人だと確定できる"""
    people = store.load("people")
    sname = MW.normalize(staff["name"])
    sdis = staff.get("disambiguator", "")
    same = [(pid, p) for pid, p in people.items() if MW.normalize(p["name"]) == sname]
    for pid, p in same:
        if sdis and p.get("disambiguator") == sdis:
            return STATUS_REGISTERED, pid
    blocking = [pid for pid, p in same if not (sdis and p.get("disambiguator") and p["disambiguator"] != sdis)]
    if blocking:
        return STATUS_CHECK, None
    return STATUS_UNREGISTERED, None


def films(store):
    """映画（作品）の一覧。関係者の数と、公開日の状態（未入力は未確認）を添える。"""
    out = []
    for wid, w in sorted(store.load("works").items(), key=lambda kv: kv[1]["title"]):
        rel = (w.get("release") or {}).get("status", "未確認")
        out.append({"id": wid, "title": w["title"], "staff": len(w.get("staff", [])), "release": rel})
    return out


def track(store, work_id, index, data_kind=None):
    """関係者を追跡対象に追加する。未登録なら人物台帳へ登録し、登録済みなら作品との結び付けだけ行う。
    要確認の関係者は登録しない（区別情報で確認するまで）。戻り値は (状態, 人物ID)。"""
    works = store.load("works")
    if work_id not in works:
        raise ValueError("作品が未登録です")
    staff_list = works[work_id].get("staff", [])
    if not 0 <= index < len(staff_list):
        raise ValueError("関係者の指定が不正です")
    staff = staff_list[index]
    status, pid = staff_status(store, staff)
    if status == STATUS_CHECK:
        raise ValueError(f"{staff['name']} は同名の人物があり、区別情報で確認するまで登録できません")
    kind = data_kind or works[work_id].get("data_kind", "実データ")
    if status == STATUS_UNREGISTERED:
        pid = FW.register_person(store, staff["name"], [], kind, staff["source_url"],
                                 disambiguator=staff.get("disambiguator", ""), checked_on=staff["checked_on"])
    FW.link_credit(store, work_id, pid, staff["role"], staff["source_url"], staff["checked_on"])
    return STATUS_REGISTERED, pid


def film_html(store, work_id):
    works = store.load("works")
    if work_id not in works:
        return page("映画が見つかりません", "<p>この作品は登録されていません。</p>")
    w = works[work_id]
    rows = []
    for i, s in enumerate(w.get("staff", [])):
        status, pid = staff_status(store, s)
        if status == STATUS_REGISTERED:
            who = f"<a href='/person?id={_e(pid)}'>{_e(s['name'])}</a>"
            action = ""
        else:
            who = _e(s["name"])
            action = ("<form method='post' action='/track'>"
                      f"<input type='hidden' name='work_id' value='{_e(work_id)}'>"
                      f"<input type='hidden' name='index' value='{i}'>"
                      "<button type='submit'>追跡対象に追加</button></form>") if status == STATUS_UNREGISTERED else ""
        rows.append(f"<tr><td>{who}</td><td>{_e(s['role'])}</td>"
                    f"<td>{_e(s.get('disambiguator') or '区別情報：未入力')}</td>"
                    f"<td>{_e(status)}</td><td>{action}</td>"
                    f"<td><a href='{_e(s['source_url'])}'>出典</a>（確認日 {_e(s['checked_on'])}）</td></tr>")
    table = ("<table><tr><th>関係者</th><th>役割</th><th>区別情報</th><th>登録状態</th><th>操作</th><th>出典</th></tr>"
             + "".join(rows) + "</table>") if rows else "<p>関係者は登録されていません。</p>"
    return page(w["title"], f"<p><a href='/'>← 映画一覧</a></p><h2>{_e(w['title'])}</h2>"
                            "<p>関係者は公式情報の出典つきの候補です。追加すると人物台帳に登録されます。</p>" + table)


def index_html(store):
    rows = "".join(f"<li><a href='/film?id={_e(f['id'])}'>{_e(f['title'])}</a> ｜ 関係者 {f['staff']}件 ｜ "
                   f"公開日：{_e(f['release'])}</li>" for f in films(store))
    return page("映画一覧", "<h2>映画を選ぶ</h2>" + (f"<ul>{rows}</ul>" if rows else "<p>映画は登録されていません。</p>"))


def person_html(db_path, person_id, today, exhibition_source, screenings_dir, film_dir):
    """登録済みの人物を、DB（構築し直す）から表示する。販売・公開の情報がなければ「未確認」と出す。"""
    D.build(db_path, film_dir, exhibition_source, screenings_dir, today=today)
    prof = Q.person_profile(db_path, person_id, today)
    if prof is None:
        return page("人物が見つかりません", "<p>この人物は登録されていません。</p>")
    return page(prof["name"], "<p><a href='/'>← 映画一覧</a></p>" + P.person_card(prof))


def page(title, body):
    return ("<!doctype html><html lang='ja'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{_e(title)}</title>"
            "<style>body{font-family:-apple-system,sans-serif;margin:16px;line-height:1.6}"
            "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px;vertical-align:top}"
            ".empty{color:#777}</style></head><body>" + body + "</body></html>")


def make_handler(film_dir, db_path, exhibition_source, screenings_dir, today=None):
    """HTTP ハンドラを作る。すべての表示は既存の関数（上の関数・portal_preview）を使う。"""
    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, code, body, location=None):
            data = body.encode("utf-8")
            self.send_response(code)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            q = urllib.parse.parse_qs(url.query)
            store = FW.Store(film_dir)
            day = today or S.AW.now_jst().date()
            if url.path == "/":
                return self._send(200, index_html(store))
            if url.path == "/film":
                return self._send(200, film_html(store, q.get("id", [""])[0]))
            if url.path == "/person":
                return self._send(200, person_html(db_path, q.get("id", [""])[0], day, exhibition_source,
                                                   screenings_dir, film_dir))
            self._send(404, page("見つかりません", "<p>ページがありません。</p>"))

        def do_POST(self):
            if self.path != "/track":
                return self._send(404, page("見つかりません", "<p>ページがありません。</p>"))
            length = min(int(self.headers.get("Content-Length") or 0), 4096)
            form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
            work_id = form.get("work_id", [""])[0]
            try:
                track(FW.Store(film_dir), work_id, int(form.get("index", ["-1"])[0]))
            except ValueError as e:
                return self._send(400, page("登録できません", f"<p>{_e(e)}</p>"))
            self._send(303, "", location=f"/film?id={urllib.parse.quote(work_id)}")

        def log_message(self, *args):   # 標準出力を汚さない
            pass

    return Handler


def serve(film_dir, db_path, exhibition_source=E.DEFAULT_SOURCE, screenings_dir="screenings_data", port=8765):
    """127.0.0.1 だけで待ち受ける（外部からは接続できない）。"""
    import http.server as hs
    handler = make_handler(film_dir, db_path, exhibition_source, screenings_dir)
    return hs.ThreadingHTTPServer(("127.0.0.1", port), handler)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=FW.DEFAULT_DIR)
    ap.add_argument("--db", default=os.path.join(HERE, "portal_data", "portal.db"))
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    srv = serve(a.dir, a.db, port=a.port)
    print(f"http://127.0.0.1:{a.port}/  （停止は Ctrl+C）")
    srv.serve_forever()
