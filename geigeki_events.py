"""東京芸術劇場（公益財団法人東京都歴史文化財団）の公演・イベント情報を、共通データ契約（event_records）に変換する。

情報源：https://www.geigeki.jp/performance/ （一覧）と、各公演ページ（/performance/<slug>/）。
- robots.txt：全体は許可。/press/ と /wp-admin/ のみ不許可（本処理は対象にしない）。
- 認証・有料API・ログインは使わない。アクセスは一覧1回＋公演ページごとに1回、間隔を空けて取得する。

取得できる項目：公演名（og:title）、日程（開催開始・終了の日付）、会場、主催、公式URL、応募期間（年の記載がある場合のみ締切）。
取得できない項目（unknown のまま）：一般発売日・先行受付日、公式発表日時。公演ページに記載がないため、推測で埋めない。

失敗の扱い：一覧の取得に失敗した場合は、何も書き換えない（前回の events.json を残す）。
公演ページの個別の失敗は、その公演だけ前回の記録を保持する。
"""
import datetime
import html as html_lib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event_records as ER  # noqa: E402

try:
    import requests
except ImportError:  # テストは requests なしでも動く（取得関数は使わない）
    requests = None

LIST_URL = "https://www.geigeki.jp/performance/"
BASE_URL = "https://www.geigeki.jp/performance/"
JST_TZ = datetime.timezone(datetime.timedelta(hours=9))
GENRE = "公演・イベント"
SOURCE_NAME = "東京芸術劇場"
OUTPUT_JSON = os.path.join("docs", "music", "events.json")
REQUEST_INTERVAL_SEC = 2.0
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ricky-kogyo-portal/1.0; +https://github.com/c6cgv9cnj4-ops)"}
GENERIC_TITLES = ("重要なお知らせ", "")
FULL_DATE = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")


def _text(fragment):
    fragment = re.sub(r"<script.*?</script>|<style.*?</style>", " ", fragment, flags=re.S)
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_listing_links(page_html):
    """一覧ページから公演ページのURL（/performance/<slug>/）を重複なしで取り出す。"""
    urls = []
    for href in re.findall(r'href="(https://www\.geigeki\.jp/performance/[^"#?]+/?)"', page_html):
        if href.rstrip("/") == BASE_URL.rstrip("/"):
            continue
        if href not in urls:
            urls.append(href)
    return urls


def _segment(text, start_label, stop_labels, limit=160):
    """『start_label』の後ろから、いずれかの stop_labels の直前までの文字列。見つからなければ空。"""
    i = text.find(start_label)
    if i < 0:
        return ""
    seg = text[i + len(start_label):i + len(start_label) + limit]
    cut = len(seg)
    for stop in stop_labels:
        j = seg.find(stop)
        if 0 <= j < cut:
            cut = j
    return seg[:cut].strip()


RANGE_END_NO_YEAR = re.compile(r"[～〜~\-―]\s*(\d{1,2})月\s*(\d{1,2})日")


def _range_end_without_year(schedule, start):
    """『2026年9月1日（火）～11月3日』のように、終了日だけ年がない範囲を開始日の年で補う。
    同じ年の中で終わる場合（終了の月が開始の月以降）のみ採用する。年跨ぎ（12月→1月など）は推測せず None。"""
    if start is None:
        return None
    m = RANGE_END_NO_YEAR.search(schedule)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    if (month, day) < (start.month, start.day):
        return None
    try:
        return datetime.date(start.year, month, day)
    except ValueError:
        return None


SALE_LINE = re.compile(r"([^\s：:]{0,30}?(?:先行|一般発売)[^：:]{0,20})[：:]\s*(?:(\d{4})年)?\s*(\d{1,2})月\s*(\d{1,2})日\s*(?:[（(]([月火水木金土日])[^）)]*[）)])?\s*(\d{1,2}):(\d{2})")
SALE_STOPS = ("チケット取扱", "お問い合わせ", "会場", "主催")  # 見出し「発売日・お申込み」の「申込み」では切らない
WEEKDAYS = "月火水木金土日"


