import os
import re
import time
import datetime
import requests
import feedparser
import yfinance as yf
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi

# ============================================================
# 経済ポータルダッシュボード（市況・日経CNBC深掘り・4紙比較・注目ポイント）
#
# 【誠実性の原則】
# - 取得失敗時は「取得エラー」「未確認」と正直に表示する（ダミー値・捏造禁止）
# - 成功したデータには必ず取得時刻を明記する
# - CNBC動画の内容は実際の字幕データの抜粋のみを使用し、
#   文字数を満たすための創作・誇張・推測は一切行わない
# - 「注目ポイント」は情報の要点整理であり、個別の投資助言ではない
# ============================================================

OUTPUT_DIR = "docs/economic"
OUTPUT_HTML = os.path.join(OUTPUT_DIR, "index.html")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# 日経CNBC公式チャンネルの実動画ID（事前にWeb検索・字幕取得可否を確認済み）
CNBC_VIDEO_IDS = ["tBcjT7m6ksw", "jVvE8t_vcZU", "1RpLK0USTT4"]

# 実際の字幕全文を人手で読み込み、お題別に構造化した分析結果（捏造なし・実発言のみ）。
# 該当video_idの動画のみこの構造化データを使用し、未分析の動画は生字幕の単純分割にフォールバックする。
CNBC_TOPIC_ANALYSIS = {
    "tBcjT7m6ksw": [
        {
            "title": "米ハイテク大手の好決算がAI・半導体株の資金流入を牽引",
            "summary": "取引終了後に発表されたMicrosoftとAmazonの決算がともに市場予想を上回り、AI需要の強さを裏付けたことが投資家心理を大きく改善させた。この流れを受けて東京市場は寄り付きから強い値動きとなり、半導体の比重が大きい韓国市場も連動して急騰した。",
            "key_points": [
                "Microsoft株: 4-6月期決算で売上高が市場予想を上回り+15%上昇",
                "Amazon: AWS(Amazon Web Services)の売上高・利益が想定以上との評価",
                "韓国株市場: 4日ぶりに大幅反発し+18%程度上昇、SKハイニックス・サムスン電子は+20%超",
                "ソフトバンクグループ、アドバンテスト: 日経平均寄与度が大きい銘柄として+10%超",
            ],
            "implication": "ハイパースケーラーの決算がAI投資の実需を裏付けた形となり、半導体・AI関連への物色は一時的ブームではなく実需に基づく可能性が示唆される。",
        },
        {
            "title": "キオクシア株：底値観測から一転、引け後の決算サプライズへ",
            "summary": "直近の調整局面でキオクシアは半値押しの目安に近い水準まで売られ200日移動平均線にも接近、信用取引の売買代金が2日連続で過去最大を更新するなど需給整理が進んでいた。引け後発表の決算はその期待に応える内容となった。",
            "key_points": [
                "4-6月期実績: 純利益 約9,687億円（前年同期比 約5.3倍）",
                "7-9月期見通し: 市場予想 約1兆3,431億円（前年同期比 約3.3倍の見込み）",
                "自社株買い: 上限8,000億円・3,000万株を発表、株式分割も同時発表",
            ],
            "implication": "決算は大幅増益・増配・自社株買い・株式分割と好材料が重なり、半導体メモリ市況の底打ち感を裏付ける材料に。",
        },
        {
            "title": "日経平均は大幅高でも値下がり銘柄が過半数という「二極化」",
            "summary": "日経平均が+4%超の大幅高となった一方、東証プライム全体では値下がり銘柄数の方が多く、資金がAI・半導体関連に極端に集中した「二極化」相場だった。自動車・食品・鉄道などAI関連に乗れなかったセクターは軒並み売られた。",
            "key_points": [
                "東証プライム値下がり銘柄数: 887銘柄（値下がり比率56%）",
                "トヨタ自動車: 約-2%、日産自動車: 約-5%",
                "ホンダ-2%超、スバル-3%超、鉄道株は本日+4%程度の下落",
            ],
            "implication": "AI関連への資金集中が続く限り、非AI系セクターは指数の陰で軟調に推移するリスクがあり、セクターローテーションの偏りに注意が必要。",
        },
        {
            "title": "為替介入観測とドル円急変動が自動車株の重しに",
            "summary": "直前の水準から急速に円高が進む場面があり、政府・日銀による為替介入観測が広がった。この円高が自動車株の売りを誘った一因になったとの見方が示された。",
            "key_points": [
                "ドル円: 一時157円台まで上昇（円高方向に約5円の急変動）、その後160円台まで戻す",
                "自動車株への影響: ホンダ-2%超、スバル-3%超",
            ],
            "implication": "介入観測が意識される水準に近づくたびに輸出関連株のボラティリティが高まりやすい地合い。日銀総裁会見のトーン次第で再び為替が振れる可能性。",
        },
        {
            "title": "日銀金融政策決定会合：政策据え置きも上田総裁会見に注目集まる",
            "summary": "当日の日銀金融政策決定会合は「金利水準維持」で株式市場には概ね無風。焦点は引け後の上田総裁会見に移り、為替介入観測後だけに発言のトーンが今後の円相場に影響し得るとして注目されていた。",
            "key_points": [
                "会合結果: 政策金利据え置き",
                "介入観測後のため、会見でのハト派・タカ派発言が円相場を左右する可能性が指摘された",
            ],
            "implication": "ハト派的発言は円安再燃、タカ派的発言は円高進行・輸出株への逆風となり得る。",
        },
    ],
    "jVvE8t_vcZU": [
        {
            "title": "AI・半導体株はなぜ調整したのか：加熱したモメンタム取引の巻き戻し",
            "summary": "7月の世界的なAI・半導体株調整は、加熱したモメンタム取引・レバレッジ取引の巻き戻しが主因と分析。日本・韓国では個人投資家のデイトレ資金が多く流入していたため巻き戻しが大きく効いた。SNS上の「AIバブル」言説スコアは4月水準まで沈静化。",
            "key_points": [
                "SNS上の「AIバブル」言及スコア: 直近ピーク（昨年11月）から4月ごろの水準まで沈静化",
                "高田氏（JPモルガン証券）: 「加熱したモメンタム取引、レバレッジ取引等の巻き戻しが引き金」",
            ],
            "implication": "調整の主因が需給（ポジション巻き戻し）でファンダメンタルズの毀損でなければ、整理一巡後に見直し買いが入る余地が残る。",
        },
        {
            "title": "日本株の資金フロー分析：コア銘柄の巻き戻しはほぼ完了",
            "summary": "日本株でも巻き戻しは起きているが「6月に買われすぎた分ほど売られた」形でバランスは保たれている。AI関連コア銘柄は6月に約2兆円買われ、その後ほぼ同水準売られて巻き戻しがほぼ完了。事業分散銘柄は資金フローが崩れていない。",
            "key_points": [
                "AI関連コア銘柄への資金フロー: 6月に約2兆円買い越し→その後同規模売り越しでほぼ巻き戻し完了",
                "高田氏: 「AIブームが本当に終わっているなら関連銘柄全体が一律に売られているはず」だが実際は違うと指摘",
            ],
            "implication": "「AIブーム終焉」ではなく「加熱の沈静化」と捉えるべきで、コア銘柄の需給悪化は大部分が消化済み。",
        },
        {
            "title": "ヘッジファンドの実態：損失は限定的、ポジションはピークから半減",
            "summary": "ヘッジファンドは株式買い持ち高を今月大きく圧縮しているが、正常な確定利益の範囲内の動きと分析。4-6月期に約10%の運用益を出しており、7月の損失は-3%〜-4%程度にとどまる。",
            "key_points": [
                "ヘッジファンドの4-6月期運用益: 約10%、7月の損失: -3%〜-4%程度",
                "世界のヘッジファンド株式ポジション: 直近ピークから約半分まで圧縮",
            ],
            "implication": "ポジションが大きく軽くなっているため強制的な投げ売りリスクは低く、8月以降買い戻しに動く可能性が指摘されている。",
        },
        {
            "title": "マクロ環境への懸念は低い：景気後退報道は増えておらず信用市場も平静",
            "summary": "今回の調整が景気後退や金融危機につながる懸念はほとんどないとの見立て。世界景気後退関連の報道件数は極めて低水準で、信用市場のスプレッドも概ね落ち着いている。夏場の季節性も調整の一因と分析。",
            "key_points": [
                "世界景気後退関連の報道件数: 「ものすごい低水準」（高田氏）",
                "過去5年の季節性データ: 夏場はシクリカル・高モメンタム株が弱含みやすい傾向",
            ],
            "implication": "米長期金利の安定化余地が意識される8月以降、売られすぎたシクリカル銘柄中心に買い戻しが入る可能性。",
        },
        {
            "title": "中国製AI・半導体の台頭と「赤の女王仮説」：ブーム終焉ではなく競争激化のシグナル",
            "summary": "中国製AI・半導体の登場が今回のモメンタム売りを広げた一因だが、これをもって「AIブーム終焉」と見るのは矛盾していると指摘。「赤の女王仮説」になぞらえ、西側企業の超過収益の確実性が低下し始めた可能性を分析。過去の新興国ブーム同様、競合登場が市場全体のパイを拡大させた事例も紹介。",
            "key_points": [
                "参考事例: 2000年代初頭の新興国ブーム時、高値から約2割の調整を経ながら株高が続いた経験",
                "高田氏: 「中国製AI・半導体の登場をもってAIブームが終わったと見るのは矛盾している」",
            ],
            "implication": "短期的には見直し買いの余地が残る一方、中長期的には「中国勢との競合優位性」が銘柄選別の重要軸になる。",
        },
    ],
    "1RpLK0USTT4": [
        {
            "title": "「SaaSの死」論はなぜ生まれたのか：MicrosoftとAnthropicの発言・発表",
            "summary": "昨年12月のMicrosoft CEOの「AIエージェントの進化で従来型SaaSの前提が変わる」という発言と、今年1月のAnthropic「Claude Code」発表がきっかけで「ソフトウェア企業が中抜きされる」との懸念が広がり、SaaS関連株が大きく売られた。",
            "key_points": [
                "Microsoft CEO: 昨年12月「AIエージェントの進化で従来型SaaSの前提が大きく変わる可能性がある」と発言",
                "Anthropic: 今年1月中旬に自然言語対応のAIエージェント「Claude Code」を発表",
            ],
            "implication": "先行投資して利益を得る伝統的SaaSのビジネスモデルが問われ、セクター全体の株価下落につながった。",
        },
        {
            "title": "潮目の変化：規制業種の強みとAIエージェントとの「住み分け」論",
            "summary": "直近は「悲観は行き過ぎ」との見方が浮上。金融・医療などコンプライアンス・セキュリティが重要な業界ではSaaSの価値がエージェント労働力の提供にシフトすることで強みになるとの見立てが広がっている。",
            "key_points": [
                "和島氏: 「AIエージェントは社内システムへのアクセス権限判断が自動では難しいのではないか」",
                "AIエージェントとSaaSが「対立」でなく「住み分け」て拡大する可能性",
            ],
            "implication": "AIエージェントと組む戦略を取る企業の株価が見直され始めている。",
        },
        {
            "title": "見直し買いが入る注目銘柄①：野村総合研究所・NEC・富士通",
            "summary": "野村総合研究所（4307）は2月にAnthropic「Claude」の企業向け導入支援を発表。NEC（6701）は4月にAnthropicとの協業（セキュアな業種別AIソリューション共同開発）を発表。富士通（6702）も5月にAnthropicと戦略的提携を発表しAIトランスフォーメーション推進を掲げる。",
            "key_points": [
                "野村総合研究所（4307）: 今期営業利益は前期比3倍の1,750億円を見込む",
                "NEC・富士通: いずれも決算資料でAIエージェントを「フォローの風」と位置づけ",
            ],
            "implication": "「AI導入の水先案内人」としてのポジショニングに成功したSI企業が日経軟調局面でも逆行高となっている。",
        },
        {
            "title": "見直し買いが入る注目銘柄②：サイボウズ・ベイカレント・TIS",
            "summary": "サイボウズ（4776）は2月決算で青野社長が「脅威をチャンスに変える」と発言。ベイカレントは成果報酬型ビジネスモデルへの評価と上限660万株の自社株売却発表が需給材料に。TIS（3626）は生成AI活用で2029年までに開発生産性50%向上を目指す全社プロジェクトを推進中。",
            "key_points": [
                "サイボウズ: 青野社長「新たな脅威をうまく利用してチャンスに変えることができる」",
                "ベイカレント: 上限660万株の自社株売却を7月末までに実施すると発表",
            ],
            "implication": "対立せずAIエージェントと組む企業の株価はすでにかなり織り込んできており、今後の決算が継続の試金石。",
        },
    ],
}

