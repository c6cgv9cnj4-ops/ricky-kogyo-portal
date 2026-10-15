"""ジャンル横断の共通データ契約（イベント・期限・差分）。Python標準ライブラリのみ、ネットワークは使わない。

共通部分（この契約）と、ジャンル固有部分（genre_extra）を分ける。
- 日付は {"value", "precision", "timezone", "source_url"} で持つ。
  precision: "day"（日付のみ）／"datetime"（時刻まで判明）／"unknown"（不明。value は必ず null）。
  不明な日付に架空の値を入れない。
- state（表示用の状態）：新着／再掲載／変更／掲載未確認／取得失敗・前回データ／通常／終了。
  「初めて確認した掲載」は announced_at（公式発表日時）とは別。公式発表日時が不明なら announced_at は unknown。
- confidence：確認済み／推定／未確認／取得不能（既存の判定に従う）。
"""
import datetime
import html as html_lib
import json
import os

SCHEMA_VERSION = 1
DATE_KEYS = ("event_start", "event_end", "ticket_sale_start", "general_sale_start", "application_deadline", "announced_at")
PRECISIONS = ("day", "datetime", "unknown")
REQUIRED_KEYS = ("id", "genre", "title", "source_url", "state", "confidence", "last_checked_at", "dates")
CONFIDENCE_VALUES = ("確認済み", "推定", "未確認", "取得不能")
# 表示の優先順（新着・変更を先頭に）。「通常」「終了」は一覧に出さない
STATE_PRIORITY = {"新着": 0, "変更": 1, "再掲載": 2, "取得失敗・前回データ": 3, "掲載未確認": 4}


def unknown_date():
    return {"value": None, "precision": "unknown", "timezone": None, "source_url": ""}


def day_date(value, source_url=""):
    """日付のみ判明している場合。value は datetime.date（YYYY-MM-DD の文字列でも可）。"""
    if value is None:
        return unknown_date()
    if isinstance(value, datetime.date):
        value = value.isoformat()
    return {"value": str(value), "precision": "day", "timezone": None, "source_url": source_url or ""}


def datetime_date(value, timezone, source_url=""):
    """時刻まで判明している場合。value は ISO 8601（タイムゾーン付き）。timezone は必須。"""
    if not value or not timezone:
        raise ValueError("datetime には value と timezone の両方が必要です")
    return {"value": str(value), "precision": "datetime", "timezone": timezone, "source_url": source_url or ""}


def validate_record(rec):
    """共通契約の検証。問題の一覧を返す（空なら合格）。"""
    errors = []
    for key in REQUIRED_KEYS:
        if key not in rec or rec[key] in (None, ""):
            errors.append(f"必須項目がありません: {key}")
    url = str(rec.get("source_url", ""))
    if url and not url.startswith(("http://", "https://")):
        errors.append("source_url が http(s) ではありません")
    if rec.get("confidence") not in CONFIDENCE_VALUES:
        errors.append(f"confidence が不正です: {rec.get('confidence')}")
    dates = rec.get("dates") or {}
    for key in DATE_KEYS:
        d = dates.get(key)
        if d is None:
            errors.append(f"dates に {key} がありません")
            continue
        if d.get("precision") not in PRECISIONS:
            errors.append(f"{key} の precision が不正です")
        if d.get("precision") == "unknown" and d.get("value") is not None:
            errors.append(f"{key}: 不明な日付に値が入っています")
        if d.get("precision") == "datetime" and not d.get("timezone"):
            errors.append(f"{key}: 時刻付きなのに timezone がありません")
    return errors


def dedupe_records(records):
    """同じ id は1件にする（先に現れたものを残す）。"""
    seen, out = set(), []
    for rec in records:
        if rec["id"] in seen:
            continue
        seen.add(rec["id"])
        out.append(rec)
    return out


def upcoming_deadlines(records, today, days=14):
    """締切（応募締切・チケット先行・一般発売）が today から days 日以内にある記録を、日付順に返す。
    日付が不明（unknown）の項目は対象外。"""
    rows = []
    for rec in records:
        for key in ("application_deadline", "ticket_sale_start", "general_sale_start"):
            d = (rec.get("dates") or {}).get(key) or {}
            if d.get("precision") not in ("day", "datetime") or not d.get("value"):
                continue
            day = datetime.date.fromisoformat(str(d["value"])[:10])
            delta = (day - today).days
            if 0 <= delta <= days:
                rows.append((day, key, rec))
    rows.sort(key=lambda r: (r[0], r[2]["id"]))
    return [{"date": day.isoformat(), "kind": key, "record": rec} for day, key, rec in rows]


def news_rows(records):
    """新着・変更・取得状況を、優先順に並べた行を返す。「通常」「終了」は含めない。"""
    rows = [r for r in records if r.get("state") in STATE_PRIORITY]
    rows.sort(key=lambda r: (STATE_PRIORITY[r["state"]], r.get("title", "")))
    return rows


def _esc(value):
    return html_lib.escape(str(value), quote=True)


def render_news_html(rows, empty_text):
    """ホームの新着・取得状況の一覧（コンパクト表示）。row は news_rows の要素。"""
    if not rows:
        return f"<p class='news-empty'>{_esc(empty_text)}</p>"
    items = []
    for rec in rows:
        start = (rec.get("dates") or {}).get("event_start") or {}
        end = (rec.get("dates") or {}).get("event_end") or {}
        period = ""
        if start.get("value") or end.get("value"):
            period = f"{start.get('value') or '?'} 〜 {end.get('value') or '?'}"
        url = rec["source_url"] if str(rec["source_url"]).startswith(("http://", "https://")) else "#"
        items.append(
            "<li class='news-row'>"
            f"<span class='news-state'>{_esc(rec['state'])}</span>"
            f"<span class='news-genre'>{_esc(rec['genre'])}</span>"
            f"<a href='{_esc(url)}' target='_blank' rel='noopener'>{_esc(rec['title'])}</a>"
            f"<span class='news-venue'>{_esc(rec.get('venue', ''))}</span>"
            f"<span class='news-period'>{_esc(period)}</span>"
            f"<span class='news-confidence'>確度：{_esc(rec['confidence'])}</span>"
            f"<span class='news-announce'>公式発表日：{_esc((rec.get('dates') or {}).get('announced_at', {}).get('value') or '未確認')}</span>"
            "</li>"
        )
    return "<ul class='news-list'>" + "".join(items) + "</ul>"


def content_key(rec):
    """記録の内容を比べるための文字列（最終確認時刻は含めない）。"""
    body = {k: v for k, v in rec.items() if k != "last_checked_at"}
    return json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)


def write_json_atomic(path, payload):
    """JSON を一時ファイル経由で保存する。キー順序を固定し、内容が同じなら書き込まない（Git差分を作らない）。"""
    text = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n"
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    return True


def official_url(url):
    """http(s) のURLだけを公式リンクとして返す（それ以外は空）。"""
    url = str(url or "")
    return url if url.startswith(("http://", "https://")) else ""
