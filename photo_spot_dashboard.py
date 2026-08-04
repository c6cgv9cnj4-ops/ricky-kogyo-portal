import os
import time
import math
import datetime
import requests

# ============================================================
# 撮影スポット＆気象条件ダッシュボード（機材レコメンド付き）
#
# - 気象データ: Open-Meteo API（APIキー不要・無料・安定運用向け）
# - 月齢・照度: 天文計算式（ネットワーク非依存、常に計算可能）
# - 雲海条件: 気温差・湿度・風速・雲量からのヒューリスティック推定（【推測】ラベル付き）
# - レンズレコメンド: スポットのタグと所有機材のカテゴリのマッチングで自動選定
# ============================================================

OUTPUT_DIR = "docs/photo-spot"
OUTPUT_HTML = os.path.join(OUTPUT_DIR, "index.html")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

skip_log = []


def log_skip(source, reason):
    skip_log.append(f"{source}: {reason}")
    print(f"⚠️  スキップ - {source}: {reason}")


# ------------------------------------------------------------
# 1. 所有機材データベース
# ------------------------------------------------------------
EQUIPMENT_DATABASE = {
    "body": {
        "name": "Canon EOS R6 + マウントアダプター",
        "weight_g": 680 + 110  # ボディ: 約680g, EF-EOS Rアダプター: 約110g
    },
    "lenses": {
        "standard_zoom": {
            "name": "EF24-105mm F4 L IS USM",
            "weight_g": 670,
            "category": ["風景", "スナップ", "雲海", "イベント", "万能"]
        },
        "telephoto_zoom": {
            "name": "EF70-200mm F4L",
            "weight_g": 705,
            "category": ["鉄道", "遠景", "鳥", "スポーツ", "圧縮効果"]
        },
        "wide_prime": {
            "name": "SIGMA 35mm F1.4 DG HSM | Art",
            "weight_g": 665,
            "category": ["星空", "夜景", "暗所", "広角スナップ"]
        },
        "mid_tele_prime": {
            "name": "EF85mm F1.8 USM",
            "weight_g": 425,
            "category": ["ポートレート", "大口径ボケ", "中望遠"]
        },
        "macro_prime": {
            "name": "SIGMA MACRO 50mm F2.8 EX DG",
            "weight_g": 320,
            "category": ["花", "植物", "昆虫", "物撮り", "マクロ"]
        }
    }
}

# ------------------------------------------------------------
# 2. 撮影スポット定義（北本市周辺、移動圏内）
# ------------------------------------------------------------
SPOTS = [
    {
        "name": "北本自然観察公園",
        "city": "北本市",
        "lat": 35.9825, "lon": 139.5314,
        "tags": ["風景", "スナップ", "花", "植物"],
        "cloud_sea_candidate": False,
    },
    {
        "name": "荒川河川敷（桶川市）",
        "city": "桶川市",
        "lat": 35.9700, "lon": 139.5280,
        "tags": ["風景", "夜景", "スナップ", "鳥", "遠景"],
        "cloud_sea_candidate": False,
    },
    {
        "name": "鴻巣ポピー・コスモスフェア会場",
        "city": "鴻巣市",
        "lat": 36.0654, "lon": 139.5133,
        "tags": ["花", "マクロ", "イベント", "植物"],
        "cloud_sea_candidate": False,
    },
    {
        "name": "美の山公園（雲海展望）",
        "city": "秩父市・皆野町",
        "lat": 36.0330, "lon": 139.0830,
        "tags": ["雲海", "風景", "星空", "夜景"],
        "cloud_sea_candidate": True,
    },
    {
        "name": "北本駅東口 商店街スナップ",
        "city": "北本市",
        "lat": 35.9805, "lon": 139.5296,
        "tags": ["スナップ", "イベント", "万能"],
        "cloud_sea_candidate": False,
    },
]

WEATHER_CODE_JP = {
    0: "快晴", 1: "晴れ", 2: "晴れ時々曇り", 3: "曇り",
    45: "霧", 48: "霧（着氷性）",
    51: "小雨", 53: "雨", 55: "強い雨",
    61: "雨", 63: "雨", 65: "強い雨",
    71: "雪", 73: "雪", 75: "強い雪",
    80: "にわか雨", 81: "にわか雨", 82: "激しいにわか雨",
    95: "雷雨", 96: "雷雨（雹）", 99: "雷雨（強い雹）",
}


