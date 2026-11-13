"""情報源の台帳（個人利用の文化情報ポータル）。

各情報源について、分野・地域・規約の確認状況・取得方法・確認日・根拠を記録する。
- 自動取得は「許可を明示的に確認できた」情報源だけ可能。それ以外は手動確認かCSV取込に限る。
- 判定は規約の原文（または原文に近い引用）を根拠とし、要約だけで確定しない。
- 規約の確認が未了の情報源は、許可済みとは扱わない（auto_allowed = False）。
"""

CHECKED_ON = "2026-10-21"

# 規約の状態：
#   "制限あり"   : 複製・転載・再配布の禁止などの記載がある（自動取得の明示はない）
#   "判断不能"   : 規約を読んだが、自動取得の許可・禁止の記載がない
#   "未確認"     : 規約ページが見つからない、または取得できなかった
#   "許可確認"   : 自動取得を明示的に許可する記載がある（現時点で0件）
STATUSES = ("許可確認", "制限あり", "判断不能", "未確認")

# (ホスト, 分野, 地域, 規約の状態, 根拠URL, 根拠の要旨, 採用方法, 理由)
REGISTRY = [
    ("www.nact.jp", "美術館・展覧会", "関東", "制限あり", "https://www.nact.jp/policy/",
     "著作権者の許可なく複製・送信等を禁止", "手動確認", "展覧会の確認は手動。自動取得の許可なし"),
    ("art-view.roppongihills.com", "美術館・展覧会", "関東", "制限あり", "https://art-view.roppongihills.com/jp/conditions/",
     "権利者の書面による事前承諾なく複製等を行うことはできない", "手動確認", "同上"),
    ("www.artizon.museum", "美術館・展覧会", "関東", "制限あり", "https://www.artizon.museum/terms-of-use/",
     "私的使用を超える複製・転用は禁止", "手動確認", "同上"),
    ("www.fujifilm.com", "美術館・展覧会", "関東", "制限あり", "https://www.fujifilm.com/jp/ja/terms",
     "非営利・個人利用に限ってダウンロード可。改変・切り取りは権利侵害", "手動確認", "同上"),
    ("shoto-museum.jp", "美術館・展覧会", "関東", "制限あり", "https://shoto-museum.jp/terms/",
     "権利者の許可なく複製等を禁止。私的利用の範囲を超える利用は不可", "手動確認", "同上"),
    ("www.nezu-muse.or.jp", "美術館・展覧会", "関東", "制限あり", "https://www.nezu-muse.or.jp/jp/terms/",
     "著作権者の承諾なく複製・転用・頒布を禁止", "手動確認", "同上"),
    ("topmuseum.jp", "美術館・展覧会", "関東", "制限あり", "https://topmuseum.jp/use/",
     "権利者の承諾なく複製・転用を禁止。再配布は許諾しない", "手動確認", "同上"),
    ("www.tnm.jp", "美術館・展覧会", "関東", "制限あり", "https://www.tnm.jp/modules/r_free_page/index.php?id=195",
     "別媒体への収録・再配布は許諾しない（リンクは自由）", "手動確認", "同上"),
    ("ota-muse（www.ukiyoe-ota-muse.jp）", "美術館・展覧会", "関東", "制限あり", "https://www.ukiyoe-ota-muse.jp/",
     "ホームページ内の画像・テキストの著作権は所有。許可なく使用・転載を禁じる（トップページの記載）", "手動確認", "同上"),
    ("www.matsuoka-museum.jp", "美術館・展覧会", "関東", "制限あり", "https://www.matsuoka-museum.jp/",
     "掲載画像の無断転写・複製を禁止（トップページの記載。画像に限定）", "手動確認", "同上"),
    ("www.tobikan.jp", "美術館・展覧会", "関東", "未確認", "https://www.tobikan.jp/",
     "規約ページが見つからない", "手動確認", "規約未確認"),
    ("www.mori.art.museum", "美術館・展覧会", "関東", "判断不能", "https://www.mori.art.museum/jp/",
     "規約ページ（conditions）は上記の art-view で確認。自動取得の記載なし", "手動確認", "自動取得の許可なし"),
    ("www.momat.go.jp", "美術館・展覧会", "関東", "未確認", "https://www.momat.go.jp/site-policy",
     "サイトポリシーのリンクのみ確認。本文は未読", "手動確認", "本文未確認"),
    ("mot-art-museum.jp", "美術館・展覧会", "関東", "未確認", "https://www.mot-art-museum.jp/sitepolicy/",
     "サイトポリシーのリンクのみ確認。本文は未読", "手動確認", "本文未確認"),
    ("www.setagayaartmuseum.or.jp", "美術館・展覧会", "関東", "未確認", "http://www.setagaya-bunka.jp/etc/sitepolicy.html",
     "利用規約のリンクのみ確認。本文は未読", "手動確認", "本文未確認"),
    ("www.mitsui-museum.jp", "美術館・展覧会", "関東", "判断不能", "https://www.mitsui-museum.jp/",
     "規約リンクなし。著作権表示のみ", "手動確認", "自動取得の記載なし"),
    ("mimt.jp", "美術館・展覧会", "関東", "未確認", "https://mimt.jp/policy/",
     "サイトポリシーのリンクのみ確認。本文は未読", "手動確認", "本文未確認"),
    ("www.sompo-museum.org", "美術館・展覧会", "関東", "未確認", "https://www.sompo-museum.org/terms/sitepolicy/",
     "サイトポリシーのリンクのみ確認。本文は未読", "手動確認", "本文未確認"),
    ("www.toguri-museum.or.jp", "美術館・展覧会", "関東", "判断不能", "https://www.toguri-museum.or.jp/",
     "規約ページなし（ソーシャルメディアポリシーのみ）", "手動確認", "自動取得の記載なし"),
    ("www.shukokan.org", "美術館・展覧会", "関東", "判断不能", "https://www.shukokan.org/",
     "規約ページなし。著作権表示のみ", "手動確認", "自動取得の記載なし"),
    ("www.seikado.or.jp", "美術館・展覧会", "関東", "未確認", "https://www.seikado.or.jp/",
     "接続エラーのため規約を読めなかった", "手動確認", "規約未確認"),
    ("www.gotoh-museum.or.jp", "美術館・展覧会", "関東", "未確認", "https://www.gotoh-museum.or.jp/site_policy/",
     "サイトポリシーのリンクのみ確認。本文は未読", "手動確認", "本文未確認"),
    ("shozokan.nich.go.jp", "美術館・展覧会", "関東", "判断不能", "https://shozokan.nich.go.jp/",
     "規約ページなし。画像利用・SNSポリシーのリンクのみ", "手動確認", "自動取得の記載なし"),
    ("museum.geidai.ac.jp", "美術館・展覧会", "関東", "判断不能", "https://museum.geidai.ac.jp/",
     "規約ページなし。著作権表示のみ", "手動確認", "自動取得の記載なし"),
    ("www.toyo-bunko.or.jp", "美術館・展覧会", "関東", "判断不能", "https://toyo-bunko.or.jp/",
     "規約ページなし。画像利用のリンクのみ（本文未読）", "手動確認", "自動取得の記載なし"),
    ("nij.nikon.com", "美術館・展覧会", "関東", "判断不能", "https://nij.nikon.com/information/",
     "規約ページなし。本サイトの案内リンクのみ", "手動確認", "自動取得の記載なし"),
    ("corp.shiseido.com", "美術館・展覧会", "関東", "未確認", "https://corp.shiseido.com/jp/term-of-use.html",
     "利用規約のリンクのみ確認。本文は未読", "手動確認", "本文未確認"),
    ("www.sony.co.jp", "美術館・展覧会", "関東", "未確認", "https://www.sony.co.jp/terms-of-use/",
     "利用規約のリンクのみ確認（推定URL）。本文は未読", "手動確認", "本文未確認"),
    ("www.operacity.jp", "美術館・展覧会", "関東", "未確認", "https://www.operacity.jp/information/sitepolicy.php",
     "サイトポリシーのリンクのみ確認。本文は未読", "手動確認", "本文未確認"),
    ("www.yamatane-museum.jp", "美術館・展覧会", "関東", "未確認", "https://www.yamatane-museum.jp/policy/",
     "著作権・リンクポリシーのリンクのみ確認。本文は未読", "手動確認", "本文未確認"),
    ("www.suntory.co.jp", "美術館・展覧会", "関東", "未確認", "https://www.suntory.co.jp/sma/",
     "アクセスが拒否された（403）。規約未確認", "手動確認", "規約未確認"),
    ("note.com（OM SYSTEM PLAZA）", "美術館・展覧会", "関東", "未確認", "https://note.com/omsystem_plaza/n/na368ff2c610c",
     "記事ページ。規約は未確認", "手動確認", "規約未確認"),
    ("news.google.com（Google News RSS）", "美術館・展覧会（集約）", "関東", "未確認", "https://news.google.com/",
     "集約サイト。Google の利用規約は未確認。掲載元は特定できない", "停止候補", "公式の出典ではない"),
    # 映画（作品公式サイト）。利用条件は、この環境では確認していない（外部アクセスしない）。許可とは扱わない
    ("godzilla-movie2023.toho.co.jp", "映画・作品公式サイト", "全国", "未確認",
     "https://godzilla-movie2023.toho.co.jp/news/oxb3-_pekb/",
     "利用条件・規約は未確認（この環境では外部アクセスしていない）", "手動確認",
     "規約を社長が確認し、許可が明記されているか判断するまで自動取得しない"),
]