def _infer_year(month, day, weekday, event_start):
    """年の記載がない発売日を、開始日の前後1年以内で曜日が一致する年に限って決める。候補が1つでなければ None。"""
    if event_start is None:
        return None
    cands = []
    for y in (event_start.year - 1, event_start.year, event_start.year + 1):
        try:
            d = datetime.date(y, month, day)
        except ValueError:
            continue
        if weekday and WEEKDAYS[d.weekday()] != weekday:
            continue
        if -366 <= (event_start - d).days <= 366:
            cands.append(d)
    return cands[0] if len(cands) == 1 else None


def parse_sales(text, event_start):
    """発売日の欄から、先行受付と一般発売を区別して取り出す。戻り値: [{kind, label, start, year_source}]。
    開始日時は JST の時刻付きのみ。年が推定できない場合は start を None（不明）にする。"""
    i = text.find("発売日")
    if i < 0:
        return []
    seg = text[i:i + 600]
    cut = min([seg.find(stop) for stop in SALE_STOPS if seg.find(stop) > 0] or [len(seg)])
    out = []
    for m in SALE_LINE.finditer(seg[:cut]):
        label = m.group(1).strip()
        if "一般" in label:
            kind = "一般発売"
        elif "先行" in label:
            kind = "先行受付"
        else:
            continue
        month, day, hour, minute = int(m.group(3)), int(m.group(4)), int(m.group(6)), int(m.group(7))
        if m.group(2):
            year, source = int(m.group(2)), "明記"
        else:
            inferred = _infer_year(month, day, m.group(5), event_start)
            year, source = (inferred.year, "曜日で推定") if inferred else (None, "未確定")
        start = None
        if year:
            start = datetime.datetime(year, month, day, hour, minute, tzinfo=JST_TZ).isoformat(timespec="minutes")
        out.append({"kind": kind, "label": label, "start": start, "year_source": source})
    return out


def parse_detail(page_html, url):
    """公演ページを解析する。戻り値は辞書。取得できない項目は None。"""
    og = re.search(r'property="og:title" content="([^"]*)"', page_html)
    title = html_lib.unescape(og.group(1)).strip() if og else ""
    if title in GENERIC_TITLES:
        title = ""
    text = _text(page_html)
    schedule = _segment(text, "日程", ["会場", "主催", "料金", "お問い合わせ"])
    venue = _segment(text, "会場", ["主催", "料金", "お問い合わせ", "日程"], limit=120)
    organizer = _segment(text, "主催", ["料金", "お問い合わせ", "後援", "協賛"], limit=60)
    dates = [datetime.date(int(y), int(m), int(d)) for y, m, d in FULL_DATE.findall(schedule)]
    end_without_year = _range_end_without_year(schedule, dates[0] if dates else None)
    if end_without_year:
        dates.append(end_without_year)
    sales = parse_sales(text, dates[0] if dates else None)
    apply_text = _segment(text, "応募期間", ["※", "受講料", "応募方法", "お問い合わせ"], limit=80)
    apply_dates = [datetime.date(int(y), int(m), int(d)) for y, m, d in FULL_DATE.findall(apply_text)]
    return {
        "url": url,
        "title": title,
        "venue": venue,
        "organizer": organizer,
        "event_start": dates[0] if dates else None,
        "event_end": dates[-1] if dates else None,
        # 年の記載がない応募期間の締切は推測しない（unknown）
        "application_deadline": apply_dates[-1] if apply_dates else None,
        "sales": sales,
        "sales_block": "発売日" in text,
    }


def slug_id(url):
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    return f"music|geigeki|{slug}"


SALES_STATUS_HAS = "取得・解析済み（発売あり）"
SALES_STATUS_NONE = "ページ確認済み（発売欄なし）"
SALES_STATUS_UNKNOWN = "未確認"


def _sales_status(detail):
    """発売欄の状態。取得失敗は to_record に来ない（前回データを保持する）ため、ここでは「発売欄なし」を確定できる。"""
    if detail.get("sales"):
        return SALES_STATUS_HAS
    if detail.get("sales_block"):
        return SALES_STATUS_UNKNOWN   # 発売欄はあるが、区分を解析できなかった
    return SALES_STATUS_NONE


