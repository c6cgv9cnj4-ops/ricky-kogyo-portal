"""海外アーティストの公演・受付情報の管理（本番から切り離した最小構成）。

- 登録：アーティスト（ID・表示名・別名・公式URL）、情報源（種類・利用条件の確認状態）、公演（ID・会場・日程・受付期間）。
- 差分：同じ公演の日程・会場・受付期間・状態の変更を履歴に残す。同じ情報の再登録は何もしない。
- 保全：情報源の失敗・空データでは、既存の公演を変更・削除しない。
- 期限：受付開始・締切接近・締切超過を判定する（日時はタイムゾーン付き。日付のみは日本時間の終日扱い）。
- 通知：外部送信はしない。通知候補（ドライラン）を作り、台帳で重複を防ぐ。
本番の収集・公開（docs/）・ワークフローには接続しない。データは既定で artist_watch_data/ に置く（リポジトリには作らない）。
"""
import datetime
import json
import os
import re
import unicodedata
import uuid

JST = datetime.timezone(datetime.timedelta(hours=9))
DEFAULT_DIR = "artist_watch_data"
DEADLINE_SOON_HOURS = 72
SALE_TYPES = ("先行受付", "一般発売", "リセール", "抽選受付")
NOTICE_TYPES = ("日程変更", "会場変更", "中止", "延期", "追加公演", "追加席", "訂正")


def norm(text):
    t = unicodedata.normalize("NFKC", str(text or "")).lower()
    return re.sub(r"[\s・･.,、。\-_]", "", t)


def now_jst():
    return datetime.datetime.now(JST)


def _parse_when(value, tz_default=JST):
    """値は ISO 文字列（日時、タイムゾーン付き）または 'YYYY-MM-DD'（日付のみ）。戻り値: (datetime, 精度)。"""
    if value in (None, ""):
        return None, "unknown"
    s = str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        d = datetime.date.fromisoformat(s)
        return datetime.datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz_default), "day"
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError(f"タイムゾーンのない日時は登録できません: {s}")
    return dt, "datetime"


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True, default=str)
    os.replace(tmp, path)


class CorruptStateError(RuntimeError):
    """台帳ファイルが壊れている（読めない）ことを示す。空扱いで続行しない。"""


class Store:
    def __init__(self, base=DEFAULT_DIR):
        self.base = base
        self.paths = {k: os.path.join(base, f"{k}.json") for k in ("artists", "sources", "events", "history", "ledger", "meta")}

    def load(self, key):
        default = {} if key in ("artists", "sources", "events", "ledger", "meta") else []
        path = self.paths[key]
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except ValueError as e:
                # 壊れた台帳を空として続けると、全候補が新規扱いになり再送のおそれがある。停止して人が確認する。
                raise CorruptStateError(f"台帳が壊れています（{key}: {path}）。内容を確認してから再実行してください: {e}")
        return default

    def save(self, key, data):
        _save(self.paths[key], data)


# ---------- A. アーティスト登録
def register_artist(store, name, official_url="", aliases=(), now=None):
    artists = store.load("artists")
    names = {norm(name)} | {norm(a) for a in aliases}
    for aid, a in artists.items():
        existing = {norm(a["name"])} | {norm(x) for x in a.get("aliases", [])}
        if existing & names:
            raise ValueError(f"同一アーティストの可能性があるため登録しません（既存ID: {aid}）")
    aid = "art_" + uuid.uuid4().hex[:8]
    artists[aid] = {"id": aid, "name": name, "aliases": list(aliases), "official_url": official_url,
                    "registered_at": (now or now_jst()).isoformat(timespec="seconds")}
    store.save("artists", artists)
    return aid


# ---------- B. 情報源登録
SOURCE_KINDS = ("公式サイト", "主催者", "会場", "公式ファンクラブ", "正規チケット販売元", "その他")