# 取得経路の種別（設計メモ 第3節）。既定は「未確認」相当の html（手動確認の対象）。
KINDS = ("rss", "atom", "api", "csv", "json", "html", "ics")

# 情報源の状態。停止中は、規約が許可確認でも自動取得しない。
LIFECYCLES = ("有効", "保留", "停止中")

# 既存の行に後から追加する項目の既定値。既存の REGISTRY は変更しない。
# 値が不明なものは「未確認」とし、許可・提供項目などを推測で埋めない。
FIELD_DEFAULTS = {
    "kind": "html",
    "data_url": "",
    "owner": "未確認",
    "fields": (),
    "license_text": "未確認",
    "store_policy": "未確認",
    "rate_limit": "未確認",
    "update_cycle": "未確認",
    "lifecycle": "保留",
    "last_ok_at": "",
    "last_error": "",
}


def validate(d):
    """行の不備を文字列のリストで返す（空なら問題なし）。
    許可確認の行は、根拠URLと確認日が揃っていて、種別と状態が既定の値である必要がある。"""
    problems = []
    if d.get("terms_status") not in STATUSES:
        problems.append(f"terms_status が不正: {d.get('terms_status')}")
    if d.get("kind") not in KINDS:
        problems.append(f"kind が不正: {d.get('kind')}")
    if d.get("lifecycle") not in LIFECYCLES:
        problems.append(f"lifecycle が不正: {d.get('lifecycle')}")
    if d.get("terms_status") == "許可確認":
        if not d.get("evidence_url"):
            problems.append("許可確認なのに根拠URLがない")
        if not d.get("checked_on"):
            problems.append("許可確認なのに確認日がない")
        if d.get("lifecycle") != "有効":
            problems.append("許可確認でも状態が有効でない（自動取得しない）")
    return problems


def rows():
    """台帳の行を辞書で返す（Python の集計・検査用）。"""
    keys = ("host", "sector", "region", "terms_status", "evidence_url", "evidence_summary", "method", "reason")
    out = []
    for r in REGISTRY:
        d = dict(zip(keys, r))
        d["checked_on"] = CHECKED_ON
        for k, v in FIELD_DEFAULTS.items():
            d.setdefault(k, v)
        # 許可確認の行でも、検査に通らないものは自動取得しない（推測で許可にしない）
        d["auto_allowed"] = d["terms_status"] == "許可確認" and not validate(d)
        out.append(d)
    return out
