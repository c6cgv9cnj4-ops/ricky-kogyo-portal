# 全成果物ポータル（ricky-kogyo-portal）

一般社団法人りっきー興行 AI情報収集・分析チーム（AI部長）が作成した全成果物を
1ページに集約したポータルダッシュボード。GitHub Actionsが24時間ごとに全リンクへ
アクセスし、生存確認（404ハレーション排除）を行った上でページを自動再生成する。

## 構成

- `auto_fetch_all.py` — 成果物URL一覧に対して生存確認を行い、`docs/index.html` を再生成するスクリプト
- `docs/index.html` — 公開されるポータルページ（GitHub Pagesのソース）
- `docs/status_history.json` — 各URLの最終正常確認日時などの履歴
- `.github/workflows/auto_update.yml` — 毎日06:00 JSTに自動実行するワークフロー

## 成果物を追加するには

`auto_fetch_all.py` 内の `ARTIFACTS` リストに以下の形式で追記する。

```python
{
    "title": "成果物のタイトル",
    "url": "https://...",
    "category": "表示グループ名（例: 地域・防災）",
    "team": "01_情報収集チーム" または "02_分析・提案チーム",
}
```

## 公開設定

GitHub Pages: `main` ブランチの `/docs` フォルダを公開ソースとして使用。
