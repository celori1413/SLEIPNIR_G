import re
import time
from datetime import datetime
import gspread
import pandas as pd
import requests
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
import streamlit as st

st.set_page_config(page_title="SLEIPNIR_DB02", layout="wide")

st.title("SLEIPNIR🏇DB02")

# ================= ================= =================
#  スプレッドシート設定（用途別にID分離）
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

# ================= ================= =================
#  共通: ログ記録機能
# ================= ================= =================
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
#  1. レース結果 スクレイピング & 書き込み
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
#  2. 馬データ スクレイピング（高精度・完全版）
# ================= ================= =================
def fetch_horse_data(url):
    res = requests.get(url, headers=HEADERS)
    res.encoding = res.apparent_encoding
    soup = BeautifulSoup(res.text, "html.parser")

    # --- 馬名取得 ---
    horse_title = soup.find("div", class_="horse_title")
    if horse_title and horse_title.find("h1"):
        horse_name = horse_title.find("h1").get_text(strip=True)
    else:
        h1 = soup.find("h1")
        horse_name = h1.get_text(strip=True) if h1 else "競走馬"
    horse_name = re.sub(r'[\r\n\t]', '', horse_name)

    # --- 基本情報テーブル解析 ---
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

    # --- 血統情報（詳細抽出ロジック）---
    father, mother, mother_father = "-", "-", "-"
    blood_table = soup.find("table", class_="blood_table")
    if blood_table:
        tds = blood_table.find_all("td")
        p_links = []
        for td in tds:
            a_tag = td.find("a")
            if a_tag and "/horse/" in a_tag.get("href", ""):
                txt = a_tag.get_text(strip=True)
                if txt and txt not in p_links:
                    p_links.append(txt)
        
        if len(p_links) >= 1:
            father = p_links[0]
        if len(p_links) >= 2:
            mother = p_links[1]
        if len(p_links) >= 3:
            mother_father = p_links[2]

    # --- コース適性・距離適性（網羅的抽出ロジック）---
    turf_dirt = "-"
    dist_apt = "-"

    # パターンA: db_dia_table / dial_table
    diag_table = soup.find("table", class_="db_dia_table") or soup.find("table", class_="dial_table") or soup.find("div", class_="db_prof_box_02")
    if diag_table:
        text_content = diag_table.get_text()
        # 芝・ダート適性
        if "芝" in text_content and "ダート" in text_content:
            m_td = re.search(r'(芝[^\n\r]*|ダート[^\n\r]*)', text_content)
            if m_td:
                turf_dirt = m_td.group(0).strip()
            else:
                turf_dirt = "芝・ダート適性情報あり"
        elif "芝" in text_content:
            turf_dirt = "芝適性重視"
        elif "ダート" in text_content:
            turf_dirt = "ダート適性重視"

        # 距離適性
        m_dist = re.search(r'(\d{4}m\s*～\s*\d{4}m|\d{4}m[^\n\r]*)', text_content)
        if m_dist:
            dist_apt = m_dist.group(0).strip()

    # パターンB: 代替テキスト抽出（「適性」表記が含まれるエレメントを探す）
    if turf_dirt == "-" or dist_apt == "-":
        for elem in soup.find_all(["div", "td", "p"], class_=re.compile(r'prof|dia|apt|style')):
            txt = elem.get_text(strip=True)
            if turf_dirt == "-" and ("万能" in txt or "芝" in txt or "ダ" in txt):
                turf_dirt = txt[:30]
            if dist_apt == "-" and ("m" in txt and ("短距離" in txt or "マイル" in txt or "中距離" in txt or "長距離" in txt or "～" in txt)):
                dist_apt = txt[:30]

    # 基本データ構築（賞金データは完全削除）
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

    # --- 競走成績テーブル（db_h_race_results 完全解析）---
    results_data = []
    race_table = soup.find("table", class_="db_h_race_results")
    
    if not race_table:
        # 代替テーブル走査
        for tbl in soup.find_all("table"):
            if "日付" in tbl.get_text() and "レース名" in tbl.get_text():
                race_table = tbl
                break

    if race_table:
        rows = race_table.find_all("tr")
        for row in rows:
            cols = row.find_all("td")
            if not cols:
                continue
                
            col_texts = [c.get_text(strip=True) for c in cols]
            if len(col_texts) >= 15:
                # 標準28列に合わせた厳密マッピング
                row_dict = {
                    "日付": col_texts[0] if len(col_texts) > 0 else "-",
                    "開催": col_texts[1] if len(col_texts) > 1 else "-",
                    "天気": col_texts[2] if len(col_texts) > 2 else "-",
                    "R": col_texts[3] if len(col_texts) > 3 else "-",
                    "レース名": col_texts[4] if len(col_texts) > 4 else "-",
                    "頭数": col_texts[6] if len(col_texts) > 6 else "-",
                    "枠番": col_texts[7] if len(col_texts) > 7 else "-",
                    "馬番": col_texts[8] if len(col_texts) > 8 else "-",
                    "オッズ": col_texts[9] if len(col_texts) > 9 else "-",
                    "人気": col_texts[10] if len(col_texts) > 10 else "-",
                    "着順": col_texts[11] if len(col_texts) > 11 else "-",
                    "騎手": col_texts[12] if len(col_texts) > 12 else "-",
                    "斤量": col_texts[13] if len(col_texts) > 13 else "-",
                    "距離": col_texts[14] if len(col_texts) > 14 else "-",
                    "馬場": col_texts[15] if len(col_texts) > 15 else "-",
                    "タイム": col_texts[17] if len(col_texts) > 17 else "-",
                    "着差": col_texts[18] if len(col_texts) > 18 else "-",
                    "通過": col_texts[20] if len(col_texts) > 20 else "-",
                    "ペース": col_texts[21] if len(col_texts) > 21 else "-",
                    "上がり": col_texts[22] if len(col_texts) > 22 else "-",
                    "体重": col_texts[23] if len(col_texts) > 23 else "-",
                    "勝ち馬(2着馬)": col_texts[26] if len(col_texts) > 26 else col_texts[-1]
                }
                results_data.append(row_dict)

    df_results = pd.DataFrame(results_data)
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
            key = f"{row['日付']}_{row['レース名']}"
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
#  Streamlit UI (画面構成)
# ================= ================= =================
tab1, tab2, tab3 = st.tabs(["🏁 レース結果取得", "🐎 単体馬データ取得", "🏇 出走表から全馬一括取得"])

