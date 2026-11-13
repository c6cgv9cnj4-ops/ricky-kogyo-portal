"""映画・芸術の機会損失防止：人物・作品・出来事の記録と差分（ローカル実装。通知の実送信はしない）。

再利用：movie_watch.normalize / match_people / movie_key（既存の正規化と照合）。
新規：人物・作品の登録と役割、出来事（企画発表・制作参加・公開決定・公開日変更・上映館・上映終了・チケット）の記録、
      同一出来事の重複抑止、変更前後の履歴、破損JSONの拒否、通知候補と台帳。

データは利用者が指定したフォルダに置く（既定 film_watch_data/）。架空データと実データは data_kind で区別する。
"""
import contextlib
import datetime
import json
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import movie_watch as MW  # noqa: E402  既存の正規化・照合を再利用

JST = datetime.timezone(datetime.timedelta(hours=9))
DEFAULT_DIR = "film_watch_data"
ROLES = ("監督", "脚本", "出演", "原作", "プロデュース")
EVENT_TYPES = ("企画発表", "制作参加", "公開決定", "公開日変更", "公開中止", "上映館決定", "上映終了", "チケット発売", "チケット締切")
DATA_KINDS = ("実データ", "テスト用架空データ", "手動確認フィクスチャ")
RELEASE_STATUS = ("公開日確定", "未発表", "未確認")          # 日本公開日の状態。日付は「公開日確定」のときだけ入る
SALE_KINDS = ("先行", "一般")                                # チケット販売の区分
SALE_STATUS = ("発売前", "発売中", "販売終了", "未確認")       # 販売の状態。日付は「発売前」のときに必須
SALE_NEAR_DAYS = 14                                          # 「発売開始が近い」とみなす日数（人物ページ・締切表示で共通）


class CorruptState(Exception):
    """状態ファイルが壊れている（読み込みを拒否する）。"""


class Store:
    def __init__(self, base=DEFAULT_DIR):
        self.base = base
        self.names = {"people": "people.json", "works": "works.json", "events": "events.json",
                      "history": "history.json", "ledger": "ledger.json"}
        self._batch_depth = 0     # 一括処理の入れ子の深さ（0 なら通常どおり即時に読み書きする）
        self._batch_cache = {}    # 一括処理の中で読んだ・書いた内容（キー → データ）
        self._batch_dirty = set()  # 一括処理の中で変わったキー（終わりにまとめて書く）

    @contextlib.contextmanager
    def batch(self):
        """一連の登録を1回の読み込み・1回の書き込みにまとめる。途中で例外が出たら何も書かず、台帳は変わらない。
        （1件ごとに全件を書き直す遅さを避けるため。読み書きの回数だけが変わり、結果は同じになる。）"""
        self._batch_depth += 1
        try:
            yield self
        except BaseException:
            self._batch_depth -= 1
            if self._batch_depth == 0:
                self._batch_cache.clear()
                self._batch_dirty.clear()
            raise
        self._batch_depth -= 1
        if self._batch_depth == 0:
            dirty, cache = sorted(self._batch_dirty), self._batch_cache
            self._batch_cache, self._batch_dirty = {}, set()
            for key in dirty:
                self._write(key, cache[key])

    def path(self, key):
        return os.path.join(self.base, self.names[key])

    def load(self, key):
        if self._batch_depth and key in self._batch_cache:
            return self._batch_cache[key]
        default = [] if key == "history" else {}
        p = self.path(key)
        if not os.path.exists(p):
            data = default
        else:
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
            except ValueError as e:
                raise CorruptState(f"{p} が壊れています（読み込みを拒否）: {e}")
            if key == "history" and not isinstance(data, list):
                raise CorruptState(f"{p} の形式が不正です")
            if key != "history" and not isinstance(data, dict):
                raise CorruptState(f"{p} の形式が不正です")
        if self._batch_depth:
            self._batch_cache[key] = data     # 一括処理の中では、同じ内容を何度も読まない
        return data

    def save(self, key, data):
        if self._batch_depth:
            self._batch_cache[key] = data     # 書き込みは一括処理の終わりにまとめて行う
            self._batch_dirty.add(key)
            return
        self._write(key, data)

    def _write(self, key, data):
        os.makedirs(self.base, exist_ok=True)
        p = self.path(key)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True, default=str)
        os.replace(tmp, p)


