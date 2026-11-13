"""東京芸術劇場の公演情報（geigeki_events.collect）を、指定したローカル出力先で1回実行する。

本番の公開先（docs/）・定期実行（auto_update.yml）には接続しない。
- 出力先は必ず指定する（既定値なし）。docs/ 配下の指定は拒否する（公開物を誤って書き換えないため）。
- 実行結果（取得件数・失敗URL・理由・取得時刻）を、出力と同じフォルダの run_summary.json に残す。
- 一覧の取得失敗・件数急減などの保護は geigeki_events.collect の既存ロジックをそのまま使う。
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import geigeki_events as G  # noqa: E402

DOCS_DIR = os.path.abspath(os.path.join(HERE, "docs"))


def guard_output_path(path):
    """docs/ 配下（公開物）への書き込みを拒否する。戻り値は絶対パス。"""
    absolute = os.path.abspath(path)
    if absolute == DOCS_DIR or absolute.startswith(DOCS_DIR + os.sep):
        raise ValueError(f"公開先（docs/）への書き込みは拒否します: {absolute}")
    return absolute


def run(out_path, fetch=None, sleep=None, now=None):
    """collect を1回実行し、結果の要約を run_summary.json に書く。戻り値は要約の辞書。"""
    out_path = guard_output_path(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    started = now or datetime.datetime.now(datetime.timezone.utc)
    summary = G.collect(fetch=fetch or G._requests_fetch, sleep=sleep or G.time.sleep,
                        checked_at=started.isoformat(timespec="seconds"), path=out_path)
    summary = dict(summary)
    summary["started_at_utc"] = started.isoformat(timespec="seconds")
    summary["output_path"] = out_path
    summary["source"] = G.LIST_URL
    with open(os.path.join(os.path.dirname(out_path), "run_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="出力JSONのパス（docs/ 配下は不可）")
    args = ap.parse_args()
    result = run(args.out)
    print(json.dumps({k: result.get(k) for k in ("ok", "listed", "records", "failed", "changed", "reason", "output_path")},
                     ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("ok") else 1)