# --- TAB 1: レース結果 ---
with tab1:
    st.header("レース結果の取得")
    st.caption("保存先: SLEIPNIR_G_2026RaceDB")
    st.write("netkeibaのレース結果URLから、全着順データを抽出してレース用スプレッドシートへ書き込みます。")
    race_url = st.text_input("レース結果のURL", value="", key="race_url_input")

    if st.button("レース結果を書き込む", type="primary", key="btn_race"):
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
                        with st.expander("📝 実行ログ（最新）"):
                            st.write(f"【成功】 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - シート: {written_title} ({len(df_res)}件)")
                    else:
                        st.error("データの取得に失敗しました。URLを確認してください。")
                        client = get_gspread_client()
                        spreadsheet = client.open_by_key(RACE_SPREADSHEET_KEY)
                        append_execution_log(spreadsheet, "レース結果取得", race_url, "FAILED", "テーブルが検出できませんでした")
                except Exception as e:
                    st.error(f"エラーが発生しました: {e}")
                    try:
                        client = get_gspread_client()
                        spreadsheet = client.open_by_key(RACE_SPREADSHEET_KEY)
                        append_execution_log(spreadsheet, "レース結果取得", race_url, "ERROR", str(e))
                    except:
                        pass

# --- TAB 2: 単体馬データ ---
with tab2:
    st.header("馬データの取得 (単体)")
    st.caption("保存先: 馬データ専用スプレッドシート")
    st.write("同一シート内に基本情報と全競走成績を書き込みます。既に存在する馬の場合は自動で最新データに更新されます。")
    horse_url = st.text_input("馬ページのURL (例: https://db.netkeiba.com/horse/2021103272)", value="", key="horse_url_input")

    if st.button("馬データを書き込む", type="primary", key="btn_horse"):
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
                        st.dataframe(df_results if not df_results.empty else pd.DataFrame([{"メッセージ": "競走成績はありません"}]))
                        
                        with st.expander("📝 実行ログ（最新）"):
                            st.write(f"【成功】 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - 馬名: {horse_name} ({msg})")
                    else:
                        st.error("馬データの取得に失敗しました。URLを確認してください。")
                        client = get_gspread_client()
                        spreadsheet = client.open_by_key(HORSE_SPREADSHEET_KEY)
                        append_execution_log(spreadsheet, "単体馬データ取得", horse_url, "FAILED", "データ解析に失敗")
                except Exception as e:
                    st.error(f"エラーが発生しました: {e}")
                    try:
                        client = get_gspread_client()
                        spreadsheet = client.open_by_key(HORSE_SPREADSHEET_KEY)
                        append_execution_log(spreadsheet, "単体馬データ取得", horse_url, "ERROR", str(e))
                    except:
                        pass

