"""注目人物が関わる映画の発見・公開日の追跡（隔離の部品。未接続：本番の収集・表示は変更しない）。

- 注目人物の登録：data/watch_people.json（利用者が登録する。リポジトリには作らない）。未登録なら一致判定は行わない。
- 一致判定：表記を正規化して一致させる。同じ表記の登録人物が複数いる場合は一致させず、保留にする（同姓同名の誤判定を防ぐ）。
- 作品：公式の作品IDを優先して識別する。作品名だけに依存しない。
- 取得失敗・0件・件数急減では、前回の有効な記録を消さない。
- 公開日の未定・判明・変更・中止は、履歴に残す。
"""
import datetime
import html as html_lib
import json
import os
import unicodedata

PRIORITY_LABELS = ["注目人物の新規発見", "公開日が新たに判明", "公開日が変更", "公開日が近い", "公開日未定（続報待ち）"]
NEAR_DAYS = 30        # 公開日が近いとみなす日数（設計値。承認が必要）
RECENT_DAYS = 14      # 新規・判明・変更を新着として示す日数（設計値。承認が必要）
DROP_RATIO = 0.5
MIN_PREVIOUS = 3


def normalize(text):
    """表記揺れを吸収する：全角半角・大文字小文字・空白・中点（・）を除く。"""
    t = unicodedata.normalize("NFKC", str(text or "")).lower()
    return "".join(ch for ch in t if not ch.isspace() and ch not in "・･.,、。")