def now_iso(now=None):
    return (now or datetime.datetime.now(JST)).isoformat(timespec="seconds")


def parse_when(value):
    """日付（YYYY-MM-DD）か、タイムゾーン付きの日時を受け取る。戻り値: (値, 精度)。タイムゾーンのない日時は拒否。"""
    if value in (None, ""):
        return None, "unknown"
    s = str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        datetime.date.fromisoformat(s)
        return s, "day"
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError(f"タイムゾーンのない日時は登録できません: {s}")
    return dt.isoformat(), "datetime"


def check_source(url):
    """出典URLは https:// で始まる必要がある（空・http・空白入りは拒否）。"""
    if not isinstance(url, str) or not url.startswith("https://") or any(c.isspace() for c in url):
        raise ValueError(f"出典URLは https:// で始まり、空白を含まない必要があります: {url!r}")


def check_checked_on(value):
    """情報の確認日は YYYY-MM-DD の実在する日付だけ受け付ける。"""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"確認日は YYYY-MM-DD で入力してください: {value!r}")
    datetime.date.fromisoformat(value)


def check_optional_source(url):
    """出典URLは、入力されたときだけ検査する（空＝出典未確認として登録できる）。"""
    if url:
        check_source(url)


def check_optional_checked_on(value):
    """確認日は、入力されたときだけ検査する（空＝確認日は未確認）。"""
    if value:
        check_checked_on(value)


def register_person(store, name, aliases=(), data_kind="実データ", source_url="", now=None,
                    disambiguator="", checked_on=""):
    """人物を登録する。同名（表記揺れを含む）の既存人物があれば拒否する。
    例外：両方に区別情報（生年・所属など）があり、その値が違う場合だけ、別人として登録する。"""
    if data_kind not in DATA_KINDS:
        raise ValueError("data_kind が不正です")
    people = store.load("people")
    keys = {MW.normalize(name)} | {MW.normalize(a) for a in aliases}
    for pid, p in people.items():
        if ({MW.normalize(p["name"])} | {MW.normalize(a) for a in p["aliases"]}) & keys:
            if disambiguator and p.get("disambiguator") and disambiguator != p["disambiguator"]:
                continue   # 同姓同名の別人（区別情報が違う）
            raise ValueError(f"同一人物の可能性があるため登録しません（既存: {pid}）。"
                             "同名の別人なら、両方に異なる区別情報（生年・所属など）を入力してください")
    pid = "p_" + uuid.uuid4().hex[:8]
    people[pid] = {"id": pid, "name": name, "aliases": list(aliases), "data_kind": data_kind,
                   "source_url": source_url, "registered_at": now_iso(now),
                   "disambiguator": disambiguator, "checked_on": checked_on}
    store.save("people", people)
    return pid


def register_work(store, title, data_kind="実データ", source_url="", now=None):
    works = store.load("works")
    wid = MW.movie_key("work", None, title)
    if wid in works:
        raise ValueError("同一作品が既にあります")
    works[wid] = {"id": wid, "title": title, "data_kind": data_kind, "source_url": source_url,
                  "credits": [], "registered_at": now_iso(now)}
    store.save("works", works)
    return wid


def link_credit(store, work_id, person_id, role, source_url="", checked_on=""):
    """作品と人物を役割で結ぶ。出典と確認日を渡した場合は、クレジットに保存する（渡さなければ従来どおり）。"""
    if role not in ROLES:
        raise ValueError(f"役割が不正です: {role}")
    works, people = store.load("works"), store.load("people")
    if work_id not in works or person_id not in people:
        raise ValueError("作品または人物が未登録です")
    cred = {"person_id": person_id, "role": role}
    if source_url:
        check_source(source_url)
        check_checked_on(checked_on)
        cred.update({"source_url": source_url, "checked_on": checked_on})
    existing = [c for c in works[work_id]["credits"] if c["person_id"] == person_id and c["role"] == role]
    if not existing:
        works[work_id]["credits"].append(cred)
    store.save("works", works)
    return cred


