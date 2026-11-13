"""人物と作品の関係を読み取り専用で問い合わせる（film_watch の台帳を読むだけ。書き込まない）。

- works_of_person：登録された人物が、台帳で役割を持つ作品（出典つき）
- shared_works：2人が同じ作品にクレジットされている作品（共同制作。出典で確認された事実）
- discover：人物の共同制作者の作品のうち、本人がクレジットされていないもの（推測。根拠の経路を説明する）
- screenings_for_person：人物の作品と、同じ作品名の上映予定（screenings.py の台帳）
- summary：件数・データ種別・出典の欠損を集計する
架空データ（data_kind が「テスト用架空データ」）は、summary で実データと分けて数える。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import film_watch as FW  # noqa: E402
import screenings as S  # noqa: E402
import artist_watch as AW  # noqa: E402


def works_of_person(store, person_id):
    works = store.load("works")
    out = []
    for wid, w in works.items():
        for c in w.get("credits", []):
            if c["person_id"] == person_id:
                out.append({"work_id": wid, "title": w["title"], "role": c["role"],
                            "release_year": w.get("release_year", ""),
                            "source_url": w.get("source_url", ""), "data_kind": w.get("data_kind", "")})
    return sorted(out, key=lambda r: (r["title"], r["role"]))


def shared_works(store, person_a, person_b):
    works = store.load("works")
    out = []
    for wid, w in works.items():
        roles_a = [c["role"] for c in w.get("credits", []) if c["person_id"] == person_a]
        roles_b = [c["role"] for c in w.get("credits", []) if c["person_id"] == person_b]
        if roles_a and roles_b:
            out.append({"work_id": wid, "title": w["title"], "roles_a": roles_a, "roles_b": roles_b,
                        "source_url": w.get("source_url", ""), "basis": "出典で確認されたクレジット（事実）"})
    return sorted(out, key=lambda r: r["title"])


def discover(store, person_id):
    """共同制作者の作品のうち、本人がクレジットされていないもの。根拠は経路つきで「推測」として示す。"""
    own = {w["work_id"] for w in works_of_person(store, person_id)}
    works = store.load("works")
    people = store.load("people")
    collaborators = set()
    for w in works.values():
        ids = {c["person_id"] for c in w.get("credits", [])}
        if person_id in ids:
            collaborators |= ids - {person_id}
    out = []
    for wid, w in works.items():
        if wid in own:
            continue
        via = sorted({c["person_id"] for c in w.get("credits", []) if c["person_id"] in collaborators})
        if via:
            out.append({"work_id": wid, "title": w["title"], "source_url": w.get("source_url", ""),
                        "basis": "推測（関連人物の作品）",
                        "path": [people.get(p, {}).get("name", p) for p in via]})
    return sorted(out, key=lambda r: r["title"])


def screenings_for_person(store, person_id, screenings_base):
    """人物の作品と上映予定を照合する（作品名は artist_watch.norm で照合）。上映の日付順。
    - 作品名と公開年が両方わかり、一致する → 「作品名＋公開年一致」
    - 両方わかり、公開年が違う → 別作品とみなして除外（同名の旧作・リメイクを混同しない）
    - どちらかの公開年が空 → 「作品名のみ（年未確認）」として残す（推測で除外しない）"""
    own = works_of_person(store, person_id)
    _, data = S.load_store(screenings_base)
    hits = []
    for r in data["screenings"].values():
        ws = [w for w in own if AW.norm(w["title"]) == AW.norm(r["title"])]
        if not ws:
            continue
        year = str(r.get("release_year") or "").strip()
        work_years = {w.get("release_year", "") for w in ws} - {""}
        if year and work_years:
            if year not in work_years:
                continue
            match = "作品名＋公開年一致"
        else:
            match = "作品名のみ（公開年未確認）"
        hits.append(dict(r, match=match))
    return sorted(hits, key=lambda r: (r["screen_date"], r["start_time"]))


def summary(store):
    people = store.load("people")
    works = store.load("works")
    kinds = {}
    for rec in list(people.values()) + list(works.values()):
        kinds[rec.get("data_kind", "")] = kinds.get(rec.get("data_kind", ""), 0) + 1
    credits = sum(len(w.get("credits", [])) for w in works.values())
    return {"people": len(people), "works": len(works), "credits": credits,
            "data_kind_counts": kinds,
            "works_without_source": sum(1 for w in works.values() if not w.get("source_url"))}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=FW.DEFAULT_DIR)
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()
    store = FW.Store(args.dir)
    print(json.dumps(summary(store), ensure_ascii=False, indent=2))
