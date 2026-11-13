"""artist_watch の実行経路（ローカル・ドライラン）。

設定（登録するアーティストと情報源）と、人が公式情報から確認して書いた取り込みファイル（batch）を読み、
台帳に反映して、期限と変更の通知候補を作る。外部送信・本番公開・ワークフロー接続はしない。

- 設定：config.json（アーティスト名・公式URL・情報源）。ファイルがなければ何もしない（勝手に対象を作らない）。
- 情報源の自動取得：本スクリプトは取得しない。利用条件が「確認済み」の情報源も、batch への転記だけを受け付ける。
- 同じ batch を2回流しても、同じ公演は new/changed/same として扱い、通知候補は台帳で重複させない。
"""
import argparse
import csv
import io
import re
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import artist_watch as AW  # noqa: E402


def find_artist_id(store, name):
    target = AW.norm(name)
    for aid, a in store.load("artists").items():
        if AW.norm(a["name"]) == target or target in {AW.norm(x) for x in a.get("aliases", [])}:
            return aid
    return None


def apply_config(store, cfg, now=None):
    """設定のアーティストと情報源を登録する。既に登録済みのものは何もしない。戻り値: 登録件数の要約。"""
    added = {"artists": 0, "sources": 0}
    for a in cfg.get("artists", []):
        if find_artist_id(store, a["name"]) is None:
            AW.register_artist(store, a["name"], official_url=a.get("official_url", ""),
                               aliases=a.get("aliases", []), now=now)
            added["artists"] += 1
    for s in cfg.get("sources", []):
        if s["id"] not in store.load("sources"):
            AW.register_source(store, s["id"], s["name"], s["url"], s["kind"], s.get("terms_status", "未確認"))
            added["sources"] += 1
    return added


def batch_items(store, batch, unknown_names):
    """batch（人が確認して書いた公演リスト）を、ingest_batch の形式に変換する。アーティスト名が未登録なら記録して除外。"""
    items = []
    for row in batch:
        aid = find_artist_id(store, row.get("artist_name", ""))
        if aid is None:
            unknown_names.append(row.get("artist_name", ""))
            continue
        items.append({"artist_id": aid, "venue": row["venue"], "name": row["name"],
                      "event_date": row.get("event_date"), "sale_windows": row.get("sale_windows", []),
                      "status": row.get("status", "公演発表"), "evidence_url": row.get("evidence_url", ""),
                      "announced_at": row.get("announced_at"), "notices": row.get("notices") or [],
                      "perf_key": row.get("source_performance_id") or ""})
    return items


def record_fetch(store, items, source_id, now):
    """取り込んだ公演に、取得時刻と情報源を付ける（出典の確認用）。"""
    events = store.load("events")
    stamp = now.isoformat(timespec="seconds")
    for it in items:
        rid = AW.item_perf_id(it)
        if rid in events:
            events[rid]["fetched_at"] = stamp
            events[rid]["source_id"] = source_id
    store.save("events", events)


FETCH_STATUSES = ("ok", "timeout", "http_error", "empty", "parse_error")
FETCH_LABELS = {"timeout": "タイムアウト", "http_error": "HTTPエラー", "empty": "空レスポンス", "parse_error": "解析失敗"}


def apply_notices(store, items, source_id, now):
    """取り込み成功時のみ、各公演の行内 notices（延期・中止・訂正など）を別記録として登録する。戻り値: 登録件数。"""
    added = 0
    for it in items:
        if not it.get("notices"):
            continue
        rid = AW.item_perf_id(it)
        for n in it["notices"]:
            _, res = AW.add_notice(store, rid, n["type"], n.get("detail", {}), evidence_url=n.get("evidence_url", ""), now=now)
            added += res == "new"
    return added


BEGINNING = "0001-01-01T00:00:00+09:00"


def get_anchor(store, source_id):
    """情報源の前回の正常な取込時刻（台帳 meta.json に保存）。無ければ None。"""
    return store.load("meta").get("anchors", {}).get(source_id)


def set_anchor(store, source_id, now):
    meta = store.load("meta")
    meta.setdefault("anchors", {})[source_id] = now.isoformat(timespec="seconds")
    store.save("meta", meta)


