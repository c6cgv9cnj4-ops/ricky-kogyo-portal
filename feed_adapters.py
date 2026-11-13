"""情報源の取り込み（アダプタ）。取得方法ごとに「読み込み」を分け、共通の行の形に直して台帳へつなぐ。

- 共通の行（1行 = 作品1件・人物1人・役割・公開状態・販売1件まで。すべて任意項目は空欄可）:
    作品名, 監督・人物名, 人物の区別情報, 役割, 公開状態, 公開日, 公開日の出典URL, 確認日,
    販売区分, 販売状態, 販売開始日時, 販売URL, 販売の出典URL
- 取り込み方式（method）:
    "file"  : 手元の提供データ（CSV・JSON）を読む。外部通信はしない。現在実装済み。
    "fetch" : 外部から取得する方式。利用条件が「自動取得の許可」と確認された情報源（source_registry の許可確認）
              のみ動く。現在は許可確認の情報源が0件のため、どれも動かない（ガードで拒否）。
- 新しい方式は ADAPTERS に1行で登録できる（parse 関数が共通の行を返す）。
- 台帳との突き合わせ：作品は題名、人物は正規化した名前と区別情報で照合する。同名で区別できない場合は、
  自動で登録せず「要確認」として報告する（名前だけで同一人物と判断しない）。
"""
import csv
import datetime
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import film_watch as FW  # noqa: E402
import movie_watch as MW  # noqa: E402
import source_registry as SR  # noqa: E402

COLUMNS = ("作品名", "監督・人物名", "人物の区別情報", "役割", "公開状態", "公開日", "公開日の出典URL", "確認日",
           "販売区分", "販売状態", "販売開始日時", "販売URL", "販売の出典URL")
JST = datetime.timezone(datetime.timedelta(hours=9))


def _read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in ("作品名",) if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"CSVに必須の列がありません: {missing}")
        unknown = [c for c in (reader.fieldnames or []) if c not in COLUMNS]
        if unknown:
            raise ValueError(f"CSVに未知の列があります（取り込まない）: {unknown}")
        return [{k: (v or "").strip() for k, v in row.items()} for row in reader if any((v or "").strip() for v in row.values())]


def parse_file_csv(path):
    """提供データ（CSV）→ 共通の行。"""
    return _read_csv(path)