def load_registry(path=os.path.join("data", "watch_people.json")):
    """注目人物の登録を読む。無ければ空（＝未登録。推測で人物を作らない）。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [p for p in (data.get("people", []) if isinstance(data, dict) else []) if p.get("id") and p.get("names")]


def match_people(credits, registry):
    """スタッフ・出演者の一覧と注目人物を照合する。
    戻り値: (一致のリスト, 保留のリスト)。保留は、同じ表記が複数の登録人物に当たる場合。"""
    index = {}
    for person in registry:
        for name in person["names"]:
            index.setdefault(normalize(name), set()).add(person["id"])
    matches, held = [], []
    seen = set()
    for role in ("directors", "writers", "cast"):
        for name in credits.get(role, []):
            key = normalize(name)
            ids = index.get(key, set())
            if not ids:
                continue
            if len(ids) > 1:
                held.append({"name": name, "role": role, "candidates": sorted(ids)})
                continue
            pid = next(iter(ids))
            if (pid, role) not in seen:
                seen.add((pid, role))
                matches.append({"person_id": pid, "name": name, "role": role})
    return matches, held


def movie_key(source, official_id, title):
    """作品の識別子。公式IDがあればそれを使い、無ければ正規化した作品名（表記揺れは吸収する）。"""
    if official_id:
        return f"{source}|{official_id}"
    return f"{source}|title|{normalize(title)}"


def _day(value):
    return value.isoformat() if isinstance(value, datetime.date) else None


def merge_movie(prev, fresh, now_iso, today):
    """既存の記録に、今回の取得結果を反映する。変更は履歴に残す。
    - 公開日：未定 → 判明（初めて判明した日時を保存）、判明 → 変更（履歴）
    - スタッフ：未発表 → 確認済み（後日の補完）。確認済みの値が消えた場合は消さず、履歴に残す。"""
    rec = dict(prev or {})
    history = list(rec.get("history", []))
    if not prev:
        rec = {"id": fresh["id"], "title": fresh["title"], "first_detected_at": now_iso, "history": [],
               "release_date": None, "release_first_known_at": None, "credits": {}, "source_url": fresh.get("source_url", "")}
        history.append({"at": now_iso, "field": "作品", "old": None, "new": fresh["title"]})
    new_date = fresh.get("release_date")
    old_date = rec.get("release_date")
    if new_date and new_date != old_date:
        if old_date is None:
            rec["release_first_known_at"] = now_iso
            history.append({"at": now_iso, "field": "公開日", "old": None, "new": _day(new_date)})
        else:
            history.append({"at": now_iso, "field": "公開日", "old": _day(old_date), "new": _day(new_date)})
        rec["release_date"] = new_date
    for role, names in (fresh.get("credits") or {}).items():
        if names and rec.get("credits", {}).get(role) != names:
            old = rec.get("credits", {}).get(role)
            if old:
                history.append({"at": now_iso, "field": role, "old": old, "new": names})
            rec.setdefault("credits", {})[role] = names
    rec["title"] = fresh.get("title", rec.get("title"))
    rec["history"] = history
    rec["last_success_at"] = now_iso
    rec["fetch_state"] = "取得成功"
    return rec


def apply_source_result(previous, fresh, source_ok, now_iso, today):
    """1つの情報源の結果を反映する。戻り値: (記録のリスト, 状態の要約)。
    - 通信・HTTP・解析の失敗、0件、件数急減では、前回の記録をそのまま残す（取得失敗として表示）。
    - 一部の作品だけ取得できなかった場合は、その作品の記録を残し、状態を「掲載確認できず」にする。"""
    prev_by_id = {r["id"]: r for r in (previous or [])}
    if not source_ok:
        return [dict(r, fetch_state="取得失敗（前回の記録を保持）") for r in (previous or [])], {"ok": False, "reason": "情報源の取得失敗"}
    if not fresh:
        return [dict(r, fetch_state="0件（前回の記録を保持）") for r in (previous or [])], {"ok": False, "reason": "0件取得"}
    if previous and len(previous) >= MIN_PREVIOUS and len(fresh) < len(previous) * DROP_RATIO:
        return [dict(r, fetch_state="件数急減（前回の記録を保持）") for r in previous], {"ok": False, "reason": "件数急減"}
    out = {}
    for f in fresh:
        out[f["id"]] = merge_movie(prev_by_id.get(f["id"]), f, now_iso, today)
    for pid, old in prev_by_id.items():
        if pid not in out:
            out[pid] = dict(old, fetch_state="掲載確認できず（削除していません）")
    return list(out.values()), {"ok": True, "reason": ""}


def home_rows(records, matches_by_id, today):
    """ホームの優先順位で並べる。注目人物が関わる作品を優先し、公開日の判明・変更は見逃さないよう上位に出す。"""
    rows = []
    for rec in records:
        matched = bool(matches_by_id.get(rec["id"]))
        date = rec.get("release_date")
        first_seen = _parse_iso_date(rec.get("first_detected_at"))
        known = _parse_iso_date(rec.get("release_first_known_at"))
        changed = [h for h in rec.get("history", []) if h.get("field") == "公開日" and h.get("old")]
        changed_recent = any(_within(h.get("at"), today, RECENT_DAYS) for h in changed)
        label = None
        if matched and first_seen and (today - first_seen).days <= RECENT_DAYS:
            label = PRIORITY_LABELS[0]
        elif known and (today - known).days <= RECENT_DAYS:
            label = PRIORITY_LABELS[1]
        elif changed_recent:
            label = PRIORITY_LABELS[2]
        elif date and 0 <= (date - today).days <= NEAR_DAYS:
            label = PRIORITY_LABELS[3]
        elif date is None and matched:
            label = PRIORITY_LABELS[4]
        if label:
            rows.append({"label": label, "rec": rec, "matches": matches_by_id.get(rec["id"], [])})
    order = {name: i for i, name in enumerate(PRIORITY_LABELS)}
    rows.sort(key=lambda r: (order[r["label"]], r["rec"].get("release_date") or datetime.date.max, r["rec"]["title"]))
    return rows


def _parse_iso_date(value):
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _within(iso_value, today, days):
    d = _parse_iso_date(iso_value)
    return bool(d and 0 <= (today - d).days <= days)


def render_home(rows):
    """ホーム用の表示（HTML）。作品名・注目人物・公開日（または未定）・情報源リンク・初回検知日を示す。"""
    if not rows:
        return "<p class='news-empty'>注目人物が関わる新着・公開日の更新はありません（情報源が未接続の場合を含みます）。</p>"
    items = []
    for r in rows:
        rec = r["rec"]
        date = rec.get("release_date")
        date_text = date.isoformat() if isinstance(date, datetime.date) else "未定"
        names = "、".join(sorted({m["name"] for m in r["matches"]})) or "（一致なし）"
        url = rec.get("source_url", "")
        link = f"<a href='{_e(url)}' target='_blank' rel='noopener'>{_e(rec['title'])}</a>" if url.startswith(("http://", "https://")) else _e(rec["title"])
        items.append(
            "<li class='news-row'>"
            f"<span class='news-state'>{_e(r['label'])}</span>"
            f"{link}"
            f"<span class='news-venue'>注目人物：{_e(names)}</span>"
            f"<span class='news-period'>公開日：{_e(date_text)}</span>"
            f"<span class='news-announce'>初回検知：{_e(str(rec.get('first_detected_at', '未確認'))[:10])}</span>"
            "</li>")
    return "<ul class='news-list'>" + "".join(items) + "</ul>"


def _e(value):
    return html_lib.escape(str(value), quote=True)
