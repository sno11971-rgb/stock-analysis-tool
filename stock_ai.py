import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import urllib3
import time
import json
import re

# --- 設定區 ---
# 1. 忽略 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 設定頁面配置
st.set_page_config(
    page_title="牛大鼻深度分析", 
    page_icon="📈", 
    layout="wide",
    initial_sidebar_state="expanded" 
)

# 【修改點】將 st.title 改為 st.header，字體會變小一點
st.header("📈 牛大鼻深度分析")
# st.markdown("### 📈 牛大鼻深度分析") # 如果覺得還不夠小，可以改用這一行 (H3大小)

st.markdown("整合 **EPS/營收**、**殖利率**、**KD指標** 與 **均線**。內建 **模型偵測** 功能，徹底解決 404 問題。")

# --- 1. Yahoo 爬蟲 (EPS + 股價 + 產業) ---
def get_yahoo_basic_data(session, stock_code):
    stock_code = str(stock_code).strip()
    if not stock_code: return None

    url_eps = f"https://tw.stock.yahoo.com/quote/{stock_code}.TW/eps"
    url_main = f"https://tw.stock.yahoo.com/quote/{stock_code}.TW"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }

    data = {
        "股票代碼": stock_code,
        "公司": "",
        "產業別": "-",
        "股價": 0.0,
        "2024(Q1~Q3)": 0.0,
        "2024 EPS": 0.0, 
        "2025(Q1~Q3)": 0.0,
        "EPS狀態": "待確認"
    }

    try:
        res_eps = session.get(url_eps, headers=headers, verify=False, timeout=10)
        soup_eps = BeautifulSoup(res_eps.text, 'html.parser')

        title_text = soup_eps.title.string if soup_eps.title else ""
        if stock_code in title_text:
            if "(" in title_text:
                data["公司"] = title_text.split("(")[0].strip()
            else:
                data["公司"] = title_text.split("-")[0].strip()
            data["EPS狀態"] = "成功"
        else:
            data["公司"] = stock_code 

        try:
            price_span = soup_eps.find('span', class_=re.compile(r'Fz\(32px\)'))
            if price_span:
                val = price_span.get_text().replace(',', '')
                data["股價"] = float(val) if val != '-' else 0.0
        except:
            pass

        targets = [
            ("2024", "Q1"), ("2024", "Q2"), ("2024", "Q3"), ("2024", "Q4"),
            ("2025", "Q1"), ("2025", "Q2"), ("2025", "Q3")
        ]
        
        all_divs = soup_eps.find_all('div')
        found_quarters = {"2024": set(), "2025": set()}
        
        for year, quarter in targets:
            search_terms = [f"{year} {quarter}", f"{year}{quarter}", f"{year}第{quarter[-1]}季"]
            for i, div in enumerate(all_divs):
                if div.get_text(strip=True) in search_terms:
                    for j in range(1, 6):
                        if i + j < len(all_divs):
                            next_text = all_divs[i+j].get_text(strip=True)
                            try:
                                val = float(next_text.replace(',', ''))
                                if '%' not in next_text and abs(val) < 800:
                                    if quarter not in found_quarters[year]:
                                        if year == "2024":
                                            data["2024 EPS"] += val
                                            if quarter in ["Q1", "Q2", "Q3"]:
                                                data["2024(Q1~Q3)"] += val
                                        else:
                                            data["2025(Q1~Q3)"] += val
                                        found_quarters[year].add(quarter)
                                    break
                            except: continue
                    break
        
        data["2024(Q1~Q3)"] = round(data["2024(Q1~Q3)"], 2)
        data["2024 EPS"] = round(data["2024 EPS"], 2)
        data["2025(Q1~Q3)"] = round(data["2025(Q1~Q3)"], 2)

        try:
            res_main = session.get(url_main, headers=headers, verify=False, timeout=10)
            soup_main = BeautifulSoup(res_main.text, 'html.parser')
            industry_link = soup_main.find('a', href=re.compile(r'class-quote'))
            if industry_link:
                data["產業別"] = industry_link.get_text(strip=True)
            else:
                cats = soup_main.find_all('li', class_=re.compile('category'))
                if cats:
                    data["產業別"] = cats[-1].get_text(strip=True)
        except:
            pass

        return data

    except Exception as e:
        data["EPS狀態"] = f"錯誤: {str(e)}"
        return data

