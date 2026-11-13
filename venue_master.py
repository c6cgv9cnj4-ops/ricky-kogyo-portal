"""施設マスター：展覧会の会場名から、施設の一覧と安定した施設IDを作る（ローカル処理。外部アクセスなし）。

- 施設IDは、表記を正規化した会場名（artist_watch.norm）の sha1 の先頭10桁で作る。同じ施設は同じIDになる。
- 同名の施設は、正規化した名前が同じなら1件に統合する。表記が違うだけの別名は、ここでは統合しない（推測しない）。
"""
import hashlib
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import artist_watch as AW  # noqa: E402


def facility_id(name):
    return "fac_" + hashlib.sha1(AW.norm(name).encode("utf-8")).hexdigest()[:10]


def build_master(rows):
    """展覧会の行（exhibition_rows の出力）から施設マスターを作る。戻り値は施設IDの順の一覧。"""
    master = {}
    for r in rows:
        name = (r.get("venue") or "").strip()
        if not name:
            continue
        fid = facility_id(name)
        rec = master.setdefault(fid, {"facility_id": fid, "name": name, "exhibitions": 0})
        rec["exhibitions"] += 1
    return sorted(master.values(), key=lambda m: m["facility_id"])