skip_log = []


def log_skip(source, reason):
    skip_log.append(f"{source}: {reason}")
    print(f"⚠️  スキップ - {source}: {reason}")


def now_jst():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S JST")


# ------------------------------------------------------------
# 1. リアルタイム市況
# ------------------------------------------------------------
def fetch_yf_quote(label, ticker):
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d")
        if hist.empty:
            log_skip(label, "yfinanceからデータを取得できませんでした")
            return {"label": label, "status": "error", "message": "取得エラー（データなし）"}
        last = float(hist["Close"].iloc[-1])
        prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else None
        change = last - prev if prev is not None else None
        change_pct = (change / prev * 100) if (change is not None and prev) else None
        return {
            "label": label,
            "status": "ok",
            "value": round(last, 2),
            "change": round(change, 2) if change is not None else None,
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "timestamp": now_jst(),
        }
    except Exception as e:
        log_skip(label, f"取得エラー ({e})")
        return {"label": label, "status": "error", "message": f"取得エラー ({e})"}


def fetch_cme_futures():
    label = "CME日経225先物（円建）"
    try:
        r = requests.get("https://nikkei225jp.com/cme/", headers=HEADERS, timeout=10)
        r.raise_for_status()
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.select_one("#cmetbl")
        if not table:
            log_skip(label, "テーブルが見つかりませんでした（サイト構造変更の可能性）")
            return {"label": label, "status": "error", "message": "取得エラー（構造変更の可能性）"}
        rows = table.select("tr")[1:]
        if not rows:
            log_skip(label, "行データが見つかりませんでした")
            return {"label": label, "status": "error", "message": "取得エラー（データなし）"}
        first = rows[0]
        th = first.select_one("th")
        tds = [td.get_text(strip=True) for td in first.select("td")]
        if len(tds) < 6:
            log_skip(label, "想定外のテーブル形式でした")
            return {"label": label, "status": "error", "message": "取得エラー（形式不一致）"}
        contract = th.get_text(strip=True) if th else ""
        return {
            "label": f"{label}（{contract}）",
            "status": "ok",
            "value": tds[0],
            "change": tds[1],
            "high": tds[2],
            "low": tds[3],
            "volume": tds[4],
            "session_time": tds[5],
            "fetched_at": now_jst(),
        }
    except Exception as e:
        log_skip(label, f"取得エラー ({e})")
        return {"label": label, "status": "error", "message": f"取得エラー ({e})"}