def effective_since(store, since=None):
    """通知候補の起点。明示指定があればそれを使う。無指定なら、全情報源の前回成功時刻の最小値
    （1つでも未取込の情報源があれば最初から）。最小値を使うので、情報源の一部だけ進んで変更を取り逃がさない。"""
    if since:
        return since
    sources = store.load("sources")
    anchors = [get_anchor(store, sid) for sid in sources]
    if not sources or any(a is None for a in anchors):
        return BEGINNING
    return min(anchors)


def run(store, source_id, batch, since=None, now=None, cfg=None, source_status="ok"):
    """1回の実行：設定の反映 → batch の取り込み → 期限と変更の通知候補（ドライラン）。
    source_status が ok 以外（タイムアウト・HTTPエラー・空・解析失敗）の場合は既存の台帳を一切変えない。"""
    now = now or AW.now_jst()
    result = {"config": apply_config(store, cfg or {}, now=now)}
    unknown = []
    items = batch_items(store, batch, unknown)
    if source_status not in FETCH_STATUSES:
        raise ValueError(f"取得結果の種類が不正です: {source_status}")
    # 取得結果の種類を台帳（meta）に残す。ページで「取得失敗」「データなし」を区別して表示するため。
    meta = store.load("meta")
    meta.setdefault("last_status", {})[source_id] = {"status": source_status, "at": now.isoformat(timespec="seconds")}
    store.save("meta", meta)
    if source_status != "ok":
        # 失敗・空は既存の台帳を変えない。「中止」「販売終了」とは判定しない。
        result["ingest"] = {"applied": False, "reason": f"{FETCH_LABELS.get(source_status, source_status)}（既存データを保持）"}
    elif items:
        result["ingest"] = AW.ingest_batch(store, source_id, items, source_ok=True, now=now)
        record_fetch(store, items, source_id, now)
        result["notices_added"] = apply_notices(store, items, source_id, now)
    else:
        result["ingest"] = {"applied": False, "reason": "取り込みなし"}
    result["unknown_artists"] = sorted(set(unknown))
    if source_status != "ok":
        # 取得失敗の実行では候補を作らず、台帳にも記録しない（記録すると、次回から二度と出なくなる）
        result["candidates"], result["overflow_count"], result["marked"] = [], 0, 0
        return result
    plan = AW.plan_candidates(store, effective_since(store, since), now=now)
    cands = plan["candidates"]
    result["candidates"] = cands
    result["overflow_count"] = len(plan["overflow"])
    result["marked"] = AW.mark_dry_run_seen(store, cands)
    # 起点を進めるのは、この情報源の取込が実際に反映された場合だけ（失敗・空では進めない）。
    # 上限で持ち越した変更がある場合は、その最も古い時刻まで起点を戻し、次回に確実に出す。
    if source_status == "ok" and result.get("ingest", {}).get("applied"):
        anchor_at = now
        if plan["earliest_overflow_at"]:
            anchor_at = min(now, datetime.datetime.fromisoformat(plan["earliest_overflow_at"]))
        set_anchor(store, source_id, anchor_at)
        result["anchor"] = get_anchor(store, source_id)
    return result


ADAPTERS = {}
ADAPTER_REQUIRED = ("artist_name", "venue", "name")


def adapter(name):
    """取り込み形式（テキスト→公演リスト）を登録するデコレータ。新しい情報源はここに形式を足すだけで追加できる。"""
    def deco(fn):
        if name in ADAPTERS:
            raise ValueError(f"アダプタ名が重複しています: {name}")
        ADAPTERS[name] = fn
        return fn
    return deco


@adapter("json_rows")
def parse_json_rows(text):
    """JSON配列（人が確認した公演リスト）を読む。必須項目が欠けている行は、推測で補わずエラーにする。"""
    rows = json.loads(text)
    if not isinstance(rows, list):
        raise ValueError("json_rows は配列である必要があります")
    for i, row in enumerate(rows):
        missing = [k for k in ADAPTER_REQUIRED if not row.get(k)]
        if missing:
            raise ValueError(f"{i}行目に必須項目がありません: {missing}")
    return rows


def parse_with_adapter(name, text):
    if name not in ADAPTERS:
        raise ValueError(f"未登録のアダプタです: {name}")
    return ADAPTERS[name](text)


def fetch_allowed(store, source_id):
    """自動取得の許可は、情報源の利用条件が「確認済み」の場合だけ。未登録・未確認は False。"""
    src = store.load("sources").get(source_id)
    return bool(src and src.get("auto_fetch_allowed"))