def register_source(store, source_id, name, url, kind, terms_status="未確認"):
    """利用条件が確認済みの情報源だけを自動取得の対象にする。未確認は手動閲覧のみ。"""
    if kind not in SOURCE_KINDS:
        raise ValueError(f"情報源の種類が不正です: {kind}")
    sources = store.load("sources")
    if source_id in sources:
        raise ValueError(f"情報源IDが既にあります: {source_id}")
    sources[source_id] = {"id": source_id, "name": name, "url": url, "kind": kind,
                          "terms_status": terms_status,
                          "auto_fetch_allowed": terms_status == "確認済み"}
    store.save("sources", sources)
    return source_id


# ---------- C/D. 公演・受付情報と差分
def performance_id(artist_id, venue, name, source_key=""):
    """公演ID。情報源が安定キー（source_key）を明示した場合は、会場が変わってもそのキーで同一公演とみなす。
    キーが無い場合は従来どおり（アーティスト・会場・公演名）。類似名による統合はしない。"""
    if source_key:
        return f"{artist_id}|key|{norm(source_key)}"
    return f"{artist_id}|performance|{norm(venue)}|{norm(name)}"


def item_perf_id(item):
    return performance_id(item["artist_id"], item["venue"], item["name"], item.get("perf_key", ""))


def _fields(rec):
    return {k: rec.get(k) for k in ("event_date", "venue", "status", "sale_windows")}


def upsert_performance(store, artist_id, venue, name, event_date=None, sale_windows=(), status="公演発表",
                       source_id="", evidence_url="", now=None, announced_at=None, perf_key=""):
    """公演を登録／更新する。変更があれば履歴に残す。同じ内容なら何もしない。戻り値: (id, 'new'|'changed'|'same')。"""
    now = now or now_jst()
    stamp = now.isoformat(timespec="seconds")
    events = store.load("events")
    history = store.load("history")
    rid = performance_id(artist_id, venue, name, perf_key)
    windows = [dict(w) for w in sale_windows]
    for w in windows:
        if w.get("type") not in SALE_TYPES:
            raise ValueError(f"受付の種類が不正です: {w.get('type')}")
        for k in ("start", "end"):
            _parse_when(w.get(k))
    new = {"id": rid, "kind": "performance", "artist_id": artist_id, "name": name, "venue": venue,
           "event_date": event_date, "sale_windows": windows, "status": status,
           "source_id": source_id, "evidence_url": evidence_url, "announced_at": announced_at}
    if rid not in events:
        new.update(first_seen_at=stamp, last_seen_at=stamp)
        events[rid] = new
        history.append({"at": stamp, "id": rid, "field": "新規", "before": None, "after": name, "evidence_url": evidence_url})
        store.save("events", events); store.save("history", history)
        return rid, "new"
    old = events[rid]
    changes = [(k, old.get(k), new[k]) for k in ("event_date", "venue", "status", "sale_windows") if old.get(k) != new[k]]
    old["last_seen_at"] = stamp
    if not changes:
        store.save("events", events)
        return rid, "same"
    for k, b, a in changes:
        history.append({"at": stamp, "id": rid, "field": k, "before": b, "after": a, "evidence_url": evidence_url})
        old[k] = a
    old["evidence_url"] = evidence_url or old.get("evidence_url", "")
    old["announced_at"] = announced_at or old.get("announced_at")
    store.save("events", events); store.save("history", history)
    return rid, "changed"


def add_notice(store, parent_id, notice_type, detail, evidence_url="", now=None):
    """日程変更・中止・延期などを、親の公演とは別の記録として登録する（別イベントとして扱う）。"""
    if notice_type not in NOTICE_TYPES:
        raise ValueError(f"お知らせの種類が不正です: {notice_type}")
    events = store.load("events")
    if parent_id not in events:
        raise ValueError("親の公演が見つかりません")
    stamp = (now or now_jst()).isoformat(timespec="seconds")
    nid = f"{parent_id}|notice|{notice_type}|{norm(json.dumps(detail, ensure_ascii=False, sort_keys=True))}"
    if nid in events:
        return nid, "same"
    events[nid] = {"id": nid, "kind": "notice", "parent_id": parent_id, "notice_type": notice_type,
                   "detail": detail, "artist_id": events[parent_id]["artist_id"], "venue": events[parent_id]["venue"],
                   "name": events[parent_id]["name"], "evidence_url": evidence_url, "first_seen_at": stamp}
    history = store.load("history")
    history.append({"at": stamp, "id": nid, "field": notice_type, "before": None, "after": detail, "evidence_url": evidence_url})
    store.save("events", events); store.save("history", history)
    return nid, "new"