def _sale_item(x, url):
    """発売機会1件を共通契約の形にする。start は JST の日時、確定しない場合は null。"""
    start = x.get("start")
    return {"kind": x["kind"], "label": x["label"], "start": start,
            "precision": "datetime" if start else "unknown",
            "timezone": "Asia/Tokyo" if start else None,
            "year_source": x.get("year_source", "未確定"), "source_url": url}


def _sale_date(detail, kind, url):
    """同じ種類の発売が1回だけで、開始日時が確定している場合だけ日時を入れる。複数回・不明は unknown。"""
    hits = [x for x in detail.get("sales", []) if x["kind"] == kind]
    if len(hits) == 1 and hits[0]["start"]:
        return ER.datetime_date(hits[0]["start"], "Asia/Tokyo", url)
    return ER.unknown_date()


def to_record(detail, state, checked_at):
    """公演ページの解析結果を共通データ契約の記録に変換する。"""
    url = detail["url"]
    has_core = bool(detail.get("title") and detail.get("event_start"))
    return {
        "id": slug_id(url),
        "genre": GENRE,
        "title": detail.get("title") or "（公演名を取得できませんでした）",
        "venue": detail.get("venue") or SOURCE_NAME,
        "source_url": ER.official_url(url),
        "evidence_url": ER.official_url(url) if has_core else "",
        "state": state,
        "confidence": "確認済み" if has_core else "未確認",
        "last_checked_at": checked_at,
        "dates": {
            "event_start": ER.day_date(detail.get("event_start"), url) if detail.get("event_start") else ER.unknown_date(),
            "event_end": ER.day_date(detail.get("event_end"), url) if detail.get("event_end") else ER.unknown_date(),
            "ticket_sale_start": _sale_date(detail, "先行受付", url),
            "general_sale_start": _sale_date(detail, "一般発売", url),
            "application_deadline": (ER.day_date(detail["application_deadline"], url)
                                     if detail.get("application_deadline") else ER.unknown_date()),
            "announced_at": ER.unknown_date(),              # 公式発表日時は情報源にない
        },
        # 公式ページの本文は保存しない（規約の転載禁止に配慮）。日程は日付として構造化して持つ。
        "sales": [_sale_item(x, url) for x in detail.get("sales", [])],
        "sales_status": _sales_status(detail),
        "genre_extra": {},   # 主催者名・日程の原文は保存しない（著作権の注意。公演名・日付・会場・公式URLのみ）
    }


def _fingerprint(rec):
    """内容の比較：表示状態（state）と最終確認時刻は含めない。"""
    body = {k: v for k, v in rec.items() if k not in ("state", "last_checked_at")}
    return json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)


def classify(previous, fresh_records, listing_ok):
    """前回の記録と今回の記録から、表示用の状態を決める。
    戻り値: 記録のリスト（前回の記録の保持を含む）。
    - 初回（前回の記録なし）は基準の記録。新着にしない。
    - 一覧から消えた公演は、削除せず「掲載未確認」にする（会期終了とは別）。"""
    prev = {r["id"]: r for r in (previous or [])}
    out = {}
    for rec in fresh_records:
        old = prev.get(rec["id"])
        if previous is None:
            state = "通常"
        elif old is None:
            state = "新着"
        elif _fingerprint(old) != _fingerprint(rec):
            state = "変更"
        else:
            state = "通常"
        rec["state"] = state
        out[rec["id"]] = rec
    if listing_ok:
        for pid, old in prev.items():
            if pid not in out:
                kept = dict(old, state="掲載未確認")
                out[pid] = kept
    return list(out.values())


def write_records(records, failed_urls, path=OUTPUT_JSON, generated_at=None):
    payload = {"schema": ER.SCHEMA_VERSION, "genre": GENRE, "source": SOURCE_NAME,
               "failed_venues": sorted(failed_urls), "records": sorted(records, key=lambda r: r["id"])}
    return ER.write_json_atomic(path, payload)


def load_previous(path=OUTPUT_JSON):
    try:
        with open(path, encoding="utf-8") as f:
            return (json.load(f) or {}).get("records", [])
    except (OSError, ValueError):
        return None


