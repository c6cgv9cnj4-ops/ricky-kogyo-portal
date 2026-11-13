"""監視対象の一覧を自動生成する（通信なし）。登録済みの人物・作品から、取得対象・情報源候補・確認が必要な項目を作る。

- 情報源候補：作品・公開日・関係者の出典URLから、ホストを取り出す。
- 情報源の状態：source_registry.py の利用条件（許可確認・制限あり・判断不能・未確認・台帳に無い）をそのまま表示する。
  許可が確認されていないものは「許可待ち」。推定で許可に変えない。
- 要確認：出典が無い・確認日が無い・同名の人物がいて区別できない、などを一覧にする。

使い方：
  python3 watch_targets.py --dir film_watch_data --out 監視対象.json
"""
import argparse
import json
import os
import sys
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import film_watch as FW  # noqa: E402
import movie_watch as MW  # noqa: E402
import source_registry as SR  # noqa: E402


def _host_status(url):
    """URLのホストを台帳と照合する。台帳に無いホストは「台帳に無い（許可待ち）」。推定で許可にしない。"""
    host = urlsplit(url).netloc if url else ""
    if not host:
        return host, "出典なし"
    for r in SR.rows():
        if r["host"] == host:
            if r["auto_allowed"]:
                return host, "許可確認（自動取得可）"
            return host, f"{r['terms_status']}（自動取得しない・許可待ち）"
    return host, "台帳に無い（許可待ち）"


def build(film_dir):
    store = FW.Store(film_dir)
    works, people = store.load("works"), store.load("people")
    review = []
    targets = []
    for wid, w in sorted(works.items(), key=lambda kv: kv[1]["title"]):
        sources = {}
        for label, url in [("作品", w.get("source_url", "")),
                           ("公開日", (w.get("release") or {}).get("source_url", ""))] + \
                          [("販売", s.get("source_url", "")) for s in w.get("sales", [])] + \
                          [("関係者", s.get("source_url", "")) for s in w.get("staff", [])]:
            if url:
                host, st = _host_status(url)
                sources.setdefault(host, {"status": st, "used_for": []})["used_for"].append(label)
        rel = w.get("release") or {}
        staff = [{"名前": people[c["person_id"]]["name"], "区別情報": people[c["person_id"]].get("disambiguator", ""),
                  "役割": c["role"]} for c in w.get("credits", []) if c["person_id"] in people]
        targets.append({"作品": w["title"], "作品ID": wid, "関係者": staff, "情報源候補": sources,
                        "公開状態": rel.get("status", "未確認")})
        if not w.get("source_url"):
            review.append(f"{w['title']}：作品の出典が無い")
        if rel.get("status") == "公開日確定" and not (rel.get("source_url") and rel.get("checked_on")):
            review.append(f"{w['title']}：公開日の出典または確認日が無い（確定扱いしない）")
        if not rel:
            review.append(f"{w['title']}：公開状態が未入力")
    for pid, p in people.items():
        same = [q for qid, q in people.items() if qid != pid and MW.normalize(q["name"]) == MW.normalize(p["name"])]
        if same and not p.get("disambiguator"):
            review.append(f"{p['name']}：同名の人物がいるが区別情報が無い（結びつけは保留）")
        if not p.get("disambiguator"):
            review.append(f"{p['name']}：区別情報（生年など）が未入力")
    return {"監視対象": len(targets), "人物": len(people), "作品": targets, "要確認": review}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=FW.DEFAULT_DIR)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    data = build(a.dir)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"監視対象：作品 {data['監視対象']} 件、人物 {data['人物']} 件、要確認 {len(data['要確認'])} 件 → {a.out}")