# ---------- 保全：情報源の失敗・空データでは何も消さない
def ingest_batch(store, source_id, batch, source_ok, now=None):
    """情報源の一括取得の結果を反映する。失敗や空データの場合は一切変更しない。"""
    sources = store.load("sources")
    if source_id not in sources:
        raise ValueError("未登録の情報源です")
    if not source_ok:
        return {"applied": False, "reason": "情報源の取得失敗（既存データを保持）"}
    if not batch:
        return {"applied": False, "reason": "0件（既存データを保持。中止と判定しない）"}
    counts = {"new": 0, "changed": 0, "same": 0}
    for item in batch:
        _, res = upsert_performance(store, item["artist_id"], item["venue"], item["name"],
                                    event_date=item.get("event_date"), sale_windows=item.get("sale_windows", []),
                                    status=item.get("status", "公演発表"), source_id=source_id,
                                    evidence_url=item.get("evidence_url", ""), now=now, perf_key=item.get("perf_key", ""))
        counts[res] += 1
    return {"applied": True, "reason": "", **counts}


# ---------- E. 期限判定と通知候補
def deadline_items(store, now=None):
    now = now or now_jst()
    out = []
    for rec in store.load("events").values():
        if rec.get("kind") != "performance":
            continue
        for w in rec.get("sale_windows", []):
            for key, label in (("start", "受付開始"), ("end", "締切")):
                dt, prec = _parse_when(w.get(key))
                if dt is None:
                    continue
                hours = (dt - now).total_seconds() / 3600
                if key == "start" and 0 <= hours <= DEADLINE_SOON_HOURS:
                    reason = "受付開始が近い"
                elif key == "end" and 0 <= hours <= DEADLINE_SOON_HOURS:
                    reason = "締切が近い"
                elif key == "end" and hours < 0:
                    reason = "締切超過"
                else:
                    continue
                out.append({"id": rec["id"], "reason": reason, "type": w.get("type"), "at": dt.isoformat(),
                            "precision": prec, "hours_left": round(hours, 1), "name": rec["name"], "venue": rec["venue"]})
    return out


# ---------- D/E. 通知候補（ドライラン）と台帳
def notification_candidates(store, since, now=None):
    """since 以降の変更（履歴）と、期限の候補から、通知候補を作る。外部送信はしない。"""
    cands = []
    for h in store.load("history"):
        if h["at"] >= since and h["field"] != "新規":
            cands.append({"key": f"change|{h['id']}|{h['field']}|{h['at']}", "reason": f"変更: {h['field']}",
                          "id": h["id"], "detail": {"before": h["before"], "after": h["after"]}, "evidence_url": h.get("evidence_url", "")})
    for d in deadline_items(store, now):
        cands.append({"key": f"deadline|{d['id']}|{d['type']}|{d['reason']}", "reason": d["reason"], "id": d["id"],
                      "detail": {"at": d["at"], "hours_left": d["hours_left"]}, "evidence_url": ""})
    ledger = store.load("ledger")
    return [c for c in cands if c["key"] not in ledger]


IMPORTANT_FIELDS = ("event_date", "sale_windows", "status")   # 公演日・発売期限・状態。古くても自動では破棄しない
NORMAL_CAP = 20   # 通常の変更（重要でないもの）を1回の候補生成で出す上限。超過分は次回へ持ち越す