def parse_file_json(path):
    """提供データ（JSON）→ 共通の行。形式は {"rows": [ {列名: 値, ...}, ... ]}。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        raise ValueError('JSONは {"rows": [...]} の形式にしてください')
    rows = []
    for r in data["rows"]:
        bad = [k for k in r if k not in COLUMNS]
        if bad:
            raise ValueError(f"JSONに未知の項目があります（取り込まない）: {bad}")
        rows.append({k: str(r.get(k, "") or "").strip() for k in COLUMNS})
    return rows


# 取り込み方式の登録。method が "fetch" の方式は、情報源が許可確認されるまで動かない。
ADAPTERS = {
    "csv_file": {"method": "file", "parse": parse_file_csv, "description": "提供データ（CSV）"},
    "json_file": {"method": "file", "parse": parse_file_json, "description": "提供データ（JSON）"},
}


# 外部取得方式の登録。利用条件（source_registry）が「許可確認」で、根拠URLと確認日がある情報源だけ動く。
# fetch(url) -> 文字列（外部通信はここに閉じ込める）。parse_text(文字列) -> 共通の行。
# 現在は登録が0件。許可確認された情報源が出た時だけ、ここに1件ずつ追加する。
FETCH_ADAPTERS = {}


def _terms_approved(host):
    """利用条件が自動取得の許可と確認済みか（許可確認・根拠URL・確認日の3つが揃っている）。"""
    for r in SR.rows():
        if r["host"] == host:
            return bool(r["auto_allowed"] and r["evidence_url"] and r["checked_on"])
    return False


def run_adapter(name, path=None):
    """取り込み方式を実行し、共通の行を返す。許可のない取得方式は、通信の前に拒否する。"""
    if name in FETCH_ADAPTERS:
        spec = FETCH_ADAPTERS[name]
        if not _terms_approved(spec["host"]):
            raise PermissionError(f"{name}：利用条件が自動取得の許可と確認されていないため取得しません（{spec['host']}）")
        return spec["parse_text"](spec["fetch"]())
    if name not in ADAPTERS:
        raise ValueError(f"未登録の取り込み方式です: {name}（登録済み: {', '.join(list(ADAPTERS) + list(FETCH_ADAPTERS))}）")
    if not path or not os.path.exists(path):
        raise ValueError(f"入力ファイルがありません: {path}")
    return ADAPTERS[name]["parse"](path)


def last_run(film_dir, source):
    """取り込みの履歴から、その取り込み方式の最後の記録を返す（無ければ None）。"""
    path = os.path.join(film_dir, "feed_runs.jsonl")
    if not os.path.exists(path):
        return None
    last = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                if rec.get("source") == source:
                    last = rec
    return last


def _norm(s):
    return MW.normalize(s or "")


def to_batch(rows, film_dir, data_kind="実データ"):
    """共通の行 → 一括入力（film_entry の形式）。台帳と突き合わせ、既存の作品は更新、既存の人物は再利用する。
    戻り値：(batch, 報告) 。報告には、要確認（同名で区別できない）・行の欠落を入れる。"""
    store = FW.Store(film_dir)
    works, people = store.load("works"), store.load("people")
    title_to_wid = {w["title"]: wid for wid, w in works.items()}
    report = {"new_works": [], "updated_works": [], "needs_check": [], "skipped_rows": []}
    new_works, updates, credits, new_people = {}, {}, [], {}
    for i, r in enumerate(rows, start=2):
        title = r.get("作品名", "")
        if not title:
            report["skipped_rows"].append(f"{i}行目：作品名が空")
            continue
        rel = {}
        if r.get("公開状態"):
            rel = {"status": r["公開状態"], "date": r.get("公開日") or None,
                   "source_url": r.get("公開日の出典URL", ""), "checked_on": r.get("確認日", "")}
            if rel["date"] is None:
                rel.pop("date")
        sale = {}
        if r.get("販売区分"):
            sale = {"kind": r["販売区分"], "status": r.get("販売状態") or "未確認",
                    "starts_at": r.get("販売開始日時") or None, "url": r.get("販売URL", ""),
                    "source_url": r.get("販売の出典URL", ""), "checked_on": r.get("確認日", "")}
            if sale["starts_at"] is None:
                sale.pop("starts_at")
        if title in title_to_wid:
            wid = title_to_wid[title]
            u = updates.setdefault(title, {"title": title, "sales": []})
            if rel:
                u["release"] = rel
            if sale:
                u["sales"].append(sale)
            if title not in report["updated_works"]:
                report["updated_works"].append(title)
        else:
            w = new_works.setdefault(title, {"ref": f"w{len(new_works)}", "title": title, "sales": []})
            if rel:
                w["release"] = rel
            if sale:
                w["sales"].append(sale)
            if title not in report["new_works"]:
                report["new_works"].append(title)
            wid = None
        name = r.get("監督・人物名", "")
        role = r.get("役割", "")
        if not name:
            continue
        if not role:
            report["skipped_rows"].append(f"{i}行目：役割が空（人物 {name} は結ばない）")
            continue
        dis = r.get("人物の区別情報", "")
        same = [pid for pid, p in people.items() if _norm(p["name"]) == _norm(name)]
        exact = [pid for pid in same if dis and people[pid].get("disambiguator") == dis]
        if exact:
            person = {"person_id": exact[0]}
        elif same and not all(dis and people[pid].get("disambiguator") and people[pid]["disambiguator"] != dis
                              for pid in same):
            report["needs_check"].append(f"{i}行目：{name}（同名の人物あり。区別情報で確認するまで結ばない）")
            continue
        else:
            ref = new_people.setdefault((name, dis), f"p{len(new_people)}")
            person = {"person": ref}
        credit = {"role": role, "source_url": r.get("公開日の出典URL", ""), "checked_on": r.get("確認日", "")}
        credit.update(person)
        if wid is not None:
            credit["work_id"] = wid
        else:
            credit["work"] = new_works[title]["ref"]
        credits.append(credit)
    batch = {}
    if new_people:
        batch["people"] = [{"ref": ref, "name": name, "disambiguator": dis}
                           for (name, dis), ref in new_people.items()]
    if new_works:
        batch["works"] = list(new_works.values())
        for w in batch["works"]:
            if not w["sales"]:
                w.pop("sales")
    if updates:
        batch["updates"] = [u for u in updates.values() if u.get("release") or u.get("sales")]
        for u in batch["updates"]:
            if not u.get("sales"):
                u.pop("sales", None)
    if credits:
        batch["credits"] = [{k: v for k, v in c.items() if v != ""} for c in credits]
    return batch, report


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def log_run(film_dir, source, input_path, rows, report, status):
    """取り込みの記録（取込日時・入力の指紋・件数）を、台帳フォルダに1行追記する。台帳そのものは変えない。"""
    os.makedirs(film_dir, exist_ok=True)
    has_file = bool(input_path) and os.path.exists(input_path)
    rec = {"imported_at": datetime.datetime.now(JST).isoformat(timespec="seconds"), "source": source,
           "input": os.path.basename(input_path) if has_file else "（外部取得）",
           "input_sha256": sha256_file(input_path) if has_file else "", "rows": len(rows),
           "new_works": len(report["new_works"]), "updated_works": len(report["updated_works"]),
           "needs_check": len(report["needs_check"]), "skipped_rows": len(report["skipped_rows"]), "status": status}
    with open(os.path.join(film_dir, "feed_runs.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def export_template(film_dir, out_path):
    """監視対象（登録済みの人物と作品の組み合わせ）から、提供データの雛形CSVを作る。
    人物・作品・役割・区別情報・出典は台帳からそのまま入れ、公開状態・公開日・販売の欄だけ空欄にする。
    空欄の行は、取り込んでも台帳を変えない（変更が無ければ差分は「変更なし」）。"""
    store = FW.Store(film_dir)
    works, people = store.load("works"), store.load("people")
    rows = []
    for w in sorted(works.values(), key=lambda x: x["title"]):
        for c in w.get("credits", []):
            p = people.get(c["person_id"])
            if p is None:
                continue
            rows.append({"作品名": w["title"], "監督・人物名": p["name"], "人物の区別情報": p.get("disambiguator", ""),
                         "役割": c["role"], "公開状態": "", "公開日": "", "公開日の出典URL": "", "確認日": "",
                         "販売区分": "", "販売状態": "", "販売開始日時": "", "販売URL": "", "販売の出典URL": ""})
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    return {"rows": len(rows), "out": out_path}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="提供データの雛形CSVを、登録済みの監視対象から作る")
    ap.add_argument("--dir", default=FW.DEFAULT_DIR)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    print(export_template(a.dir, a.out))
