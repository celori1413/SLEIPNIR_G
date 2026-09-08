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

st.set_page_config(page_title="SLEIPNIR_DB02", layout="wide")

st.title("SLEIPNIR🏇DB02")

# ================= ================= =================
#  スプレッドシート設定
# ================= ================= =================
RACE_SPREADSHEET_KEY = "1_N4GQm5DeWh6lQrRjsA3nDDX5RgSNYwDy83cZiuCJ2c"  # レース結果用 (SLEIPNIR_G_2026RaceDB)
HORSE_SPREADSHEET_KEY = "1sGPn1S8Uz98YvQjYHaAWpDU-u7kzGoMtig31RFKHK8Q" # 馬データ専用

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

def append_execution_log(spreadsheet, tab_name, target_info, status, detail=""):
    log_sheet_name = "ログ"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        try:
            ws = spreadsheet.worksheet(log_sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(title=log_sheet_name, rows=500, cols=10)
            ws.append_row(["日時", "実行機能", "対象", "ステータス", "詳細"])
        ws.append_row([now_str, tab_name, str(target_info), status, str(detail)])
    except Exception as e:
        st.warning(f"ログの保存時に警告が発生しました: {e}")

# ================= ================= =================
#  1. レース結果 スクレイピング
# ================= ================= =================
def fetch_race_results(url):
    res = requests.get(url, headers=HEADERS)
    res.encoding = res.apparent_encoding
    soup = BeautifulSoup(res.text, "html.parser")
    
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
    if race_title_elem:
        race_name = re.sub(r'[\r\n\t]+', '', race_title_elem.get_text(strip=True))
    else:
        race_name = "レース結果"

    full_sheet_name = f"{year}{race_name}"
    full_sheet_name = re.sub(r'[/\\?*:[\]]', '', full_sheet_name)[:80]

    data = []
    rows = table.find_all("tr", class_="HorseList")
    
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 11:
            continue
            
        jockey_col = row.find("td", class_="Jockey") or (cols[6] if len(cols) > 6 else None)
        jockey = "-"
        if jockey_col:
            jockey_a = jockey_col.find("a")
            jockey = jockey_a.get("title").strip() if (jockey_a and jockey_a.get("title")) else jockey_col.get_text(strip=True)
        
        trainer_col = row.find("td", class_="Trainer") or (cols[13] if len(cols) > 13 else None)
        stable = "-"
        if trainer_col:
            belonging_text = trainer_col.get_text(strip=True)
            belonging = belonging_text[belonging_text.find("["):belonging_text.find("]")+1] if "[" in belonging_text and "]" in belonging_text else ""
            trainer_a = trainer_col.find("a")
            if trainer_a and trainer_a.get("title"):
                trainer_name = trainer_a.get("title").strip()
                stable = f"{belonging}{trainer_name}" if belonging else trainer_name
            else:
                stable = belonging_text

        data.append({
            "着順": cols[0].get_text(strip=True),
            "枠": cols[1].get_text(strip=True),
            "馬番": cols[2].get_text(strip=True),
            "馬名": cols[3].get_text(strip=True),
            "性齢": cols[4].get_text(strip=True),
            "騎手": jockey,
            "厩舎": stable,
            "タイム": cols[7].get_text(strip=True),
            "着差": cols[8].get_text(strip=True),
            "コーナー通過順": cols[10].get_text(strip=True)
        })
        
    return pd.DataFrame(data), full_sheet_name

def write_race_to_sheet(spreadsheet, sheet_name, df):
    try:
        target_ws = spreadsheet.worksheet(sheet_name)
        target_ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        target_ws = spreadsheet.add_worksheet(title=sheet_name, rows=len(df)+10, cols=len(df.columns)+5)

    df = df.fillna("")
    rows_to_append = [df.columns.tolist()] + df.astype(str).values.tolist()
    target_ws.update(values=rows_to_append)
    return target_ws.title

# ================= ================= =================
#  2. 馬データ スクレイピング
# ================= ================= =================
def fetch_horse_data(url):
    res = requests.get(url, headers=HEADERS)
    res.encoding = res.apparent_encoding
    html_text = res.text
    soup = BeautifulSoup(html_text, "html.parser")

    # --- 馬名 ---
    horse_title = soup.find("div", class_="horse_title")
    if horse_title and horse_title.find("h1"):
        horse_name = horse_title.find("h1").get_text(strip=True)
    else:
        h1 = soup.find("h1")
        horse_name = h1.get_text(strip=True) if h1 else "競走馬"
    horse_name = re.sub(r'[\r\n\t\s]+', '', horse_name)

    # --- プロフィール情報 ---
    info_dict = {}
    info_table = soup.find("table", class_="db_prof_table")
    if info_table:
        for tr in info_table.find_all("tr"):
            th = tr.find("th")
            td = tr.find("td")
            if th and td:
                key = th.get_text(strip=True)
                val = td.get_text(" ", strip=True)
                info_dict[key] = val

    # --- 血統情報 ---
    father, mother, mother_father = "-", "-", "-"
    blood_table = soup.find("table", class_="blood_table")
    if blood_table:
        td_list = blood_table.find_all("td")
        extracted_names = []
        for td in td_list:
            a_tag = td.find("a")
            if a_tag:
                txt = a_tag.get_text(strip=True)
                if txt and txt not in extracted_names:
                    extracted_names.append(txt)
        if len(extracted_names) >= 1: father = extracted_names[0]
        if len(extracted_names) >= 2: mother = extracted_names[1]
        if len(extracted_names) >= 3: mother_father = extracted_names[2]

    # --- コース適性・距離適性 ---
    turf_dirt = "-"
    dist_apt = "-"
    diag_table = soup.find("table", class_="db_dia_table") or soup.find("table", class_="dial_table")
    if diag_table:
        text_content = diag_table.get_text()
        if "芝" in text_content and "ダート" in text_content: turf_dirt = "芝・ダート兼配"
        elif "芝" in text_content: turf_dirt = "芝"
        elif "ダート" in text_content: turf_dirt = "ダート"

        m_dist = re.search(r'(\d{4}m\s*～\s*\d{4}m|\d{4}m前後|\d{4}m)', text_content)
        if m_dist: dist_apt = m_dist.group(0)

    basic_data = [
        {"項目": "馬名", "内容": horse_name},
        {"項目": "生年月日", "内容": info_dict.get("生年月日", "-")},
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
        {"項目": "適性・芝ダート", "内容": turf_dirt},
        {"項目": "距離適性", "内容": dist_apt}
    ]
    df_basic = pd.DataFrame(basic_data)

    # --- 競走成績テーブル ---
    df_results = pd.DataFrame()
    try:
        tables = pd.read_html(io.StringIO(html_text))
        for df in tables:
            cols = [str(c) for c in df.columns]
            cols_str = "".join(cols)
            if "日付" in cols_str and "レース名" in cols_str:
                df_results = df
                break
    except Exception:
        pass

    if not df_results.empty:
        df_results = df_results.fillna("-")
        df_results.columns = [str(c).strip() for c in df_results.columns]
        if "映像" in df_results.columns:
            df_results = df_results.drop(columns=["映像"])

    clean_horse_name = re.sub(r'[/\\?*:[\]]', '', horse_name)[:30]
    return df_basic, df_results, clean_horse_name


def update_horse_sheet(spreadsheet, sheet_name, df_basic, df_results):
    try:
        ws = spreadsheet.worksheet(sheet_name)
        existing_values = ws.get_all_values()
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=sheet_name, rows=300, cols=30)
        existing_values = []

    existing_keys = set()
    for row in existing_values:
        if len(row) >= 5 and row[0] not in ["日付", "【基本情報】", "【競走成績】", ""]:
            existing_keys.add(f"{row[0]}_{row[4]}")

    new_races_count = 0
    if existing_keys and not df_results.empty:
        for idx, row in df_results.iterrows():
            date_val = str(row.get("日付", ""))
            race_val = str(row.get("レース名", ""))
            key = f"{date_val}_{race_val}"
            if key not in existing_keys:
                new_races_count += 1

    all_rows = []
    all_rows.append(["【基本情報】", ""])
    for idx, row in df_basic.iterrows():
        all_rows.append([str(row["項目"]), str(row["内容"])])
    
    all_rows.append([])
    all_rows.append(["【競走成績】"])
    
    if not df_results.empty:
        all_rows.append(df_results.columns.tolist())
        for idx, row in df_results.iterrows():
            all_rows.append(row.astype(str).tolist())
    else:
        all_rows.append(["（※競走成績データなし・未出走馬）"])

    ws.clear()
    ws.update(values=all_rows)

    if not existing_values:
        return "新規作成完了"
    elif new_races_count > 0:
        return f"更新完了 (+{new_races_count}件追加)"
    else:
        return "最新化完了 (追加データなし)"