def fetch_source(store, source_id, fetch):
    """許可された情報源だけ fetch を呼ぶ。許可が無い場合は fetch を一切呼ばずに例外にする。
    fetch(url) -> (status, text)。status は FETCH_STATUSES のいずれか。"""
    if not fetch_allowed(store, source_id):
        raise PermissionError(f"自動取得の許可が未確認のため取得しません: {source_id}")
    src = store.load("sources")[source_id]
    status, text = fetch(src["url"])
    if status not in FETCH_STATUSES:
        raise ValueError(f"取得結果の種類が不正です: {status}")
    return status, text


def ingest_source(store, source_id, adapter_name, text, since=None, now=None, status="ok"):
    """取得済みのテキストをアダプタで読み、run に渡す。status が ok 以外なら既存データを変えない。"""
    rows = parse_with_adapter(adapter_name, text) if status == "ok" else []
    return run(store, source_id, rows, since, now=now, source_status=status)


CSV_COLUMNS = ("artist_name", "venue", "name", "event_date", "status", "evidence_url",
               "source_performance_id", "sale_type", "sale_start", "sale_end")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _validate_row(row):
    """1行を検証する。戻り値: (正規化した行, エラー理由)。不正な行は（None, 理由）。推測で補わない。"""
    for k in ADAPTER_REQUIRED:
        if not str(row.get(k) or "").strip():
            return None, f"必須項目がありません: {k}"
    ev = str(row.get("event_date") or "").strip()
    if ev and not DATE_RE.fullmatch(ev):
        return None, f"event_date が YYYY-MM-DD ではありません: {ev}"
    out = {k: str(row.get(k) or "").strip() for k in ("artist_name", "venue", "name", "event_date", "status",
                                                    "evidence_url", "source_performance_id")}
    out["event_date"] = out["event_date"] or None
    out["status"] = out["status"] or "公演発表"
    windows = row.get("sale_windows") or []
    if row.get("sale_type"):   # CSV：1行1発売
        windows = [{"type": str(row["sale_type"]).strip(), "start": str(row.get("sale_start") or "").strip(),
                    "end": str(row.get("sale_end") or "").strip()}]
    for w in windows:
        try:
            AW._parse_when(w.get("start"))
            AW._parse_when(w.get("end"))
        except ValueError as e:
            return None, f"発売日時が解析できません: {e}"
        if w.get("type") not in AW.SALE_TYPES:
            return None, f"発売の種類が不正です: {w.get('type')}"
    out["sale_windows"] = [w for w in windows if w.get("start") or w.get("end")]
    return out, ""


def read_batch(path):
    """取り込みファイル（.json または .csv）を読み、(有効な行, 不正な行の一覧) を返す。
    不正な行は取り込まない。同じ公演の行はそのまま渡す（既存の重複排除に任せる）。"""
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    if path.lower().endswith(".csv"):
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None or any(c not in reader.fieldnames for c in ADAPTER_REQUIRED):
            raise ValueError("CSVに必須の列（artist_name, venue, name）がありません")
        raw = list(reader)
    else:
        raw = json.loads(text)
        if not isinstance(raw, list):
            raise ValueError("JSON は配列である必要があります")
    valid, rejected = [], []
    for i, row in enumerate(raw, start=1):
        clean, why = _validate_row(row)
        if clean is None:
            rejected.append({"行": i, "理由": why})
        else:
            valid.append(clean)
    return valid, rejected


HERE_DIR = os.path.dirname(os.path.abspath(__file__))


FETCHERS = {}   # 情報源ID -> (取得関数 fetch(url) -> (status, text), アダプタ名)。取得経路の入口。登録が無い情報源は「未接続」。


def register_fetcher(source_id, fetch, adapter_name):
    """情報源ごとの取得経路を登録する。取得の可否（許可）は fetch_allowed が別に判定する。"""
    if source_id in FETCHERS:
        raise ValueError(f"取得経路が既に登録されています: {source_id}")
    FETCHERS[source_id] = (fetch, adapter_name)


