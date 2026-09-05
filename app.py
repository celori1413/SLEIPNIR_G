import json
import gspread
import pandas as pd
import requests
from bs4 import BeautifulSoup
from oauth2client.service_account import ServiceAccountCredentials
import streamlit as st

st.set_page_config(page_title="SLEIPNIR_DB02", layout="centered")

st.title("SLEIPNIR🏇DB02")
st.write("netkeibaのURLを入力すると、データを抽出してGoogleスプレッドシートへ書き込みます。")

# --- 画面入力項目 ---
race_url = st.text_input(
    "レース結果のURL",
    value="https://race.netkeiba.com/race/result.html?race_id=202609030411&rf=race_submenu"
)

SPREADSHEET_KEY = "13YkfSZvwRV-sfX6F_rv6mVrEvZku0GZ4jJltbtIgYIE"
TARGET_GID = 675289019  # 数値型・文字列型の両方に対応

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Safari/537.36"
    )
}

def fetch_race_results(url):
    res = requests.get(url, headers=HEADERS)
    res.encoding = res.apparent_encoding
    soup = BeautifulSoup(res.text, "html.parser")
    
    table = soup.find("table", id="All_Result_Table") or soup.find("table", class_="RaceTable01")
    if not table:
        return None
        
    data = []
    rows = table.find_all("tr", class_="HorseList")
    
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 11:
            continue
            
        jockey_a = cols[6].find("a")
        jockey = (
            jockey_a.get("title").strip()
            if (jockey_a and jockey_a.get("title"))
            else cols[6].get_text(strip=True)
        )
        
        stable_col = cols[13] if len(cols) > 13 else None
        if stable_col:
            stable_a = stable_col.find("a")
            if stable_a and stable_a.get("title"):
                trainer_name = stable_a.get("title").strip()
                belonging = stable_col.get_text(strip=True).split("\n")[0]
                stable = f"{belonging}{trainer_name}"
            else:
                stable = stable_col.get_text(strip=True)
        else:
            stable = "-"

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
        
    return pd.DataFrame(data)

def append_to_sheet(df):
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    
    # 認証情報の取得
    if "gcp_service_account" in st.secrets:
        key_dict = dict(st.secrets["gcp_service_account"])
        if "private_key" in key_dict:
            key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")
        creds = ServiceAccountCredentials.from_json_keyfile_dict(key_dict, scope)
    else:
        creds = ServiceAccountCredentials.from_json_keyfile_name("secret_key.json", scope)
        
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SPREADSHEET_KEY)
    
    # シートの検索（ID比較の安全化）
    target_ws = None
    for ws in spreadsheet.worksheets():
        if str(ws.id) == str(TARGET_GID):
            target_ws = ws
            break
            
    # 見つからない場合は1枚目のワークシートをフォールバックとして使用
    if not target_ws:
        target_ws = spreadsheet.sheet1
        st.warning(f"指定のGID ({TARGET_GID}) が見つからなかったため、最初のシート '{target_ws.title}' に書き込みます。")

    # NaN（空文字）の変換処理を行いデータ整形
    df = df.fillna("")
    rows_to_append = df.astype(str).values.tolist()

    existing = target_ws.get_all_values()
    if not existing:
        target_ws.append_row(df.columns.tolist())
        
    target_ws.append_rows(rows_to_append)
    st.success(f"✅ シート '{target_ws.title}' へ {len(df)} 件のデータを追記しました！")

# --- 実行ボタン ---
if st.button("スプレッドシートへ書き込む", type="primary"):
    with st.spinner("データを取得・送信中..."):
        try:
            df_res = fetch_race_results(race_url)
            if df_res is not None and not df_res.empty:
                append_to_sheet(df_res)
                st.dataframe(df_res)
            else:
                st.error("データの取得に失敗しました。URLを確認してください。")
        except Exception as e:
            st.error(f"エラーが発生しました: {e}")
