import os
import re
import time
from datetime import datetime
import io
import gspread
import pandas as pd
import requests
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
import streamlit as st

st.set_page_config(page_title="SLEIPNIR_DB_NEW", layout="wide")
st.title("SLEIPNIR🏇DB (マルチタブ対応版)")

# ================= ================= =================
#  設定と保存フォルダ準備
# ================= ================= =================
RACE_SPREADSHEET_KEY = "1_N4GQm5DeWh6lQrRjsA3nDDX5RgSNYwDy83cZiuCJ2c"  # レース結果用
HORSE_SPREADSHEET_KEY = "1sGPn1S8Uz98YvQjYHaAWpDU-u7kzGoMtig31RFKHK8Q" # 馬データ用

CACHE_DIR = "./html_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Safari/537.36"
    )
}

def clean_private_key(raw_key):
    key_str = str(raw_key).replace("\\n", "\n")
    body = key_str.replace("-----BEGIN PRIVATE KEY-----", "").replace("-----END PRIVATE KEY-----", "")
    body = re.sub(r'[\s\r\n\t]+', '', body)
    formatted_body = "\n".join([body[i:i+64] for i in range(0, len(body), 64)])
    return f"-----BEGIN PRIVATE KEY-----\n{formatted_body}\n-----END PRIVATE KEY-----\n"

def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    if "gcp_service_account" in st.secrets:
        key_dict = dict(st.secrets["gcp_service_account"])
        if "private_key" in key_dict:
            key_dict["private_key"] = clean_private_key(key_dict["private_key"])
        creds = Credentials.from_service_account_info(key_dict, scopes=scopes)
    else:
        creds = Credentials.from_service_account_file("secret_key.json", scopes=scopes)
    return gspread.authorize(creds)

# ================= ================= =================
#  HTMLキャッシュ保存・取得機能
# ================= ================= =================
def save_and_get_html(url, prefix="page"):
    url_id = re.sub(r'[^a-zA-Z0-9]', '_', url)
    filename = f"{prefix}_{url_id}.html"
    filepath = os.path.join(CACHE_DIR, filename)

    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read(), filepath

    res = requests.get(url, headers=HEADERS)
    res.encoding = res.apparent_encoding
    html_content = res.text

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)

    return html_content, filepath

# ================= ================= =================
#  1. レース結果のスクレイピング
# ================= ================= =================
def parse_race_html(html_text, url):
    soup = BeautifulSoup(html_text, "html.parser")
    table = soup.find("table", id="All_Result_Table") or soup.find("table", class_="RaceTable01")
    if not table:
        return None, None

    year_match = re.search(r'race_id=(\d{4})', url)
    year = year_match.group(1) if year_match else "2026"

    race_title_elem = (
        soup.find("div", class_="RaceName") or 
        soup.find("h1", class_="RaceName") or 
        soup.find("div", class_="race_name")
    )
    race_name = re.sub(r'[\r\n\t]+', '', race_title_elem.get_text(strip=True)) if race_title_elem else "レース結果"
    full_sheet_name = re.sub(r'[/\\?*:[\]]', '', f"{year}{race_name}")[:80]

    data = []
    rows = table.find_all("tr", class_="HorseList")
    
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 13:
            continue
            
        jockey_col = row.find("td", class_="Jockey") or cols[6]
        jockey_a = jockey_col.find("a") if jockey_col else None
        jockey_full = jockey_a.get("title").strip() if (jockey_a and jockey_a.get("title")) else jockey_col.get_text(strip=True)

        trainer_col = row.find("td", class_="Trainer") or (cols[13] if len(cols) > 13 else None)
        trainer_full = "-"
        if trainer_col:
            belonging_text = trainer_col.get_text(strip=True)
            belonging = belonging_text[belonging_text.find("["):belonging_text.find("]")+1] if "[" in belonging_text and "]" in belonging_text else ""
            trainer_a = trainer_col.find("a")
            if trainer_a and trainer_a.get("title"):
                trainer_full = f"{belonging}{trainer_a.get('title').strip()}"
            else:
                trainer_full = belonging_text

        data.append({
            "着順": cols[0].get_text(strip=True),
            "枠": cols[1].get_text(strip=True),
            "馬番": cols[2].get_text(strip=True),
            "馬名": cols[3].get_text(strip=True),
            "性齢": cols[4].get_text(strip=True),
            "斤量": cols[5].get_text(strip=True),
            "騎手": jockey_full,
            "タイム": cols[7].get_text(strip=True),
            "着差": cols[8].get_text(strip=True),
            "人気": cols[9].get_text(strip=True) if len(cols) > 9 else "-",
            "単勝オッズ": cols[10].get_text(strip=True) if len(cols) > 10 else "-",
            "後3F": cols[11].get_text(strip=True) if len(cols) > 11 else "-",
            "コーナ通過順": cols[12].get_text(strip=True) if len(cols) > 12 else "-",
            "厩舎": trainer_full,
            "馬体重": cols[14].get_text(strip=True) if len(cols) > 14 else "-"
        })

    return pd.DataFrame(data), full_sheet_name