def run_source(store, source_id, now=None, since=None):
    """情報源1つ分の取得→取り込み。状態は 未接続／未実行（許可未確認）／成功／失敗 のいずれか。
    取得経路が無い・許可が無い場合は、取得関数を呼ばず、台帳は変えない。失敗時も既存データと履歴は保持する。"""
    now = now or AW.now_jst()
    if source_id not in store.load("sources"):
        raise ValueError(f"未登録の情報源です: {source_id}")
    if source_id not in FETCHERS:
        return {"state": "未接続", "detail": "取得経路が登録されていません（ローカル入力は batch で取り込む）"}
    if not fetch_allowed(store, source_id):
        return {"state": "未実行", "detail": "自動取得の許可が未確認のため、取得していません"}
    fetch, adapter_name = FETCHERS[source_id]
    try:
        status, text = fetch_source(store, source_id, fetch)
    except Exception as e:   # 取得関数の例外は「取得失敗」として扱う（成功にしない）
        status, text = "http_error", None
        detail = f"取得中の例外: {type(e).__name__}"
    else:
        detail = ""
    if status == "ok" and text is not None:
        try:
            rows = parse_with_adapter(adapter_name, text)   # 解析できない内容は取り込まない（台帳は変えない）
        except (ValueError, json.JSONDecodeError) as e:
            record_status(store, source_id, "parse_error", now)
            return {"state": "失敗", "detail": f"解析失敗（既存データを保持）: {e}"}
        if not rows:   # 0件は「空」として扱い、既存データを消さない（中止と判定しない）
            record_status(store, source_id, "empty", now)
            return {"state": "失敗", "detail": "取得結果が0件（既存データを保持）"}
        result = run(store, source_id, rows, since, now=now, source_status="ok")
        applied = result.get("ingest", {}).get("applied")
        return {"state": "成功" if applied else "失敗", "detail": result.get("ingest", {}).get("reason", ""), **result}
    record_status(store, source_id, status, now)
    return {"state": "失敗", "detail": detail or f"取得失敗: {status}（既存データを保持）"}


def record_status(store, source_id, status, now):
    meta = store.load("meta")
    meta.setdefault("last_status", {})[source_id] = {"status": status, "at": (now or AW.now_jst()).isoformat(timespec="seconds")}
    store.save("meta", meta)


def main(argv=None):
    p = argparse.ArgumentParser(description="artist_watch のローカル・ドライラン")
    p.add_argument("--dir", default=AW.DEFAULT_DIR, help="台帳の保存先（既定: artist_watch_data）")
    p.add_argument("--config", default="", help="アーティストと情報源の設定（JSON）")
    p.add_argument("--source", required=False, default="", help="取り込み先の情報源ID（登録済みであること）")
    p.add_argument("--batch", default="", help="人が確認した公演リスト（JSON配列）")
    p.add_argument("--since", default="", help="変更を集める起点（ISO日時）。省略時は台帳に保存された前回成功時刻")
    p.add_argument("--site-out", default="", help="ホームページ（静的HTML）を書き出すフォルダ。本番の docs/ は指定しないこと")
    p.add_argument("--data-label", default="データ出所：未指定", help="ページに表示する出所（例：合成データ／実データ）")
    args = p.parse_args(argv)
    store = AW.Store(args.dir)
    cfg = {}
    if args.config:
        with open(args.config, encoding="utf-8") as f:
            cfg = json.load(f)
    batch, rejected = [], []
    if args.batch:
        try:
            batch, rejected = read_batch(args.batch)   # JSON・CSV とも同じ検証を通す
        except (OSError, ValueError) as e:             # 読めない入力は何も書かずに終了（既存データは変えない）
            print(f"入力ファイルを読めませんでした（台帳は変更していません）: {e}")
            return 2
    since = args.since or None
    if not args.batch and not args.config:
        print("設定も取り込みファイルもないため、何もしません。")
        return 0
    if batch and not args.source:
        print("--batch を使うには --source（登録済みの情報源ID）が必要です。")
        return 1
    result = run(store, args.source, batch, since, cfg=cfg)
    result["rejected_rows"] = rejected
    if args.site_out:
        import artist_watch_site as SITE
        if os.path.abspath(args.site_out).startswith(os.path.abspath(os.path.join(HERE_DIR, "docs"))):
            print("docs/ 配下（本番の公開先）への書き出しは拒否します。")
            return 1
        result["site_files"] = SITE.render_site(store, args.site_out, data_label=args.data_label)
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