LISTING_DROP_RATIO = 0.5   # 一覧の件数が前回のこの割合未満なら、一覧の取得失敗として扱う
MIN_PREVIOUS_FOR_GUARD = 3  # 前回件数がこれ未満なら、件数の急減を判定しない


def usable_detail(detail):
    """公演ページの解析結果が使えるか。公演名が取れない場合は、ページの構造が変わった疑いとして使わない。"""
    return bool(detail.get("title"))


def collect(fetch=None, sleep=time.sleep, checked_at=None, path=OUTPUT_JSON):
    """一覧と公演ページを取得し、events.json を更新する。fetch(url) -> (成功か, 本文 or 失敗理由)。
    保護：一覧の失敗・0件・件数急減では書き換えない。公演ページの失敗・解析不能・日付の消失は、その公演の前回記録を保持する。
    戻り値: 要約の辞書（ok が False の場合は書き込みなし）。"""
    checked_at = checked_at or datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    previous = load_previous(path)
    prev_by_id = {r["id"]: r for r in (previous or [])}
    ok, listing = fetch(LIST_URL)
    if not ok:
        return {"ok": False, "reason": f"一覧の取得に失敗（{listing}）。書き換えません"}
    urls = parse_listing_links(listing)
    if not urls:
        return {"ok": False, "reason": "一覧に公演リンクがありません（HTML構造の変化の疑い）。書き換えません"}
    if previous and len(previous) >= MIN_PREVIOUS_FOR_GUARD and len(urls) < len(previous) * LISTING_DROP_RATIO:
        return {"ok": False, "reason": f"一覧の件数が急減（前回{len(previous)}件 → 今回{len(urls)}件）。書き換えません"}
    fresh, failed, reasons = [], [], {}
    for i, url in enumerate(urls):
        if i:
            sleep(REQUEST_INTERVAL_SEC)
        ok_d, page = fetch(url)
        pid = slug_id(url)
        if not ok_d:
            failed.append(url); reasons[url] = f"取得失敗（{page}）"
            if pid in prev_by_id:
                fresh.append(dict(prev_by_id[pid]))   # 前回の記録を保持
            continue
        detail = parse_detail(page, url)
        if not usable_detail(detail):
            failed.append(url); reasons[url] = "解析不能（公演名を取得できない）"
            if pid in prev_by_id:
                fresh.append(dict(prev_by_id[pid]))
            continue
        rec = to_record(detail, "通常", checked_at)
        old = prev_by_id.get(pid)
        if old and old["dates"]["event_start"]["precision"] == "day" and rec["dates"]["event_start"]["precision"] == "unknown":
            failed.append(url); reasons[url] = "日付が消えた（解析の変化の疑い）。前回の記録を保持"
            fresh.append(dict(old))
            continue
        fresh.append(rec)
    if previous and len(previous) >= MIN_PREVIOUS_FOR_GUARD and len(fresh) < len(previous) * LISTING_DROP_RATIO:
        return {"ok": False, "reason": f"取得できた公演が急減（前回{len(previous)}件 → {len(fresh)}件）。書き換えません"}
    for rec in fresh:   # 内容が同じ記録は、最終確認時刻も前回の値を保つ（同じ入力で差分を出さない）
        old = prev_by_id.get(rec["id"])
        if old and _fingerprint(old) == _fingerprint(rec):
            rec["last_checked_at"] = old["last_checked_at"]
    records = classify(previous, fresh, listing_ok=True)
    changed = write_records(records, failed, path)
    return {"ok": True, "listed": len(urls), "records": len(records), "failed": failed,
            "failed_reasons": reasons, "changed": changed}


def _requests_fetch(url):
    """戻り値 (成功か, 本文または失敗理由)。HTTPエラーは失敗理由に HTTP ステータスを残す。"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.encoding = "utf-8"
        if r.status_code >= 400:
            return False, f"HTTP {r.status_code}"
        return True, r.text
    except Exception as e:  # 通信の失敗は隠さず、呼び出し元で記録する
        return False, f"通信エラー: {e}"


if __name__ == "__main__":
    summary = collect(fetch=_requests_fetch)
    print(summary)
    sys.exit(0 if summary.get("ok") else 1)