# ------------------------------------------------------------
# 3. 気象データ取得（Open-Meteo）
# ------------------------------------------------------------
def fetch_weather(spot):
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={spot['lat']}&longitude={spot['lon']}"
        "&current=temperature_2m,relative_humidity_2m,wind_speed_10m,cloud_cover,weather_code"
        "&daily=temperature_2m_max,temperature_2m_min,sunrise,sunset"
        "&timezone=Asia%2FTokyo"
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json()
        current = data.get("current", {})
        daily = data.get("daily", {})
        return {
            "temp": current.get("temperature_2m"),
            "humidity": current.get("relative_humidity_2m"),
            "wind": current.get("wind_speed_10m"),
            "cloud_cover": current.get("cloud_cover"),
            "weather_jp": WEATHER_CODE_JP.get(current.get("weather_code"), "不明"),
            "temp_max": daily.get("temperature_2m_max", [None])[0],
            "temp_min": daily.get("temperature_2m_min", [None])[0],
            "sunrise": (daily.get("sunrise", [""])[0] or "")[-5:],
            "sunset": (daily.get("sunset", [""])[0] or "")[-5:],
        }
    except Exception as e:
        log_skip(f"気象データ({spot['name']})", f"取得エラー ({e})")
        return None


# ------------------------------------------------------------
# 4. 月齢・月の照度（天文計算・ネットワーク非依存）
# ------------------------------------------------------------
def calc_moon_phase(now=None):
    now = now or datetime.datetime.utcnow()
    reference_new_moon = datetime.datetime(2000, 1, 6, 18, 14)
    synodic_month = 29.53058867
    days_since = (now - reference_new_moon).total_seconds() / 86400.0
    age = days_since % synodic_month
    illumination = (1 - math.cos(2 * math.pi * age / synodic_month)) / 2 * 100

    if age < 1.5 or age > synodic_month - 1.5:
        phase_name = "新月"
    elif age < 6.5:
        phase_name = "三日月"
    elif age < 8.5:
        phase_name = "上弦の月"
    elif age < 13.5:
        phase_name = "十三夜〜満月前"
    elif age < 15.5:
        phase_name = "満月"
    elif age < 21.5:
        phase_name = "満月〜下弦"
    elif age < 23.5:
        phase_name = "下弦の月"
    else:
        phase_name = "有明月"

    return {"age": round(age, 1), "illumination": round(illumination, 1), "phase_name": phase_name}


# ------------------------------------------------------------
# 5. 雲海条件ヒューリスティック（【推測】ラベル必須）
# ------------------------------------------------------------
def estimate_cloud_sea_score(weather):
    if weather is None or weather["temp_max"] is None or weather["temp_min"] is None:
        return None
    temp_diff = weather["temp_max"] - weather["temp_min"]
    wind = weather["wind"] or 0
    humidity = weather["humidity"] or 0
    cloud_cover = weather["cloud_cover"] if weather["cloud_cover"] is not None else 100

    score = 0
    reasons = []
    if temp_diff >= 8:
        score += 35
        reasons.append(f"寒暖差{temp_diff:.1f}℃（放射冷却が期待できる水準）")
    elif temp_diff >= 5:
        score += 15
        reasons.append(f"寒暖差{temp_diff:.1f}℃（やや弱いが可能性あり）")

    if wind <= 2:
        score += 30
        reasons.append(f"風速{wind:.1f}m/s（無風〜微風で霧が滞留しやすい）")
    elif wind <= 4:
        score += 10
        reasons.append(f"風速{wind:.1f}m/s（やや風あり）")

    if humidity >= 80:
        score += 20
        reasons.append(f"湿度{humidity:.0f}%（高湿度）")

    if cloud_cover <= 30:
        score += 15
        reasons.append(f"雲量{cloud_cover:.0f}%（晴れて放射冷却が進みやすい）")

    if score >= 70:
        label = "発生可能性：高い"
    elif score >= 40:
        label = "発生可能性：中程度"
    else:
        label = "発生可能性：低い"

    return {"score": score, "label": label, "reasons": reasons}


# ------------------------------------------------------------
# 6. レンズ最適化レコメンドロジック
# ------------------------------------------------------------
def recommend_lens(spot_tags):
    best_lens_key = None
    best_score = -1
    best_matched_tags = []
    for key, lens in EQUIPMENT_DATABASE["lenses"].items():
        matched = [t for t in spot_tags if t in lens["category"]]
        score = len(matched)
        if score > best_score or (score == best_score and best_lens_key and
                                   lens["weight_g"] < EQUIPMENT_DATABASE["lenses"][best_lens_key]["weight_g"]):
            best_score = score
            best_lens_key = key
            best_matched_tags = matched

    if best_lens_key is None or best_score == 0:
        # マッチなし → 万能な標準ズームをデフォルト推奨
        best_lens_key = "standard_zoom"
        best_matched_tags = ["万能（明確な一致タグなし）"]

    lens = EQUIPMENT_DATABASE["lenses"][best_lens_key]
    total_weight = EQUIPMENT_DATABASE["body"]["weight_g"] + lens["weight_g"]
    return {
        "lens_name": lens["name"],
        "lens_weight": lens["weight_g"],
        "total_weight": total_weight,
        "matched_tags": best_matched_tags,
    }