def plan_candidates(store, since, now=None, normal_cap=NORMAL_CAP):
    """停止期間の変更を、公演ごと・項目ごとに「最終状態」にまとめて候補にする。
    - 重要（公演日・発売期限・状態・お知らせ）は上限なしで全部出す（取り逃がさない）。
    - 通常の変更は normal_cap まで。超過分は overflow として返し、呼び出し側が次回の起点を戻す。
    戻り値: {"candidates": [...], "overflow": [...], "earliest_overflow_at": str|None}"""
    groups = {}
    for idx, h in enumerate(store.load("history")):   # 履歴は追記のみ。位置（idx）は変わらないため、通知キーの安定した根拠になる
        if h["at"] < since or h["field"] == "新規":
            continue
        g = groups.setdefault((h["id"], h["field"]), {"id": h["id"], "field": h["field"], "first_before": h["before"],
                                                     "after": h["after"], "first_at": h["at"], "last_at": h["at"],
                                                     "last_idx": idx, "evidence_url": h.get("evidence_url", ""), "count": 0})
        g["after"], g["last_at"], g["last_idx"], g["count"] = h["after"], h["at"], idx, g["count"] + 1
        g["evidence_url"] = h.get("evidence_url", "") or g["evidence_url"]
    ledger = store.load("ledger")
    important, normal = [], []
    for g in groups.values():
        cand = {"key": f"change|{g['id']}|{g['field']}|#{g['last_idx']}", "reason": f"変更: {g['field']}", "id": g["id"],
                "detail": {"before": g["first_before"], "after": g["after"], "count": g["count"],
                           "first_at": g["first_at"], "last_at": g["last_at"]}, "evidence_url": g["evidence_url"]}
        if cand["key"] in ledger:
            continue
        is_important = g["field"] in IMPORTANT_FIELDS or g["field"] in NOTICE_TYPES
        (important if is_important else normal).append(cand)
    for d in deadline_items(store, now):
        if d["reason"] == "締切超過":
            continue   # 期限切れは行動できない状態。停止中に溜まるため通知候補にしない（掲載の状態として残す）
        cand = {"key": f"deadline|{d['id']}|{d['type']}|{d['reason']}", "reason": d["reason"], "id": d["id"],
                "detail": {"at": d["at"], "hours_left": d["hours_left"]}, "evidence_url": ""}
        if cand["key"] not in ledger:
            important.append(cand)
    # 通常は時刻順に配る。上限で切るのは「最新」側になるため、持ち越しの起点（最も古い未配信の時刻）より前は必ず配信済みになる
    important.sort(key=lambda c: (c["detail"].get("last_at") or c["detail"].get("at") or "", c["key"]))
    normal.sort(key=lambda c: (c["detail"]["last_at"], c["key"]))
    shown, overflow = important + normal[:normal_cap], normal[normal_cap:]
    earliest = min((c["detail"]["last_at"] for c in overflow), default=None)
    return {"candidates": shown, "overflow": overflow, "earliest_overflow_at": earliest}


# ---------- 送信状態（台帳 ledger.json に保存。候補の生成と送信は別の状態）
# 生成済み：候補を作って台帳に記録しただけ（未送信）
# 送信待ち：送信を試みている途中（この状態のまま再起動したら、未送信として再試行する）
# 送信成功：送信関数が成功を返した後にだけ記録する
# 送信失敗：送信関数が失敗、または例外。試行回数を残し、上限まで再試行する
SEND_GENERATED, SEND_IN_PROGRESS, SEND_SENT, SEND_FAILED = "生成済み", "送信待ち", "送信成功", "送信失敗"
MAX_SEND_ATTEMPTS = 3


def mark_dry_run_seen(store, cands, now=None):
    """候補を「生成済み」として台帳に記録する（送信はしていない）。既に記録済みの候補は変えない。
    送信に必要な内容（payload）も保存するため、再起動後も送信できる。"""
    ledger = store.load("ledger")
    stamp = (now or now_jst()).isoformat(timespec="seconds")
    for c in cands:
        ledger.setdefault(c["key"], {"status": SEND_GENERATED, "attempts": 0, "generated_at": stamp,
                                     "payload": c, "sent_at": None, "last_error": ""})
    store.save("ledger", ledger)
    return len(cands)