# ================= ================= =================
#  2. 馬単体データ（マルチタブ対応）のスクレイピング
# ================= ================= =================
def fetch_horse_multi_data(base_url):
    # 馬IDの抽出
    match = re.search(r'/horse/(\d{10})', base_url)
    if not match:
        # 万が一ID形式でない場合でも動作するようフォールバック
        horse_id = base_url.strip("/").split("/")[-1]
    else:
        horse_id = match.group(1)

    ped_url = f"https://db.netkeiba.com/horse/ped/{horse_id}/"
    result_url = f"https://db.netkeiba.com/horse/result/{horse_id}/"

    # 1. 血統・適性ページ (ped) の取得＆保存
    html_ped, filepath_ped = save_and_get_html(ped_url, prefix=f"ped_{horse_id}")
    
    # 2. 競走成績ページ (result) の取得＆保存
    html_res, filepath_res = save_and_get_html(result_url, prefix=f"result_{horse_id}")

    # --- 血統・プロフィール解析 ---
    soup_ped = BeautifulSoup(html_ped, "html.parser")

    # 馬名
    horse_title = soup_ped.find("div", class_="horse_title")
    horse_name = horse_title.find("h1").get_text(strip=True) if (horse_title and horse_title.find("h1")) else "競走馬"
    horse_name = re.sub(r'[\r\n\t\s]+', '', horse_name)

    # 基本情報
    info_dict = {}
    info_table = soup_ped.find("table", class_="db_prof_table")
    if info_table:
        for tr in info_table.find_all("tr"):
            th, td = tr.find("th"), tr.find("td")
            if th and td:
                info_dict[th.get_text(strip=True)] = td.get_text(" ", strip=True)

    birth_date = info_dict.get("生年月日", "-")
    age = "-"
    if birth_date != "-":
        try:
            b_year = int(birth_date.split("年")[0])
            age = f"{datetime.now().year - b_year}歳"
        except:
            pass

    # 血統（父・母・母父）
    father, mother, mother_father = "-", "-", "-"
    blood_table = soup_ped.find("table", class_="blood_table")
    if blood_table:
        extracted = [a.get_text(strip=True) for a in blood_table.find_all("a") if a.get_text(strip=True)]
        extracted_unique = []
        for name in extracted:
            if name not in extracted_unique:
                extracted_unique.append(name)
        if len(extracted_unique) >= 1: father = extracted_unique[0]
        if len(extracted_unique) >= 2: mother = extracted_unique[1]
        if len(extracted_unique) >= 3: mother_father = extracted_unique[2]

    # コース適性・適正距離
    turf_dirt, dist_apt = "-", "-"
    diag_table = soup_ped.find("table", class_="db_dia_table") or soup_ped.find("table", class_="dial_table")
    if diag_table:
        text_content = diag_table.get_text()
        if "芝" in text_content and "ダート" in text_content: turf_dirt = "芝・ダート兼配"
        elif "芝" in text_content: turf_dirt = "芝"
        elif "ダート" in text_content: turf_dirt = "ダート"

        m_dist = re.search(r'(\d{4}m\s*～\s*\d{4}m|\d{4}m前後|\d{4}m)', text_content)
        if m_dist: dist_apt = m_dist.group(0)

    basic_data = [
        {"項目": "馬名", "内容": horse_name},
        {"項目": "生年月日", "内容": birth_date},
        {"項目": "年齢", "内容": age},
        {"項目": "調教師", "内容": info_dict.get("調教師", "-")},
        {"項目": "馬主", "内容": info_dict.get("馬主", "-")},
        {"項目": "生産者", "内容": info_dict.get("生産者", "-")},
        {"項目": "産地", "内容": info_dict.get("産地", "-")},
        {"項目": "通算成績", "内容": info_dict.get("通算成績", "-")},
        {"項目": "主な勝鞍", "内容": info_dict.get("主な勝鞍", "-")},
        {"項目": "近親馬", "内容": info_dict.get("近親馬", "-")},
        {"項目": "父", "内容": father},
        {"項目": "母", "内容": mother},
        {"項目": "母父", "内容": mother_father},
        {"項目": "コース適性", "内容": turf_dirt},
        {"項目": "適正距離", "内容": dist_apt}
    ]
    df_basic = pd.DataFrame(basic_data)

    # --- 競走成績解析 (result ページから抽出) ---
    soup_res = BeautifulSoup(html_res, "html.parser")
    df_results = pd.DataFrame()
    
    try:
        tables = pd.read_html(io.StringIO(html_res))
        for df in tables:
            cols_str = "".join([str(c) for c in df.columns])
            if any(k in cols_str for k in ["日付", "レース名", "着順", "開催"]):
                df_results = df
                break
    except:
        pass

    if df_results.empty:
        results_table = soup_res.find("table", class_="db_h_race_results") or soup_res.find("table", class_="NK_RaceResult_Table")
        if results_table:
            headers = [th.get_text(strip=True) for th in results_table.find_all("th")]
            rows = []
            for tr in results_table.find_all("tr"):
                tds = tr.find_all("td")
                if tds:
                    rows.append([td.get_text(strip=True) for td in tds])
            if headers and rows:
                df_results = pd.DataFrame(rows, columns=headers[:len(rows[0])])

    if not df_results.empty:
        df_results = df_results.fillna("-")
        df_results.columns = [str(c).strip() for c in df_results.columns]
        for drop_col in ["映像", "画像", "掲示板"]:
            if drop_col in df_results.columns:
                df_results = df_results.drop(columns=[drop_col])

    clean_horse_name = re.sub(r'[/\\?*:[\]]', '', horse_name)[:30]
    return df_basic, df_results, clean_horse_name, filepath_ped, filepath_res

