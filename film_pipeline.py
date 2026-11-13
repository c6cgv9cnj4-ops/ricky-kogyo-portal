"""文化HPの更新パイプライン（1コマンド）：提供データ → 台帳 → DB → HTML → 差分 → ローカル通知。

使い方：
  python3 film_pipeline.py --source csv_file --input 提供データ.csv --notice-out 通知.txt
  python3 film_pipeline.py --source json_file --input 提供データ.json --notice-out 通知.txt

- 取り込み方式は feed_adapters.ADAPTERS に登録する（提供データの file 方式のみ動く。外部取得は許可確認の情報源だけ）。
- 失敗（入力の不正、重複、内部リンク切れなど）の場合は、台帳・DB・既存のHTMLを変えない（film_site の巻き戻し）。
- 同じ入力を再度取り込んでも、差分と通知は増えない（変更なしは「変更なし」、通知は通知済みを除く）。
- 外部への送信は一切行わない。通知はローカルのテキストファイルに書く。
"""
import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import feed_adapters as FA  # noqa: E402
import film_site as FS  # noqa: E402
import film_watch as FW  # noqa: E402
from exhibition_watch import DEFAULT_SOURCE  # noqa: E402


EMPTY_REPORT = {"new_works": [], "updated_works": [], "needs_check": [], "skipped_rows": []}


def run(source, input_path, film_dir, db_path, out_dir, notice_out, data_kind="実データ",
        exhibition_source=DEFAULT_SOURCE, screenings_dir="screenings_data", today=None):
    """1回の取り込みを実行し、結果（報告・差分・通知）を返す。失敗時は履歴に失敗を残し、例外を返す（台帳は変えない）。"""
    try:
        return _run(source, input_path, film_dir, db_path, out_dir, notice_out, data_kind,
                    exhibition_source, screenings_dir, today)
    except Exception as e:
        FA.log_run(film_dir, source, input_path, [], EMPTY_REPORT, f"失敗：{type(e).__name__}：{e}"[:200])
        raise


def _run(source, input_path, film_dir, db_path, out_dir, notice_out, data_kind,
         exhibition_source, screenings_dir, today):
    rows = FA.run_adapter(source, input_path)
    batch, report = FA.to_batch(rows, film_dir, data_kind)
    result = {"rows": len(rows), "report": report, "diff": "変更なし", "notice": {"new": 0, "file": None}}
    if batch:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(batch, f, ensure_ascii=False)
            batch_path = f.name
        try:
            built = FS.build_site(film_dir, db_path, out_dir, batch=batch_path, data_kind=data_kind,
                                  exhibition_source=exhibition_source, screenings_dir=screenings_dir, today=today)
        finally:
            os.remove(batch_path)
        result["diff"] = built["diff"]
        result["pages"] = built["pages"]
        result["db"] = built["db"]
    if notice_out:
        result["notice"] = FS.write_notice(film_dir, result["diff"], notice_out)
    FA.log_run(film_dir, source, input_path, rows, report, "成功")
    return result


def format_result(result):
    rep = result["report"]
    lines = [f"取込行数：{result['rows']}"]
    lines.append("新規の作品：" + ("、".join(rep["new_works"]) or "なし"))
    lines.append("既存の作品の更新：" + ("、".join(rep["updated_works"]) or "なし"))
    if rep["needs_check"]:
        lines.append("要確認（同名の人物。区別情報で確認するまで結ばない）：")
        lines += [f"  - {x}" for x in rep["needs_check"]]
    if rep["skipped_rows"]:
        lines.append("取り込まなかった行：")
        lines += [f"  - {x}" for x in rep["skipped_rows"]]
    lines.append("差分：")
    lines.append(result["diff"])
    n = result["notice"]
    lines.append(f"通知：新しく {n['new']} 件" + (f"（{n['file']}）" if n.get("file") else "（新しい通知なし）"))
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", required=True, choices=sorted(list(FA.ADAPTERS) + list(FA.FETCH_ADAPTERS)),
                    help="取り込み方式")
    ap.add_argument("--input", default=None, help="提供データのファイル（CSV・JSON）。外部取得方式では不要")
    ap.add_argument("--dir", default=FW.DEFAULT_DIR, help="台帳フォルダ")
    ap.add_argument("--db", default=os.path.join(HERE, "portal_data", "portal.db"), help="DB（生成先）")
    ap.add_argument("--out", default=FS.DEFAULT_OUT, help="HTMLの出力先（docs/ の中の film フォルダ）")
    ap.add_argument("--notice-out", default=None, help="ローカル通知ファイル（任意）")
    ap.add_argument("--data-kind", default="実データ", choices=FW.DATA_KINDS)
    ap.add_argument("--screenings-dir", default="screenings_data", help="上映台帳のフォルダ（screenings.py の ingest で作る）")
    ap.add_argument("--exhibition-source", default=DEFAULT_SOURCE, help="展覧会の掲載ファイル（DB用）")
    a = ap.parse_args()
    try:
        result = run(a.source, a.input, a.dir, a.db, a.out, a.notice_out, a.data_kind,
                     exhibition_source=a.exhibition_source, screenings_dir=a.screenings_dir)
    except (ValueError, PermissionError, FW.CorruptState) as e:
        sys.stderr.write(f"取り込みを中止（台帳・DB・HTMLは変更していません）: {e}\n")
        sys.exit(2)
    print(format_result(result))
