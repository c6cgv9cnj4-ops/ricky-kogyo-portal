import os
import re
import time
import html as html_lib
import datetime
import urllib.parse
import requests
import feedparser
from bs4 import BeautifulSoup

# ============================================================
# 都内 美術館・写真展 データベース（ダッシュボード）
#
# 【誠実性についての注記】
# 「障害者手帳割引」は、各展覧会の個別ページに自由記述の文章で書かれて
# おり、館・展覧会ごとに書式がバラバラで、確実な自動判定ができない。
# 誤った割引情報を表示するリスクの方が「未確認」表示より害が大きいため、
# 確認できたもの以外は「公式サイトで要確認」と正直に表示する（捏造しない）。
# ============================================================

OUTPUT_DIR = "docs/culture-exhibition"
OUTPUT_HTML = os.path.join(OUTPUT_DIR, "index.html")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

skip_log = []


def log_skip(source, reason):
    skip_log.append(f"{source}: {reason}")
    print(f"⚠️  スキップ - {source}: {reason}")


# 障害者手帳の割引情報：各施設の公式ページ・公式発表を実際に確認した内容のみを記載。
# 「公式ページ直接確認」は該当施設の一次情報ページを直接取得して確認したもの。
# 「複数情報源で一致」は公式ページの直接取得はできなかったが、複数の第三者情報源が
# 同一内容を報告しており、蓋然性が高いと判断したもの（断定はしない）。
DISCOUNT_INFO = {
    "国立新美術館": "本人・付添者1名 無料（公式ページ直接確認）",
    "国立西洋美術館": "本人・付添者1名 無料、常設展・企画展とも（公式ページ直接確認）",
    "アーティゾン美術館": "本人・付添者1名 無料、予約不要（公式チケットページ直接確認）",
    "東京都美術館": "本人・介護者1名 無料とみられる（複数情報源で一致、公式ページでの料金記載は未確認）",
    "森美術館": "本人・介助者1名 無料とみられる（公式ニュースページ複数で確認）",
    "東京都写真美術館": "本人・介護者2名まで無料とみられる（展覧会により異なる場合ありと公式に明記）",
    "東京国立博物館": "総合文化展は本人・介護者各1名無料（公式ページ確認）。特別展は個別に取得困難（要アクセス）",
    "渋谷区立松濤美術館": "本人・付添者各1名 無料（公式FAQ確認）",
    "根津美術館": "本人・同伴者1名まで各200円引き（複数情報源で一致）",
    "サントリー美術館": "本人・介助者1名 無料（公式ページ実地確認、2026-08-02）",
    "江戸東京たてもの園": "本人・付添者2名まで無料（公式ページ直接確認、2026-08-03）",
    "葛西臨海水族園": "本人・付添者1名（原則）無料（公式チケットページ直接確認、2026-08-03）",
    "恩賜上野動物園": "本人・付添者1名（原則）無料（公式チケットページ直接確認、2026-08-03）",
    "多摩動物公園": "本人・付添者1名（原則）無料（公式チケットページ直接確認、2026-08-03）",
    "井の頭自然文化園": "本人・付添者1名（原則）無料（公式チケットページ直接確認、2026-08-03）",
    "神代植物公園": "本人・付添者1名（原則）無料（公式ページ直接確認、2026-08-03）",
    "夢の島熱帯植物館": "本人・付添者1名 無料（受給者証は対象外、公式ページ直接確認、2026-08-03）",
}


# ============================================================
# 施設規定を個別項目に適用する方式。"推定"（既定）＝個別確認なしでは確認済みにしない／"確認済み"＝施設規定の範囲内で確認済みとする
FACILITY_RULE_MODE = "施設種別別"  # 推奨: 入園・入館の常設規定は確認済み、展覧会型の美術館は推定
# 施設種別別: 入園・入館の常設規定（料金が展覧会ごとに変わらない）は確認済み、展覧会型の美術館は個別料金があるため推定
FACILITY_KIND_MODE = {
    "恩賜上野動物園": "確認済み", "多摩動物公園": "確認済み", "葛西臨海水族園": "確認済み", "井の頭自然文化園": "確認済み",
    "神代植物公園": "確認済み", "夢の島熱帯植物館": "確認済み", "江戸東京たてもの園": "確認済み",
    "国立新美術館": "推定", "国立西洋美術館": "推定", "アーティゾン美術館": "推定", "渋谷区立松濤美術館": "推定",
    "東京国立博物館": "推定", "東京都美術館": "推定", "森美術館": "推定", "東京都写真美術館": "推定", "根津美術館": "推定",
}

# ---- 公式の確認結果（引用文は取得した本文に含まれることを検証済み。検証は CULTURE_FIX_TEST で実施）----
# kind: 展覧会 / 上映 / 施設規定。scope: 施設規定の適用範囲
OFFICIAL_CHECKED_AT = "2026-10-14"  # OFFICIAL_FACTS の内容を公式ページで確認した日
OFFICIAL_FACTS = {
    # 個別ページ（展覧会・上映）
    "https://topmuseum.jp/exhibition/5423/": dict(venue="東京都写真美術館", kind="展覧会", scope="個別", subject="本人（障害者手帳）", companion="介護者2名まで無料", fee="無料", quote="中学生以下および障害者手帳をお持ちの方とその介護者（2名まで）無料"),
    "https://topmuseum.jp/exhibition/5421/": dict(venue="東京都写真美術館", kind="展覧会", scope="個別", subject="本人（障害者手帳）", companion="介護者2名まで無料", fee="無料", quote="中学生以下および障害者手帳をお持ちの方とその介護者（2名まで）は無料"),
    "https://topmuseum.jp/movie/12613/": dict(venue="東京都写真美術館", kind="上映", scope="個別", subject="本人（障害者手帳）", companion="介護者2名まで1名につき1,000円", fee="1,000円", quote="障害者手帳をお持ちの方（介護者2名まで1名につき）1,000円"),
    "https://topmuseum.jp/movie/13035/": dict(venue="東京都写真美術館", kind="上映", scope="個別", subject="本人（障害者手帳）", companion="介護者2名まで1名につき1,000円", fee="1,000円", quote="障害者手帳をお持ちの方（介護者2名まで1名につき）1,000円"),
    "https://topmuseum.jp/movie/13025/": dict(venue="東京都写真美術館", kind="上映", scope="個別", subject="本人（障害者手帳）", companion="介護者2名まで1名につき1,000円", fee="1,000円", quote="障害者手帳をお持ちの方（介護者2名まで1名につき）1,000円"),
    "https://topmuseum.jp/exhibition/12474/": dict(venue="東京都写真美術館", kind="展覧会", scope="個別", subject="一般と同じ", companion="該当記載なし", fee="無料", quote="料金：無料"),
    "https://topmuseum.jp/exhibition/5478/": dict(venue="東京都写真美術館", kind="展覧会", scope="個別", subject="不明", companion="不明", fee="料金未定", quote="料金：未定 詳細が決まり次第掲載します"),
    "https://www.nact.jp/exhibition_special/2026/louvre2026/": dict(venue="国立新美術館", kind="展覧会", scope="個別", subject="本人（障害者手帳等）", companion="付添者1名まで無料", fee="無料", quote="中学生以下、障害者手帳等をお持ちの方とその付添者 1 名は無料です"),
    "https://www.nact.jp/exhibition_special/2026/shojomanga/": dict(venue="国立新美術館", kind="展覧会", scope="個別", subject="本人（障害者手帳等）", companion="付添者1名まで無料", fee="無料", quote="中学生以下、障害者手帳等をお持ちの方とその付添者 1 名は無料です"),
    "https://www.nezu-muse.or.jp/jp/exhibitions/current/": dict(venue="根津美術館", kind="展覧会", scope="個別", subject="本人（障害者手帳提示者）と同伴者", companion="同伴者（人数の記載なし）", fee="200円引き", quote="障害者手帳提示者および同伴者は200円引き、高校生以下は無料"),
    "https://www.tobikan.jp/exhibition/2026_britishmuseum.html": dict(venue="東京都美術館", kind="展覧会", scope="個別", subject="本人（障害者手帳）", companion="介護者1名まで無料", fee="無料", quote="身体障害者手帳・愛の手帳・療育手帳・精神障害者保健福祉手帳・被爆者健康手帳をお持ちの方とその介護者（1名まで）は無料"),
    "https://www.tobikan.jp/exhibition/2026_orsay.html": dict(venue="東京都美術館", kind="展覧会", scope="個別", subject="本人（障害者手帳）", companion="介護者1名まで無料", fee="無料", quote="身体障害者手帳・愛の手帳・療育手帳・精神障害者保健福祉手帳・被爆者健康手帳をお持ちの方とその介護者（1名まで）は無料"),
    "https://www.tobikan.jp/exhibition/2026_viewsofthisplace.html": dict(venue="東京都美術館", kind="展覧会", scope="個別", subject="本人（障害者手帳）", companion="介護者1名まで無料", fee="無料", quote="身体障害者手帳・愛の手帳・療育手帳・精神障害者保健福祉手帳・被爆者健康手帳をお持ちの方とその介護者（1名まで）は無料"),
    # 施設規定（展覧会・入園などの一般規定。個別項目には推定として適用）
    "https://www.nact.jp/ticket/index.html": dict(venue="国立新美術館", kind="施設規定", scope="企画展示室・展覧会（施設規定）", subject="本人（障害者手帳等）", companion="付添者1名まで無料", fee="無料", quote="中学生以下、障害者手帳等をお持ちの方とその付添者1名は無料です"),
    "https://www.artizon.museum/ticket/": dict(venue="アーティゾン美術館", kind="施設規定", scope="施設（チケット案内）", subject="本人（障がい者手帳）", companion="付き添いの方1名まで無料", fee="無料", quote="障がい者手帳をお持ちの方と付き添いの方1名 無料"),
    "https://www.nmwa.go.jp/jp/visit/index.html": dict(venue="国立西洋美術館", kind="施設規定", scope="施設（入館案内）", subject="本人（障害者手帳）", companion="付添者1名まで無料", fee="無料", quote="障害者手帳*をお持ちの方とその付添者1名は無料"),
    "https://shoto-museum.jp/visit/": dict(venue="渋谷区立松濤美術館", kind="施設規定", scope="施設（入館案内）", subject="本人（障がい者手帳等）", companion="付添の方各1名まで無料", fee="無料", quote="障がい者とその付添の方各1名は無料です"),
    "https://www.tatemonoen.jp/raien/holiday.php": dict(venue="江戸東京たてもの園", kind="施設規定", scope="施設（来園案内）", subject="本人（障害者手帳）", companion="付き添いの方2名まで無料", fee="無料", quote="身体障害者手帳・愛の手帳・療育手帳・精神障害者保健福祉手帳・被爆者健康手帳をお持ちの方とその付き添いの方(2名まで)は無料"),
    "https://www.tokyo-zoo.net/ueno/visitor-info/tickets/index.html": dict(venue="恩賜上野動物園", kind="施設規定", scope="施設（入園）", subject="本人（手帳）", companion="付添（原則1名）無料", fee="無料", quote="ご本人と付添の方（原則1名）が無料"),
    "https://www.tokyo-zoo.net/tama/visitor-info/tickets/index.html": dict(venue="多摩動物公園", kind="施設規定", scope="施設（入園）", subject="本人（手帳）", companion="付添者（原則1名）無料", fee="無料", quote="その付添者（原則1名）は無料です"),
    "https://www.tokyo-zoo.net/kasai/visitor-info/tickets/index.html": dict(venue="葛西臨海水族園", kind="施設規定", scope="施設（入園）", subject="本人（手帳）", companion="付添の方（原則1名）無料", fee="無料", quote="ご本人と付添の方（原則1名）が無料"),
    "https://www.tokyo-zoo.net/inokashira/visitor-info/tickets/index.html": dict(venue="井の頭自然文化園", kind="施設規定", scope="施設（入園）", subject="本人（手帳）", companion="付添者（原則1名）無料", fee="無料", quote="その付添者（原則1名）は無料です"),
    "https://www.tokyo-park.or.jp/park/jindai/index.html": dict(venue="神代植物公園", kind="施設規定", scope="施設（入園）", subject="本人（手帳）", companion="付添（原則1名まで）無料", fee="無料", quote="付添の方は原則1名まで無料"),
    "https://www.yumenoshima.jp/botanicalhall/access": dict(venue="夢の島熱帯植物館", kind="施設規定", scope="施設（入館）", subject="本人（障害者手帳）", companion="付添者1名まで無料", fee="無料", quote="付添者1名は無料です"),
    "https://www.tnm.jp/modules/r_free_page/index.php?id=113": dict(venue="東京国立博物館", kind="施設規定", scope="平常展（東博コレクション展）のみ", subject="障害者", companion="介護者1名まで無料", fee="無料", quote="障害者とその介護者各1名 東博コレクション展（平常展）が無料です"),
}
# 施設規定の適用除外（室）・対象外の種別
FACILITY_EXCLUDED_ROOMS = {"東京都写真美術館": {"B1F", "地下1階", "地下１階"}}
FACILITY_FACT_BY_VENUE = {
    "東京都写真美術館": "https://topmuseum.jp/guide/",  # 施設規定（展覧会・B1F除外）は下の FACILITY_OVERRIDES で定義
    "国立新美術館": "https://www.nact.jp/ticket/index.html",
    "アーティゾン美術館": "https://www.artizon.museum/ticket/",
    "国立西洋美術館": "https://www.nmwa.go.jp/jp/visit/index.html",
    "渋谷区立松濤美術館": "https://shoto-museum.jp/visit/",
    "江戸東京たてもの園": "https://www.tatemonoen.jp/raien/holiday.php",
    "恩賜上野動物園": "https://www.tokyo-zoo.net/ueno/visitor-info/tickets/index.html",
    "多摩動物公園": "https://www.tokyo-zoo.net/tama/visitor-info/tickets/index.html",
    "葛西臨海水族園": "https://www.tokyo-zoo.net/kasai/visitor-info/tickets/index.html",
    "井の頭自然文化園": "https://www.tokyo-zoo.net/inokashira/visitor-info/tickets/index.html",
    "神代植物公園": "https://www.tokyo-park.or.jp/park/jindai/index.html",
    "夢の島熱帯植物館": "https://www.yumenoshima.jp/botanicalhall/access",
    "東京国立博物館": "https://www.tnm.jp/modules/r_free_page/index.php?id=113",
}
# 写真美術館の施設規定（topmuseum.jp/guide/）: 展覧会のみ・B1F除外・手帳提示者とその介護者（2名まで）無料
PHOTO_MUSEUM_FACILITY = dict(venue="東京都写真美術館", kind="展覧会", scope="施設規定（展覧会・B1F除外）", subject="本人（障害者手帳提示者）",
    companion="介護者2名まで無料", fee="無料", source="https://topmuseum.jp/guide/",
    quote="＜無料対象＞ 展覧会を無料でご観覧いただけます。B1F展示室は対象外です。")