# ------------------------------------------------------------
# 7. データ構築
# ------------------------------------------------------------
def build_dataset():
    moon = calc_moon_phase()
    spot_results = []
    for spot in SPOTS:
        weather = fetch_weather(spot)
        lens_rec = recommend_lens(spot["tags"])
        cloud_sea = estimate_cloud_sea_score(weather) if spot["cloud_sea_candidate"] else None
        spot_results.append({
            "spot": spot,
            "weather": weather,
            "lens": lens_rec,
            "cloud_sea": cloud_sea,
        })
    return moon, spot_results


# ------------------------------------------------------------
# 8. HTML生成
# ------------------------------------------------------------
def render_spot_card(entry):
    spot = entry["spot"]
    weather = entry["weather"]
    lens = entry["lens"]
    cloud_sea = entry["cloud_sea"]

    if weather:
        weather_html = f"""
        <div class="weather-grid">
          <div class="wstat"><span class="wlabel">天気</span><span class="wvalue">{weather['weather_jp']}</span></div>
          <div class="wstat"><span class="wlabel">気温</span><span class="wvalue mono">{weather['temp']}℃</span></div>
          <div class="wstat"><span class="wlabel">湿度</span><span class="wvalue mono">{weather['humidity']}%</span></div>
          <div class="wstat"><span class="wlabel">風速</span><span class="wvalue mono">{weather['wind']}m/s</span></div>
          <div class="wstat"><span class="wlabel">雲量</span><span class="wvalue mono">{weather['cloud_cover']}%</span></div>
          <div class="wstat"><span class="wlabel">日出/日没</span><span class="wvalue mono">{weather['sunrise']} / {weather['sunset']}</span></div>
        </div>"""
    else:
        weather_html = "<p class='empty'>気象データを取得できませんでした（要確認）。</p>"

    cloud_sea_html = ""
    if cloud_sea:
        reasons_html = "".join(f"<li>{r}</li>" for r in cloud_sea["reasons"]) or "<li>該当条件なし</li>"
        cloud_sea_html = f"""
        <div class="cloudsea-box">
          <div class="cloudsea-label">【推測】雲海 {cloud_sea['label']}（スコア{cloud_sea['score']}/100）</div>
          <ul class="cloudsea-reasons">{reasons_html}</ul>
          <div class="disclaimer">※気象条件からのAI推定であり、公式予報ではありません。</div>
        </div>"""

    tags_html = "".join(f"<span class='tag'>{t}</span>" for t in spot["tags"])
    matched_html = "・".join(lens["matched_tags"])

    return f"""
    <div class="card">
      <div class="card-head">
        <h3>{spot['name']}</h3>
        <span class="city-badge">{spot['city']}</span>
      </div>
      <div class="tag-row">{tags_html}</div>
      {weather_html}
      {cloud_sea_html}
      <div class="lens-box">
        <div class="lens-title">推奨レンズ</div>
        <div class="lens-name">{lens['lens_name']}</div>
        <div class="lens-meta mono">レンズ単体 {lens['lens_weight']}g ／ 総重量（ボディ込み） {lens['total_weight']}g</div>
        <div class="lens-reason">選定根拠: {matched_html}</div>
      </div>
    </div>"""