# ================= ================= =================
#  3. 出走表からの馬URL一括抽出機能
# ================= ================= =================
def extract_horse_urls_from_shutuba(shutuba_url):
    res = requests.get(shutuba_url, headers=HEADERS)
    res.encoding = res.apparent_encoding
    soup = BeautifulSoup(res.text, "html.parser")

    horse_links = []
    seen = set()
    
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/horse/" in href:
            match = re.search(r'/horse/(\d{10})', href)
            if match:
                horse_id = match.group(1)
                full_url = f"https://db.netkeiba.com/horse/{horse_id}"
                if full_url not in seen:
                    seen.add(full_url)
                    horse_links.append(full_url)

    return horse_links


# ================= ================= =================
#  Streamlit UI (st.formで入力項目を完全保護)
# ================= ================= =================
tab1, tab2, tab3 = st.tabs(["🏁 レース結果取得", "🐎 単体馬データ取得", "🏇 出走表から全馬一括取得"])

# --- TAB 1: レース結果 ---
with tab1:
    st.header("レース結果の取得")
    st.caption("保存先: SLEIPNIR_G_2026RaceDB")
    
    with st.form(key="form_race"):
        race_url = st.text_input("レース結果のURL", value="", key="race_url_input")
        submit_race = st.form_submit_button("レース結果を書き込む", type="primary")

    if submit_race:
        if not race_url.strip():
            st.warning("レース結果のURLを入力してください。")
        else:
            with st.spinner("レースデータを取得中..."):
                try:
                    df_res, sheet_name = fetch_race_results(race_url)
                    if df_res is not None and not df_res.empty:
                        client = get_gspread_client()
                        spreadsheet = client.open_by_key(RACE_SPREADSHEET_KEY)
                        written_title = write_race_to_sheet(spreadsheet, sheet_name, df_res)
                        
                        append_execution_log(spreadsheet, "レース結果取得", race_url, "SUCCESS", f"シート '{written_title}' ({len(df_res)}件)")
                        st.success(f"✅ シート '{written_title}' へデータを出力しました！")
                        st.dataframe(df_res)
                    else:
                        st.error("データの取得に失敗しました。URLを確認してください。")
                except Exception as e:
                    st.error(f"エラーが発生しました: {e}")