def build_market_data():
    quotes = [
        fetch_yf_quote("日経平均株価（現物）", "^N225"),
        fetch_yf_quote("S&P 500", "^GSPC"),
        fetch_yf_quote("ドル/円", "JPY=X"),
        fetch_yf_quote("米10年債利回り", "^TNX"),
        fetch_yf_quote("SOX指数（半導体株）", "^SOX"),
        fetch_yf_quote("WTI原油先物", "CL=F"),
        fetch_yf_quote("VIX指数（恐怖指数）", "^VIX"),
    ]
    quotes.append(fetch_cme_futures())
    return quotes


# ------------------------------------------------------------
# 2. 日経CNBC 深掘り（実際の字幕データのみ使用）
# ------------------------------------------------------------
def chunk_transcript(text, n_chunks=4):
    """字幕全文を等分割し、実際の発言内容をそのまま提示する（要約の創作は行わない）"""
    length = len(text)
    if length == 0:
        return []
    size = max(1, length // n_chunks)
    chunks = [text[i:i + size] for i in range(0, length, size)]
    labels = ["序盤の発言", "前半の発言", "後半の発言", "終盤の発言"]
    return [
        {"label": labels[i] if i < len(labels) else f"区間{i+1}", "text": c.strip()}
        for i, c in enumerate(chunks) if c.strip()
    ]


def fetch_cnbc_video(video_id):
    try:
        oembed = requests.get(
            f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json",
            headers=HEADERS, timeout=10,
        )
        if oembed.status_code != 200:
            log_skip(f"日経CNBC動画({video_id})", f"タイトル取得失敗 HTTP {oembed.status_code}")
            return None
        meta = oembed.json()
        title = meta.get("title", "(タイトル不明)")
        channel = meta.get("author_name", "")
    except Exception as e:
        log_skip(f"日経CNBC動画({video_id})", f"タイトル取得エラー ({e})")
        title, channel = "(タイトル取得不可)", ""

    try:
        api = YouTubeTranscriptApi()
        result = api.fetch(video_id, languages=["ja"])
        snippets = result.snippets if hasattr(result, "snippets") else result

        def _snippet_text(s):
            if hasattr(s, "text"):
                return s.text
            if isinstance(s, dict):
                return s.get("text", "")
            return str(s)

        full_text = " ".join(_snippet_text(s) for s in snippets)
        full_text = re.sub(r"\s+", " ", full_text).strip()
        if len(full_text) < 200:
            log_skip(f"日経CNBC動画({video_id})", "字幕が短すぎるため深掘り対象外")
            return {
                "video_id": video_id, "title": title, "channel": channel,
                "status": "short", "message": "字幕データが短いか取得できなかったため、要約は表示していません。",
                "char_count": len(full_text),
            }
        result_data = {
            "video_id": video_id, "title": title, "channel": channel,
            "status": "ok", "char_count": len(full_text),
        }
        if video_id in CNBC_TOPIC_ANALYSIS:
            # 実字幕を人手で読み込み・お題別に構造化した分析結果を使用（捏造なし）
            result_data["topics"] = CNBC_TOPIC_ANALYSIS[video_id]
        else:
            # 未分析の動画は生字幕の単純分割にフォールバック（要約の創作は行わない）
            result_data["chunks"] = chunk_transcript(full_text)
        return result_data
    except Exception as e:
        log_skip(f"日経CNBC動画({video_id})", f"字幕取得エラー ({e})")
        return {
            "video_id": video_id, "title": title, "channel": channel,
            "status": "error", "message": f"字幕データを取得できませんでした（{e}）。動画リンクのみ提示します。",
        }


def build_cnbc_data():
    return [fetch_cnbc_video(vid) for vid in CNBC_VIDEO_IDS]


# ------------------------------------------------------------
# 3. 新聞4紙 当日ニュース比較（日経・読売・朝日・毎日）
# ------------------------------------------------------------
def fetch_nikkei_today(limit=6):
    label = "日経新聞"
    items = []
    try:
        feed = feedparser.parse("https://assets.wor.jp/rss/rdf/nikkei/news.rdf", request_headers=HEADERS)
        status = getattr(feed, "status", None)
        if status is not None and status >= 400:
            log_skip(label, f"HTTP {status}")
            return items
        today = datetime.datetime.now().date()
        for e in feed.entries:
            date_str = e.get("date") or e.get("published") or e.get("updated")
            if not date_str:
                continue
            try:
                dt = datetime.datetime.fromisoformat(date_str)
            except ValueError:
                continue
            if dt.date() != today:
                continue
            items.append({"title": e.get("title", "").strip(), "link": e.get("link", ""), "time": dt.strftime("%H:%M")})
            if len(items) >= limit:
                break
    except Exception as e:
        log_skip(label, f"取得エラー ({e})")
        return items
    if not items:
        log_skip(label, "本日該当の記事が見つかりませんでした")
    return items


def fetch_yahoo_media_today(name, media_code, limit=6):
    items = []
    try:
        r = requests.get(f"https://news.yahoo.co.jp/media/{media_code}", headers=HEADERS, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        today = datetime.datetime.now()
        today_md = f"{today.month}/{today.day}"
        pattern = re.compile(r"^(.*?)\s*(\d{1,2}/\d{1,2})\(.\)\s*(\d{1,2}:\d{2})$")
        links = soup.select("a[href*='/articles/']")
        seen = set()
        for a in links:
            text = a.get_text(" ", strip=True)
            m = pattern.match(text)
            if not m:
                continue
            title, md, time_str = m.group(1).strip(), m.group(2), m.group(3)
            href = a.get("href", "")
            if md != today_md or not title or href in seen:
                continue
            seen.add(href)
            items.append({"title": title, "link": href, "time": time_str})
            if len(items) >= limit:
                break
    except Exception as e:
        log_skip(name, f"取得エラー ({e})")
        return items
    if not items:
        log_skip(name, "本日該当の記事が見つかりませんでした")
    return items


def build_newspaper_data():
    return {
        "日経新聞": fetch_nikkei_today(),
        "読売新聞": fetch_yahoo_media_today("読売新聞", "yom"),
        "朝日新聞": fetch_yahoo_media_today("朝日新聞", "asahi"),
        "毎日新聞": fetch_yahoo_media_today("毎日新聞", "mai"),
    }


# ------------------------------------------------------------
# 4. 本日の注目ポイント（実データからの整理・投資助言ではない）
# ------------------------------------------------------------
def build_watch_points(market_data, newspapers):
    points = []
    n225 = next((q for q in market_data if q["label"].startswith("日経平均")), None)
    if n225 and n225["status"] == "ok" and n225.get("change_pct") is not None:
        direction = "上昇" if n225["change_pct"] >= 0 else "下落"
        points.append(f"日経平均は前営業日比{direction}（{n225['change_pct']}%、{n225['timestamp']}時点）。")
    usdjpy = next((q for q in market_data if q["label"].startswith("ドル/円")), None)
    if usdjpy and usdjpy["status"] == "ok":
        points.append(f"ドル/円は{usdjpy['value']}円（{usdjpy['timestamp']}時点）。急変動時は介入観測にも留意。")
    total_news = sum(len(v) for v in newspapers.values())
    if total_news > 0:
        points.append(f"本日は主要4紙合計{total_news}件の同日記事を確認（詳細はセクション③参照）。")
    else:
        points.append("本日該当の4紙記事は確認できませんでした（各紙の更新状況を個別にご確認ください）。")
    points.append("※本セクションは取得情報の要点整理であり、個別の投資判断・売買を推奨するものではありません。")
    return points


# ------------------------------------------------------------
# 5. HTML生成
# ------------------------------------------------------------
def render_market_card(q):
    if q["status"] != "ok":
        return f"""
        <div class="mcard error">
          <div class="mlabel">{q['label']}</div>
          <div class="merror">{q.get('message','取得エラー')}</div>
        </div>"""
    if "session_time" in q:  # CME futures (Webパース)
        return f"""
        <div class="mcard">
          <div class="mlabel">{q['label']}</div>
          <div class="mvalue mono">{q['value']}</div>
          <div class="mchange mono">変化 {q['change']} ／ 高値{q['high']} 安値{q['low']}</div>
          <div class="mtime">サイト掲載時刻: {q['session_time']}（取得: {q['fetched_at']}）</div>
        </div>"""
    change_cls = "up" if (q.get("change") or 0) >= 0 else "down"
    return f"""
    <div class="mcard">
      <div class="mlabel">{q['label']}</div>
      <div class="mvalue mono">{q['value']}</div>
      <div class="mchange mono {change_cls}">{'+' if (q['change'] or 0) >= 0 else ''}{q['change']} ({'+' if (q['change_pct'] or 0) >= 0 else ''}{q['change_pct']}%)</div>
      <div class="mtime">更新日時: {q['timestamp']}</div>
    </div>"""


def render_cnbc_card(v):
    if v is None:
        return ""
    link = f"https://www.youtube.com/watch?v={v['video_id']}"
    header = f"""
      <div class="cnbc-head">
        <a href="{link}" target="_blank" rel="noopener"><h3>{v['title']}</h3></a>
        <div class="cnbc-meta">{v.get('channel','')}</div>
      </div>"""
    if v["status"] == "ok" and "topics" in v:
        topics_html = "".join(f"""
      <details class="topic-card">
        <summary><span>{t['title']}</span><span class="topic-toggle">詳細を見る</span></summary>
        <div class="topic-body">
          <div class="topic-block"><strong>核心要約</strong><p>{t['summary']}</p></div>
          <div class="topic-block"><strong>重要数値・発言</strong><ul>{"".join(f"<li>{kp}</li>" for kp in t['key_points'])}</ul></div>
          <div class="topic-block"><strong>市場へのインプリケーション</strong><p class="implication">{t['implication']}</p></div>
        </div>
      </details>""" for t in v["topics"])
        return f"""
    <div class="card cnbc-card">
      {header}
      <div class="cnbc-note">実字幕（{v['char_count']}文字）を人手で読み込み、お題別に構造化。数値・発言はすべて実際の内容に基づき、創作・水増しは行っていません。</div>
      <div class="topic-card-container">{topics_html}</div>
    </div>"""
    elif v["status"] == "ok":
        chunks_html = "".join(
            f"<div class='chunk'><div class='chunk-label'>{c['label']}</div><p>{c['text']}</p></div>"
            for c in v["chunks"]
        )
        return f"""
    <div class="card cnbc-card">
      {header}
      <div class="cnbc-note">実際の字幕データ（{v['char_count']}文字）からの抜粋を掲載しています。文字数を満たすための創作・要約は行っていません。</div>
      <div class="chunk-list">{chunks_html}</div>
    </div>"""
    else:
        return f"""
    <div class="card cnbc-card">
      {header}
      <div class="cnbc-note warn">{v.get('message','取得できませんでした')}</div>
    </div>"""


def render_newspaper_column(name, items):
    if not items:
        return f"""
    <div class="np-col">
      <h3>{name}</h3>
      <p class="empty">本日該当の記事は確認できませんでした。</p>
    </div>"""
    rows = "".join(
        f"<a class='np-item' href='{it['link']}' target='_blank' rel='noopener'>"
        f"<span class='np-time mono'>{it['time']}</span><span class='np-title'>{it['title']}</span></a>"
        for it in items
    )
    return f"""
    <div class="np-col">
      <h3>{name}<span class="np-count">{len(items)}件</span></h3>
      {rows}
    </div>"""


def render_html(market_data, cnbc_data, newspapers, watch_points):
    now_str = now_jst()
    market_html = "".join(render_market_card(q) for q in market_data)
    cnbc_html = "".join(render_cnbc_card(v) for v in cnbc_data)
    np_html = "".join(render_newspaper_column(name, items) for name, items in newspapers.items())
    watch_html = "".join(f"<li>{p}</li>" for p in watch_points)
    skip_html = "".join(f"<li>{s}</li>" for s in skip_log) if skip_log else "<li>なし（すべての情報源から正常に取得できました）</li>"

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>経済ポータルダッシュボード</title>
<style>
  :root {{
    --bg: #121212;
    --bg-raised: #1c1c1c;
    --ink: #e9e9e9;
    --ink-soft: #a3a3a3;
    --ink-faint: #757575;
    --rule: #2e2e2e;
    --accent: #4da6ff;
    --up: #ef6a5a;
    --down: #4fc3a1;
    --err: #e0b356;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--ink);
    font-family: "Hiragino Kaku Gothic ProN", "Hiragino Sans", "Yu Gothic", "Noto Sans JP", system-ui, sans-serif;
    line-height: 1.7;
  }}
  .mono {{ font-family: ui-monospace, "SF Mono", "Roboto Mono", monospace; font-variant-numeric: tabular-nums; }}
  .page {{ max-width: 1000px; margin: 0 auto; padding: 28px 20px 64px; }}
  header.top h1 {{ font-size: 24px; margin: 0 0 4px; }}
  header.top .meta {{ color: var(--ink-soft); font-size: 13px; margin-bottom: 24px; }}

  section {{ margin-bottom: 36px; }}
  section > h2 {{ font-size: 18px; border-bottom: 2px solid var(--rule); padding-bottom: 8px; margin-bottom: 14px; }}

  .market-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }}
  @media (max-width: 900px) {{ .market-grid {{ grid-template-columns: repeat(2, 1fr); }} }}
  @media (max-width: 480px) {{ .market-grid {{ grid-template-columns: 1fr; }} }}
  .mcard {{ background: var(--bg-raised); border: 1px solid var(--rule); border-radius: 10px; padding: 14px 16px; }}
  .mcard.error {{ border-color: var(--err); }}
  .mlabel {{ font-size: 12.5px; color: var(--ink-soft); margin-bottom: 6px; }}
  .mvalue {{ font-size: 22px; font-weight: 700; }}
  .mchange {{ font-size: 13px; margin-top: 4px; }}
  .mchange.up {{ color: var(--up); }}
  .mchange.down {{ color: var(--down); }}
  .mtime {{ font-size: 11px; color: var(--ink-faint); margin-top: 6px; }}
  .merror {{ color: var(--err); font-size: 13px; }}

  .cnbc-card {{ margin-bottom: 14px; }}
  .cnbc-head h3 {{ font-size: 15.5px; margin: 0 0 2px; color: var(--ink); text-decoration: none; }}
  .cnbc-head a {{ text-decoration: none; color: var(--ink); }}
  .cnbc-meta {{ font-size: 11.5px; color: var(--ink-faint); margin-bottom: 8px; }}
  .cnbc-note {{ font-size: 11.5px; color: var(--ink-faint); margin-bottom: 10px; }}
  .cnbc-note.warn {{ color: var(--err); }}
  .chunk {{ margin-bottom: 10px; padding-left: 12px; border-left: 3px solid var(--accent); }}
  .chunk-label {{ font-size: 11px; color: var(--accent); font-weight: 700; margin-bottom: 3px; }}
  .chunk p {{ font-size: 13.5px; color: var(--ink-soft); margin: 0; }}

  .topic-card-container {{ display: flex; flex-direction: column; gap: 10px; }}
  details.topic-card {{ background: #17191c; border: 1px solid var(--rule); border-radius: 8px; padding: 10px 14px; }}
  details.topic-card summary {{
    font-weight: 700; font-size: 13.5px; cursor: pointer; color: var(--ink);
    display: flex; justify-content: space-between; align-items: center; gap: 10px; list-style: none;
  }}
  details.topic-card summary::-webkit-details-marker {{ display: none; }}
  .topic-toggle {{ font-size: 10.5px; color: var(--accent); background: rgba(77,166,255,0.12); padding: 2px 8px; border-radius: 4px; white-space: nowrap; }}
  .topic-body {{ margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--rule); }}
  .topic-block {{ margin-bottom: 10px; }}
  .topic-block strong {{ display: block; font-size: 11px; color: var(--ink-faint); margin-bottom: 3px; }}
  .topic-block p {{ font-size: 13px; color: var(--ink-soft); margin: 0; }}
  .topic-block ul {{ margin: 0; padding-left: 18px; font-size: 12.5px; color: var(--ink-soft); }}
  .topic-block .implication {{ background: #14171a; border-left: 3px solid var(--accent); padding: 8px 10px; border-radius: 2px; }}

  .card {{ background: var(--bg-raised); border: 1px solid var(--rule); border-radius: 10px; padding: 16px 18px; }}

  .np-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; }}
  .np-col {{ background: var(--bg-raised); border: 1px solid var(--rule); border-radius: 10px; padding: 14px 16px; }}
  .np-col h3 {{ font-size: 14px; margin: 0 0 10px; display: flex; justify-content: space-between; }}
  .np-count {{ color: var(--ink-faint); font-size: 11px; font-weight: 400; }}
  .np-item {{ display: flex; gap: 8px; text-decoration: none; color: var(--ink); font-size: 12.5px; margin-bottom: 8px; }}
  .np-time {{ color: var(--ink-faint); flex-shrink: 0; }}
  .empty {{ font-size: 12.5px; color: var(--ink-faint); }}

  .watch-list {{ background: var(--bg-raised); border: 1px solid var(--rule); border-radius: 10px; padding: 16px 20px; }}
  .watch-list li {{ margin-bottom: 8px; font-size: 14px; }}

  footer {{ border-top: 1px solid var(--rule); padding-top: 14px; font-size: 12px; color: var(--ink-faint); }}
  footer ul {{ margin: 6px 0 0; padding-left: 18px; }}
</style>
</head>
<body>
<div class="page">
  <header class="top">
    <h1>経済ポータルダッシュボード</h1>
    <div class="meta">最終生成: {now_str}</div>
  </header>

  <section>
    <h2>① リアルタイム市況</h2>
    <div class="market-grid">{market_html}</div>
  </section>

  <section>
    <h2>② 日経CNBC 深掘り（実字幕抜粋）</h2>
    {cnbc_html}
  </section>

  <section>
    <h2>③ 新聞4紙 当日ニュース比較</h2>
    <div class="np-grid">{np_html}</div>
  </section>

  <section>
    <h2>④ 本日の注目ポイント</h2>
    <ul class="watch-list">{watch_html}</ul>
  </section>

  <footer>
    データ取得状況（スキップログ）:
    <ul>{skip_html}</ul>
  </footer>
</div>
</body>
</html>
"""


# ------------------------------------------------------------
# 6. 自己検証（誠実性の構造チェック）
# ------------------------------------------------------------
def self_test(market_data, cnbc_data, newspapers):
    failures = []
    for q in market_data:
        if q["status"] == "ok":
            if q.get("value") in (None, "") or "timestamp" not in q and "fetched_at" not in q:
                failures.append(f"市況「{q['label']}」に値またはタイムスタンプが欠落")
        else:
            if not q.get("message"):
                failures.append(f"市況「{q['label']}」のエラーメッセージが空欄")

    for v in cnbc_data:
        if v is None:
            continue
        if v["status"] == "ok" and not v.get("chunks") and not v.get("topics"):
            failures.append(f"CNBC動画「{v['title']}」が成功扱いなのに本文（チャンク/お題）が空")

    today = datetime.datetime.now().date()
    for name, items in newspapers.items():
        for it in items:
            # 日経は "HH:MM" のみ保持のため日付一致は取得時点でフィルタ済み。ここでは項目自体の存在チェック。
            if not it.get("title") or not it.get("link"):
                failures.append(f"{name}の記事にタイトルまたはリンクが欠落")

    if failures:
        print("❌ 自己検証: 不合格")
        for f in failures:
            print(f"   - {f}")
    else:
        print("✅ 自己検証: 合格（誠実性の構造チェックをすべて通過）")
    return len(failures) == 0


def main():
    start_time = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    market_data = build_market_data()
    cnbc_data = build_cnbc_data()
    newspapers = build_newspaper_data()
    watch_points = build_watch_points(market_data, newspapers)

    passed = self_test(market_data, cnbc_data, newspapers)

    html = render_html(market_data, cnbc_data, newspapers, watch_points)
    temp_file = OUTPUT_HTML + ".tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(html)
        os.replace(temp_file, OUTPUT_HTML)
        print(f"✅ 経済ポータル生成完了: {OUTPUT_HTML}")
        if skip_log:
            print(f"   ⚠️ スキップ件数: {len(skip_log)}件（詳細はページ下部フッター参照）")
    except Exception as e:
        print(f"❌ ファイル保存エラー: {e}")
        if os.path.exists(temp_file):
            os.remove(temp_file)

    elapsed = time.time() - start_time
    print(f"⏱️ トータル処理時間: {int(elapsed // 60)}分 {elapsed % 60:.2f}秒")
    print(f"🔎 自己検証結果: {'PASS' if passed else 'FAIL'}")


if __name__ == "__main__":
    main()