# ================= ================= =================
#  スプレッドシート書き込み機能
# ================= ================= =================
def write_to_spreadsheet(spreadsheet, sheet_name, df_data):
    try:
        ws = spreadsheet.worksheet(sheet_name)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=sheet_name, rows=len(df_data)+50, cols=30)

    rows = [df_data.columns.tolist()] + df_data.astype(str).values.tolist()
    ws.update(values=rows)

def update_horse_to_spreadsheet(spreadsheet, sheet_name, df_basic, df_results):
    try:
        ws = spreadsheet.worksheet(sheet_name)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=sheet_name, rows=300, cols=30)

    all_rows = [["【基本情報】", ""]]
    for idx, row in df_basic.iterrows():
        all_rows.append([str(row["項目"]), str(row["内容"])])

    all_rows.append([])
    all_rows.append(["【競走成績】"])

    if not df_results.empty:
        all_rows.append(df_results.columns.tolist())
        for idx, row in df_results.iterrows():
            all_rows.append(row.astype(str).tolist())
    else:
        all_rows.append(["（競走成績データなし）"])

    ws.update(values=all_rows)

# ================= ================= =================
#  Streamlit UI
# ================= ================= =================
tab1, tab2 = st.tabs(["🏁 レース結果取得", "🐎 馬データ取得 (単体)"])

with tab1:
    st.header("レース結果の取得")
    with st.form("race_form"):
        race_url = st.text_input("レース結果URL")
        submit_race = st.form_submit_button("HTML保存 & データ解析実行", type="primary")

    if submit_race and race_url:
        with st.spinner("HTMLを保存して解析中..."):
            html_content, filepath = save_and_get_html(race_url, prefix="race")
            st.info(f"📁 レース結果HTMLを保存しました: `{filepath}`")

            df_race, sheet_name = parse_race_html(html_content, race_url)
            if df_race is not None and not df_race.empty:
                client = get_gspread_client()
                sp = client.open_by_key(RACE_SPREADSHEET_KEY)
                write_to_spreadsheet(sp, sheet_name, df_race)
                st.success(f"✅ スプレッドシート '{sheet_name}' へ記入が完了しました！")
                st.dataframe(df_race)

with tab2:
    st.header("馬データの取得 (単体)")
    with st.form("horse_form"):
        horse_url = st.text_input("馬ページURL (例: https://db.netkeiba.com/horse/2017101429)")
        submit_horse = st.form_submit_button("HTML保存 & データ解析実行", type="primary")

    if submit_horse and horse_url:
        with st.spinner("血統ページと成績ページのHTMLを保存・解析中..."):
            df_basic, df_results, horse_name, fp_ped, fp_res = fetch_horse_multi_data(horse_url)
            
            st.info(f"📁 血統HTMLを保存しました: `{fp_ped}`")
            st.info(f"📁 成績HTMLを保存しました: `{fp_res}`")

            client = get_gspread_client()
            sp = client.open_by_key(HORSE_SPREADSHEET_KEY)
            update_horse_to_spreadsheet(sp, horse_name, df_basic, df_results)

            st.success(f"✅ シート '{horse_name}' へ基本情報および競走成績の記入が完了しました！")
            st.subheader("【基本情報】")
            st.dataframe(df_basic)
            st.subheader("【競走成績】")
            st.dataframe(df_results)
