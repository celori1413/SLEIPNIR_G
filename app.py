import re
import gspread
import pandas as pd
import requests
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
import streamlit as st

st.set_page_config(page_title="SLEIPNIR_DB02", layout="centered")

st.title("SLEIPNIR🏇DB02")
st.write("netkeibaのURLを入力すると、データを抽出してGoogleスプレッドシートへ書き込みます。")

# --- 画面入力項目 ---
race_url = st.text_input(
    "レース結果のURL",
    value="https://race.netkeiba.com/race/result.html?race_id=202609030411&rf=race_submenu"
)

# 新しいスプレッドシート（SLEIPNIR_G_DB_2026）の設定
SPREADSHEET_KEY = "1_N4GQm5DeWh6lQrRjsA3nDDX5RgSNYwDy83cZiuCJ2c"
TARGET_GID = 0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Safari/537.36"
    )
}

def clean_private_key(raw_key):
    """
    Secretsから受け取った秘密鍵の不要な空白、制御文字、ヘッダー/フッター崩れを完全補正する関数
    """
    key_str = str(raw_key)
    # \n という2文字の文字列があれば実際の改行コードに置換
    key_str = key_str.replace("\\n", "\n")
    
    # ヘッダーとフッターを除いた本体部分の英数字・記号のみを取り出す
    body = key_str.replace("-----BEGIN PRIVATE KEY-----", "").replace("-----END PRIVATE KEY-----", "")
    # 余計な空白・改行・制御文字を除去
    body = re.sub(r'[\s\r\n\t]+', '', body)
    
    # 64文字ごとに改行を入れてPEMフォーマットを正規化
    formatted_body = "\n".join([body[i:i+64] for i in range(0, len(body), 64)])
    
    # 正しいPEMフォーマットを再構築
    clean_key = f"-----BEGIN PRIVATE KEY-----\n{formatted_body}\n-----END PRIVATE KEY-----\n"
    return clean_key

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
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    
    # Secrets から認証情報を取得
    if "gcp_service_account" in st.secrets:
        key_dict = dict(st.secrets["gcp_service_account"])
        if "private_key" in key_dict:
            key_dict["private_key"] = clean_private_key(key_dict["private_key"])
            
        creds = Credentials.from_service_account_info(key_dict, scopes=scopes)
    else:
        creds = Credentials.from_service_account_file("secret_key.json", scopes=scopes)
        
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SPREADSHEET_KEY)
    
    # GID によるワークシート検索
    target_ws = None
    for ws in spreadsheet.worksheets():
        if str(ws.id) == str(TARGET_GID):
            target_ws = ws
            break
            
    if not target_ws:
        target_ws = spreadsheet.sheet1
        st.warning(f"指定のGID ({TARGET_GID}) が見つからなかったため、最初のシート '{target_ws.title}' に書き込みます。")

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