def add_staff(store, work_id, name, role, source_url, checked_on, disambiguator=""):
    """作品の関係者（映画の公式情報に載る人物）を、出典つきで登録する。同じ人物・役割は1件にする。
    関係者はまだ人物台帳に入っていない候補。追跡対象にするのは、後で人物として登録したとき。"""
    if role not in ROLES:
        raise ValueError(f"役割が不正です: {role}")
    if not name or not name.strip():
        raise ValueError("人物名が空です")
    check_optional_source(source_url)
    check_optional_checked_on(checked_on)
    works = store.load("works")
    if work_id not in works:
        raise ValueError("作品が未登録です")
    staff = works[work_id].setdefault("staff", [])
    rec = {"name": name.strip(), "role": role, "disambiguator": disambiguator, "source_url": source_url,
           "checked_on": checked_on}
    for s in staff:
        if (s["name"], s["role"], s["disambiguator"]) == (rec["name"], role, disambiguator):
            raise ValueError("同じ関係者（人物・役割・区別情報）が既に登録されています")
    staff.append(rec)
    store.save("works", works)
    return rec


def set_release(store, work_id, status, source_url, checked_on, value=None):
    """日本公開日の状態を登録する。日付は「公開日確定」のときだけ受け付け、他の状態では推測しない。"""
    if status not in RELEASE_STATUS:
        raise ValueError(f"公開の状態が不正です: {status}")
    check_optional_source(source_url)
    check_optional_checked_on(checked_on)
    works = store.load("works")
    if work_id not in works:
        raise ValueError("作品が未登録です")
    if status == "公開日確定":
        v, prec = parse_when(value)
        if v is None:
            raise ValueError("公開日確定には日付が必要です")
    else:
        if value:
            raise ValueError("日付は「公開日確定」のときだけ登録できます（未発表・未確認では日付を入れない）")
        v, prec = None, "unknown"
    prev = works[work_id].get("release") or {}
    works[work_id]["release"] = {"date": v, "precision": prec, "status": status,
                                 "source_url": source_url, "checked_on": checked_on}
    store.save("works", works)
    if status == "公開日確定" and v != prev.get("date"):
        _append_release_history(store, work_id, prev.get("date") if prev.get("status") == "公開日確定" else None,
                                v, source_url, checked_on)
    return works[work_id]["release"]


def _append_release_history(store, work_id, before, after, source_url, checked_on):
    """公開日の変更を履歴に残す（過去の日付・新しい日付・確認日・出典）。同じ変更は重複させない。
    before が None のときは「それ以前の公開日は台帳に無い（不明）」を意味する。推測で埋めない。"""
    history = store.load("history")
    same = [h for h in history if h.get("field") == "公開日" and h["work_id"] == work_id
            and h["before"] == before and h["after"] == after]
    if same:
        return False
    history.append({"at": checked_on, "work_id": work_id, "field": "公開日", "before": before, "after": after,
                    "source_url": source_url})
    store.save("history", history)
    return True


def release_history(store, work_id):
    """作品の公開日の変更履歴（古い順）。"""
    return [h for h in store.load("history") if h.get("field") == "公開日" and h["work_id"] == work_id]


def add_sale(store, work_id, kind, status, url, source_url, checked_on, starts_at=None):
    """チケット販売（先行／一般）を登録する。同じ区分・同じ販売URLは1件に更新する（重複させない）。
    開始日時は「発売前」のときに必須。販売中・販売終了・未確認では、日時がなくてもよい。"""
    if kind not in SALE_KINDS:
        raise ValueError(f"販売の区分が不正です: {kind}")
    if status not in SALE_STATUS:
        raise ValueError(f"販売の状態が不正です: {status}")
    check_optional_source(url)
    check_optional_source(source_url)
    check_optional_checked_on(checked_on)
    v, prec = parse_when(starts_at)
    if status == "発売前" and v is None:
        raise ValueError("発売前には開始日時が必要です（不明なら状態を「未確認」にする）")
    works = store.load("works")
    if work_id not in works:
        raise ValueError("作品が未登録です")
    rec = {"kind": kind, "status": status, "starts_at": v, "precision": prec, "url": url,
           "source_url": source_url, "checked_on": checked_on}
    sales = works[work_id].setdefault("sales", [])
    for i, s in enumerate(sales):
        if s["kind"] == kind and s["url"] == url:
            sales[i] = rec
            break
    else:
        sales.append(rec)
    store.save("works", works)
    return rec


