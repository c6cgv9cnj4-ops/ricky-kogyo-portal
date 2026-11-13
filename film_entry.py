"""人物・作品・役割・公開日・販売情報を手動で登録する入口（外部通信なし。台帳は film_watch の形式）。

- 出典URL（https://）と確認日（YYYY-MM-DD）は必須。推測で日付を補わない
- 同名の人物は、両方に異なる区別情報（生年・所属など）がある場合だけ別人として登録できる
- 登録の中身は film_watch.py の関数を使う（ここは入力の受け付けと表示だけ）
例：
  python film_entry.py --dir film_watch_data person-add --name "架空 太郎" --disambiguator "1975年生" \
      --source-url https://example.invalid/p1 --checked-on 2026-10-21
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import film_watch as FW  # noqa: E402


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=FW.DEFAULT_DIR, help="台帳フォルダ（既定 film_watch_data/）")
    ap.add_argument("--data-kind", default="実データ", choices=FW.DATA_KINDS)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("person-add", help="人物を登録する")
    p.add_argument("--name", required=True)
    p.add_argument("--disambiguator", default="", help="区別情報（生年・所属など）。同名の別人を分けるのに使う")
    p.add_argument("--alias", action="append", default=[])
    p.add_argument("--source-url", default="", help="出典URL（https://）。任意：空なら出典未確認")
    p.add_argument("--checked-on", default="", help="確認日 YYYY-MM-DD。任意：空なら確認日は未確認")

    w = sub.add_parser("work-add", help="作品を登録する")
    w.add_argument("--title", required=True)
    w.add_argument("--source-url", default="", help="出典URL（https://）。任意：空なら出典未確認")
    w.add_argument("--checked-on", default="", help="確認日 YYYY-MM-DD。任意：空なら確認日は未確認")

    c = sub.add_parser("credit-add", help="作品と人物を役割で結ぶ")
    c.add_argument("--work-id", required=True)
    c.add_argument("--person-id", required=True)
    c.add_argument("--role", required=True, choices=FW.ROLES)
    c.add_argument("--source-url", default="", help="出典URL（https://）。任意：空なら出典未確認")
    c.add_argument("--checked-on", default="", help="確認日 YYYY-MM-DD。任意：空なら確認日は未確認")

    r = sub.add_parser("release-set", help="日本公開日の状態を登録する（日付は公開日確定のときだけ）")
    r.add_argument("--work-id", required=True)
    r.add_argument("--status", required=True, choices=FW.RELEASE_STATUS)
    r.add_argument("--date", default=None, help="YYYY-MM-DD（公開日確定のときだけ）")
    r.add_argument("--source-url", default="", help="出典URL（https://）。任意：空なら出典未確認")
    r.add_argument("--checked-on", default="", help="確認日 YYYY-MM-DD。任意：空なら確認日は未確認")

    s = sub.add_parser("sale-set", help="チケット販売（先行・一般）を登録する")
    s.add_argument("--work-id", required=True)
    s.add_argument("--kind", required=True, choices=FW.SALE_KINDS)
    s.add_argument("--status", required=True, choices=FW.SALE_STATUS)
    s.add_argument("--starts-at", default=None, help="開始日（YYYY-MM-DD）か、タイムゾーン付き日時。発売前には必須")
    s.add_argument("--url", default="", help="公式販売URL（https://）")
    s.add_argument("--source-url", default="", help="出典URL（https://）。任意：空なら出典未確認")
    s.add_argument("--checked-on", default="", help="確認日 YYYY-MM-DD。任意：空なら確認日は未確認")

    g = sub.add_parser("staff-add", help="作品の関係者（候補・出典つき）を登録する。人物台帳には入れない")
    g.add_argument("--work-id", required=True)
    g.add_argument("--name", required=True)
    g.add_argument("--role", required=True, choices=FW.ROLES)
    g.add_argument("--disambiguator", default="", help="区別情報（生年・所属など）")
    g.add_argument("--source-url", default="", help="出典URL（https://）。任意：空なら出典未確認")
    g.add_argument("--checked-on", default="", help="確認日 YYYY-MM-DD。任意：空なら確認日は未確認")

    i = sub.add_parser("import", help="JSONファイルで映画・関係者・役割・公開日・販売をまとめて登録する")
    i.add_argument("--file", required=True, help="一括登録のJSON（film_batch_template.json の形式）")

    sub.add_parser("list", help="登録済みの人物・作品のIDを表示する")
    return ap


BATCH_KEYS = ("people", "works", "credits", "updates")
BATCH_FILES = ("people", "works", "history")   # 一括登録で書き換えるファイル


def _apply_batch(store, batch, data_kind):
    """一括登録の中身。参照名（ref）でIDを結ぶので、IDを手でコピーしなくてよい。
    どこかで失敗したら例外。呼び出し側は一時コピーで実行し、成功したときだけ本体へ反映する。"""
    people_ids, work_ids = {}, {}
    try:
        with store.batch():                       # 読み込み・書き込みを1回ずつにまとめる（失敗時は書かない）
            return _apply_items(store, batch, data_kind, people_ids, work_ids)
    except KeyError as e:
        raise ValueError(f"必須項目または参照名が不足しています: {e}") from e


def _apply_items(store, batch, data_kind, people_ids, work_ids):
    for p in batch.get("people", []):
        if p["ref"] in people_ids:
            raise ValueError(f"人物の参照名が重複しています: {p['ref']}")
        FW.check_optional_source(p.get("source_url", ""))
        FW.check_optional_checked_on(p.get("checked_on", ""))
        people_ids[p["ref"]] = FW.register_person(
            store, p["name"], p.get("aliases", []), p.get("data_kind", data_kind), p.get("source_url", ""),
            disambiguator=p.get("disambiguator", ""), checked_on=p.get("checked_on", ""))
    for w in batch.get("works", []):
        if w["ref"] in work_ids:
            raise ValueError(f"作品の参照名が重複しています: {w['ref']}")
        FW.check_optional_source(w.get("source_url", ""))
        FW.check_optional_checked_on(w.get("checked_on", ""))
        wid = FW.register_work(store, w["title"], w.get("data_kind", data_kind), w.get("source_url", ""))
        works = store.load("works")
        works[wid]["checked_on"] = w.get("checked_on", "")
        store.save("works", works)
        work_ids[w["ref"]] = wid
        for s in w.get("staff", []):
            FW.add_staff(store, wid, s["name"], s["role"], s.get("source_url", ""), s.get("checked_on", ""),
                         s.get("disambiguator", ""))
        if "release" in w:
            r = w["release"]
            FW.set_release(store, wid, r["status"], r.get("source_url", ""), r.get("checked_on", ""), r.get("date"))
        for s in w.get("sales", []):
            FW.add_sale(store, wid, s["kind"], s["status"], s.get("url", ""), s.get("source_url", ""),
                        s.get("checked_on", ""), s.get("starts_at"))
    for u in batch.get("updates", []):           # 登録済みの作品への更新（公開日の変更・販売情報の追加）
        wid = _work_for_update(store, u)
        if "release" in u:
            r = u["release"]
            FW.set_release(store, wid, r["status"], r.get("source_url", ""), r.get("checked_on", ""), r.get("date"))
        for s in u.get("sales", []):
            FW.add_sale(store, wid, s["kind"], s["status"], s.get("url", ""), s.get("source_url", ""),
                        s.get("checked_on", ""), s.get("starts_at"))
    for c in batch.get("credits", []):
        # 人物・作品は、同じ一括の参照名（person / work）か、登録済みのID（person_id / work_id）で指定する。
        # 登録済みのIDは存在を確認してから使う（推測で別の人物・作品に結ばない）
        pid = people_ids[c["person"]] if "person" in c else _existing_id(store, "people", c["person_id"])
        wid = work_ids[c["work"]] if "work" in c else _existing_id(store, "works", c["work_id"])
        FW.link_credit(store, wid, pid, c["role"], c.get("source_url", ""), c.get("checked_on", ""))
    return {"people": len(people_ids), "works": len(work_ids), "credits": len(batch.get("credits", []))}


def _work_for_update(store, u):
    """更新の対象の作品を、作品ID、または題名（完全一致で1件のみ）で特定する。曖昧なら拒否する。"""
    works = store.load("works")
    if "work_id" in u:
        return _existing_id(store, "works", u["work_id"])
    hits = [wid for wid, w in works.items() if w["title"] == u["title"]]
    if len(hits) != 1:
        raise ValueError(f"更新対象の作品が1件に特定できません（題名: {u['title']}、該当 {len(hits)} 件）")
    return hits[0]


def _existing_id(store, key, item_id):
    """登録済みのID（人物・作品）を確認する。存在しなければ例外（台帳は変えない）。"""
    if item_id not in store.load(key):
        raise ValueError(f"登録されていないIDです（{key}）: {item_id}")
    return item_id


def _placeholders(node, path="$"):
    """雛形の（ ）のまま残った文字列を探す。未入力のまま登録されないようにする。"""
    found = []
    if isinstance(node, dict):
        for k, v in node.items():
            found += _placeholders(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found += _placeholders(v, f"{path}[{i}]")
    elif isinstance(node, str) and node.startswith("（") and node.endswith("）"):
        found.append(path)
    return found


def import_file(folder, path, data_kind):
    """JSONを一括登録する。まず一時コピーで全件を検証し、全部成功したときだけ本体の台帳へ反映する（途中失敗では変えない）。"""
    with open(path, encoding="utf-8") as f:
        batch = json.load(f)
    if not isinstance(batch, dict) or set(batch) - set(BATCH_KEYS):
        raise ValueError(f"JSONの項目は {', '.join(BATCH_KEYS)} のみ使えます")
    placeholders = _placeholders(batch)
    if placeholders:
        raise ValueError("未入力の項目があります（雛形の（ ）のまま）: " + ", ".join(placeholders[:10]))
    tmp = tempfile.mkdtemp(prefix="film_import_")
    try:
        work = os.path.join(tmp, "film")
        if os.path.isdir(folder):
            shutil.copytree(folder, work)
        else:
            os.makedirs(work)
        summary = _apply_batch(FW.Store(work), batch, data_kind)
        os.makedirs(folder, exist_ok=True)
        for key in BATCH_FILES:
            src = FW.Store(work).path(key)
            if os.path.exists(src):
                shutil.copyfile(src, FW.Store(folder).path(key))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return summary


def run(argv, out=None):
    """登録を実行し、結果を返す。入力エラーは ValueError として呼び出し元に返す（台帳は変わらない）。"""
    out = out or sys.stdout   # 呼び出し時の標準出力を使う（定義時に固定しない）
    args = build_parser().parse_args(argv)
    store = FW.Store(args.dir)
    if args.cmd == "import":
        result = import_file(args.dir, args.file, args.data_kind)
        out.write(json.dumps(result, ensure_ascii=False) + "\n")
        return result
    if args.cmd == "person-add":
        FW.check_optional_source(args.source_url)
        FW.check_optional_checked_on(args.checked_on)
        pid = FW.register_person(store, args.name, args.alias, args.data_kind, args.source_url,
                                 disambiguator=args.disambiguator, checked_on=args.checked_on)
        result = {"person_id": pid}
    elif args.cmd == "work-add":
        FW.check_optional_source(args.source_url)
        FW.check_optional_checked_on(args.checked_on)
        wid = FW.register_work(store, args.title, args.data_kind, args.source_url)
        works = store.load("works")
        works[wid]["checked_on"] = args.checked_on
        store.save("works", works)
        result = {"work_id": wid}
    elif args.cmd == "credit-add":
        FW.link_credit(store, args.work_id, args.person_id, args.role, args.source_url, args.checked_on)
        result = {"work_id": args.work_id, "person_id": args.person_id, "role": args.role}
    elif args.cmd == "release-set":
        FW.set_release(store, args.work_id, args.status, args.source_url, args.checked_on, args.date)
        result = {"work_id": args.work_id, "release_status": args.status}
    elif args.cmd == "sale-set":
        FW.add_sale(store, args.work_id, args.kind, args.status, args.url, args.source_url,
                    args.checked_on, args.starts_at)
        result = {"work_id": args.work_id, "sale": args.kind, "status": args.status}
    elif args.cmd == "staff-add":
        FW.add_staff(store, args.work_id, args.name, args.role, args.source_url, args.checked_on, args.disambiguator)
        result = {"work_id": args.work_id, "staff": args.name, "role": args.role}
    else:
        result = {"people": {k: v["name"] for k, v in store.load("people").items()},
                  "works": {k: v["title"] for k, v in store.load("works").items()}}
    out.write(json.dumps(result, ensure_ascii=False) + "\n")
    return result


def main(argv=None):
    try:
        run(sys.argv[1:] if argv is None else argv)
    except (ValueError, FW.CorruptState) as e:
        sys.stderr.write(f"登録しません: {e}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
