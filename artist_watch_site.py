"""巨大情報収集のホームページ（静的HTML）。台帳（artist_watch の Store）の内容を、一覧・詳細・情報源の状態として書き出す。

- 入力：台帳（events・history・sources・meta）。新しいDBは作らない。
- 出力：指定したフォルダ（既定では本番の docs/ には書かない）。
- 表示の区別：データなし／取得失敗（最後の取得結果）／合成データ・実データ（data_label で指定）。
"""
import datetime
import hashlib
import html
import os

import artist_watch as AW

PAGE_CSS = """
body{font-family:-apple-system,"Hiragino Sans",sans-serif;margin:0;padding:16px;color:#222;background:#fafafa}
h1{font-size:20px;margin:0 0 4px} h2{font-size:16px;margin:18px 0 6px}
.label{display:inline-block;padding:2px 8px;border-radius:4px;background:#fde68a;font-size:12px}
.warn{background:#fee2e2;padding:8px;border-radius:6px;margin:8px 0}
.empty{color:#666;padding:12px 0}
table{border-collapse:collapse;width:100%;background:#fff} th,td{border-bottom:1px solid #e5e7eb;padding:6px;text-align:left;font-size:14px;vertical-align:top}
a{color:#1d4ed8} .muted{color:#666;font-size:12px}
@media (max-width:600px){table,thead,tbody,tr,th,td{display:block} th{display:none} td{border:none;padding:2px 0}}
"""


def _e(v):
    return html.escape("" if v is None else str(v), quote=True)


def _banner(store, data_label, ledger_dir):
    """全ページ共通の状態表示：データの場所・出所・件数・最終取得・定期実行の未接続。"""
    n = len([e for e in store.load("events").values() if e.get("kind") == "performance"])
    srcs = store.load("sources")
    last = store.load("meta").get("last_status", {})
    lines = [f"<li>データの場所：<code>{_e(ledger_dir)}</code></li>",
             f"<li>{_e(data_label)}</li>",
             f"<li>公演の件数：{n}件{'（データなし）' if n == 0 else ''}</li>"]
    if not srcs:
        lines.append("<li>情報源：登録なし</li>")
    for sid, src in srcs.items():
        st = last.get(sid, {}).get("status")
        when = last.get(sid, {}).get("at", "")
        if st is None:
            state = "取り込みの記録なし"
        elif st == "ok":
            state = f"最終の取り込み：成功（{_e(when)}）"
        else:
            state = f"最終の取得：失敗（{_e(st)}・{_e(when)}）。表示は前回の正常データ"
        lines.append(f"<li>情報源 {_e(src.get('name'))}：{state}</li>")
    lines.append("<li><b>定期実行：未接続</b>（自動取得は動いていません。取り込みは手動のCSV/JSONのみ）</li>")
    return "<div class='warn' style='background:#eef2ff'><ul style='margin:0;padding-left:18px'>" + "".join(lines) + "</ul></div>"


def _page(title, body, data_label, banner=""):
    label = f'<span class="label">{_e(data_label)}</span>'
    return ("<!doctype html><html lang='ja'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{_e(title)}</title><style>{PAGE_CSS}</style></head><body>"
            f"<h1>{_e(title)}</h1><p>{label} <a href='index.html'>一覧へ</a> ・ <a href='sources.html'>情報源の状態</a></p>"
            f"{banner}{body}</body></html>")


def detail_file(perf_id):
    return "p_" + hashlib.sha1(perf_id.encode("utf-8")).hexdigest()[:16] + ".html"


def _performances(store):
    perfs = [e for e in store.load("events").values() if e.get("kind") == "performance"]
    # 開催日の近い順。日付不明は末尾
    return sorted(perfs, key=lambda e: (e.get("event_date") is None, e.get("event_date") or "", e.get("name", "")))


def _last_change(store, pid):
    hs = [h for h in store.load("history") if h.get("id") == pid and h.get("field") != "新規"]
    return hs[-1]["at"] if hs else ""


def _artist_name(store, artist_id):
    a = store.load("artists").get(artist_id, {})
    return a.get("name") or "（アーティスト名なし）"


def _index(store, data_label, ledger_dir="(不明)"):
    perfs = _performances(store)
    if not perfs:
        body = "<p class='empty'>まだ公演の情報がありません（データなし）。情報源を登録し、取り込みを実行してください。</p>"
        return _page("巨大情報収集：公演一覧", body, data_label, _banner(store, data_label, ledger_dir))
    rows = []
    for p in perfs:
        pid = p["id"]
        sales = "・".join(f"{_e(w.get('type'))} {_e(w.get('start') or '未定')}" for w in p.get("sale_windows", [])) or "未確認"
        last = _last_change(store, pid) or p.get("first_seen_at", "")
        rows.append(
            "<tr>"
            f"<td><a href='{detail_file(pid)}'>{_e(p.get('name'))}</a><div class='muted'>{_e(_artist_name(store, p.get('artist_id')))}</div></td>"
            f"<td>{_e(p.get('event_date') or '未確認')}</td>"
            f"<td>{_e(p.get('venue'))}</td>"
            f"<td>{_e(p.get('status'))}</td>"
            f"<td>{sales}</td>"
            f"<td class='muted'>最終更新 {_e(last)}</td>"
            "</tr>")
    body = ("<table><thead><tr><th>公演</th><th>開催日</th><th>会場</th><th>状態</th><th>発売</th><th>更新</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>")
    return _page("巨大情報収集：公演一覧", body, data_label, _banner(store, data_label, ledger_dir))


