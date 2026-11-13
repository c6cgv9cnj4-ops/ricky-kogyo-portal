"""海外アーティストの来日実績（過去の公演）を蓄積する台帳。

今後の公演の告知検出（artist_watch）とは別の用途。過去実績の少なさから来日を断定しない。
- 保存先：<dir>/tour_history.json（artist_watch の公演台帳 events.json とは別ファイル）
- 必須：artist_name, arrival_month(YYYY-MM), venue, promoter(主催・招聘元), evidence_url(https の公式URL)
- 任意：title(公演名), source_kind(情報源の種類。artist_watch と同じ一覧)
- 重複キー：アーティスト・来日年月・会場・公演名（表記ゆれは artist_watch.norm で吸収）
- 同じキーで主催・招聘元が違う場合は上書きせず conflicts に記録する（人が確認する）
- 未来の年月は拒否する（来日実績ではない）
- 利用条件が未確認の情報源は、自動取得しない。入力はこの CSV（手動）か、許可済みの公式ページからの手入力に限る
"""
import argparse
import csv
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import artist_watch as AW  # noqa: E402

FILE = "tour_history.json"
REQUIRED = ("artist_name", "arrival_month", "venue", "promoter", "evidence_url")
OPTIONAL = ("title", "source_kind")
MONTH_RE = re.compile(r"\d{4}-\d{2}")


def validate(row, today=None):
    """1行の検査。戻り値は理由のリスト（空なら有効）。"""
    today = today or AW.now_jst().date()
    errors = []
    for k in REQUIRED:
        if not str(row.get(k) or "").strip():
            errors.append(f"{k} が空")
    month = str(row.get("arrival_month") or "").strip()
    if month and not MONTH_RE.fullmatch(month):
        errors.append(f"arrival_month は YYYY-MM 形式: {month}")
    elif MONTH_RE.fullmatch(month):
        y, mo = int(month[:4]), int(month[5:7])
        if not 1 <= mo <= 12:
            errors.append(f"arrival_month の月が不正: {month}")
        elif (y, mo) > (today.year, today.month):
            errors.append(f"未来の年月は来日実績ではありません: {month}")
    url = str(row.get("evidence_url") or "").strip()
    if url and not url.startswith("https://"):
        errors.append("evidence_url は https:// で始まる公式URLが必要")
    kind = str(row.get("source_kind") or "").strip()
    if kind and kind not in AW.SOURCE_KINDS:
        errors.append(f"source_kind が不正: {kind}")
    return errors


def key_of(row):
    return "|".join([AW.norm(row["artist_name"]), str(row["arrival_month"]).strip(),
                     AW.norm(row["venue"]), AW.norm(row.get("title", ""))])


def load_store(base):
    path = os.path.join(base, FILE)
    data = AW._load(path, {"records": {}, "conflicts": []})
    return path, data


def ingest(base, rows, today=None, now=None):
    """行の配列を取り込む。戻り値: {'new', 'same', 'conflict', 'rejected'} の件数と理由。"""
    today = today or AW.now_jst().date()
    stamp = (now or AW.now_jst()).isoformat(timespec="seconds")
    path, data = load_store(base)
    recs, conflicts = data["records"], data["conflicts"]
    result = {"new": 0, "same": 0, "conflict": 0, "rejected": []}
    for n, row in enumerate(rows, start=1):
        errors = validate(row, today)
        if errors:
            result["rejected"].append({"row": n, "errors": errors})
            continue
        k = key_of(row)
        clean = {f: str(row.get(f) or "").strip() for f in REQUIRED + OPTIONAL}
        if k not in recs:
            recs[k] = dict(clean, key=k, first_seen_at=stamp, last_seen_at=stamp)
            result["new"] += 1
            continue
        old = recs[k]
        old["last_seen_at"] = stamp
        if old["promoter"] != clean["promoter"]:
            conflicts.append({"key": k, "field": "promoter", "kept": old["promoter"],
                              "incoming": clean["promoter"], "row": n, "at": stamp})
            result["conflict"] += 1
        else:
            result["same"] += 1
    AW._save(path, data)
    return result


def summarize(base):
    """件数・年別・欠損率・未解決の衝突を集計する（機械処理）。"""
    _, data = load_store(base)
    recs = list(data["records"].values())
    total = len(recs)
    years = {}
    for r in recs:
        years[r["arrival_month"][:4]] = years.get(r["arrival_month"][:4], 0) + 1
    missing = {f: sum(1 for r in recs if not str(r.get(f) or "").strip()) for f in OPTIONAL}
    return {"total": total,
            "artists": len({AW.norm(r["artist_name"]) for r in recs}),
            "by_year": dict(sorted(years.items())),
            "missing_rate": {f: (round(missing[f] / total, 3) if total else None) for f in OPTIONAL},
            "conflicts": len(data["conflicts"])}


def load_csv(path):
    """CSV（UTF-8、BOM 可）を読む。必須列が無ければ例外。"""
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"必須列がありません: {missing}")
        return list(reader)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_in = sub.add_parser("ingest", help="CSV を来日実績台帳へ取り込む")
    p_in.add_argument("--dir", required=True)
    p_in.add_argument("--csv", required=True)
    p_sum = sub.add_parser("summary", help="件数・年別・欠損率を表示")
    p_sum.add_argument("--dir", required=True)
    args = ap.parse_args()
    if args.cmd == "ingest":
        out = ingest(args.dir, load_csv(args.csv))
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(summarize(args.dir), ensure_ascii=False, indent=2))