# --- 2. 技術指標 (KD + 均線) ---
def get_technical_data(session, stock_code):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{stock_code}.TW?interval=1d&range=1y"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    result = {
        "K值": 0.0, "D值": 0.0,
        "5MA": 0.0, "10MA": 0.0, "20MA": 0.0, "120MA": 0.0
    }
    
    try:
        response = session.get(url, headers=headers, verify=False, timeout=10)
        data = response.json()
        quote = data['chart']['result'][0]['indicators']['quote'][0]
        highs = quote['high']
        lows = quote['low']
        closes = quote['close']
        
        df = pd.DataFrame({'High': highs, 'Low': lows, 'Close': closes}).dropna()
        if len(df) < 9: return result
        
        k, d = 50, 50
        k_list, d_list = [], []
        
        for i in range(len(df)):
            if i < 8:
                k_list.append(50); d_list.append(50)
                continue
            window_high = max(df['High'][i-8:i+1])
            window_low = min(df['Low'][i-8:i+1])
            close = df['Close'].iloc[i]
            if window_high == window_low: rsv = 50
            else: rsv = (close - window_low) / (window_high - window_low) * 100
            k = (2/3) * k + (1/3) * rsv
            d = (2/3) * d + (1/3) * k
            k_list.append(k); d_list.append(d)
            
        result["K值"] = round(k_list[-1], 2)
        result["D值"] = round(d_list[-1], 2)
        
        # MA 計算
        df['5MA'] = df['Close'].rolling(window=5).mean()
        df['10MA'] = df['Close'].rolling(window=10).mean()
        df['20MA'] = df['Close'].rolling(window=20).mean()
        df['120MA'] = df['Close'].rolling(window=120).mean()
        
        last_row = df.iloc[-1]
        result["5MA"] = round(last_row['5MA'], 2) if not pd.isna(last_row['5MA']) else 0.0
        result["10MA"] = round(last_row['10MA'], 2) if not pd.isna(last_row['10MA']) else 0.0
        result["20MA"] = round(last_row['20MA'], 2) if not pd.isna(last_row['20MA']) else 0.0
        result["120MA"] = round(last_row['120MA'], 2) if not pd.isna(last_row['120MA']) else 0.0

    except: pass
    return result

# --- 3. CMoney 股利 ---
def get_dividend_data_cmoney(session, stock_code):
    url = f"https://www.cmoney.tw/forum/stock/{stock_code}?s=dividend"
    headers = {'User-Agent': 'Mozilla/5.0'}
    result = {"現金股利": 0.0, "股票股利": 0.0, "除息日": "-", "除權日": "-"}
    try:
        response = session.get(url, headers=headers, verify=False, timeout=10)
        dfs = pd.read_html(response.text)
        target_df = None
        for df in dfs:
            if "現金股利" in str(df.columns) and "股票股利" in str(df.columns):
                target_df = df; break
        if target_df is not None and not target_df.empty:
            row = target_df.iloc[0].tolist()
            try: result["現金股利"] = float(str(row[1]).replace('-', '0'))
            except: pass
            if len(row) > 2 and re.match(r'202\d/\d{2}/\d{2}', str(row[2])):
                result["除息日"] = str(row[2])
            flat_cols = [''.join(str(col)).replace(' ', '') for col in target_df.columns.values]
            stock_idx = -1
            for i, c in enumerate(flat_cols):
                if "股票股利" in c and "股" in c: stock_idx = i; break
            if stock_idx != -1:
                try: result["股票股利"] = float(str(row[stock_idx]).replace('-', '0'))
                except: pass
                if stock_idx + 1 < len(row):
                    val = str(row[stock_idx+1])
                    if re.match(r'202\d/\d{2}/\d{2}', val): result["除權日"] = val
        return result
    except: return result

