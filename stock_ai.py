import os
import re
import json
import pdfplumber
import PyPDF2
import streamlit as st
import requests
import urllib3

# 忽略 SSL 警告 (配合 requests 使用 verify=False，避免雲端環境報錯)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# 🤖 Gemini API 設定與模型偵測區 (原生 requests 版)
# ==========================================
def get_gemini_key():
    """安全且全面地抓取 API Key (自動尋寶模式)"""
    # 1. 最標準的位置
    try:
        if "GEMINI_API_KEY" in st.secrets:
            return st.secrets["GEMINI_API_KEY"]
    except Exception: pass
        
    # 2. 如果不小心被包在 connections.gsheets 裡面
    try:
        if "connections" in st.secrets and "gsheets" in st.secrets["connections"]:
            if "GEMINI_API_KEY" in st.secrets["connections"]["gsheets"]:
                return st.secrets["connections"]["gsheets"]["GEMINI_API_KEY"]
    except Exception: pass

    # 3. 地毯式搜索所有層級
    try:
        for key, value in st.secrets.items():
            if isinstance(value, dict) and "GEMINI_API_KEY" in value:
                return value["GEMINI_API_KEY"]
    except Exception: pass
        
    # 4. 本地端測試用的環境變數
    return os.environ.get("GEMINI_API_KEY", "")

def get_available_model(api_key):
    """動態偵測可用的模型，徹底解決 404 Not Found 問題"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        res = requests.get(url, verify=False, timeout=5)
        if res.status_code == 200:
            data = res.json()
            # 抓出支援生成內容的模型
            models = [m['name'].replace('models/', '') for m in data.get('models', []) if 'generateContent' in m.get('supportedGenerationMethods', [])]
            
            # 優先挑選速度快且免費的 flash 模型
            if "gemini-1.5-flash" in models:
                return "gemini-1.5-flash"
            elif "gemini-1.5-flash-latest" in models:
                return "gemini-1.5-flash-latest"
            elif models:
                return models[0] # 備案：抓清單裡的第一個可用模型
    except Exception as e:
        print(f"偵測模型失敗: {e}")
    
    # 最底線的預設退路
    return "gemini-1.5-flash"

def parse_with_gemini(full_text, bank_name="信用卡"):
    """
    使用原生 requests 呼叫 Gemini，擺脫官方套件版本限制
    """
    api_key = get_gemini_key()
    
    if not api_key:
        return "0", "API未設定"

    # 1. 取得絕對能用的模型名稱
    model_name = get_available_model(api_key)
    
    # 2. 準備 API 網址與標頭
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}

    # 3. 準備給 AI 的提示詞 (Prompt)
    prompt = f"""
    你是一個專業的財務助理。請從下方的{bank_name}帳單文字中，提取出「應繳總金額」與「繳款截止日」。
    
    【🕵️ 聯邦銀行專屬破解指南】
    這份帳單的文字排版非常破碎，標題與金額通常會錯位。請優先根據以下三個最可靠的特徵來尋找「應繳總金額」：
    1. 尋找「自動轉帳扣款金額」字眼，它後方或下方的數字通常是正確答案（例如 297）。
    2. 尋找文件最底端（繳款單區域）出現的「應繳總金額」，其緊接著的數字就是答案。
    3. 尋找「本期新增(含調整)款項」後方的數字。

    【⚠️ 注意事項與規則】
    1. amount (應繳總金額)：請只提供數字字串，必須移除千分位逗號。例如 "2,980" 請輸出 "2980"。
    2. due_date (繳款截止日)：請維持帳單上的民國年日期格式（如 "115/04/03"）。請排除「優惠有效期間」。
    3. 輸出格式：請嚴格只回傳 JSON 格式，不要有任何其他文字。
    
    JSON 格式範例：
    {{
        "amount": "297",
        "due_date": "115/04/03"
    }}

    帳單文字內容如下：
    ---
    {full_text}
    ---
    """
    
    # 4. 組裝發送給 Google 的資料
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1} # 溫度調低，讓 AI 不要亂發揮，專注抓數字
    }

    # 5. 發送請求與處理結果
    try:
        res = requests.post(url, headers=headers, data=json.dumps(payload), verify=False, timeout=15)
        
        if res.status_code == 200:
            res_text = res.json()['candidates'][0]['content']['parts'][0]['text']
            
            # 使用正則表達式硬抓出 {} 包起來的 JSON 區塊，過濾掉 AI 的廢話
            json_match = re.search(r'\{.*?\}', res_text, re.DOTALL)
            if json_match:
                clean_json = json_match.group(0)
                data = json.loads(clean_json)
                amount = str(data.get("amount", "0")).replace(",", "")
                due_date = data.get("due_date", "未知")
                return amount, due_date
            else:
                return "0", "未回傳JSON格式"
        else:
            # 發生錯誤時，將錯誤代碼或訊息擷取出來顯示在畫面上
            try:
                err = res.json()['error']['message']
                return "0", f"API錯: {err[:12]}..."
            except:
                return "0", f"HTTP {res.status_code}"
                
    except Exception as e:
        return "0", f"連線失敗: {str(e)[:10]}"


# ==========================================
# 🔓 PDF 解密與基礎提取區
# ==========================================
def decrypt_pdf(pdf_path, passwords):
    """
    嘗試使用密碼清單解密 PDF。
    若成功，回傳 (解密後的暫存檔案路徑, True)。
    """
    decrypted_path = pdf_path.replace(".pdf", "_decrypted.pdf")
    success = False
    
    try:
        with open(pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            if reader.is_encrypted:
                for pwd in passwords:
                    if reader.decrypt(pwd):
                        writer = PyPDF2.PdfWriter()
                        for page in reader.pages:
                            writer.add_page(page)
                        with open(decrypted_path, 'wb') as out_f:
                            writer.write(out_f)
                        success = True
                        break
            else:
                # 沒加密就直接當作解密成功
                decrypted_path = pdf_path
                success = True
    except Exception as e:
        print(f"解密過程發生錯誤: {e}")
        
    return decrypted_path, success


# ==========================================
# 🔀 帳單路由與解析器
# ==========================================
def route_and_extract(pdf_path):
    """
    讀取 PDF 內容並根據檔名分派給對應的解析器。
    現在已經全面進化，統一交給 Gemini AI 處理！
    """
    filename = os.path.basename(pdf_path)
    bank = "未知銀行"
    amount = "0"
    due_date = "未知"
    
    # 1. 抽取 PDF 內的所有文字
    full_text = ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text: full_text += text + "\n"
    except Exception as e:
        print(f"讀取 PDF 失敗: {e}")
        return bank, amount, due_date

    # 2. 根據檔名分配銀行名稱，隨後交給 AI
    if "聯邦銀行" in filename or "UBOT" in filename:
        bank = "聯邦銀行"
    elif "玉山銀行" in filename or "ESUN" in filename:
        bank = "玉山銀行"
    elif "永豐銀行" in filename or "SinoPac" in filename:
        bank = "永豐銀行"
    elif "第一銀行" in filename:
        bank = "第一銀行"
    elif "星展銀行" in filename:
        bank = "星展銀行"
    elif "台北富邦" in filename:
        bank = "台北富邦"
    elif "台新銀行" in filename:
        bank = "台新銀行"
    elif "國泰世華" in filename:
        bank = "國泰世華"
    elif "中國信託" in filename:
        bank = "中國信託"
    else:
        bank = f"未知 ({filename[:10]})"

    # 3. 呼叫終極武器：AI 語義解析
    amount, due_date = parse_with_gemini(full_text, bank)
    
    return bank, amount, due_date
