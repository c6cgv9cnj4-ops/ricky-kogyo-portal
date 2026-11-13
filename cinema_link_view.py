"""こうのすシネマの上映情報を、公開表示の「リンク中心」の形に変換する（未接続：本番の表示は変更しない）。

映画.com（eiga.com）の利用条件（第13条）が、情報の複製・再生を事前の同意なく禁止しているため、
公開ページには作品名・上映時刻・監督・出演・作品説明を載せず、件数・掲載期間・公式ページへのリンクだけを載せる。
取得失敗や0件は「正常な空データ」として扱わず、前回の有効な結果を取得日時つきで残す。
"""
import datetime
import html as html_lib

THEATER_NAME = "こうのすシネマ"
SCHEDULE_URL = "https://eiga.com/theater/11/110208/3254/"


def _esc(value):
    return html_lib.escape(str(value), quote=True)


def link_view(films):
    """取得した上映データ（作品と日付・時刻の一覧）から、公開用の要約だけを作る。
    作品名・時刻・出演・監督・作品説明は含めない（件数と掲載期間だけを数える）。"""
    dates = sorted({d["date"] for f in films for d in f.get("days", [])})
    return {
        "theater": THEATER_NAME,
        "url": SCHEDULE_URL,
        "film_count": len(films),
        "date_from": dates[0].isoformat() if dates else None,
        "date_to": dates[-1].isoformat() if dates else None,
    }


def apply_last_good(previous, fresh, now_iso):
    """今回の結果を、表示に使う状態へ変える。
    - 作品が1本以上取得できた場合だけ「取得成功」。
    - 取得失敗・0件は、前回の有効な結果を残し、古いことを明示する（stale=True）。
    - 前回もなければ「取得できていません」（架空の0件にしない）。"""
    if fresh and fresh.get("film_count", 0) > 0:
        return dict(fresh, fetched_at=now_iso, status="取得成功", stale=False)
    if previous and previous.get("film_count", 0) > 0:
        return dict(previous, status="今回取得できず（前回の取得日時を表示）", stale=True)
    return {"theater": THEATER_NAME, "url": SCHEDULE_URL, "film_count": 0, "date_from": None,
            "date_to": None, "fetched_at": None, "status": "取得できていません", "stale": True}


def render_link_card(view):
    """公開用のリンク中心のカード（HTML）。作品の内容は含めない。"""
    if view.get("date_from") and view.get("date_to"):
        period = f"掲載期間 {_esc(view['date_from'])} 〜 {_esc(view['date_to'])}"
    else:
        period = "掲載期間：未確認"
    fetched = view.get("fetched_at") or "未確認"
    return (
        "<div class='cinema-link-card'>"
        f"<div class='cinema-link-title'>{_esc(view.get('theater', THEATER_NAME))} 上映スケジュール</div>"
        f"<div class='cinema-link-meta'>上映作品 {int(view.get('film_count') or 0)}本｜{period}</div>"
        f"<div class='cinema-link-status'>取得状況：{_esc(view.get('status', ''))}｜最終取得：{_esc(fetched)}</div>"
        f"<a class='cinema-link-btn' href='{_esc(view.get('url', SCHEDULE_URL))}' target='_blank' rel='noopener'>"
        "公式の上映スケジュールで確認する</a>"
        "</div>"
    )