# --- TAB 3: 出走表から全馬取得 ---
with tab3:
    st.header("出走表からの全馬一括取得")
    st.caption("保存先: 馬データ専用スプレッドシート")
    st.write("出走表（枠順表）のURLを入力すると、出走する全馬のデータを取得して一括でスプレッドシートへ書き込み・更新します。")
    shutuba_url = st.text_input("出走表のURL (例: https://race.netkeiba.com/race/shutuba.html?race_id=...)", value="", key="shutuba_url_input")

    if st.button("出走全馬のデータを一括書き込み", type="primary", key="btn_shutuba"):
        if not shutuba_url.strip():
            st.warning("出走表のURLを入力してください。")
        else:
            with st.spinner("出走馬のURLを抽出中..."):
                horse_urls = extract_horse_urls_from_shutuba(shutuba_url)

            if not horse_urls:
                st.error("出走馬のリンクを検出できませんでした。URLを確認してください。")
            else:
                st.info(f"🐎 計 {len(horse_urls)} 頭の出走馬を検出しました。データの取得・書き込みを開始します...")
                
                client = get_gspread_client()
                spreadsheet = client.open_by_key(HORSE_SPREADSHEET_KEY)

                progress_bar = st.progress(0)
                status_text = st.empty()

                success_count = 0
                log_details = []

                for idx, h_url in enumerate(horse_urls):
                    try:
                        df_basic, df_results, horse_name = fetch_horse_data(h_url)
                        if df_basic is not None and not df_basic.empty:
                            msg = update_horse_sheet(spreadsheet, horse_name, df_basic, df_results)
                            res_msg = f"[{idx+1}/{len(horse_urls)}] {horse_name} : {msg}"
                            status_text.text(res_msg)
                            log_details.append(res_msg)
                            
                            append_execution_log(spreadsheet, "出走表全馬取得", horse_name, "SUCCESS", msg)
                            success_count += 1
                        else:
                            res_msg = f"[{idx+1}/{len(horse_urls)}] 取得失敗: {h_url}"
                            status_text.text(res_msg)
                            log_details.append(res_msg)
                            append_execution_log(spreadsheet, "出走表全馬取得", h_url, "FAILED", "データ解析失敗")
                    except Exception as e:
                        res_msg = f"[{idx+1}/{len(horse_urls)}] エラー: {e}"
                        status_text.text(res_msg)
                        log_details.append(res_msg)
                        append_execution_log(spreadsheet, "出走表全馬取得", h_url, "ERROR", str(e))

                    progress_bar.progress((idx + 1) / len(horse_urls))
                    time.sleep(1)

                st.success(f"🎉 一括処理が完了しました！ ({success_count}/{len(horse_urls)} 頭成功)")
                
                with st.expander("📝 実行ログ詳細"):
                    for l in log_details:
                        st.write(l)
