"""巨大情報収集のホームページを、ローカルで作って見るための起動スクリプト。

使い方（このフォルダで実行）:
  python3 artist_watch_home.py build   # 台帳からホームページを作り直す
  python3 artist_watch_home.py serve   # 作り直してから http://127.0.0.1:8765/index.html で配信（Ctrl+C で停止）

既定の場所:
  台帳     : ./artist_watch_data          （取り込み先。--dir で変更）
  ホーム   : ~/Desktop/巨大情報収集_ホーム  （--site で変更。docs/ は使わない）
  出所表示 : 「データ出所：手動入力（CSV/JSON）」（--label で変更）

取り込み（CSV/JSON → 台帳。ホームは次の build で反映）:
  python3 artist_watch_run.py --dir ./artist_watch_data --config <config.json> --source <情報源ID> --batch <入力.csv>

定期実行は未接続です。このスクリプトは自動で取得しません。
"""
import argparse
import http.server
import functools
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import artist_watch as AW  # noqa: E402
import artist_watch_site as SITE  # noqa: E402

DEFAULT_LEDGER = os.path.join(HERE, "artist_watch_data")
DEFAULT_SITE = os.path.join(os.path.expanduser("~"), "Desktop", "巨大情報収集_ホーム")
DEFAULT_LABEL = "データ出所：手動入力（CSV/JSON）"
PORT = 8765


def build(ledger_dir, site_dir, label):
    if os.path.abspath(site_dir).startswith(os.path.join(HERE, "docs")):
        raise SystemExit("docs/ 配下（本番の公開先）には書き出しません。--site を変えてください。")
    store = AW.Store(ledger_dir)
    n = SITE.render_site(store, site_dir, data_label=label, ledger_dir=os.path.abspath(ledger_dir))
    print(f"ホームを作成しました：{site_dir}（{n}ファイル）")
    return store


def serve(ledger_dir, site_dir, label, port=PORT):
    build(ledger_dir, site_dir, label)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=site_dir)
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        print(f"表示URL：http://127.0.0.1:{port}/index.html  （停止：Ctrl+C）")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("停止しました。")


def main(argv=None):
    p = argparse.ArgumentParser(description="巨大情報収集のホームページ（ローカル）")
    p.add_argument("command", choices=["build", "serve"])
    p.add_argument("--dir", default=DEFAULT_LEDGER, help="台帳フォルダ")
    p.add_argument("--site", default=DEFAULT_SITE, help="ホームの出力フォルダ")
    p.add_argument("--label", default=DEFAULT_LABEL, help="画面に出す出所ラベル")
    p.add_argument("--port", type=int, default=PORT)
    a = p.parse_args(argv)
    if a.command == "build":
        build(a.dir, a.site, a.label)
    else:
        serve(a.dir, a.site, a.label, a.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
