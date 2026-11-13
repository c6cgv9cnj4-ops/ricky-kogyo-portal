"""美術館・美術展の見逃し防止（既存の展示データを読み取るだけの独立モジュール）。

- 入力：既存の展示ダッシュボードの生成HTML（docs/culture-exhibition/index.html）を読み取り専用で解析する（アダプター）。
  既存の culture_exhibition_dashboard.py は変更しない。公開HTMLも変更しない。
- 登録：作家（名・別名・作品名）。登録が空でも動く。本番用の登録リストは作らない（利用者が手入力する）。
- 照合：完全一致（区切りで囲まれた一致）は「確定」、部分一致は「要確認」。一致した文字列と方式を記録する。
- 差分：新規・更新（会期・状態）・終了（会期終了日を過ぎた）。取得失敗・空データでは何も変更しない。
- 予約・発売・締切：既存データに無いため、推測せず null（未確認）で保持する。
"""
import datetime
import html as html_lib
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event_records as ER  # noqa: E402

JST = datetime.timezone(datetime.timedelta(hours=9))
DEFAULT_SOURCE = os.path.join("docs", "culture-exhibition", "index.html")
DEFAULT_DIR = "exhibition_watch_data"
SEP_CHARS = " 　・、，,/／「」『』（）()［］[]【】:：-－ー"


def norm(text):
    t = unicodedata.normalize("NFKC", str(text or "")).lower()
    return re.sub(r"[\s・･.,、。\-_]", "", t)


def _esc_free(text):
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def load_cards(source_html=DEFAULT_SOURCE):
    """既存の展示ページを読み取り専用で解析する（アダプター）。戻り値: カードの辞書のリスト。"""
    with open(source_html, encoding="utf-8") as f:
        text = f.read()
    cards = []
    for block in text.split('<a class="card"')[1:]:
        tag = block.split(">", 1)[0]
        href = re.search(r'href="([^"]*)"', tag)
        venue = re.search(r'data-venue="([^"]*)"', tag)
        title = re.search(r'<h3 class="title">(.*?)<span', block, re.S)
        period = re.search(r"<div class='period[^']*'>(.*?)</div>", block, re.S)
        state = re.search(r'data-state="([^"]*)"', tag)
        cards.append({
            "venue": _esc_free(venue.group(1)) if venue else "",
            "title": _esc_free(title.group(1)) if title else "",
            "period": _esc_free(period.group(1)) if period else "",
            "link": href.group(1) if href else "",
            "state": state.group(1) if state else "",
        })
    return cards


