"""映画の上映予定（作品名・映画館・上映日・開始時刻）を手動CSVから取り込む最小経路。

- 作品台帳（film_watch.py）とは別の単位。上映は「作品 × 映画館 × スクリーン × 日時」で1件。
- 自動取得はしない（利用条件が確認できた情報源の入力は、この CSV から手で行う）。
- 保存先：<dir>/screenings.json（他の台帳とは別ファイル）。既存の上映データは削除しない。
- 必須列：title（作品名）, theater（映画館）, screen_date（YYYY-MM-DD）, start_time（HH:MM、JST）, evidence_url（https の公式URL）
- 任意列：screen_name（スクリーン）, runtime_min（上映時間・分、1〜400の整数）, source_kind（artist_watch と同じ一覧）
- 不正な行（必須の欠落、日付・時刻・上映時間の形式違い、https でないURL）は保存せず、行番号つきで報告する。
- 重複キー：映画館・スクリーン・作品名・上映日・開始時刻（表記ゆれは artist_watch.norm で吸収）。
- 同じキーで上映時間などが変わった場合は、値を更新し、変更履歴（history）に残す。
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

FILE = "screenings.json"
REQUIRED = ("title", "theater", "screen_date", "start_time", "evidence_url")
OPTIONAL = ("screen_name", "runtime_min", "source_kind", "screening_type", "program_title", "release_year")
# 任意項目の規則：空欄は可（既存CSVはそのまま取り込める）。値があるのに規則外なら行を拒否する（推測で補わない）
SCREENING_TYPES = ("通常上映", "新作", "リバイバル", "名画座", "映画祭", "特集上映")
PROGRAM_TITLE_MAX = 100
YEAR_MIN = 1888          # 映画の誕生（1895年）より前の年は誤入力とみなす下限
YEAR_RE = re.compile(r"\d{4}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
TIME_RE = re.compile(r"\d{2}:\d{2}")
RUNTIME_MAX = 400
UPCOMING_DAYS = 14   # 見逃し防止の一覧に出す期間（今日から）
UPDATABLE = ("runtime_min", "source_kind", "evidence_url", "screening_type", "program_title", "release_year")


class CorruptStoreError(RuntimeError):
    pass


def validate(row, today=None):
    """1行の検査。戻り値は理由のリスト（空なら有効）。"""
    today = today or AW.now_jst().date()
    errors = []
    for k in REQUIRED:
        if not str(row.get(k) or "").strip():
            errors.append(f"{k} が空")
    date = str(row.get("screen_date") or "").strip()
    if date and not DATE_RE.fullmatch(date):
        errors.append(f"screen_date は YYYY-MM-DD: {date}")
    elif date:
        try:
            datetime.date.fromisoformat(date)
        except ValueError:
            errors.append(f"screen_date が存在しない日付: {date}")
    time = str(row.get("start_time") or "").strip()
    if time:
        ok = TIME_RE.fullmatch(time) is not None
        if ok:
            hh, mm = int(time[:2]), int(time[3:])
            ok = hh <= 23 and mm <= 59
        if not ok:
            errors.append(f"start_time は HH:MM（00:00〜23:59）: {time}")
    runtime = str(row.get("runtime_min") or "").strip()
    if runtime:
        if not runtime.isdigit() or not 1 <= int(runtime) <= RUNTIME_MAX:
            errors.append(f"runtime_min は 1〜{RUNTIME_MAX} の整数（空欄は可）: {runtime}")
    url = str(row.get("evidence_url") or "").strip()
    if url and not url.startswith("https://"):
        errors.append("evidence_url は https:// で始まる公式URLが必要")
    kind = str(row.get("source_kind") or "").strip()
    if kind and kind not in AW.SOURCE_KINDS:
        errors.append(f"source_kind が不正: {kind}")
    stype = str(row.get("screening_type") or "").strip()
    if stype and stype not in SCREENING_TYPES:
        errors.append(f"screening_type が不正（空欄可）: {stype}")
    program = str(row.get("program_title") or "").strip()
    if len(program) > PROGRAM_TITLE_MAX:
        errors.append(f"program_title は{PROGRAM_TITLE_MAX}文字以内")
    year = str(row.get("release_year") or "").strip()
    if year:
        ok = YEAR_RE.fullmatch(year) is not None and YEAR_MIN <= int(year) <= today.year + 1
        if not ok:
            errors.append(f"release_year は {YEAR_MIN}〜{today.year + 1} の4桁の年（空欄可）: {year}")
    return errors


def key_of(row):
    return "|".join([AW.norm(row["theater"]), AW.norm(row.get("screen_name", "")), AW.norm(row["title"]),
                     row["screen_date"].strip(), row["start_time"].strip()])


def load_store(base):
    path = os.path.join(base, FILE)
    if not os.path.exists(path):
        return path, {"screenings": {}, "history": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except ValueError as e:
        # 壊れた台帳を空として続けると、既存の上映が消える。停止して人が確認する。
        raise CorruptStoreError(f"上映台帳が壊れています（{path}）。内容を確認してから再実行してください: {e}")
    return path, data


def ingest(base, rows, now=None):
    """行を取り込む。不正な行は保存しない。有効な行が1つも無ければ、ファイルには触らない。
    戻り値: {'new', 'same', 'updated', 'rejected': [{'row', 'errors'}]}"""
    stamp = (now or AW.now_jst()).isoformat(timespec="seconds")
    path, data = load_store(base)
    recs, history = data["screenings"], data["history"]
    result = {"new": 0, "same": 0, "updated": 0, "rejected": []}
    for n, row in enumerate(rows, start=1):
        errors = validate(row)
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
        changes = [f for f in UPDATABLE if old.get(f, "") != clean[f]]
        if not changes:
            result["same"] += 1
            continue
        for f in changes:
            history.append({"at": stamp, "key": k, "field": f, "before": old.get(f, ""), "after": clean[f]})
            old[f] = clean[f]
        result["updated"] += 1
    if result["new"] or result["updated"] or result["same"]:
        AW._save(path, {"screenings": recs, "history": history})
    return result


def summarize(base):
    """件数・期間・映画館数・作品数・欠損率を集計する（機械処理）。"""
    _, data = load_store(base)
    recs = list(data["screenings"].values())
    total = len(recs)
    dates = sorted(r["screen_date"] for r in recs)
    missing = {f: sum(1 for r in recs if not str(r.get(f) or "").strip()) for f in OPTIONAL}
    return {"total": total,
            "titles": len({AW.norm(r["title"]) for r in recs}),
            "theaters": len({AW.norm(r["theater"]) for r in recs}),
            "period": [dates[0], dates[-1]] if dates else None,
            "missing_rate": {f: (round(missing[f] / total, 3) if total else None) for f in OPTIONAL},
            "history_count": len(data["history"])}


def upcoming(base, today=None, days=UPCOMING_DAYS):
    """見逃し防止の一覧に使う：今日から days 日以内の上映を、日時順で返す（過去は含めない）。"""
    today = today or AW.now_jst().date()
    end = today + datetime.timedelta(days=days)
    _, data = load_store(base)
    hits = []
    for r in data["screenings"].values():
        d = datetime.date.fromisoformat(r["screen_date"])
        if today <= d <= end:
            hits.append(r)
    return sorted(hits, key=lambda r: (r["screen_date"], r["start_time"], r["theater"]))


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
    p_in = sub.add_parser("ingest", help="CSV を上映台帳へ取り込む")
    p_in.add_argument("--dir", required=True)
    p_in.add_argument("--csv", required=True)
    p_sum = sub.add_parser("summary", help="件数・期間・欠損率を表示")
    p_sum.add_argument("--dir", required=True)
    p_up = sub.add_parser("upcoming", help="今後の上映を一覧表示")
    p_up.add_argument("--dir", required=True)
    p_up.add_argument("--days", type=int, default=14)
    args = ap.parse_args()
    if args.cmd == "ingest":
        print(json.dumps(ingest(args.dir, load_csv(args.csv)), ensure_ascii=False, indent=2))
    elif args.cmd == "summary":
        print(json.dumps(summarize(args.dir), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(upcoming(args.dir, days=args.days), ensure_ascii=False, indent=2))