LEGACY_DRY_RUN = "dry_run"   # 旧形式：送信内容（payload）が無い記録


def pending_sends(store, max_attempts=MAX_SEND_ATTEMPTS):
    """まだ送っていない候補（生成済み・送信待ち・旧形式・再試行可能な送信失敗）。キー順。"""
    ledger = store.load("ledger")
    out = []
    for key in sorted(ledger):
        rec = ledger[key]
        status = rec.get("status")
        if status in (SEND_GENERATED, SEND_IN_PROGRESS, LEGACY_DRY_RUN) or (
                status == SEND_FAILED and rec.get("attempts", 0) < max_attempts):
            out.append(key)
    return out


def needs_attention(store, max_attempts=MAX_SEND_ATTEMPTS):
    """要対応：上限まで失敗した候補、または送信内容が無い旧形式の記録。自動では送らない。"""
    ledger = store.load("ledger")
    out = []
    for key in sorted(ledger):
        rec = ledger[key]
        if (rec.get("status") == SEND_FAILED and rec.get("attempts", 0) >= max_attempts) or (
                rec.get("status") == LEGACY_DRY_RUN and "payload" not in rec):
            out.append({"key": key, "status": rec.get("status"), "attempts": rec.get("attempts", 0),
                        "last_error": rec.get("last_error", "")})
    return out


def requeue_failed(store, keys, max_attempts=MAX_SEND_ATTEMPTS):
    """要対応の候補を、明示的な操作で再試行できる状態（生成済み）に戻す。送信内容が無いものは戻せない。
    戻り値: 戻した件数。送信済みの候補は戻さない（再送を防ぐため）。"""
    ledger = store.load("ledger")
    n = 0
    for key in keys:
        rec = ledger.get(key)
        if not rec or rec.get("status") == SEND_SENT or "payload" not in rec:
            continue
        rec.update(status=SEND_GENERATED, attempts=0, last_error="", requeued=True)
        n += 1
    store.save("ledger", ledger)
    return n


def dispatch(store, sender, now=None, max_attempts=MAX_SEND_ATTEMPTS):
    """未送信の候補を1件ずつ送る。1件ごとに状態を保存するため、途中で失敗・停止しても、残りは未送信のまま残る。
    sender(payload) -> True（成功）／False・例外（失敗）。戻り値: 件数の集計。"""
    stamp_now = now or now_jst()
    counts = {"attempted": 0, "sent": 0, "failed": 0, "skipped_limit": 0}
    for key in pending_sends(store, max_attempts):
        ledger = store.load("ledger")
        rec = ledger[key]
        if rec.get("status") == SEND_FAILED and rec.get("attempts", 0) >= max_attempts:
            counts["skipped_limit"] += 1
            continue
        if "payload" not in rec:   # 旧形式：送る内容が無いので送らず、要対応として残す
            rec.update(status=SEND_FAILED, attempts=max_attempts, last_error="旧形式（送信内容なし）。要対応")
            store.save("ledger", ledger)
            counts["failed"] += 1
            continue
        rec["status"] = SEND_IN_PROGRESS
        store.save("ledger", ledger)
        counts["attempted"] += 1
        try:
            ok = sender(rec["payload"]) is True
            error = "" if ok else "送信関数が成功を返しませんでした"
        except Exception as e:   # 送信の例外は失敗として記録する（成功扱いにしない）
            ok, error = False, f"{type(e).__name__}: {e}"
        ledger = store.load("ledger")
        rec = ledger[key]
        if ok:
            rec.update(status=SEND_SENT, sent_at=stamp_now.isoformat(timespec="seconds"), last_error="")
            counts["sent"] += 1
        else:
            rec.update(status=SEND_FAILED, attempts=rec.get("attempts", 0) + 1, last_error=error)
            counts["failed"] += 1
        store.save("ledger", ledger)
    return counts
