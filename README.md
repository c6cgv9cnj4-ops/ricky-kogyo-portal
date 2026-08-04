# 全成果物ポータル（ricky-kogyo-portal）

一般社団法人りっきー興行 AI情報収集・分析チーム（AI部長）が作成した全成果物を、
外部（claude.ai Artifacts）へのリンクに頼らず、それぞれ独立したWebページとして
GitHub Pages上で直接動作・配信するモノレポ。GitHub Actionsが毎日、各ページ専用の
Python fetchスクリプトを実行して外部サイトから実データを再取得し、HTMLを再生成・
自動デプロイする。

## 構成（8ページ + トップポータル）

| ページ | 生成スクリプト | 公開パス |
|---|---|---|
| AI部長 朝刊 | `morning_news_fetch.py`（他7ページの見出しを集約） | `docs/morning-news/` |
| 公式一次情報収集・分析ダッシュボード | `official_primary_fetch.py`（埼玉県警・消防本部・気象庁） | `docs/official-primary/` |
| 近隣3市 地域ポータル | `local_portal_fetch.py` | `docs/local-portal/` |
| 4紙見出し比較ポータル | `newspaper_portal.py` | `docs/newspaper/` |
| 経済ポータルダッシュボード | `economic_portal_dashboard.py` | `docs/economic/` |
| バドミントン代表・動向インテリジェンス | `badminton_intelligence.py`（`local_portal_fetch.py`のユーティリティを再利用） | `docs/badminton/` |
| 都内 美術館・写真展データベース | `culture_exhibition_dashboard.py` | `docs/culture-exhibition/` |
| 撮影スポット＆気象条件ダッシュボード | `photo_spot_dashboard.py` | `docs/photo-spot/` |
| （トップ）全成果物ポータル | `auto_fetch_all.py`（各ページの存在確認＋一覧生成） | `docs/index.html` |

`gourmet_data.json` / `coffee_tracker.json` は `local_portal_fetch.py` が参照する
グルメ・コーヒー店データ。更新する場合はこのリポジトリ側のファイルも合わせて編集する。

## 実行順序（重要）

`morning_news_fetch.py` は他7ページの生成済みHTMLから見出しを抽出するため、
**必ず他の全fetchスクリプトの後、`auto_fetch_all.py` の前**に実行すること。
`.github/workflows/auto_update.yml` はこの順序で構成済み。

## 自動更新

- スケジュール: 毎日06:00 JST（`0 21 * * *`）＋ 手動実行ボタン
- 近隣3市 地域ポータルは姉妹リポジトリ（[kitamoto-okegawa-konosu-portal](https://github.com/c6cgv9cnj4-ops/kitamoto-okegawa-konosu-portal)）でも15分毎に独立して更新されている。本リポジトリ内の同ページは日次更新版。

## 公開設定

GitHub Pages: `main` ブランチの `/docs` フォルダを公開ソースとして使用。
公開URL: https://c6cgv9cnj4-ops.github.io/ricky-kogyo-portal/