# --- TAB 2: 単体馬データ ---
with tab2:
    st.header("馬データの取得 (単体)")
    st.caption("保存先: 馬データ専用スプレッドシート")
    
    with st.form(key="form_horse"):
        horse_url = st.text_input("馬ページのURL (例: https://db.netkeiba.com/horse/2021103272)", value="", key="horse_url_input")
        submit_horse = st.form_submit_button("馬データを書き込む", type="primary")

    if submit_horse:
        if not horse_url.strip():
            st.warning("馬ページのURLを入力してください。")
        else:
            with st.spinner("馬データを解析・照合中..."):
                try:
                    df_basic, df_results, horse_name = fetch_horse_data(horse_url)
                    if df_basic is not None and not df_basic.empty:
                        client = get_gspread_client()
                        spreadsheet = client.open_by_key(HORSE_SPREADSHEET_KEY)
                        
                        msg = update_horse_sheet(spreadsheet, horse_name, df_basic, df_results)
                        append_execution_log(spreadsheet, "単体馬データ取得", horse_name, "SUCCESS", f"{msg} (成績:{len(df_results)}件)")
                        
                        st.success(f"✅ シート '{horse_name}' : {msg}")
                        
                        st.subheader("【基本情報】")
                        st.dataframe(df_basic)
                        
                        st.subheader("【競走成績】")
                        if not df_results.empty:
                            st.dataframe(df_results)
                        else:
                            st.warning("競走成績データが抽出されませんでした。")
                    else:
                        st.error("馬データの取得に失敗しました。URLを確認してください。")
                except Exception as e:
                    st.error(f"エラーが発生しました: {e}")

# --- TAB 3: 出走表から全馬取得 ---
with tab3:
    st.header("出走表からの全馬一括取得")
    st.caption("保存先: 馬データ専用スプレッドシート")
    
    with st.form(key="form_shutuba"):
        shutuba_url = st.text_input("出走表のURL", value="", key="shutuba_url_input")
        submit_shutuba = st.form_submit_button("出走全馬のデータを一括書き込み", type="primary")

    if submit_shutuba:
        if not shutuba_url.strip():
            st.warning("出走表のURLを入力してください。")
        else:
            with st.spinner("出走馬のURLを抽出中..."):
                horse_urls = extract_horse_urls_from_shutuba(shutuba_url)

            if not horse_urls:
                st.error("出走馬のリンクを検出できませんでした。URLを確認してください。")
            else:
                st.info(f"🐎 計 {len(horse_urls)} 頭の出走馬を検出しました。開始します...")
                client = get_gspread_client()
                spreadsheet = client.open_by_key(HORSE_SPREADSHEET_KEY)

                progress_bar = st.progress(0)
                status_text = st.empty()
                success_count = 0

                for idx, h_url in enumerate(horse_urls):
                    try:
                        df_basic, df_results, horse_name = fetch_horse_data(h_url)
                        if df_basic is not None and not df_basic.empty:
                            msg = update_horse_sheet(spreadsheet, horse_name, df_basic, df_results)
                            res_msg = f"[{idx+1}/{len(horse_urls)}] {horse_name} : {msg}"
                            status_text.text(res_msg)
                            append_execution_log(spreadsheet, "出走表全馬取得", horse_name, "SUCCESS", msg)
                            success_count += 1
                    except Exception as e:
                        status_text.text(f"[{idx+1}/{len(horse_urls)}] エラー: {e}")

                    progress_bar.progress((idx + 1) / len(horse_urls))
                    time.sleep(1)

                st.success(f"🎉 処理完了 ({success_count}/{len(horse_urls)} 頭成功)")