def parse_dates(period_text):
    """会期文字列から開始・終了日を取り出す。判明しない場合は None（推測しない）。"""
    import calendar  # noqa: F401  (標準ライブラリ)
    full = re.findall(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", period_text or "")
    start = end = None
    if full:
        y, m, d = map(int, full[0])
        start = datetime.date(y, m, d)
    if len(full) >= 2:
        y, m, d = map(int, full[-1])
        end = datetime.date(y, m, d)
    elif full:
        end = _end_from_text(period_text, start)
    return start, end


def _end_from_text(period_text, start):
    """「2026年7月8日 〜 8月30日」「…まで」の形式だけ終了日として扱う（既存の期限解析に合わせる）。"""
    m = re.search(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日", period_text[period_text.find("日") + 1:] if "日" in period_text else "")
    if m and start:
        year = start.year + 1 if int(m.group(1)) < start.month else start.year
        try:
            return datetime.date(year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    if re.search(r"まで|迄|終了", period_text or "") and start:
        return start
    return None


def make_id(card):
    link = urllib_defrag(card.get("link", ""))
    return f"{card.get('venue','')}|{norm(card.get('title',''))}|{link}"


def urllib_defrag(url):
    return str(url or "").split("#", 1)[0]


def match_card(card, registry):
    """登録作家・作品との一致。戻り値: 一致の一覧。各要素に一致した文字列・方式・確度を記録する。"""
    out = []
    fields = [("展示名", card.get("title", "")), ("会場", card.get("venue", ""))]
    for artist in registry.get("artists", []):
        names = [artist["name"]] + list(artist.get("aliases", []))
        for name in names:
            n = norm(name)
            if len(n) < 2:
                continue
            for field, text in fields:
                t = norm(text)
                if not t or n not in t:
                    continue
                exact_token = _bounded(text, name)
                confidence = "確定" if (exact_token and len(n) >= 3) else "要確認"
                out.append({"artist_id": artist["id"], "matched": name, "field": field,
                            "method": "区切り一致" if exact_token else "部分一致", "confidence": confidence})
        for work in artist.get("works", []):
            w = norm(work)
            if len(w) >= 2 and w in norm(card.get("title", "")):
                exact = norm(card.get("title", "")) == w
                out.append({"artist_id": artist["id"], "matched": work, "field": "作品名",
                            "method": "完全一致" if exact else "部分一致", "confidence": "確定" if exact else "要確認"})
    seen, uniq = set(), []
    for m in out:
        k = (m["artist_id"], m["matched"], m["field"])
        if k not in seen:
            seen.add(k); uniq.append(m)
    return uniq


def _bounded(text, name):
    """名前が、区切り記号か文字列の端で囲まれて現れるか。"""
    s = unicodedata.normalize("NFKC", text)
    n = unicodedata.normalize("NFKC", name)
    for m in re.finditer(re.escape(n), s):
        before = s[m.start() - 1] if m.start() > 0 else " "
        after = s[m.end()] if m.end() < len(s) else " "
        if (before in SEP_CHARS or before == " ") and (after in SEP_CHARS or after == " "):
            return True
    return False


class State:
    def __init__(self, base=DEFAULT_DIR):
        self.base = base
        self.paths = {"exhibitions": os.path.join(base, "exhibitions.json"),
                      "registry": os.path.join(base, "registry.json"),
                      "ledger": os.path.join(base, "ledger.json")}

    def load(self, key):
        default = {"artists": []} if key == "registry" else {}
        try:
            with open(self.paths[key], encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def save(self, key, data):
        os.makedirs(self.base, exist_ok=True)
        ER.write_json_atomic(self.paths[key], data)


def run(state, cards, source_ok, today, checked_at, source_url=DEFAULT_SOURCE):
    """展示データを前回と比較する。取得失敗・空データでは何も変えない。戻り値: 差分の要約と候補。"""
    if not source_ok:
        return {"applied": False, "reason": "情報源の取得失敗（既存データを保持）", "events": []}
    if not cards:
        return {"applied": False, "reason": "0件（既存データを保持。終了と判定しない）", "events": []}
    prev = state.load("exhibitions")
    events, seen = [], set()
    for c in cards:
        rid = make_id(c)
        seen.add(rid)
        start, end = parse_dates(c["period"])
        rec = {"id": rid, "venue": c["venue"], "title": c["title"], "period_text": c["period"],
               "start": start.isoformat() if start else None, "end": end.isoformat() if end else None,
               "official_url": urllib_defrag(c["link"]), "source_url": source_url,
               "checked_at": checked_at, "reservation": None, "sale_start": None, "deadline": None}
        old = prev.get(rid)
        if old is None:
            rec["first_seen_at"] = checked_at
            prev[rid] = rec
            events.append({"id": rid, "event": "新規", "title": c["title"], "venue": c["venue"]})
        elif old["period_text"] != rec["period_text"] or old["end"] != rec["end"] or old["start"] != rec["start"]:
            events.append({"id": rid, "event": "更新", "title": c["title"], "venue": c["venue"],
                           "before": old["period_text"], "after": rec["period_text"]})
            rec["first_seen_at"] = old.get("first_seen_at", checked_at)
            prev[rid] = rec
        else:
            old["checked_at"] = checked_at
    for rid, old in prev.items():
        if rid in seen:
            continue
        if old.get("end") and datetime.date.fromisoformat(old["end"]) < today and old.get("ended_at") is None:
            old["ended_at"] = checked_at
            events.append({"id": rid, "event": "終了", "title": old["title"], "venue": old["venue"]})
    state.save("exhibitions", prev)
    return {"applied": True, "reason": "", "events": events}


def candidates(state, events, registry):
    """通知候補（外部送信はしない）。同じ候補は台帳で一度だけ作る。"""
    ledger = state.load("ledger")
    out = []
    for ev in events:
        key = f"{ev['id']}|{ev['event']}|{ev.get('after','')}"
        if key in ledger:
            continue
        out.append({"key": key, "event": ev["event"], "title": ev["title"], "venue": ev["venue"]})
    return out


def mark_dry_run(state, cands):
    ledger = state.load("ledger")
    for c in cands:
        ledger.setdefault(c["key"], {"status": "dry_run"})
    state.save("ledger", ledger)
    return len(cands)