def _detail(store, p, data_label, ledger_dir="(不明)"):
    pid = p["id"]
    notices = [e for e in store.load("events").values() if e.get("kind") == "notice" and e.get("parent_id") == pid]
    hist = [h for h in store.load("history") if h.get("id") == pid or h.get("id") in {n["id"] for n in notices}]
    sales = "".join(f"<li>{_e(w.get('type'))}：{_e(w.get('start') or '未定')} 〜 {_e(w.get('end') or '未定')}</li>"
                    for w in p.get("sale_windows", [])) or "<li>発売情報なし（未確認）</li>"
    nots = "".join(f"<li>{_e(n.get('notice_type'))}：{_e(n.get('detail'))} <a href='{_e(n.get('evidence_url'))}'>出典</a></li>"
                   for n in notices) or "<li>お知らせなし</li>"
    hrows = "".join(f"<tr><td>{_e(h.get('at'))}</td><td>{_e(h.get('field'))}</td><td>{_e(h.get('before'))}</td>"
                    f"<td>{_e(h.get('after'))}</td><td><a href='{_e(h.get('evidence_url'))}'>出典</a></td></tr>" for h in hist)
    body = (f"<h2>基本情報</h2><ul><li>アーティスト：{_e(_artist_name(store, p.get('artist_id')))}</li><li>会場：{_e(p.get('venue'))}</li><li>開催日：{_e(p.get('event_date') or '未確認')}</li>"
            f"<li>状態：{_e(p.get('status'))}</li></ul>"
            f"<h2>発売</h2><ul>{sales}</ul><h2>お知らせ（延期・中止など）</h2><ul>{nots}</ul>"
            f"<h2>出典と取得</h2><ul><li>情報源：{_e(p.get('source_id'))}</li><li>取得時刻：{_e(p.get('fetched_at') or '未記録')}</li>"
            f"<li>出典：<a href='{_e(p.get('evidence_url'))}'>{_e(p.get('evidence_url'))}</a></li></ul>"
            "<h2>変更履歴</h2><table><thead><tr><th>時刻</th><th>項目</th><th>変更前</th><th>変更後</th><th>出典</th></tr></thead>"
            f"<tbody>{hrows or '<tr><td colspan=5>履歴なし</td></tr>'}</tbody></table>")
    return _page(p.get("name") or pid, body, data_label, _banner(store, data_label, ledger_dir))


def _sources(store, data_label, now, ledger_dir="(不明)"):
    srcs = store.load("sources")
    last = store.load("meta").get("last_status", {})
    anchors = store.load("meta").get("anchors", {})
    if not srcs:
        body = "<p class='empty'>情報源が登録されていません（データなし）。</p>"
        return _page("巨大情報収集：情報源の状態", body, data_label, _banner(store, data_label, ledger_dir))
    rows = []
    for sid, s in srcs.items():
        st = last.get(sid, {})
        status = st.get("status")
        if status is None:
            state = "未取得（取得経路が未接続、またはまだ実行していない）"
        elif status == "ok":
            state = "最終取得は成功"
        else:
            state = f"最終取得は失敗（{_e(status)}）。既存のデータは保持"
        auto = "自動取得の許可：確認済み" if s.get("auto_fetch_allowed") else "自動取得の許可：未確認（取得しない）"
        rows.append(f"<tr><td>{_e(s.get('name'))}<div class='muted'>{_e(sid)}</div></td><td>{_e(s.get('kind'))}</td>"
                    f"<td>{auto}</td><td>{state}<div class='muted'>{_e(st.get('at', ''))}</div></td>"
                    f"<td class='muted'>成功した最終時刻 {_e(anchors.get(sid, '未記録'))}</td></tr>")
    body = ("<table><thead><tr><th>情報源</th><th>種類</th><th>自動取得</th><th>最後の取り込み・取得</th><th>最後の成功</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>")
    return _page("巨大情報収集：情報源の状態", body, data_label, _banner(store, data_label, ledger_dir))


def render_site(store, out_dir, data_label="データ出所：未指定", now=None, ledger_dir=None):
    """台帳からページを書き出す。戻り値: 書き出したファイル数。ledger_dir は画面に表示する台帳の場所。"""
    now = now or AW.now_jst()
    ledger_dir = ledger_dir or getattr(store, "base", "(不明)")
    os.makedirs(out_dir, exist_ok=True)
    files = {"index.html": _index(store, data_label, ledger_dir),
             "sources.html": _sources(store, data_label, now, ledger_dir)}
    for p in _performances(store):
        files[detail_file(p["id"])] = _detail(store, p, data_label, ledger_dir)
    for name, text in files.items():
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write(text)
    return len(files)