# --- 4. Yahoo 營收 ---
def get_revenue_data_yahoo(session, stock_code):
    url = f"https://tw.stock.yahoo.com/quote/{stock_code}.TW/revenue"
    headers = {'User-Agent': 'Mozilla/5.0'}
    result = {"2024 Q4營收": 0, "2025 Q4營收": 0}
    try:
        response = session.get(url, headers=headers, verify=False, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        list_items = soup.find_all('li')
        targets_2024 = ["2024/10", "2024/11", "113/10", "113/11"]
        targets_2025 = ["2025/10", "2025/11", "114/10", "114/11"]
        for li in list_items:
            cols = [d.get_text(strip=True) for d in li.find_all('div', recursive=False)]
            if len(cols) < 2: 
                cols = li.get_text(" ", strip=True).split(" ")
                if len(cols) < 2: continue
            date_col = cols[0]
            try: revenue = float(cols[1].replace(',', '').replace('-', '0'))
            except: continue
            if any(t in date_col for t in targets_2024): result["2024 Q4營收"] += revenue
            if any(t in date_col for t in targets_2025): result["2025 Q4營收"] += revenue
    except: pass 
    return result

# --- 5. AI 分析函數 (動態模型) ---
def analyze_with_gemini_dynamic(api_key, model_name, company, eps_diff_pct, div_data, yield_rate, rev_diff_pct, kd_k, kd_d, industry):
    if not api_key: return "未輸入 API Key"
    
    # 直接使用偵測到的有效模型名稱
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    
    kd_status = "黃金交叉" if kd_k > kd_d else "死亡交叉"
    
    prompt = f"""
    分析公司：{company} ({industry})
    數據：
    1. EPS成長率：{eps_diff_pct:+.2f}% (前三季YoY)。
    2. 營收成長率：{rev_diff_pct:+.2f}% (Q4前兩月YoY)。
    3. 殖利率：{yield_rate}%。
    4. 技術面：KD ({kd_status}, K={kd_k})。
    
    請用「繁體中文」一句話簡評：
    綜合成長動能(EPS/營收)與殖利率/技術面，給出短評。50字內。
    """
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {'Content-Type': 'application/json'}

    try:
        res = requests.post(url, headers=headers, data=json.dumps(payload), verify=False, timeout=15)
        
        if res.status_code == 200:
            return res.json()['candidates'][0]['content']['parts'][0]['text']
        else:
            try:
                err = res.json()['error']['message']
                return f"Err {res.status_code}: {err[:15]}..."
            except:
                return f"Err {res.status_code}"
                
    except Exception as e:
        return f"連線失敗"

# --- 偵測模型函數 ---
def get_available_models(api_key):
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        res = requests.get(url, verify=False, timeout=5)
        if res.status_code == 200:
            data = res.json()
            # 抓出支援 generateContent 的模型名稱 (移除 models/ 前綴)
            return [m['name'].replace('models/', '') for m in data.get('models', []) if 'generateContent' in m.get('supportedGenerationMethods', [])]
    except: pass
    return []

# --- 側邊欄設定 ---
st.sidebar.header("🔑 設定與輸入")

# 1. 取得 API Key (放最上面) 
loaded_from_secrets = False 
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
    loaded_from_secrets = True 
    use_ai_default = True 
else:
    api_key = st.sidebar.text_input("Gemini API Key", type="password")
    use_ai_default = False

# 2. AI 功能開關 (放最上面)
use_ai = st.sidebar.checkbox("開啟 AI 分析功能", value=use_ai_default)

st.sidebar.divider()

# 3. 輸入方式與股票代碼 (放在中間)
input_method = st.sidebar.radio("選擇輸入方式", ["直接輸入代號", "上傳 Excel/CSV"])
stock_codes = []

if input_method == "直接輸入代號":
    user_input = st.sidebar.text_area("輸入代號 (換行分隔)", "2330\n2317\n6691")
    if user_input: stock_codes = [c.strip() for c in user_input.split('\n') if c.strip()]
elif input_method == "上傳 Excel/CSV":
    uploaded = st.sidebar.file_uploader("上傳檔案", type=['xlsx', 'csv'])
    if uploaded:
        try:
            if uploaded.name.endswith('.csv'): df = pd.read_csv(uploaded, dtype=str)
            else: df = pd.read_excel(uploaded, dtype=str)
            target = df.columns[0]
            for c in df.columns:
                if "代號" in str(c) or "code" in str(c).lower(): target = c; break
            stock_codes = df[target].dropna().astype(str).tolist()
            st.sidebar.success(f"讀取 {len(stock_codes)} 筆")
        except: st.sidebar.error("讀取失敗")

st.sidebar.divider()

# 4. AI 模型偵測與選擇 (維持在最下方)
model_option = None # 先初始化變數，避免錯誤

if use_ai:
    # 初始化 Session State
    if 'model_list' not in st.session_state: 
        st.session_state['model_list'] = ["gemini-1.5-flash", "gemini-pro"]
    if 'is_detected' not in st.session_state:
        st.session_state['is_detected'] = False

    # 封裝偵測函數
    def run_model_detection():
        with st.spinner("正在自動連接 Google 取得模型清單..."):
            detected = get_available_models(api_key)
            if detected:
                st.session_state['model_list'] = detected
                st.session_state['is_detected'] = True
                return True, len(detected)
            else:
                return False, 0

    # 自動執行邏輯
    if api_key and not st.session_state['is_detected']:
        success, count = run_model_detection()
        if success:
            st.sidebar.success(f"已自動載入 {count} 個模型 ✅")

    # 手動按鈕
    if st.sidebar.button("🔄 手動重新偵測模型"):
        success, count = run_model_detection()
        if success:
            st.sidebar.success(f"成功！找到 {count} 個可用模型")
        else:
            st.sidebar.error("偵測失敗，請檢查 Key")
    
    # 選擇模型
    options = st.session_state['model_list']
    idx = 0
    if "gemini-1.5-flash" in options:
        idx = options.index("gemini-1.5-flash")
    model_option = st.sidebar.selectbox("2. 選擇模型", options, index=idx)

# 5. Secrets 載入成功訊息 (維持在最底部)
if loaded_from_secrets:
    st.sidebar.markdown("---") 
    st.sidebar.success("已從系統設定載入 API Key 🔑")

# --- 主程式 ---
if stock_codes:
    if st.button("🚀 開始查詢", type="primary"):
        session = requests.Session()
        results = []
        bar = st.progress(0)
        status = st.empty()
        
        for i, code in enumerate(stock_codes):
            status.text(f"正在處理: {code} ...")
            
            row = get_yahoo_basic_data(session, code)
            div_data = get_dividend_data_cmoney(session, code)
            row.update(div_data)
            rev_data = get_revenue_data_yahoo(session, code)
            row.update(rev_data)
            
            # 技術指標 (KD + 均線)
            tech_data = get_technical_data(session, code)
            row.update(tech_data)
            
            # --- 數據計算 ---
            rev_24 = row["2024 Q4營收"]
            rev_25 = row["2025 Q4營收"]
            if rev_24 != 0:
                row["營收差異(%)"] = ((rev_25 - rev_24) / rev_24) * 100
            else:
                row["營收差異(%)"] = 0.0
            
            if row["股價"] and row["股價"] > 0:
                try:
                    cash_yield = row["現金股利"] / row["股價"]
                    stock_yield = row["股票股利"] / 10
                    total_yield = (cash_yield + stock_yield) * 100
                    row["還原殖利率(%)"] = round(total_yield, 2)
                except:
                    row["還原殖利率(%)"] = 0.0
            else:
                row["還原殖利率(%)"] = 0.0
            
            if row.get("EPS狀態") == "成功":
                eps_24 = row["2024(Q1~Q3)"]
                eps_25 = row["2025(Q1~Q3)"]
                if eps_24 != 0:
                    row["EPS 差異(%)"] = ((eps_25 - eps_24) / abs(eps_24)) * 100
                else:
                    row["EPS 差異(%)"] = 0.0
                
                # AI 分析
                if use_ai and api_key and model_option:
                    status.text(f"分析中: {code}...")
                    anl = analyze_with_gemini_dynamic(
                        api_key, model_option, row["公司"], 
                        row["EPS 差異(%)"], div_data, row["還原殖利率(%)"], row["營收差異(%)"],
                        row["K值"], row["D值"], row["產業別"]
                    )
                    row["AI 分析"] = anl
                    if "Err" not in anl: time.sleep(4)
                else:
                    row["AI 分析"] = "未開啟" if not use_ai else "請先偵測模型"
                    time.sleep(0.5)
            else:
                 row["EPS 差異(%)"] = 0.0
            
            results.append(row)
            bar.progress((i + 1) / len(stock_codes))
            
        bar.progress(100)
        status.text("查詢完成！")
        
        if results:
            df_result = pd.DataFrame(results)
            
            # 更新欄位順序 (加入均線)
            cols = ["股票代碼", "公司", "產業別", "股價", 
                    "K值", "D值", "5MA", "10MA", "20MA", "120MA",
                    "還原殖利率(%)",
                    "2024(Q1~Q3)", "2025(Q1~Q3)", "EPS 差異(%)", "2024 EPS",
                    "2024 Q4營收", "2025 Q4營收", "營收差異(%)",
                    "現金股利", "股票股利", "AI 分析"]
            
            cols = [c for c in cols if c in df_result.columns]
            df_result = df_result[cols]
            
            def highlight_color(row):
                styles = [''] * len(row)
                def get_idx(col_name):
                    try: return row.index.get_loc(col_name)
                    except: return -1

                idx = get_idx('還原殖利率(%)')
                if idx != -1 and isinstance(row['還原殖利率(%)'], (int, float)) and row['還原殖利率(%)'] > 5:
                    styles[idx] = 'color: red; font-weight: bold'
                
                for col in ['EPS 差異(%)', '營收差異(%)']:
                    idx = get_idx(col)
                    if idx != -1 and isinstance(row[col], (int, float)):
                        if row[col] > 0: styles[idx] = 'color: red'
                        elif row[col] < 0: styles[idx] = 'color: green'
                
                k_idx = get_idx('K值')
                d_idx = get_idx('D值')
                if k_idx != -1 and d_idx != -1:
                    k_val = row['K值']
                    d_val = row['D值']
                    if k_val == 0: k_val = 50
                    if d_val == 0: d_val = 50
                    if k_val > d_val:
                        styles[k_idx] = 'color: red'
                        styles[d_idx] = 'color: red'
                    else:
                        styles[k_idx] = 'color: green'
                        styles[d_idx] = 'color: green'
                return styles

            styled_df = df_result.style.apply(highlight_color, axis=1)
            
            st.dataframe(
                styled_df,
                column_config={
                    "還原殖利率(%)": st.column_config.NumberColumn(format="%.2f%%"),
                    "EPS 差異(%)": st.column_config.NumberColumn(format="%+.2f%%"),
                    "營收差異(%)": st.column_config.NumberColumn(format="%+.2f%%"),
                    "2024 Q4營收": st.column_config.NumberColumn(format="%d"),
                    "2025 Q4營收": st.column_config.NumberColumn(format="%d"),
                    "股價": st.column_config.NumberColumn(format="%.1f"),
                    "K值": st.column_config.NumberColumn(format="%.2f"),
                    "D值": st.column_config.NumberColumn(format="%.2f"),
                    "5MA": st.column_config.NumberColumn(format="%.2f"),
                    "10MA": st.column_config.NumberColumn(format="%.2f"),
                    "20MA": st.column_config.NumberColumn(format="%.2f"),
                    "120MA": st.column_config.NumberColumn(format="%.2f"),
                },
                use_container_width=True,
                height=600
            )
            
            csv = df_result.to_csv(index=False).encode('utf-8-sig')
            st.download_button("📥 下載表格資料", csv, "stock_analysis_full.csv", "text/csv")
