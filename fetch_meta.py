"""取得処理の実行時刻メタデータ（機械可読・タイムゾーン付き）。

- 記録するもの：最終成功時刻（last_success_at）と、最後に試行した時刻・結果（last_attempt_*）
- 成功時だけ last_success_at を更新する。失敗・未実行では、前回の成功時刻を新しい時刻で上書きしない
- 掲載ファイルの更新時刻（ファイルのmtime）は、ここでは扱わない。取得時刻とは別物として呼び出し側で表示する
- メタデータが無い・壊れている・タイムゾーンがない場合は「不明」として扱い、別の時刻で埋めない
"""
import datetime
import json
import os

JST = datetime.timezone(datetime.timedelta(hours=9))
META_PATH = os.path.join("data", "culture_fetch_meta.json")
UNKNOWN = "不明"


def load(path=None):
    """メタデータを読む。無い・壊れている・形式が違う場合は None。"""
    try:
        with open(path or META_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _aware(value):
    """タイムゾーン付きのISO文字列だけを datetime にする。それ以外は None（推測で補わない）。"""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def display_success_time(meta):
    """最終成功時刻を日本時間の表示文字列にする。確認できなければ「不明」。"""
    success = _aware((meta or {}).get("last_success_at"))
    if success is None:
        return UNKNOWN
    return success.astimezone(JST).strftime("%Y-%m-%d %H:%M") + " JST"


def _write(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _now():
    return datetime.datetime.now(JST).isoformat(timespec="seconds")


def record_success(path=None, when=None):
    """取得・書き出しが正常に終わったときだけ呼ぶ。最終成功時刻と試行結果を更新する。"""
    stamp = when or _now()
    path = path or META_PATH
    _write(path, {"version": 1, "last_success_at": stamp,
                  "last_attempt_at": stamp, "last_attempt_status": "成功", "last_attempt_reason": ""})


def record_outcome(partial_venues, path=None, when=None):
    """全体の結果を記録する。取得失敗・件数急減・特定不能の施設が1つでもあれば「一部失敗」として、
    最終成功時刻は更新しない。無ければ成功として記録する。"""
    partial = sorted(set(partial_venues))
    if partial:
        record_failure("一部の施設が前回値: " + ", ".join(partial), path, when=when, status="一部失敗")
    else:
        record_success(path, when=when)


def record_failure(reason, path=None, when=None, status="失敗"):
    """失敗・一部失敗のときに呼ぶ。最終成功時刻は前回の値をそのまま残す（無ければ null のまま）。
    status は「失敗」か「一部失敗」（一部の施設だけ取得できず、前回値で補った場合）。"""
    path = path or META_PATH
    previous = load(path) or {}
    success = previous.get("last_success_at") if _aware(previous.get("last_success_at")) else None
    _write(path, {"version": 1, "last_success_at": success,
                  "last_attempt_at": when or _now(), "last_attempt_status": status,
                  "last_attempt_reason": str(reason)[:300]})