STATUS_CONFIRMED = "確認済み"
STATUS_ESTIMATE = "推定"
STATUS_UNKNOWN = "未確認"
STATUS_FETCH_FAILED = "取得不能"
STATUS_CONFLICT = "矛盾"


# 施設規定の分類（v4）: 個別の催事に無料を確定する根拠（公式の個別ページ）が無いため、施設規定は常に「推定」に留める。
APPLICABLE_CATEGORIES = {"平常展", "展覧会（施設の一般規定）", "入園型の展示"}
# 入園・入館の常設規定を当てはめる施設（展示以外の参加型イベントは対象外）
ADMISSION_TYPE_VENUES = {"恩賜上野動物園", "多摩動物公園", "葛西臨海水族園", "井の頭自然文化園",
                         "神代植物公園", "夢の島熱帯植物館", "江戸東京たてもの園"}
PARTICIPATION_WORDS = re.compile(r"予約制|ワークショップ|セミナー|体験(会|教室|講座|セミナー|ツアー|型)|講座|ガイド|教室|キャンペーン|謎|スタンプラリー|配布|イベント|実演|飾り|年中行事|夕涼み|ツアー|講演|コンサート|飲み比べ")
EXHIBITION_WORDS = re.compile(r"展示|展")
DATED_PERIOD = re.compile(r"\d{4}\s*[年/.]\s*\d{1,2}\s*[月/.]\s*\d{1,2}")


def facility_category(venue, kind, room, title, period=""):
    """施設規定の分類。タイトル語・日付は「分類のヒント」にだけ使い、無料の確定には使わない。
    分類: 参加型・イベント / 平常展 / 特別展・個別催事 / 上映 / 展覧会（施設の一般規定） / 入園型の展示 / 不明"""
    t = title or ""
    if PARTICIPATION_WORDS.search(t):
        return "参加型・イベント"
    if venue == "東京国立博物館":
        return "平常展" if ("平常展" in t or "コレクション" in t) else "特別展・個別催事"
    if venue == "東京都写真美術館":
        if kind == "上映":
            return "上映"
        if _room_excluded(venue, room):
            return "特別展・個別催事"
        return "展覧会（施設の一般規定）" if kind == "展覧会" else "不明"
    if venue in ADMISSION_TYPE_VENUES:
        return "入園型の展示" if (EXHIBITION_WORDS.search(t) and DATED_PERIOD.search(period or "")) else "不明"
    return "展覧会（施設の一般規定）" if kind == "展覧会" else "不明"


def _facility_scope_ok(venue, kind, room, title, period=""):
    """施設規定を当てはめてよいかの判定。範囲が不明な場合は False（推測で当てはめない）。
    - 参加型のイベント（予約制・体験・配布・キャンペーン等）は、入館料に含まれるか公式に確認できないため対象外
    - 入園型の施設は、日付つきの展示（企画展示など）だけを対象とする（展示名でない項目は対象外）
    - 東京国立博物館は平常展のみ、写真美術館は展覧会（B1F除外）のみ"""
    title = title or ""
    if PARTICIPATION_WORDS.search(title):
        return False
    if venue == "東京国立博物館":
        return "平常展" in title or "コレクション" in title
    if venue == "東京都写真美術館":
        return kind == "展覧会" and not _room_excluded(venue, room)
    if venue in ADMISSION_TYPE_VENUES:
        return bool(EXHIBITION_WORDS.search(title)) and bool(DATED_PERIOD.search(period or ""))
    return True


def _room_excluded(venue, room):
    return bool(room) and room in FACILITY_EXCLUDED_ROOMS.get(venue, set())


def judge_discount(card):
    """card: venue, link, kind(展覧会/上映/その他), room, title, source(手書き表/個別ページ/固定文/記載なし/取得困難), text, general_price
    戻り値: 手帳本人・介護者・一般料金の状態と、表示文、根拠。"""
    venue, link, kind, room, src, text = card["venue"], card["link"], card["kind"], card.get("room", ""), card["source"], card["text"]
    res = dict(手帳本人="未確認", 介護者="未確認", 一般料金=card.get("general_price", "") or "未確認", 根拠URL="", 根拠="", 施設規定="適用なし")
    if venue == "東京都写真美術館":
        if kind == "展覧会" and not _room_excluded(venue, room):
            res["施設規定"] = "推定（施設規定の対象の可能性。個別未確認）"
            res["根拠URL"] = PHOTO_MUSEUM_FACILITY["source"]; res["根拠"] = PHOTO_MUSEUM_FACILITY["quote"]
        elif kind == "展覧会" and _room_excluded(venue, room):
            res["施設規定"] = "適用なし（B1F展示室は対象外）"
        else:
            res["施設規定"] = "適用なし（上映には適用しない）"
    # 1) 個別項目の公式確認（同じURLの確認結果）
    fact = OFFICIAL_FACTS.get(link)
    if fact and fact["scope"] == "個別":
        fee = fact["fee"]
        if "未定" in fee:
            h = STATUS_UNKNOWN + ":料金未定（公式ページ）"
        elif "円引" in fee:
            h = STATUS_CONFIRMED + ":割引" + fee
        elif fee == "無料" and "一般" in fact["subject"]:
            h = STATUS_CONFIRMED + ":一般料金と同じ（無料）"
        else:
            h = STATUS_CONFIRMED + ":" + fee
        res.update(手帳本人=h, 介護者=fact["companion"], 根拠URL=link, 根拠=fact["quote"])
        return _display(res, card)
    # 2) 施設規定（展覧会のみ・除外室あり）。個別に確認できない場合は推定
    if venue == "東京都写真美術館":
        pass  # 施設規定の注記は judge_card の冒頭で設定済み
    else:
        f2 = FACILITY_FACT_BY_VENUE.get(venue)
        if f2 and OFFICIAL_FACTS.get(f2):
            ff = OFFICIAL_FACTS[f2]
            if ff["kind"] == "施設規定":
                # v4: 施設規定は個別の催事に無料を確定しない（個別の公式根拠が無いため）。分類だけを記録し、推定とする
                cat = facility_category(venue, kind, room, card.get("title", ""), card.get("period", ""))
                if cat in APPLICABLE_CATEGORIES:
                    res["施設規定"] = f"推定（分類: {cat}。個別の催事への適用は公式で未確認）"
                    res["根拠URL"] = f2; res["根拠"] = ff["quote"]
                else:
                    res["施設規定"] = f"適用なし（分類: {cat}）"
    # 3) 手書き表（出典URLなし）＝推定。無料とは扱わない
    if src == "手書き表" and res["手帳本人"] == STATUS_UNKNOWN:
        res["手帳本人"] = STATUS_ESTIMATE + ":手書き表（出典未特定）"
        return _display(res, card)
    if src == "手書き表" and res["手帳本人"] != STATUS_UNKNOWN:
        return _display(res, card)
    # 4) 取得失敗・記載なし
    if src == "取得困難":
        res["手帳本人"] = STATUS_FETCH_FAILED; return _display(res, card)
    if src == "記載なし":
        res["手帳本人"] = STATUS_UNKNOWN + ":ページ読了・記載なし"; return _display(res, card)
    if src == "固定文":
        res["手帳本人"] = STATUS_UNKNOWN + ":固定文は一般料金のみ"; return _display(res, card)
    # 5) 個別ページ由来の本文（生成時に取得）: 手帳節の文字列判定
    if res["手帳本人"] == "未確認":
        res.update(_text_judge(text))
        if res["手帳本人"].startswith(STATUS_CONFIRMED) and card.get("source") == "個別ページ":
            # 根拠は、本文を取得した対象ページ自身（施設規定のページではない）
            res["根拠URL"] = card["link"]
    return _display(res, card)


# 本人（手帳提示者）を指す語。「障害者手帳等をお持ちの方」「障害者手帳をご呈示いただいた方」「手帳提示者」「本人」を含む
HOLDER_PHRASE = re.compile(r"手帳[^、。]{0,12}?(の方|をお持ちの方|提示者|保持者|いただいた方|をご?呈示|をご?提示)|本人")


def _price_gap(text):
    """手帳提示者の料金が一般より低く、割引の文言がない場合の料金差（推定）。"""
    HAND = re.compile(r"手帳|療育|被爆者|愛の手帳|精神障害者")
    hm = HAND.search(text)
    if not hm: return None
    am = re.search(r"([\d,]{3,6})円", text[hm.end():hm.end() + 40])
    gm = re.search(r"一般\s*([\d,]{3,6})円", text)
    if am and gm and int(am.group(1).replace(",", "")) < int(gm.group(1).replace(",", "")):
        return "料金差（手帳" + am.group(1) + "円・一般" + gm.group(1) + "円。割引の文言なし）"
    return None