def render_html(moon, spot_results):
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    cards_html = "".join(render_spot_card(e) for e in spot_results)
    skip_html = "".join(f"<li>{s}</li>" for s in skip_log) if skip_log else "<li>なし（すべての情報源から正常に取得できました）</li>"

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>撮影スポット＆気象条件ダッシュボード</title>
<style>
  :root {{
    --paper: #EFF2ED;
    --paper-raised: #FFFFFF;
    --ink: #1F2620;
    --ink-soft: #57604F;
    --ink-faint: #838C7C;
    --accent: #3F6B4A;
    --accent-warm: #96690E;
    --rule: #D6DCD1;
    --sky: #3E6FA6;
    --night: #2C3454;
    --warn-bg: #F3ECD9;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--paper);
    color: var(--ink);
    font-family: "Hiragino Kaku Gothic ProN", "Hiragino Sans", "Yu Gothic", "Noto Sans JP", system-ui, sans-serif;
    line-height: 1.7;
  }}
  .mono {{ font-family: ui-monospace, "SF Mono", "Roboto Mono", monospace; font-variant-numeric: tabular-nums; }}
  .page {{ max-width: 960px; margin: 0 auto; padding: 32px 20px 64px; }}

  header.top {{ border-bottom: 3px solid var(--accent); padding-bottom: 16px; margin-bottom: 20px; }}
  header.top h1 {{ font-size: 26px; margin: 0 0 6px; color: var(--accent); }}
  header.top .meta {{ color: var(--ink-soft); font-size: 13px; }}

  .moon-strip {{
    display: flex;
    align-items: center;
    gap: 20px;
    background: var(--night);
    color: #E8ECF7;
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 28px;
    flex-wrap: wrap;
  }}
  .moon-strip .moon-title {{ font-weight: 700; font-size: 14px; opacity: 0.85; }}
  .moon-strip .moon-main {{ font-size: 20px; font-weight: 700; }}
  .moon-strip .moon-sub {{ font-size: 13px; opacity: 0.85; }}

  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
  }}
  .card {{
    background: var(--paper-raised);
    border: 1px solid var(--rule);
    border-radius: 12px;
    padding: 18px 20px;
    box-shadow: 0 1px 2px rgba(31,38,32,0.06), 0 8px 20px -14px rgba(31,38,32,0.3);
  }}
  .card-head {{ display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }}
  .card-head h3 {{ font-size: 16.5px; margin: 0; text-wrap: balance; }}
  .city-badge {{ font-size: 11px; color: var(--ink-faint); white-space: nowrap; }}

  .tag-row {{ margin: 8px 0 12px; display: flex; flex-wrap: wrap; gap: 6px; }}
  .tag {{
    font-size: 11px; font-weight: 600;
    background: rgba(63,107,74,0.1); color: var(--accent);
    padding: 2px 8px; border-radius: 999px;
  }}

  .weather-grid {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px 6px;
    border-top: 1px solid var(--rule);
    border-bottom: 1px solid var(--rule);
    padding: 10px 0;
    margin-bottom: 12px;
  }}
  .wstat {{ display: flex; flex-direction: column; }}
  .wlabel {{ font-size: 10.5px; color: var(--ink-faint); }}
  .wvalue {{ font-size: 13.5px; font-weight: 700; color: var(--sky); }}

  .cloudsea-box {{
    background: var(--warn-bg);
    border-radius: 8px;
    padding: 10px 12px;
    margin-bottom: 12px;
  }}
  .cloudsea-label {{ font-size: 13px; font-weight: 700; color: var(--accent-warm); margin-bottom: 4px; }}
  .cloudsea-reasons {{ margin: 0; padding-left: 18px; font-size: 12px; color: var(--ink-soft); }}
  .disclaimer {{ font-size: 10.5px; color: var(--ink-faint); margin-top: 4px; }}

  .lens-box {{ border-top: 1px dashed var(--rule); padding-top: 10px; }}
  .lens-title {{ font-size: 11px; color: var(--ink-faint); }}
  .lens-name {{ font-size: 14.5px; font-weight: 700; margin: 2px 0; }}
  .lens-meta {{ font-size: 12px; color: var(--ink-soft); }}
  .lens-reason {{ font-size: 11.5px; color: var(--ink-faint); margin-top: 2px; }}

  .empty {{ font-size: 12.5px; color: var(--ink-faint); }}

  footer {{ margin-top: 32px; border-top: 1px solid var(--rule); padding-top: 14px; font-size: 12px; color: var(--ink-faint); }}
  footer ul {{ margin: 6px 0 0; padding-left: 18px; }}
</style>
</head>
<body>
<div class="page">
  <header class="top">
    <h1>撮影スポット＆気象条件ダッシュボード</h1>
    <div class="meta">機材レコメンド付き｜最終更新: {now_str}</div>
  </header>

  <div class="moon-strip">
    <div class="moon-title">今夜の月</div>
    <div class="moon-main">{moon['phase_name']}</div>
    <div class="moon-sub mono">月齢 {moon['age']} ／ 照度目安 {moon['illumination']}%（低いほど星空向き）</div>
  </div>

  <div class="grid">
    {cards_html}
  </div>

  <footer>
    データ取得状況（スキップログ）:
    <ul>{skip_html}</ul>
    <p>気象データ: Open-Meteo（無償API）／ 月齢: 天文計算式（常時計算可能）／ 雲海スコアはAIによる推定値です。</p>
  </footer>
</div>
</body>
</html>
"""


def main():
    start_time = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    moon, spot_results = build_dataset()
    html = render_html(moon, spot_results)

    temp_file = OUTPUT_HTML + ".tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(html)
        os.replace(temp_file, OUTPUT_HTML)
        print(f"✅ 撮影スポットダッシュボード生成完了: {OUTPUT_HTML}")
        print(f"   スポット数: {len(spot_results)}件")
        if skip_log:
            print(f"   ⚠️ スキップ件数: {len(skip_log)}件（詳細はページ下部フッター参照）")
    except Exception as e:
        print(f"❌ ファイル保存エラー: {e}")
        if os.path.exists(temp_file):
            os.remove(temp_file)

    elapsed = time.time() - start_time
    print(f"⏱️ トータル処理時間: {int(elapsed // 60)}分 {elapsed % 60:.2f}秒")


if __name__ == "__main__":
    main()