def record_event(store, work_id, event_type, value, source_url, checked_at, data_kind="実データ", value_precision=None):
    """出来事を記録する。同じ出来事は重複させない。公開日変更は、同じ作品の既存の公開日を旧値として履歴に残す。"""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"出来事の種類が不正です: {event_type}")
    if data_kind not in DATA_KINDS:
        raise ValueError("data_kind が不正です")
    works = store.load("works")
    if work_id not in works:
        raise ValueError("作品が未登録です")
    events, history = store.load("events"), store.load("history")
    if event_type in ("公開決定", "公開日変更", "チケット発売", "チケット締切", "上映終了"):
        v, prec = parse_when(value)
    else:
        v, prec = (value if value else None), "text"
    eid = f"{work_id}|{event_type}|{v}"
    if eid in events:
        events[eid]["checked_at"] = checked_at
        store.save("events", events)
        return eid, "duplicate"
    # 公開日の変更は、同じ作品の既存の公開決定を旧値として残す（新規作品として扱わない）
    prev_release = [e for e in events.values() if e["work_id"] == work_id and e["type"] in ("公開決定", "公開日変更")]
    rec = {"id": eid, "work_id": work_id, "type": event_type, "value": v, "precision": prec,
           "source_url": source_url, "checked_at": checked_at, "data_kind": data_kind}
    events[eid] = rec
    if event_type == "公開日変更" and prev_release:
        last = sorted(prev_release, key=lambda e: e["checked_at"])[-1]
        history.append({"at": checked_at, "work_id": work_id, "field": "公開日", "before": last["value"],
                        "after": v, "source_url": source_url})
    elif event_type == "公開決定" and not prev_release:
        history.append({"at": checked_at, "work_id": work_id, "field": "公開日", "before": None, "after": v,
                        "source_url": source_url})
    store.save("events", events)
    store.save("history", history)
    return eid, "new"


def deadline_items(store, today):
    """チケット締切・チケット発売の日付で、今日から SALE_NEAR_DAYS 日以内のもの（日付のみは日本時間の終日扱い）。
    イベント台帳を見る（先行／一般の区別と販売URLは持たない）。人物ページの発売予定は sales を使う。"""
    out = []
    for e in store.load("events").values():
        if e["type"] in ("チケット締切", "チケット発売") and e["precision"] in ("day", "datetime"):
            d = datetime.date.fromisoformat(str(e["value"])[:10])
            left = (d - today).days
            if 0 <= left <= SALE_NEAR_DAYS:
                out.append({"id": e["id"], "type": e["type"], "date": d.isoformat(), "days_left": left, "work_id": e["work_id"]})
    return sorted(out, key=lambda x: (x["days_left"], x["id"]))


def notification_candidates(store, since):
    """通知候補（送信しない）。台帳にないものだけ。"""
    ledger = store.load("ledger")
    cands = []
    for h in store.load("history"):
        if h["at"] >= since:
            key = f"history|{h['work_id']}|{h['field']}|{h['before']}|{h['after']}|{h['at']}"
            if key not in ledger:
                cands.append({"key": key, "reason": f"{h['field']}の変更", "work_id": h["work_id"], "before": h["before"], "after": h["after"], "source_url": h["source_url"]})
    return cands


def mark_candidates_seen(store, cands):
    ledger = store.load("ledger")
    for c in cands:
        ledger.setdefault(c["key"], {"status": "dry_run_seen"})
    store.save("ledger", ledger)
    return len(cands)


def safe_ingest(store, source_ok, items, checked_at, data_kind, source_url):
    """取得結果を反映する。取得失敗・空結果では何も変えない（既存情報を消さない）。"""
    if not source_ok:
        return {"applied": False, "reason": "取得失敗（既存データを保持）"}
    if not items:
        return {"applied": False, "reason": "0件（既存データを保持。終了と判定しない）"}
    counts = {"new": 0, "duplicate": 0}
    for it in items:
        wid = MW.movie_key("work", None, it["title"])
        if wid not in store.load("works"):
            register_work(store, it["title"], data_kind, source_url)
        _, res = record_event(store, wid, it["type"], it["value"], source_url, checked_at, data_kind)
        counts[res] += 1
    return {"applied": True, "reason": "", **counts}


def extract_release_phrase(text):
    """公式ページの文から「YYYY年M月D日…公開」の日付を取り出す。見つからなければ None（推測しない）。"""
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日[^。\n]{0,12}?公開", text or "")
    if not m:
        return None
    return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