def _text_judge(text):
    HAND = re.compile(r"手帳|療育|被爆者|愛の手帳|精神障害者")
    GROUP = re.compile(r"中学生以下|高校生以下|高校生|大学生|小学生|学生|未就学児|乳幼児|幼児|子ども|子供|18歳|65歳以上|シニア")
    COMP = re.compile(r"同伴者|付添者|付添の方|付き添いの方|介護者|介助者")
    HOLDER = HOLDER_PHRASE  # 本人（手帳提示者）を指す語
    # 否定文（「無料ではありません」「無料にはなりません」「割引は適用されません」「割引対象外」「割引なし」「除外」）は肯定の根拠にしない
    NEG = re.compile(r"(無料|割引|引き|半額|減免)[^。]{0,10}(ではありません|ではない|ではございません|にはなりません|になりません|対象外|適用されません|適用外|はありません|はできません)|(無料|割引)なし|除外")
    out = {}
    clauses = [c for c in re.split(r"[。\n■●○＊※|｜]|\s{2,}", text) if HAND.search(c) and not NEG.search(c)]
    if not clauses:
        return {"手帳本人": STATUS_UNKNOWN + ":手帳の記載なし（または否定文のみ）", "根拠": ""}
    # 割引: 手帳提示者（本人）の割引だけを採用。同伴者のみ・中学生以下などの割引は除外
    for c in clauses:
        for m in re.finditer(r"円引|割引|半額|減免", c):
            before = c[:m.start()]
            g = max([x.start() for x in GROUP.finditer(before)] or [-1])
            h = max([x.start() for x in HAND.finditer(before)] or [-1])
            cp = max([x.start() for x in COMP.finditer(before)] or [-1])
            if g >= 0 and g > h:
                continue  # 中学生以下などの割引は手帳の割引ではない
            holder_any = HOLDER_PHRASE.search(before)  # 本人を指す語が割引の前に無ければ同伴者のみ
            if cp >= 0 and not holder_any:
                out["手帳本人"] = STATUS_UNKNOWN + ":割引は同伴者のみ"; out["根拠"] = c.strip()[:160]; return out
            am = re.search(r"(\d{2,5})円引", c)
            out["手帳本人"] = STATUS_CONFIRMED + ":割引" + (am.group(1) + "円引" if am else "（額は要確認）")
            out["根拠"] = c.strip()[:160]; return out
    # 無料: 中学生以下・同伴者の無料は本人の無料として扱わない
    for c in clauses:
        for m in re.finditer("無料", c):
            before = c[:m.start()]
            g = max([x.start() for x in GROUP.finditer(before)] or [-1])
            h = max([x.start() for x in HAND.finditer(before)] or [-1])
            cp = max([x.start() for x in COMP.finditer(before)] or [-1])
            if g >= 0 and g > h:
                continue
            if re.search(r"(同伴者|付添者|付添の方|付き添いの方|介護者|介助者)のみ", c[:m.end()]) or (cp >= 0 and not HOLDER.search(before)):
                # 介助者・同伴者の無料で、本人（手帳提示者）を指す語が無い（「介助者のみ無料」など）
                out["手帳本人"] = STATUS_UNKNOWN + ":無料は同伴者・介助者のみ"; out["根拠"] = c.strip()[:160]; return out
            if re.search(r"同伴者[^）)〉]{0,3}[（(〈][^）)〉]*無料", c):
                pd = _price_gap(text)
                out["手帳本人"] = (STATUS_ESTIMATE + ":" + pd) if pd else (STATUS_UNKNOWN + ":無料は同伴者のみ")
                out["根拠"] = c.strip()[:160]; return out
            out["手帳本人"] = STATUS_CONFIRMED + ":無料"; out["根拠"] = c.strip()[:160]; return out
    pd = _price_gap(text)
    if pd:
        return {"手帳本人": STATUS_ESTIMATE + ":" + pd, "根拠": clauses[0].strip()[:160]}
    return {"手帳本人": STATUS_UNKNOWN + ":手帳の文に無料・割引の記載なし", "根拠": clauses[0].strip()[:160]}


def _display(res, card):
    h = res["手帳本人"]
    if h.startswith(STATUS_CONFIRMED + ":無料") or h.startswith(STATUS_CONFIRMED + ":一般料金と同じ"):
        disp = "無料（確認済み）"
    elif h.startswith(STATUS_CONFIRMED + ":割引"):
        disp = "割引（確認済み）: " + h.split(":", 1)[1]
    elif h.startswith(STATUS_CONFIRMED):
        disp = "確認済み: " + h.split(":", 1)[1]
    elif h.startswith(STATUS_ESTIMATE):
        disp = "要確認（推定）"
    elif h.startswith(STATUS_FETCH_FAILED):
        disp = "要確認（取得不能）"
    else:
        disp = "要確認（未確認）"
    res["表示"] = disp
    res["無料表示"] = disp.startswith("無料")
    res["割引表示"] = disp.startswith("割引")
    return res


def period_label(period_text):
    """期間を日付化できない場合は「期間不明」として表示対象に残す（既存の終了フィルターで消さない）。"""
    if re.search(r"20\d\d年\s*\d{1,2}月\s*\d{1,2}日", period_text or ""):
        return period_text
    return "期間不明（表示は残す）"


def discount_kind(link):
    """URLの種別: 映画上映（/movie/）・展覧会（/exhibition/）・その他。種別の混同を防ぐため判定に使う。"""
    if "/movie/" in link: return "上映"
    if "/exhibition/" in link: return "展覧会"
    return "その他"


def discount_room(period, title):
    m = re.search(r"(B1F|地下[１1]階|1F ホール|1F|2F|3F|4F)", (period or "") + " " + (title or ""))
    return m.group(1) if m else ""


def process_discount(item, ticket_discount, page_checked):
    """個別ページ取得結果から障害者本人・介護者の判定を行う。手書き情報（DISCOUNT_INFO）は消さずに保持する。
    item["discount"] には表示用の文言、item["judgment"] には判定結果（状態・根拠URL・施設規定など）を入れる。"""
    legacy = DISCOUNT_INFO.get(item["venue"], "")
    original = item.get("discount", "")
    fact = OFFICIAL_FACTS.get(item.get("link", ""))
    if fact and "未定" in fact.get("fee", "") and item.get("general_price", "") in ("", "料金情報取得中"):
        item["general_price"] = "料金未定（公式ページ）"  # 「取得中」と「未定」を区別する
    if ticket_discount:
        source, text = "個別ページ", ticket_discount
    elif legacy and original == legacy and not page_checked:
        source, text = "手書き表", legacy
    elif page_checked and (original == "取得困難（要アクセス）" or original == legacy):
        # v5: 個別ページを読了して手帳の記載が無い場合は、施設の手書き情報（出典未特定）より優先して「記載なし＝未確認」とする。
        # 手書き表の「推定」は、個別ページを読めなかった場合のみ使う（読了済みなのに推定にすると、確認の無い無料が推定として残る）
        source, text = "記載なし", "公式ページに障害者手帳の記載なし（要現地確認）"
    elif original.startswith("入場無料"):
        source, text = "固定文", original
    elif "記載なし" in original:
        source, text = "記載なし", original
    elif "該当なし" in original:
        source, text = "該当なし", original
    else:
        source, text = "取得困難", original or "取得困難（要アクセス）"
    judge = judge_discount(dict(venue=item["venue"], link=item["link"], kind=discount_kind(item["link"]),
                                room=discount_room(item.get("period", ""), item["title"]), title=item["title"], period=item.get("period", ""),
                                source=source, text=text, general_price=item.get("general_price", "")))
    # 手書き情報との関係を記録する（上書きで矛盾・欠落を残さないため）
    relation = ""
    if legacy and "無料" in legacy and source == "個別ページ" and not judge["無料表示"]:
        relation = "矛盾: 手書き情報は無料、個別ページでは手帳の無料を確認できない"
    judge["手書き情報"] = legacy
    judge["手書き情報との関係"] = relation
    url = judge.get("根拠URL") or (item["link"] if source == "個別ページ" else "")
    judge["確認日"] = ""
    if judge["手帳本人"].startswith("確認済み"):
        if not (isinstance(url, str) and url.startswith(("http://", "https://"))):
            # 根拠URLが無い「確認済み」は確定させない（推定に格下げ）
            judge.update(手帳本人=STATUS_ESTIMATE + ":根拠URLなし", 無料表示=False, 割引表示=False, 表示="要確認（推定）")
        elif judge.get("根拠URL") in OFFICIAL_FACTS:
            judge["確認日"] = OFFICIAL_CHECKED_AT
        elif source == "個別ページ":
            judge["確認日"] = datetime.date.today().isoformat()
    item["judgment"] = judge
    item["discount"] = judge["表示"]
    item["discount_source_url"] = url if judge["手帳本人"].startswith("確認済み") else ""
    item["discount_raw"] = text
    return item


# 最寄駅・徒歩分数：各施設のアクセス案内を実際に調査した内容のみを記載（未調査の施設は空欄のまま＝捏造しない）
ACCESS_INFO = {
    "国立新美術館": "🚶 東京メトロ千代田線「乃木坂駅」6出口 徒歩1分（直結）",
    "国立西洋美術館": "🚶 JR「上野駅」公園口 徒歩1分",
    "アーティゾン美術館": "🚶 東京メトロ「京橋駅」6・7出口 徒歩5分／JR「東京駅」八重洲中央口 徒歩5分",
    "東京都美術館": "🚶 JR「上野駅」公園改札 徒歩7分",
    "森美術館": "🚶 東京メトロ日比谷線「六本木駅」1C出口 徒歩3分（直結）",
    "東京都写真美術館": "🚶 JR「恵比寿駅」東口 徒歩7分",
    "東京国立博物館": "🚶 JR「上野駅」公園口 徒歩10分",
    "フジフイルムスクエア": "🚶 都営大江戸線「六本木駅」8番出口 直結",
    "渋谷区立松濤美術館": "🚶 京王井の頭線「神泉駅」徒歩5分／JR「渋谷駅」徒歩15分",
    "根津美術館": "🚶 東京メトロ「表参道駅」A5出口 徒歩8分",
}


def fetch_nact():
    """国立新美術館"""
    name = "国立新美術館"
    items = []
    base = "https://www.nact.jp/exhibition_and_event/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for li in soup.select("ul.main_box li"):
            a = li.select_one("a")
            title = li.select_one("h2")
            date = li.select_one("p.ex_date")
            status = li.select_one("li.ca_cur")
            if not a or not title:
                continue
            items.append({
                "venue": name, "title": title.get_text(strip=True),
                "period": date.get_text(strip=True) if date else "",
                "status": "開催中" if status else "",
                "link": requests.compat.urljoin(base, a.get("href", "")), "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_tobikan():
    """東京都美術館"""
    name = "東京都美術館"
    items = []
    base = "https://www.tobikan.jp/exhibition/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        # 「過去の展覧会」セクション（id="anchor3"）は対象外にする
        for section in soup.select("section.container"):
            heading = section.select_one("h2")
            if heading and "過去" in heading.get_text():
                continue
            for a in section.select("a.exhibition-item"):
                title = a.select_one(".-title")
                period = a.select_one(".-period")
                if not title:
                    continue
                items.append({
                    "venue": name, "title": title.get_text(" ", strip=True),
                    "period": period.get_text(strip=True) if period else "",
                    "status": "", "link": requests.compat.urljoin(base, a.get("href", "")), "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
                })
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_mori():
    """森美術館"""
    name = "森美術館"
    items = []
    base = "https://www.mori.art.museum/jp/exhibitions/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select(".exhibitionsList a"):
            title = a.select_one(".exhibitions-title")
            period = a.select_one(".exhibitions-date")
            if not title:
                continue
            items.append({
                "venue": name, "title": title.get_text(strip=True),
                "period": period.get_text(strip=True) if period else "",
                "status": "", "link": requests.compat.urljoin(base, a.get("href", "")), "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_artizon():
    """アーティゾン美術館"""
    name = "アーティゾン美術館"
    items = []
    base = "https://www.artizon.museum/exhibition/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        # 「過去の展覧会」セクションは除外し、開催中・開催予定のみ対象にする
        for section in soup.select("section.container"):
            heading = section.select_one("h2")
            if heading and "過去" in heading.get_text():
                continue
            for a in section.select(".case a"):
                title = a.select_one(".exhibitionBox__title")
                period = a.select_one(".exhibitionBox__textDate")
                status = a.select_one(".exhibitionBox__icoStatus")
                if not title:
                    continue
                items.append({
                    "venue": name, "title": title.get_text(strip=True),
                    "period": period.get_text(strip=True) if period else "",
                    "status": status.get_text(strip=True) if status else "",
                    "link": requests.compat.urljoin(base, a.get("href", "")), "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
                })
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_m84():
    """Art Gallery M84（銀座）: アート写真専門ギャラリー。
    公式サイトを確認したところ「成人限定」等の年齢制限記載は見当たらなかったため、
    フラグは付与しない（存在しない注意書きを捏造しないため）。"""
    name = "Art Gallery M84"
    items = []
    base = "http://artgallery-m84.com/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        seen = set()
        for article in soup.select("article.post"):
            a = article.select_one("h1.entry-title a, h2.entry-title a")
            if not a:
                continue
            raw_title = a.get_text(strip=True)
            # 「SOLDグッズ」「展示の様子」等のブログ投稿サフィックスを除いた展覧会名で重複排除
            base_title = re.sub(r'\s*(SOLDグッズ|展示の様子|開催中|終了しました).*$', '', raw_title).strip()
            if not base_title or base_title in seen:
                continue
            seen.add(base_title)
            items.append({
                "venue": name, "title": base_title,
                "period": "", "status": "",
                "link": a.get("href", "") or base, "discount": "取得困難（要アクセス）",
            })
            if len(items) >= 5:
                break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_fujifilm_square():
    """フジフイルムスクエア（写真展・入場無料が基本）"""
    name = "フジフイルムスクエア"
    items = []
    base = "https://fujifilmsquare.jp/event.html"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a.area-link"):
            title = a.select_one(".area-link__title")
            if not title:
                continue
            items.append({
                "venue": name, "title": title.get_text(" ", strip=True),
                "period": "", "status": "",
                "link": requests.compat.urljoin(base, a.get("href", "")),
                "discount": "入場無料（フジフイルムスクエア共通）",
            })
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_via_google_news(venue, query, base_url):
    """公式サイトの構造が未確認の施設向け：Google News RSSから
    「〇〇展」のような実際の報道タイトルのみを抽出する（quoted title以外は採用しない＝ノイズ除去）。
    会期・障害者手帳情報は個別記事ごとに書式が異なり確実な抽出ができないため取得困難（要アクセス）とする。"""
    name = venue
    items = []
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(query) + "&hl=ja&gl=JP&ceid=JP:ja"
    try:
        feed = feedparser.parse(url, request_headers=HEADERS)
        status = getattr(feed, "status", None)
        if status is not None and status >= 400:
            log_skip(name, f"HTTP {status}")
            return items
        seen_titles = set()
        for e in feed.entries[:20]:
            raw_title = re.sub(r'\s*-\s*[^-]+$', '', e.get("title", ""))
            m = re.search(r'「(.+?)」', raw_title)
            if not m:
                continue
            ex_title = m.group(1).strip()
            if not ex_title or ex_title in seen_titles or len(ex_title) < 3:
                continue
            seen_titles.add(ex_title)
            items.append({
                "venue": name, "title": ex_title,
                "period": "", "status": "",
                "link": e.get("link", "") or base_url, "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
            if len(items) >= 6:
                break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_tnm():
    """東京国立博物館：公式トップページの「注目の展示・催し物」欄を直接取得（特別展・本館企画展を含む）"""
    name = "東京国立博物館"
    items = []
    base = "https://www.tnm.jp/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        wrapper = soup.select_one(".top-attention-exhibition__main .exhibition_wrapper")
        if wrapper:
            for block in wrapper.select(".exhibition_item._desc"):
                title = block.select_one("h3.title")
                date = block.select_one("p.date")
                location = block.select_one(".top_location")
                link_a = block.select_one("a.el_btn_link")
                if not title:
                    continue
                title_text = title.get_text(" ", strip=True)
                items.append({
                    "venue": name, "title": title_text,
                    "period": date.get_text(strip=True) if date else "",
                    "status": "開催中" if date and "2026" in date.get_text() else "",
                    "link": requests.compat.urljoin(base, link_a.get("href", "")) if link_a else base,
                    "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
                })
        if not items:
            # フォールバック: Google Newsの報道見出しから抽出（会期は個別ページ取得を試みる）
            items = fetch_via_google_news(name, "東京国立博物館 展覧会", base)
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
        items = fetch_via_google_news(name, "東京国立博物館 展覧会", base)
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_seiyo():
    """国立西洋美術館（Google Newsの報道見出しから抽出。会期は個別ページ取得を試みる）"""
    return fetch_via_google_news("国立西洋美術館", "国立西洋美術館 展覧会", "https://www.nmwa.go.jp/")


def fetch_shoto():
    """渋谷区立松濤美術館：公式トップページの展覧会一覧を直接取得"""
    name = "渋谷区立松濤美術館"
    items = []
    base = "https://shoto-museum.jp/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for box in soup.select(".home_exhibitons_cont .box"):
            icon = box.select_one("p.icon")
            title = box.select_one("h4")
            date = box.select_one("p.date")
            link_a = box.select_one("a")
            if not title:
                continue
            items.append({
                "venue": name, "title": title.get_text(strip=True),
                "period": date.get_text(strip=True) if date else "",
                "status": icon.get_text(strip=True) if icon else "",
                "link": requests.compat.urljoin(base, link_a.get("href", "")) if link_a else base,
                "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_nezu():
    """根津美術館：トップページがAJAX(POST /jp/exhibitions/)で展覧会情報を読み込む構造のため、
    同じPOSTリクエストを直接叩いて実データを取得する"""
    name = "根津美術館"
    items = []
    base = "https://www.nezu-muse.or.jp/"
    try:
        r = requests.post(
            "https://www.nezu-muse.or.jp/jp/exhibitions/",
            headers={**HEADERS, "X-Requested-With": "XMLHttpRequest"},
            data={"at": "ex/top"}, timeout=10,
        )
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.select_one("h3.title")
        date = soup.select_one("p.term")
        link_a = soup.select_one(".btnArea a")
        if title:
            items.append({
                "venue": name, "title": title.get_text(" ", strip=True),
                "period": date.get_text(strip=True) if date else "",
                "status": "", "link": requests.compat.urljoin(base, link_a.get("href", "")) if link_a else base,
                "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_topmuseum():
    """東京都写真美術館"""
    name = "東京都写真美術館"
    items = []
    base = "https://topmuseum.jp/contents/exhibition/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select(".slider__item a"):
            title = a.select_one("em.main")
            date = a.select_one("dd")
            if not title:
                continue
            items.append({
                "venue": name, "title": title.get_text(strip=True),
                "period": date.get_text(" ", strip=True) if date else "",
                "status": "", "link": requests.compat.urljoin(base, a.get("href", "")), "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_momat():
    """東京国立近代美術館"""
    name = "東京国立近代美術館"
    items = []
    base = "https://www.momat.go.jp/exhibitions"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select(".exhibitions-archive-list section.item a"):
            title_attr = a.get("title", "")
            title = re.sub(r'展覧会詳細ページ「(.+?)」を開きます', r'\1', title_attr) or a.get_text(strip=True)
            if not title:
                continue
            items.append({
                "venue": name, "title": title,
                "period": "", "status": "",
                "link": requests.compat.urljoin(base, a.get("href", "")), "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


# 以下3施設は公式サイトがJavaScriptで展覧会一覧を描画するSPA構造のため、
# requestsによる自動取得ができない（生HTMLに実際の展覧会情報が含まれない）。
# ブラウザで実際にレンダリングされた画面を目視確認し、確認できた実データの
# みを手動で記載する。自動更新はできないため、確認日を明記して正直に示す
# （次回のデータ更新には再度ブラウザでの手動確認が必要）。
MANUAL_CHECK_DATE = "2026-08-02"
MANUAL_CHECK_NOTE = f"🔍手動確認 {MANUAL_CHECK_DATE}（JS描画サイトのため自動更新非対応）"


def fetch_mot():
    """東京都現代美術館：公式サイトはJS描画のためrequestsでは取得不可。
    ブラウザで実際にレンダリングされた内容を目視確認して手動記載（{}）。""".format(MANUAL_CHECK_NOTE)
    name = "東京都現代美術館"
    base = "https://mot-art-museum.jp/exhibitions/"
    log_skip(name, f"JS描画サイトのため自動取得不可。{MANUAL_CHECK_NOTE}のブラウザ目視確認データを使用")
    return [
        {
            "venue": name, "title": "MOTコレクション はじめて、びじゅつ",
            "period": f"2026年4月28日 〜 8月16日｜{MANUAL_CHECK_NOTE}", "status": "開催中",
            "link": "https://mot-art-museum.jp/exhibitions/mot-collection-260428/",
            "discount": "取得困難（要アクセス・JS描画サイトのため個別ページ自動確認不可）",
        },
        {
            "venue": name, "title": "多田美波―光、凛と ゆれる",
            "period": f"2026年8月29日 〜 12月6日｜{MANUAL_CHECK_NOTE}", "status": "",
            "link": base, "discount": "取得困難（要アクセス・JS描画サイトのため個別ページ自動確認不可）",
        },
        {
            "venue": name, "title": "共時的星叢──時を共にした星たち　越境する芸術のまなざし",
            "period": f"2026年9月5日 〜 12月13日｜{MANUAL_CHECK_NOTE}", "status": "",
            "link": base, "discount": "取得困難（要アクセス・JS描画サイトのため個別ページ自動確認不可）",
        },
    ]


def fetch_setagaya():
    """世田谷美術館"""
    name = "世田谷美術館"
    items = []
    base = "https://www.setagayaartmuseum.or.jp/exhibition/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a"):
            href = a.get("href", "")
            if "/exhibition/" not in href or href.rstrip("/").endswith("/exhibition"):
                continue
            t = a.get_text(" ", strip=True)
            if not t or len(t) < 4 or len(t) > 80:
                continue
            items.append({
                "venue": name, "title": t,
                "period": "", "status": "",
                "link": requests.compat.urljoin(base, href), "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
            if len(items) >= 5:
                break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_mitsui():
    """三井記念美術館"""
    name = "三井記念美術館"
    items = []
    base = "https://www.mitsui-museum.jp/exhibition/index.html"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.select_one("h1, h2")
        if title:
            items.append({
                "venue": name, "title": title.get_text(strip=True),
                "period": "", "status": "",
                "link": base, "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_mimt():
    """三菱一号館美術館"""
    name = "三菱一号館美術館"
    items = []
    base = "https://mimt.jp/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a"):
            href = a.get("href", "")
            if "/exh/" not in href and "/exhibition" not in href:
                continue
            t = a.get_text(" ", strip=True)
            if not t or len(t) < 4:
                continue
            items.append({
                "venue": name, "title": t,
                "period": "", "status": "",
                "link": requests.compat.urljoin(base, href), "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
            if len(items) >= 3:
                break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_ota_ukiyoe():
    """太田記念美術館"""
    name = "太田記念美術館"
    items = []
    base = "https://www.ukiyoe-ota-muse.jp/exhibition/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.select_one("h1, h2")
        if title:
            items.append({
                "venue": name, "title": title.get_text(strip=True),
                "period": "", "status": "",
                "link": base, "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_matsuoka():
    """松岡美術館"""
    name = "松岡美術館"
    items = []
    base = "https://www.matsuoka-museum.jp/exhibition/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a.exhibition_navA"):
            t = a.get_text(" ", strip=True)
            if not t or len(t) < 4:
                continue
            items.append({
                "venue": name, "title": t,
                "period": "", "status": "",
                "link": requests.compat.urljoin(base, a.get("href", "")), "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
            if len(items) >= 3:
                break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_kusama():
    """草間彌生美術館：公式サイトはJS描画のためrequestsでは取得不可。
    ブラウザで実際にレンダリングされた内容を目視確認して手動記載。"""
    name = "草間彌生美術館"
    base = "https://yayoikusamamuseum.jp/exhibition/"
    log_skip(name, f"JS描画サイトのため自動取得不可。{MANUAL_CHECK_NOTE}のブラウザ目視確認データを使用")
    return [
        {
            "venue": name, "title": "クサマズ・ポップ",
            "period": f"2026年4月16日 〜 8月30日｜{MANUAL_CHECK_NOTE}", "status": "開催中",
            "link": base, "discount": "取得困難（要アクセス・JS描画サイトのため個別ページ自動確認不可）",
        },
    ]


def fetch_itabashi():
    """板橋区立美術館：年度別展覧会スケジュールページ（静的HTML）を直接取得。
    「会期（<p><strong>）」の直後の <ul class="objectlink"> に展覧会名・リンクが
    並ぶ構造になっているため、会期と展覧会名をペアで抽出する。"""
    name = "板橋区立美術館"
    items = []
    base = "https://www.city.itabashi.tokyo.jp/artmuseum/4000016/4002027/index.html"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for strong in soup.select("article#content p strong"):
            period_text = strong.get_text(strip=True)
            p_tag = strong.find_parent("p")
            ul = p_tag.find_next_sibling("ul", class_="objectlink") if p_tag else None
            if not ul:
                continue
            a = ul.select_one("a")
            if not a:
                continue
            items.append({
                "venue": name, "title": a.get_text(strip=True),
                "period": period_text, "status": "",
                "link": requests.compat.urljoin(base, a.get("href", "")), "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
            if len(items) >= 4:
                break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_sompo():
    """SOMPO美術館"""
    name = "SOMPO美術館"
    items = []
    base = "https://www.sompo-museum.org/exhibitions/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for h in soup.select("h2, h3"):
            t = h.get_text(strip=True)
            if t.endswith("展") and len(t) < 40:
                a = h.find_parent("a") or h.find_next("a")
                link = requests.compat.urljoin(base, a.get("href", "")) if a else base
                items.append({
                    "venue": name, "title": t,
                    "period": "", "status": "",
                    "link": link, "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
                })
            if len(items) >= 3:
                break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_toguri():
    """戸栗美術館：トップページのH1に展覧会名と会期が両方含まれる"""
    name = "戸栗美術館"
    items = []
    base = "https://www.toguri-museum.or.jp/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        h1 = soup.select_one("h1, h2")
        if h1:
            text = h1.get_text(strip=True)
            m = re.match(r"(.+?)\s*(\d{4}年\d{1,2}月\d{1,2}日.+)", text)
            title = m.group(1) if m else text
            period = m.group(2) if m else ""
            items.append({
                "venue": name, "title": title.strip("『』 "),
                "period": period, "status": "",
                "link": base, "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_okura():
    """大倉集古館"""
    name = "大倉集古館"
    items = []
    base = "https://www.shukokan.org/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for h in soup.select("h1, h2, h3"):
            t = h.get_text(strip=True)
            if "展" in t and 4 < len(t) < 60 and t != "展覧会":
                items.append({
                    "venue": name, "title": t,
                    "period": "", "status": "",
                    "link": base, "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
                })
                break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_seikado():
    """静嘉堂文庫美術館"""
    name = "静嘉堂文庫美術館"
    items = []
    base = "https://www.seikado.or.jp/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a"):
            t = a.get_text(" ", strip=True)
            if t and "展" in t and 4 < len(t) < 50:
                items.append({
                    "venue": name, "title": t,
                    "period": "", "status": "",
                    "link": requests.compat.urljoin(base, a.get("href", "")), "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
                })
                break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_gotoh():
    """五島美術館"""
    name = "五島美術館"
    items = []
    base = "https://www.gotoh-museum.or.jp/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a"):
            t = a.get_text(" ", strip=True)
            if t and "展" in t and 4 < len(t) < 50:
                items.append({
                    "venue": name, "title": t,
                    "period": "", "status": "",
                    "link": requests.compat.urljoin(base, a.get("href", "")), "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
                })
                break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_shozokan():
    """皇居三の丸尚蔵館"""
    name = "皇居三の丸尚蔵館"
    items = []
    base = "https://shozokan.nich.go.jp/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a"):
            t = a.get_text(" ", strip=True)
            href = a.get("href", "")
            if t and ("展" in t) and 4 < len(t) < 60 and ("exhibi" in href.lower() or "kikaku" in href.lower() or href.startswith("http")):
                items.append({
                    "venue": name, "title": t,
                    "period": "", "status": "",
                    "link": requests.compat.urljoin(base, href), "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
                })
                break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_geidai():
    """東京藝術大学大学美術館"""
    name = "東京藝術大学大学美術館"
    items = []
    base = "https://museum.geidai.ac.jp/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a"):
            t = a.get_text(" ", strip=True)
            href = a.get("href", "")
            if t and "展" in t and 4 < len(t) < 60 and "exhibit" in href.lower():
                items.append({
                    "venue": name, "title": t,
                    "period": "", "status": "",
                    "link": requests.compat.urljoin(base, href), "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
                })
                if len(items) >= 3:
                    break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_toyobunko():
    """東洋文庫ミュージアム"""
    name = "東洋文庫ミュージアム"
    items = []
    base = "https://www.toyo-bunko.or.jp/museum/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for h in soup.select("h1, h2, h3"):
            t = h.get_text(strip=True)
            if t and t not in ("お知らせ", "ご利用案内", "講演会・イベント") and 2 < len(t) < 40:
                a = h.find_parent("a") or h.find_next("a")
                link = requests.compat.urljoin(base, a.get("href", "")) if a else base
                items.append({
                    "venue": name, "title": t,
                    "period": "", "status": "",
                    "link": link, "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
                })
                break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_suntory():
    """サントリー美術館：公式サイトはAkamaiのボット対策により
    requestsでは常に403（Access Denied）となり自動取得不可。
    ブラウザで実際にページ内容を目視確認して手動記載。"""
    name = "サントリー美術館"
    base = "https://www.suntory.co.jp/sma/exhibition/"
    log_skip(name, f"Akamaiボット対策のため自動取得不可（403）。{MANUAL_CHECK_NOTE}のブラウザ目視確認データを使用")
    return [
        {
            "venue": name, "title": "眼のごちそう　食器",
            "period": f"2026年7月8日 〜 8月30日｜{MANUAL_CHECK_NOTE}", "status": "開催中",
            "link": base, "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
        },
    ]


def fetch_nikon_salon():
    """ニコンサロン：公式スケジュールページを実際にブラウザで確認したところ、
    大阪ニコンサロンは既に閉館、銀座ニコンサロンも2026年10月20日をもって
    閉館予定であることが公式に案内されている。カレンダーはJS描画のため
    requestsでの自動取得ができず、閉館という重要な事実を誤って古いまま
    表示し続けるリスクを避けるため、確認できた事実のみを手動で記載する。"""
    name = "ニコンサロン"
    base = "https://nij.nikon.com/activity/exhibition/salon/schedule/"
    log_skip(name, f"JS描画サイトのため自動取得不可。{MANUAL_CHECK_NOTE}のブラウザ目視確認データを使用（閉館情報あり）")
    return [
        {
            "venue": name,
            "title": "【重要】大阪ニコンサロンは閉館済み／銀座ニコンサロンも2026年10月20日閉館予定",
            "period": f"直近の写真展（銀座）: 下川晋平「Neon Calligraphy」9/23〜10/6、三浦健司「十勝晴れ」10/7〜10/20｜{MANUAL_CHECK_NOTE}",
            "status": "", "link": base,
            "discount": "取得困難（要アクセス・JS描画サイトのため個別ページ自動確認不可）",
        },
    ]


def fetch_placem():
    """Place M（新宿）：トップページの「Exhibition information」ブロック
    （#topindex_exbition）を直接取得する"""
    name = "Place M"
    items = []
    base = "http://www.placem.com/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        block = soup.select_one("#topindex_exbition .maintitle a")
        if block:
            lines = [ln.strip() for ln in block.get_text("\n", strip=True).split("\n") if ln.strip()]
            if lines:
                period = lines[0]
                title = " ".join(lines[1:]) if len(lines) > 1 else ""
                items.append({
                    "venue": name, "title": title or period,
                    "period": period, "status": "",
                    "link": requests.compat.urljoin(base, block.get("href", "")), "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
                })
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_epsite():
    """エプソンイメージングギャラリー epSITE：報道（デジカメWatch, 2025年5月2日付）
    により、施設が所在した「エプソンスクエア丸の内」の閉館に伴い、
    2025年5月3日をもって閉廊したことが確認できた。存在しない施設として
    最新情報の取得を試み続けるのは誠実でないため、閉廊の事実を正直に記載する。"""
    name = "エプソンイメージングギャラリー epSITE"
    log_skip(name, "2025年5月3日付で閉廊が確認されたため、以後の展覧会情報取得は行わない")
    return [
        {
            "venue": name,
            "title": "【閉廊】エプサイトギャラリーは施設所在の「エプソンスクエア丸の内」閉館に伴い2025年5月3日をもって閉廊しました",
            "period": f"（報道情報: デジカメWatch 2025年5月2日付）｜{MANUAL_CHECK_NOTE}", "status": "",
            "link": "https://dc.watch.impress.co.jp/docs/news/exhibition/2011541.html",
            "discount": "該当なし（施設閉廊のため）",
        },
    ]


def fetch_shiseido_gallery():
    """資生堂ギャラリー：トップページの「本日のお知らせ」欄（#gl-today-date /
    .today-status / .today-detail）を直接取得する。確認時点で次回展が
    公表されていない場合は、その旨（開催中の展覧会なし）を正直に伝える。"""
    name = "資生堂ギャラリー"
    items = []
    base = "https://gallery.shiseido.com/jp/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        status = soup.select_one(".today-status")
        detail = soup.select_one(".today-detail")
        if detail:
            detail_text = detail.get_text(" ", strip=True)
            link_a = detail.select_one("a")
            title = f"{status.get_text(strip=True)} {detail_text}" if status else detail_text
            items.append({
                "venue": name, "title": title,
                # 空文字のままだと後段の処理でリンク先ページの無関係な会期情報が
                # 誤って補完され、既に終了した展覧会として非表示になってしまう
                # 事故が実際に発生したため、常に非空のプレースホルダを入れておく
                "period": f"確認日: {MANUAL_CHECK_DATE}時点のトップページ掲載内容", "status": "",
                "link": requests.compat.urljoin(base, link_a.get("href", "")) if link_a else base,
                "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_sony_gallery():
    """ソニーイメージングギャラリー（銀座）：「作品展スケジュール」欄を直接取得。
    このページのみShift_JISエンコードのため個別に指定する。"""
    name = "ソニーイメージングギャラリー"
    items = []
    base = "https://www.sony.co.jp/united/imaging/gallery/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "shift_jis"
        soup = BeautifulSoup(r.text, "html.parser")
        sched = soup.select_one("#schedule ul")
        if sched:
            for a in sched.select("li a"):
                title = a.get("aria-label", "")
                date_span = a.select_one(".data")
                if not title:
                    continue
                items.append({
                    "venue": name, "title": title,
                    "period": date_span.get_text(strip=True) if date_span else "", "status": "",
                    "link": requests.compat.urljoin(base, a.get("href", "")), "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
                })
                if len(items) >= 4:
                    break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_leica_gallery_tokyo():
    """ライカギャラリー東京（銀座店内）：ライカオンラインストアのイベント一覧
    （写真展カテゴリ）から「ライカギャラリー東京」名義の展示のみを抽出する
    （ライカGINZA SIX等の他店舗イベントは対象外にする）"""
    name = "ライカギャラリー東京"
    items = []
    base = "https://store.leica-camera.jp/event?category_data_key%5B0%5D=photo_exhibition"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for art in soup.select("article.tabacms_post"):
            img = art.select_one("img[title]")
            if not img or "ライカギャラリー東京" not in img.get("title", ""):
                continue
            date_p = art.select_one("p.fw-bold")
            link_a = art.select_one("a[href^='/event/']")
            items.append({
                "venue": name, "title": img.get("title", "").strip("【】 "),
                "period": date_p.get_text(strip=True) if date_p else "", "status": "",
                "link": requests.compat.urljoin(base, link_a.get("href", "")) if link_a else base,
                "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_om_system_plaza():
    """OM SYSTEM GALLERY（新宿）：2023年に「OM SYSTEM PLAZA」に名称・運営が
    移行しており、最新スケジュールはnote.com上の記事で公開されている。
    ただし各回の展覧会名は画像（見出し画像）内にのみ記載されており、
    altテキストも「OM SYSTEM PLAZA」等の汎用文言のためタイトルを実際に
    抽出することはできない。確認できた会期の事実のみを正直に記載する
    （展覧会名は現地・SNSでの確認が必要）。"""
    name = "OM SYSTEM GALLERY（現OM SYSTEM PLAZA）"
    base = "https://note.com/omsystem_plaza/n/na368ff2c610c"
    log_skip(name, f"名称がOM SYSTEM PLAZAに移行、展覧会名は画像内のため自動取得不可。{MANUAL_CHECK_NOTE}のブラウザ目視確認データを使用")
    return [
        {
            "venue": name,
            "title": "写真展開催中（展覧会名は公式note記事の見出し画像内のみに記載のため自動取得不可）",
            "period": f"2026年7月30日 〜 8月10日｜{MANUAL_CHECK_NOTE}", "status": "",
            "link": base, "discount": "取得困難（要アクセス）",
        },
    ]


def fetch_canon_gallery():
    """キヤノンギャラリー（品川・銀座・大阪）：「開催中／開催予定の写真展」一覧を直接取得"""
    name = "キヤノンギャラリー"
    items = []
    base = "https://personal.canon.jp/showroom/gallery"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        panel = soup.select_one("div.mod-pnl-04")
        if panel:
            for a in panel.select("a.pnl"):
                title = a.select_one(".title span")
                dd = a.select_one(".description dd")
                spans = a.select(".description > span")
                location = spans[0].get_text(strip=True) if spans else ""
                status = spans[1].get_text(strip=True) if len(spans) > 1 else ""
                if not title:
                    continue
                items.append({
                    "venue": name, "title": f"{title.get_text(strip=True)}（{location}）" if location else title.get_text(strip=True),
                    "period": dd.get_text(strip=True) if dd else "",
                    "status": status,
                    "link": requests.compat.urljoin(base, a.get("href", "")), "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
                })
                if len(items) >= 5:
                    break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_2121designsight():
    """21_21 DESIGN SIGHT：公式サイトはJS描画のためrequestsでは取得不可
    （生HTMLに展覧会名・会期が含まれない）。ブラウザで実際にレンダリングされた
    内容を目視確認して手動記載。"""
    name = "21_21 DESIGN SIGHT"
    log_skip(name, f"JS描画サイトのため自動取得不可。{MANUAL_CHECK_NOTE}のブラウザ目視確認データを使用")
    return [
        {
            "venue": name, "title": "企画展「スープはいのち」",
            "period": f"2026年3月27日 〜 8月9日｜{MANUAL_CHECK_NOTE}", "status": "開催中",
            "link": "https://www.2121designsight.jp/program/soup/",
            "discount": "取得困難（要アクセス・JS描画サイトのため個別ページ自動確認不可）",
        },
    ]


def fetch_operacity():
    """東京オペラシティ アートギャラリー：トップページの展覧会サムネイル画像の
    alt属性に「タイトル＋会期」がまとめて記載されているため、そこから抽出する"""
    name = "東京オペラシティ アートギャラリー"
    items = []
    base = "https://www.operacity.jp/ag/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a"):
            href = a.get("href", "")
            if not re.match(r"^/ag/exh\d+/?$", href):
                continue
            img = a.select_one("img")
            if not img:
                continue
            alt = img.get("alt", "")
            m = re.search(r"(\d{4}年\d{1,2}月\d{1,2}日.*)", alt)
            title = alt[:m.start()].strip("　 ") if m else alt.strip()
            if not title:
                continue
            items.append({
                "venue": name, "title": title,
                "period": m.group(1) if m else "", "status": "",
                "link": requests.compat.urljoin(base, href), "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
            if len(items) >= 3:
                break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_yamatane():
    """山種美術館：トップページの「次回の展覧会」ブロックを直接取得"""
    name = "山種美術館"
    items = []
    base = "https://www.yamatane-museum.jp/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        article = soup.select_one("article.o-firstview-article")
        if article:
            status = article.select_one(".c-label-category")
            heading = article.select_one("h3.o-firstview-article__heading")
            sub = article.select_one("p.o-firstview-article__sub")
            term = article.select_one(".o-firstview-article__term")
            link_a = article.select_one("a.c-link-with-icon") or article.select_one("h3 a")
            if heading:
                title = heading.get_text(strip=True)
                if sub:
                    title += " " + sub.get_text(strip=True)
                items.append({
                    "venue": name, "title": title,
                    "period": term.get_text(" ", strip=True) if term else "",
                    "status": status.get_text(strip=True) if status else "",
                    "link": requests.compat.urljoin(base, link_a.get("href", "")) if link_a else base,
                    "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
                })
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_teien():
    """東京都庭園美術館"""
    name = "東京都庭園美術館"
    items = []
    base = "https://www.teien-art-museum.ne.jp/exhibition/"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for h in soup.select("h1, h2, h3"):
            t = h.get_text(strip=True)
            if t and "展" in t and 4 < len(t) < 60:
                a = h.find_parent("a") or h.find_next("a")
                link = requests.compat.urljoin(base, a.get("href", "")) if a else base
                items.append({
                    "venue": name, "title": t,
                    "period": "", "status": "",
                    "link": link, "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
                })
                break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "展覧会情報が見つかりませんでした")
    return items


def fetch_tokyo_zoo_events(name, base_url, event_api_id, limit=3):
    """東京都動物園協会（上野・多摩・井の頭・葛西臨海水族園）共通のJSON API
    （/api/event/event-list.jsp?id=...）を直接取得する。ページ本体はJS描画
    だが、このAPIエンドポイントは認証不要でJSONをそのまま返すためJS実行は
    不要（実機確認済み）。"""
    items = []
    api_url = f"https://www.tokyo-zoo.net/api/event/event-list.jsp?id={event_api_id}"
    try:
        r = requests.get(api_url, headers=HEADERS, timeout=10)
        data = r.json()
        # このAPIには「date」が空の画像アセット項目（ギャラリー画像等、
        # headingTextがファイル名になっている）が混在するため、実際に
        # 開催日が入っている項目のみを実イベントとして扱う
        for e in data.get("items", []):
            if e.get("isCancelled") or not e.get("date"):
                continue
            title = e.get("headingText", "").strip()
            if not title:
                continue
            dates = e.get("date", [])
            period = f"{dates[0]} 〜 {dates[-1]}" if len(dates) > 1 else dates[0]
            link = e.get("url", "")
            items.append({
                "venue": name, "title": title,
                "period": period, "status": "",
                "link": requests.compat.urljoin(base_url, link) if link else base_url,
                "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
            if len(items) >= limit:
                break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "催し物情報が見つかりませんでした")
    return items


def fetch_ueno_zoo():
    return fetch_tokyo_zoo_events("恩賜上野動物園", "https://www.tokyo-zoo.net/ueno/", 2718)


def fetch_tama_zoo():
    return fetch_tokyo_zoo_events("多摩動物公園", "https://www.tokyo-zoo.net/tama/", 3003)


def fetch_inokashira_zoo():
    return fetch_tokyo_zoo_events("井の頭自然文化園", "https://www.tokyo-zoo.net/inokashira/", 3006)


def fetch_kasai_aquarium():
    return fetch_tokyo_zoo_events("葛西臨海水族園", "https://www.tokyo-zoo.net/kasai/", 3004)


def fetch_tatemonoen():
    """江戸東京たてもの園：静的HTMLの催し物スケジュール表を取得する。
    タイトル用の統一クラスが無いため、各<li>内の<p>の出現順
    （1つ目=日付、2つ目=タイトル）で位置的に判定する。"""
    name = "江戸東京たてもの園"
    items = []
    base = "https://www.tatemonoen.jp/event/schedule.php"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for li in soup.select("table.ev_schedule_table li"):
            ps = li.select("p")
            if len(ps) < 2:
                continue
            period_text = ps[0].get_text(strip=True)
            title_text = ps[1].get_text(strip=True)
            if not title_text:
                continue
            a = li.select_one("a")
            link = requests.compat.urljoin(base, a.get("href", "")) if a else base
            items.append({
                "venue": name, "title": title_text,
                "period": period_text, "status": "",
                "link": link, "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
            if len(items) >= 3:
                break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "催し物情報が見つかりませんでした")
    return items


def fetch_jindai_botanical():
    """神代植物公園：東京都公園協会の横断イベント検索ページから、
    class名に「parkjindai」を含む項目のみを抽出する。クエリ無しだと
    空応答になる罠があるため、必ずダミークエリを付与してアクセスする。"""
    name = "神代植物公園"
    items = []
    base = "https://www.tokyo-park.or.jp/event_search/index.html?park=jindai"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for li in soup.select("ul.card-list_items > li"):
            classes = li.get("class", [])
            if not any("parkjindai" in c for c in classes):
                continue
            h3a = li.select_one(".detail h3 a")
            if not h3a:
                continue
            title = h3a.get_text(strip=True)
            date_p = li.select_one(".detail p.date")
            items.append({
                "venue": name, "title": title,
                "period": date_p.get_text(strip=True) if date_p else "", "status": "",
                "link": requests.compat.urljoin(base, h3a.get("href", "")),
                "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
            if len(items) >= 3:
                break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "催し物情報が見つかりませんでした")
    return items


def fetch_yumenoshima():
    """夢の島熱帯植物館：WordPressベースの静的HTML（JS不要）から
    .c-archive__title / .c-archive__date を直接取得する。"""
    name = "夢の島熱帯植物館"
    items = []
    base = "https://www.yumenoshima.jp/botanicalhall/event"
    try:
        r = requests.get(base, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for article in soup.select(".c-archive__article"):
            title_el = article.select_one(".c-archive__title")
            date_el = article.select_one(".c-archive__date")
            link_a = article.select_one("a.c-archive__link") or article.select_one("a")
            if not title_el:
                continue
            items.append({
                "venue": name, "title": title_el.get_text(strip=True),
                "period": date_el.get_text(strip=True) if date_el else "", "status": "",
                "link": requests.compat.urljoin(base, link_a.get("href", "")) if link_a else base,
                "discount": DISCOUNT_INFO.get(name, "取得困難（要アクセス）"),
            })
            if len(items) >= 3:
                break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
    if not items:
        log_skip(name, "催し物情報が見つかりませんでした")
    return items


FETCHERS = [fetch_nact, fetch_tobikan, fetch_mori, fetch_artizon, fetch_fujifilm_square, fetch_topmuseum,
            fetch_tnm, fetch_seiyo, fetch_shoto, fetch_nezu, fetch_m84,
            fetch_momat, fetch_mot, fetch_setagaya, fetch_mitsui, fetch_mimt,
            fetch_ota_ukiyoe, fetch_matsuoka, fetch_kusama, fetch_itabashi,
            fetch_sompo, fetch_toguri, fetch_okura, fetch_seikado, fetch_gotoh,
            fetch_shozokan, fetch_geidai, fetch_toyobunko, fetch_teien, fetch_yamatane, fetch_suntory,
            fetch_operacity, fetch_2121designsight, fetch_canon_gallery, fetch_nikon_salon,
            fetch_om_system_plaza, fetch_leica_gallery_tokyo, fetch_sony_gallery, fetch_shiseido_gallery,
            fetch_epsite, fetch_placem,
            fetch_tatemonoen, fetch_kasai_aquarium, fetch_ueno_zoo, fetch_tama_zoo,
            fetch_inokashira_zoo, fetch_jindai_botanical, fetch_yumenoshima]

# カテゴリ判定は実際に取得したタイトル文字列のキーワードのみで行う（推測での断定はしない）
PHOTO_KEYWORDS = ["写真", "フォト", "Photo", "PHOTO"]
ART_KEYWORDS = ["絵画", "美術", "彫刻", "印象派", "デザイン", "アート"]


def classify_category(title):
    if any(k in title for k in PHOTO_KEYWORDS):
        return "photo", "📷 写真展"
    if any(k in title for k in ART_KEYWORDS):
        return "art", "🎨 美術展"
    return "other", "🖼 展覧会"


# 開催中を優先表示するためのソート優先度（実際に取得できたstatusのみで判定。取得できない場合は中立扱い）
def status_priority(status):
    if "開催中" in status:
        return 0
    if "予約受付中" in status:
        return 0
    if status:
        return 1
    return 2


FULL_DATE_PATTERN = re.compile(r"(\d{4})[年.]\s*(\d{1,2})\s*[月.]\s*(\d{1,2})")
SHORT_DATE_PATTERN = re.compile(r"(?<!\d)(\d{1,2})\s*[月.]\s*(\d{1,2})日?(?!\d)")


def parse_end_date(period_str):
    """会期文字列から終了日を実際の日付として抽出する。
    複数解釈が生じうる曖昧な表記（年をまたぐ短縮形など）は、誤って
    「終了」と判定して非表示にするリスクの方が大きいため、確信が
    持てない場合は None を返し、表示は継続する（安全側に倒す）。"""
    if not period_str:
        return None
    full_matches = list(FULL_DATE_PATTERN.finditer(period_str))
    if len(full_matches) >= 2:
        # 「2026年6月10日 〜 2026年9月21日」のように開始・終了とも年表記がある場合
        y, m, d = full_matches[-1].groups()
        try:
            return datetime.date(int(y), int(m), int(d))
        except ValueError:
            return None
    if len(full_matches) == 1:
        # 「2026年7月4日 〜 8月30日」のように開始日のみ年表記があるケース。
        # 開始日より後ろの位置に短縮形（月日のみ）があれば、それを終了日として扱う。
        # 開始日の前に短縮形がある場合は終了日ではないため対象外（誤判定防止）。
        start = full_matches[0]
        after_text = period_str[start.end():]
        short_after = SHORT_DATE_PATTERN.search(after_text)
        if short_after:
            start_year, start_month = int(start.group(1)), int(start.group(2))
            m, d = int(short_after.group(1)), int(short_after.group(2))
            year = start_year + 1 if m < start_month else start_year
            try:
                return datetime.date(year, m, d)
            except ValueError:
                return None
        # 終了日を示す短縮形が見つからない＝開始日しか分からない場合は、
        # 誤って終了扱いにしないよう None を返す。
        return None
    return None


GENERAL_PRICE_PATTERNS = [
    re.compile(r"一般[^\d]{0,6}([\d,]{3,6})円"),
    re.compile(r"([\d,]{3,6})円[（(]一般[）)]"),
    re.compile(r"当日[^\d]{0,10}一般[^\d]{0,6}([\d,]{3,6})円"),
]

# 会期（開始日・終了日）の日付レンジパターン。西暦4桁の年月日表記のみを対象とし、
# 不確実な短縮表記（月日のみ）は誤読のリスクが高いため対象外とする。
PERIOD_PATTERNS = [
    re.compile(r"(\d{4}年\d{1,2}月\d{1,2}日)[^\d]{0,6}(?:〜|～|-|から)[^\d]{0,6}(\d{4}年\d{1,2}月\d{1,2}日)"),
    re.compile(r"(\d{4}年\d{1,2}月\d{1,2}日)[^\d]{0,6}(?:〜|～|-|から)[^\d]{0,6}(\d{1,2}月\d{1,2}日)"),
]


def fetch_ticket_info(url):
    """展覧会の個別ページから会期・一般観覧料・障害者手帳の割引条件を実際に取得する。
    取得できない場合は正直に None を返す（推測で埋めない）。
    4つ目の戻り値 page_checked は「ページを実際に読み込めたかどうか」を示す。
    これにより「ページは読んだが記載がなかった」（事実）と「そもそも読めなかった」
    （未確認）を区別できるようにし、前者の場合に断定的すぎる表現を避けつつも
    「読んだ結果」を正直に伝えられるようにする。"""
    if not url or not url.startswith("http"):
        return None, None, None, False
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code != 200:
            # 404・5xx は「読めなかった」。「記載なし」と区別するため読了扱いにしない
            return None, None, None, False
        r.encoding = "utf-8"
        # 生HTMLを文字数でスライスするとタグの途中で切れて壊れたHTML断片が
        # 混入する事故につながるため、必ず先にBeautifulSoupでページ全体を
        # 解析してから、タグを含まないクリーンなテキストの上で検索する。
        text = BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)
    except Exception:
        return None, None, None, False
    if len(text) < 300:
        # 本文が極端に短い（エラーページ・準備中・壊れたHTML）は読了とみなさない
        return None, None, None, False

    price = None
    if "無料" in text and re.search(r"(観覧料|入館料|入場料)[^\n]{0,10}無料", text):
        price = "無料"
    else:
        for pattern in GENERAL_PRICE_PATTERNS:
            m = pattern.search(text)
            if m:
                price = f"{m.group(1)}円"
                break

    period = None
    # ページ内のどこか無関係な日付（過去の実績紹介など）を誤って会期と
    # 拾わないよう、「会期」「開催期間」「展示期間」などの語の近傍（前後80文字）
    # に限定して日付レンジを探す。
    period_anchor_idx = None
    for kw in ("会期", "開催期間", "展示期間", "開催日程"):
        i = text.find(kw)
        if i != -1:
            period_anchor_idx = i
            break
    search_scope = text[max(0, period_anchor_idx - 10):period_anchor_idx + 80] if period_anchor_idx is not None else text
    for pattern in PERIOD_PATTERNS:
        m = pattern.search(search_scope)
        if m:
            period = f"{m.group(1)} 〜 {m.group(2)}"
            break

    discount = None
    idx = text.find("障害者手帳")
    if idx == -1:
        idx = text.find("障がい者手帳")
    if idx != -1:
        snippet = text[max(0, idx - 150):idx + 150]
        sentences = re.split(r"(?<=[。.])", snippet)
        for s in sentences:
            if "障害者手帳" in s or "障がい者手帳" in s:
                discount = s.strip()
                break

    return price, discount, period, True


def dedupe_items(items):
    """同じ会場・展覧会名・期間の重複レコードを1件にする。同じURLでも展覧会名が違えば別物として残す。"""
    seen, out = set(), []
    for it in items:
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(it.get("title", "")))).strip()
        key = (str(it.get("venue", "")), title, re.sub(r"\s+", "", str(it.get("period", ""))))
        if key in seen:
            continue
        seen.add(key); out.append(it)
    return out


def build_dataset():
    all_items = []
    for fetcher in FETCHERS:
        all_items.extend(fetcher())
    all_items = dedupe_items(all_items)
    for item in all_items:
        cat, cat_name = classify_category(item["title"])
        item["category"] = cat
        item["category_name"] = cat_name
        item["access"] = ACCESS_INFO.get(item["venue"], "")

        price, ticket_discount, ticket_period, page_checked = fetch_ticket_info(item["link"])
        item["general_price"] = price if price else "料金情報取得中"
        if not item.get("period") and ticket_period:
            # 一覧ページに会期がなかった場合のみ、詳細ページから取得した会期で補完する
            item["period"] = ticket_period
        item["is_free_price"] = bool(price == "無料")
        # 手書き情報を消さずに、個別ページの結果と並べて判定する（施設規定・本人・介護者を分離）
        process_discount(item, ticket_discount, page_checked)

    today = datetime.date.today()
    ended_count = 0
    active_items = []
    for item in all_items:
        end_date = parse_end_date(item.get("period", ""))
        if end_date is not None and end_date < today:
            ended_count += 1
            continue  # 会期終了が実際の日付で確認できたもののみ非表示にする
        active_items.append(item)

    active_items.sort(key=lambda i: status_priority(i["status"]))
    open_count = sum(1 for i in active_items if status_priority(i["status"]) == 0)
    return active_items, ended_count, open_count


# ------------------------------------------------------------
# HTML生成
# ------------------------------------------------------------
def esc(value):
    """動的に取得したテキストをHTMLエスケープする（未知の壊れたタグ混入を防ぐ多重防御）"""
    return html_lib.escape(str(value), quote=True)


def render_card(item):
    item = dict(item)  # 呼び出し元のデータを書き換えない
    for k, v in {"venue": "", "link": "", "title": "", "category": "other", "category_name": "その他",
                 "status": "", "period": "", "access": "", "general_price": "", "discount": "取得困難（要アクセス）"}.items():
        item.setdefault(k, v)
    href = item["link"] if str(item["link"]).startswith(("http://", "https://")) else "#"
    status_badge = f"<span class='status-badge'>{esc(item['status'])}</span>" if item["status"] else ""
    cat_badge = f"<span class='cat-badge cat-{item['category']}'>{esc(item['category_name'])}</span>"
    period_html = f"<div class='period'>{esc(item['period'])}</div>" if item["period"] else "<div class='period muted'>取得困難（要アクセス）</div>"
    # 実際に取得できたstatusが「開催中」または「予約受付中」（＝現在鑑賞・予約可能）の場合のみ「現在開催中」扱いにする
    is_open = "開催中" in item["status"] or "予約受付中" in item["status"]
    access_html = f"<div class='access-badge'>{esc(item['access'])}</div>" if item.get("access") else ""
    price_html = f"<div class='price-badge'>🎟️ {esc(item.get('general_price',''))}</div>"
    data_free_price = "1" if item.get("is_free_price") else "0"
    # 障害者手帳の判定：確認済みの無料・割引だけを1にする（推定・未確認・取得不能は0）
    judge = item.get("judgment") or {}
    disc_free = "1" if judge.get("無料表示") else "0"
    disc_discount = "1" if judge.get("割引表示") else "0"
    state = (judge.get("手帳本人") or "未確認").split(":")[0]
    source_html = ""
    if item.get("discount_source_url") and state == "確認済み":
        source_html = f" <a class='evidence' href='{esc(item['discount_source_url'])}' target='_blank' rel='noopener'>根拠</a>"
    if judge.get("確認日"):
        source_html += f" <span class='checked'>確認日 {esc(judge['確認日'])}</span>"
    relation_html = f"<div class='relation'>{esc(judge['手書き情報との関係'])}</div>" if judge.get("手書き情報との関係") else ""
    return f"""
    <a class="card" href="{esc(href)}" target="_blank" rel="noopener"
       data-venue="{esc(item['venue'])}" data-free="{data_free_price}" data-open="{'1' if is_open else '0'}"
       data-free-price="{data_free_price}" data-category="{item['category']}"
       data-disc-free="{disc_free}" data-disc-discount="{disc_discount}" data-state="{esc(state)}">
      <div class="card-top"><div class="venue-tag">{esc(item['venue'])}</div>{cat_badge}</div>
      <h3 class="title">{esc(item['title'])} {status_badge}</h3>
      {period_html}
      {access_html}
      {price_html}
      <div class="info-row"><span class="info-label">障害者手帳</span><span class="info-value">{esc(item['discount'])}{source_html}</span></div>
      {relation_html}
    </a>"""


def render_html(items):
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    cards_html = "".join(render_card(i) for i in items) if items else "<p class='empty'>展覧会情報を取得できませんでした。</p>"
    skip_html = "".join(f"<li>{s}</li>" for s in skip_log) if skip_log else "<li>なし（すべての施設から正常に取得できました）</li>"

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>都内 美術館・写真展データベース</title>
<style>
  :root {{ --bg:#121212; --bg-raised:#1e1e1e; --ink:#f2f2f2; --ink-soft:#a3a3a3; --rule:#333; --accent:#c9a24b; --free:#4fb08a; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); font-family:"Hiragino Kaku Gothic ProN","Hiragino Sans","Yu Gothic",sans-serif; line-height:1.7; }}
  .page {{ max-width: 1000px; margin:0 auto; padding:28px 20px 64px; }}
  header h1 {{ font-size:22px; margin:0 0 4px; }}
  header .meta {{ font-size:12.5px; color:var(--ink-soft); margin-bottom:16px; }}

  .tabs {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:16px; }}
  .tab-btn {{ background:var(--bg-raised); color:var(--ink); border:1px solid var(--rule); border-radius:999px; padding:7px 14px; font-size:13px; cursor:pointer; }}
  .tab-btn.active {{ background:var(--accent); color:#1a1400; border-color:var(--accent); font-weight:700; }}

  .filters {{ display:flex; gap:8px; margin-bottom:20px; flex-wrap:wrap; }}
  .filter-btn {{ background:var(--bg-raised); color:var(--ink-soft); border:1px solid var(--rule); border-radius:6px; padding:6px 12px; font-size:12px; cursor:pointer; font-weight:600; }}
  .filter-btn.active {{ color:var(--free); border-color:var(--free); }}
  .filter-btn-hot {{ border-color:#e2665a; color:#e2665a; }}
  .filter-btn-hot.active {{ background:linear-gradient(135deg, #e2665a, #c9a24b); color:#1a0d0a; border-color:#e2665a; box-shadow:0 0 10px rgba(226,102,90,0.5); }}

  .grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap:14px; }}
  .card {{ display:block; background:var(--bg-raised); border:1px solid var(--rule); border-radius:10px; padding:16px 18px; text-decoration:none; color:var(--ink); }}
  .card:hover {{ border-color:var(--accent); }}
  .card-top {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; }}
  .venue-tag {{ font-size:11px; color:var(--accent); font-weight:700; }}
  .cat-badge {{ font-size:10px; font-weight:700; padding:2px 8px; border-radius:999px; }}
  .cat-badge.cat-photo {{ background:rgba(6,182,212,0.18); color:#22d3ee; }}
  .cat-badge.cat-art {{ background:rgba(168,85,247,0.18); color:#c084fc; }}
  .cat-badge.cat-other {{ background:rgba(163,163,163,0.18); color:var(--ink-soft); }}
  .title {{ font-size:14.5px; margin:0 0 6px; }}
  .status-badge {{ font-size:10.5px; background:rgba(79,176,138,0.2); color:var(--free); padding:2px 6px; border-radius:4px; }}
  .period {{ font-size:12px; color:var(--ink-soft); margin-bottom:10px; }}
  .period.muted {{ font-style:italic; }}
  .access-badge {{ font-size:11px; color:var(--accent); background:rgba(201,162,75,0.1); padding:4px 8px; border-radius:6px; margin-bottom:8px; display:inline-block; }}
  .price-badge {{ font-size:12px; font-weight:700; color:var(--free); background:rgba(79,176,138,0.1); padding:4px 8px; border-radius:6px; margin-bottom:8px; display:inline-block; }}
  .info-row {{ display:flex; justify-content:space-between; font-size:11.5px; border-top:1px solid var(--rule); padding-top:6px; margin-top:6px; }}
  .info-label {{ color:var(--ink-soft); }}
  .info-value {{ text-align:right; max-width:65%; }}
  .empty {{ color:var(--ink-soft); }}

  footer {{ margin-top:32px; border-top:1px solid var(--rule); padding-top:14px; font-size:12px; color:var(--ink-soft); }}
  footer ul {{ margin:6px 0 14px; padding-left:18px; }}
</style>
</head>
<body>
<div class="page">
  <header>
    <h1>都内 美術館・写真展データベース</h1>
    <div class="meta">最終生成: {now_str}｜取得件数: {len(items)}件</div>
  </header>

  <div class="filters">
    <button class="filter-btn" data-filter="disc-free">♿ 障害者手帳：無料</button>
    <button class="filter-btn" data-filter="disc-discount">♿ 障害者手帳：割引</button>
    <button class="filter-btn" data-filter="cat-art">🎨 美術展</button>
    <button class="filter-btn" data-filter="cat-photo">📷 写真展</button>
    <button class="filter-btn filter-btn-hot" data-filter="open">🔥 現在開催中</button>
  </div>

  <div class="grid" id="grid">{cards_html}</div>

  <footer>
    データ取得状況（スキップログ）:
    <ul>{skip_html}</ul>
    <p>※「障害者手帳」は各展覧会ごとに記載形式が異なり自動判定が不確実なため、確認できたもの以外は正直に「取得困難（要アクセス）」と表示しています（誤った割引情報の掲載を避けるため）。</p>
  </footer>
</div>

<script>
  const filterButtons = document.querySelectorAll(".filter-btn");
  const cards = document.querySelectorAll("#grid .card");
  let activeFilters = new Set();

  function matchesFilter(c, filter) {{
    switch (filter) {{
      case "disc-free": return c.dataset.discFree === "1";
      case "disc-discount": return c.dataset.discDiscount === "1";
      case "cat-art": return c.dataset.category === "art";
      case "cat-photo": return c.dataset.category === "photo";
      case "open": return c.dataset.open === "1";
      default: return true;
    }}
  }}

  function applyFilters() {{
    cards.forEach(c => {{
      let show = true;
      activeFilters.forEach(f => {{ if (!matchesFilter(c, f)) show = false; }});
      c.style.display = show ? "" : "none";
    }});
  }}

  filterButtons.forEach(btn => btn.addEventListener("click", () => {{
    btn.classList.toggle("active");
    if (activeFilters.has(btn.dataset.filter)) activeFilters.delete(btn.dataset.filter);
    else activeFilters.add(btn.dataset.filter);
    applyFilters();
  }}));
</script>
</body>
</html>
"""


def self_test(items):
    failures = []
    if not items:
        failures.append("展覧会情報が1件も取得できていない")
    for i in items:
        if not i.get("title") or not i.get("link"):
            failures.append(f"タイトルまたはリンクが欠落: {i}")
    if failures:
        print("❌ 自己検証: 不合格")
        for f in failures:
            print(f"   - {f}")
    else:
        print("✅ 自己検証: 合格")
    return len(failures) == 0


def quality_check(html, items):
    """生成HTMLの整合性検品。戻り値は不整合の一覧（空なら合格）。"""
    problems = []
    blocks = html.split('<a class="card"')[1:]
    if len(blocks) != len(items):
        problems.append(f"カード数{len(blocks)}件と取得件数{len(items)}件が一致しない")
    for b in blocks:
        head = b.split(">", 1)[0]
        st = re.search(r'data-state="([^"]*)"', head)
        state = st.group(1) if st else ""
        if state not in ("確認済み", "推定", "未確認", "取得不能", "矛盾"):
            problems.append(f"状態属性が不正: {state!r}")
            continue
        if state != "確認済み":
            if 'data-disc-free="1"' in head or 'data-disc-discount="1"' in head:
                problems.append(f"確認済み以外（{state}）で無料・割引フラグが立っている")
            if "無料（確認済み）" in b:
                problems.append(f"確認済み以外（{state}）で「無料（確認済み）」と表示")
        if state == "確認済み" and 'data-disc-free="1"' in head and "class='evidence'" not in b:
            problems.append("確認済みの無料に根拠リンクが無い")
    return problems


def existing_page_counts():
    """既存の公開ページについて (会期中のカード件数, 会期中の確認済み無料件数) を返す。
    会期終了が実際の日付で確認できたものは除く（終了の一斉更新は正当な更新のため比較対象から外す）。
    読めない場合は (0, 0)（比較しない）。"""
    try:
        with open(OUTPUT_HTML, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return 0, 0
    today = datetime.date.today()
    running = free = 0
    for block in text.split('<a class="card"')[1:]:
        m = re.search(r"<div class='period[^']*'>(.*?)</div>", block, re.S)
        end = parse_end_date(html_lib.unescape(m.group(1))) if m else None
        if end is None or end >= today:
            running += 1
            if 'data-disc-free="1"' in block.split(">", 1)[0]:
                free += 1
    return running, free


def main():
    start_time = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    items, ended_count, open_count = build_dataset()
    passed = self_test(items)
    if not passed:
        # 自己検証NGは公開データとして不適格。既存ファイルを残して異常終了する
        print("❌ 自己検証NG: 本番ファイルは書き換えずに終了します")
        raise SystemExit(1)

    # 件数急減ガード: 取得失敗で一部の施設しか残らなかった場合に、既存の公開ページを部分的な内容で上書きしない
    previous, previous_free = existing_page_counts()
    # 運用上の逃げ道: 恒久的な件数減少（サイト改修など）で更新が止まり続けた場合に、担当者が意図的に上書きする
    force = os.environ.get("CULTURE_FORCE_UPDATE") == "1"
    # 下限（FORCEでも解除しない）: 既存の50%未満への急減は全件失敗・大量失敗とみなして止める
    if previous and len(items) < previous * 0.5:
        print(f"❌ 下限ガード: 既存{previous}件 → 今回{len(items)}件（50%未満）。FORCEでも解除しません。本番ファイルは書き換えずに終了します")
        raise SystemExit(1)
    if force and (len(items) < previous * 0.8 or previous_free >= 5):
        print("⚠️ CULTURE_FORCE_UPDATE=1: 件数（50%以上）・無料の急減ガードを無効化して上書きします（担当者の確認済みであること）")
    if not force and previous and len(items) < previous * 0.8:
        print(f"❌ 件数急減ガード: 既存{previous}件 → 今回{len(items)}件（80%未満）。取得失敗の可能性があるため本番ファイルは書き換えずに終了します")
        raise SystemExit(1)
    # 確認済み無料の急減ガード: 詳細ページの取得失敗で、確認済みの無料表示だけが消えるのを防ぐ（件数は残るため件数ガードでは拾えない）
    new_free = sum(1 for i in items if (i.get("judgment") or {}).get("無料表示"))
    if not force and previous_free >= 5 and new_free < previous_free * 0.8:
        print(f"❌ 無料急減ガード: 既存の確認済み無料{previous_free}件 → 今回{new_free}件（80%未満）。詳細ページの取得失敗の可能性があるため本番ファイルは書き換えずに終了します")
        raise SystemExit(1)

    html = render_html(items)

    # 検品: 文言の有無ではなく、データと表示の整合性を見る（「要確認」は正式な状態表示であり、NG条件ではない）
    problems = quality_check(html, items)
    if problems:
        print(f"❌ 検品NG: {len(problems)}件の不整合。本番ファイルは書き換えずに終了します")
        for pr in problems[:20]:
            print(f"   - {pr}")
        raise SystemExit(1)
    print(f"✅ 検品OK: カード{len(items)}件の状態・フラグ・根拠の整合性を確認しました")

    temp_file = OUTPUT_HTML + ".tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(html)
        os.replace(temp_file, OUTPUT_HTML)
        print(f"✅ 生成完了: {OUTPUT_HTML}")
    except Exception as e:
        print(f"❌ ファイル保存エラー: {e}")
        if os.path.exists(temp_file):
            os.remove(temp_file)
        raise SystemExit(1)  # 保存失敗を成功扱いにしない

    by_venue = {}
    for i in items:
        by_venue[i["venue"]] = by_venue.get(i["venue"], 0) + 1
    now_jst = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("📊 施設別取得件数:", by_venue)
    print(f"📥 取得した最新データ数: {len(items) + ended_count}件")
    print(f"🕒 更新完了時刻（JST）: {now_jst}")
    print(f"🟢 現在開催中と判定した数: {open_count}件")
    print(f"🔚 終了して非表示にした数: {ended_count}件（実際の終了日が判明したもののみ。曖昧な会期表記は安全側に倒し表示継続）")
    elapsed = time.time() - start_time
    print(f"⏱️ トータル処理時間: {int(elapsed // 60)}分 {elapsed % 60:.2f}秒")
    print(f"🔎 自己検証結果: {'PASS' if passed else 'FAIL'}")


if __name__ == "__main__":
    main()
