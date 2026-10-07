import os
import json
import re
import io
import shutil
import sqlite3
import requests
from datetime import datetime
from pathlib import Path
import pandas as pd
import pdfplumber
from PIL import Image
import pytesseract
import base64

import streamlit as st
import streamlit.components.v1 as components

# =====================================================================
# [SECTION 1] 시스템 환경 및 UI 기본 설정
# =====================================================================
st.set_page_config(page_title="GSE 현장 통합 사무장", layout="wide", page_icon="🚀", initial_sidebar_state="expanded")

BASE_DIR = Path("/app") if os.path.exists("/app") else Path(".")
ALL_DATA_DIR = BASE_DIR / "all_data"
INBOX_DIR = BASE_DIR / "inbox"
DATA_DIR = BASE_DIR / "data"
KNOWLEDGE_DIR = DATA_DIR / "web_knowledge"
BACKUP_DIR = BASE_DIR / "backup"

for d in [ALL_DATA_DIR, INBOX_DIR, DATA_DIR, KNOWLEDGE_DIR, BACKUP_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# 💡 모델 분리 및 최적화: 챗봇 전용 경량 모델 사용 (RTX 4060 8GB 최적화)
CHAT_MODEL_NAME = "qwen2.5:7b" 

if 'chat_history' not in st.session_state: st.session_state.chat_history = []

# 인쇄용 CSS (사이드바, 툴바 등 숨김 처리)
st.markdown(
    """
    <style>
        [data-testid="collapsedControl"] { display: none; }
        @media print {
            body { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
            [data-testid="stSidebar"], header[data-testid="stHeader"], [data-testid="stToolbar"] { display: none !important; }
            .stButton, [data-testid="stForm"], [data-testid="stFileUploader"], [data-testid="stTextInput"], 
            [data-testid="stSelectbox"], [data-testid="stTextArea"], [data-testid="stDateInput"], 
            [data-testid="stRadio"], [data-testid="stCheckbox"], [data-testid="stTabs"] [role="tablist"] { display: none !important; }
            [data-testid="stExpander"] details summary { display: none !important; } 
            [data-testid="stExpander"] details { border: none !important; margin-bottom: 20px !important; }
            table { font-size: 14px !important; width: 100% !important; }
        }
    </style>
    """,
    unsafe_allow_html=True,
)

def show_print_button():
    components.html(
        """
        <script>function printPage() { window.parent.print(); }</script>
        <div style="text-align: right;">
            <button onclick="printPage()" style="background-color: #4CAF50; border: none; color: white; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-weight: bold;">🖨️ 실제 인쇄 / PDF 저장하기</button>
        </div>
        """, height=60
    )


# =====================================================================
# [SECTION 2] 데이터베이스(SQLite) 초기화 및 백업 스위칭
# =====================================================================
backup_files = list(BACKUP_DIR.glob("*.db"))
backup_names = [f.name for f in sorted(backup_files, reverse=True)]
db_options = ["현재 사용 중인 장부 (gse_precision.db)"] + backup_names

st.sidebar.title("🏭 GSE 통합 시스템")
selected_db_name = st.sidebar.selectbox("📂 열어볼 장부 파일(백업) 선택", db_options)
is_backup_mode = selected_db_name != "현재 사용 중인 장부 (gse_precision.db)"

if is_backup_mode:
    DB_PATH = BACKUP_DIR / selected_db_name
    st.sidebar.error("⚠️ 과거 장부를 열람 중입니다. (조회 전용/저장 불가)")
else:
    DB_PATH = DATA_DIR / "gse_precision.db"

def init_db():
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, company TEXT, 
            part_name TEXT, material TEXT, unit_price INTEGER, quantity INTEGER, total_price INTEGER,
            quote_date TEXT, file_path TEXT, raw_text TEXT, payment_status TEXT, due_date TEXT, estimated_hours TEXT)""")
        
        conn.execute("""CREATE TABLE IF NOT EXISTS tool_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT, quote_date TEXT, company TEXT, 
            category TEXT, part_name TEXT, unit_price INTEGER, quantity INTEGER, total_price INTEGER)""")
        
        conn.execute("""CREATE TABLE IF NOT EXISTS setup_sheets (
            id TEXT PRIMARY KEY, quote_date TEXT, part_name TEXT, machine TEXT, category TEXT, 
            material TEXT, g_code_coord TEXT, tool_info TEXT, knowhow TEXT, images_b64 TEXT)""")
        
        conn.execute("""CREATE TABLE IF NOT EXISTS quick_prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, prompt_text TEXT UNIQUE, category TEXT DEFAULT '기타')""")
            
        # G코드 및 메모 테이블 추가
        conn.execute("""CREATE TABLE IF NOT EXISTS gcodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, machine TEXT, code TEXT, description TEXT)""")
        
        conn.execute("""CREATE TABLE IF NOT EXISTS memos (
            id TEXT PRIMARY KEY, title TEXT, content TEXT, date TEXT)""")

def migrate_old_json_data():
    if is_backup_mode: return
    with sqlite3.connect(str(DB_PATH)) as conn:
        # 1. 기존 매입 장부 마이그레이션
        for json_file in BASE_DIR.glob("*_장부.json"):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for item in data:
                    conn.execute("""
                        INSERT INTO tool_ledger (quote_date, company, category, part_name, unit_price, quantity, total_price)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        item.get("일자", ""), item.get("거래처", ""), item.get("분류", "미분류"),
                        item.get("품목명", ""), int(item.get("단가", 0)), int(item.get("수량", 0)), int(item.get("총액", 0))
                    ))
                os.rename(json_file, str(json_file) + ".backup")
            except Exception:
                pass

        # 2. machining_db.json (셋업 시트, G코드, 메모) 마이그레이션
        machining_json = BASE_DIR / "machining_db.json"
        if machining_json.exists():
            try:
                with open(machining_json, 'r', encoding='utf-8') as f:
                    db_data = json.load(f)
                
                # 셋업 시트 복원
                for sheet in db_data.get("setup_sheets", []):
                    imgs = sheet.get("images_b64", [])
                    if sheet.get("image_b64") and not imgs: imgs = [sheet.get("image_b64")]
                    date_str = sheet.get("date", "").split(" ")[0] if sheet.get("date") else datetime.now().strftime("%Y-%m-%d")
                    
                    conn.execute("""INSERT OR IGNORE INTO setup_sheets 
                        (id, quote_date, part_name, machine, category, material, g_code_coord, tool_info, knowhow, images_b64) 
                        VALUES (?,?,?,?,?,?,?,?,?,?)""", 
                        (sheet.get("id", ""), date_str, sheet.get("part_name", ""), sheet.get("machine", ""), 
                         sheet.get("category", "방산/일반"), sheet.get("material", ""), sheet.get("g_code_coord", ""), 
                         sheet.get("tool_info", ""), sheet.get("knowhow", ""), json.dumps(imgs)))
                
                # G코드 복원
                for g in db_data.get("gcodes", []):
                    conn.execute("INSERT INTO gcodes (title, machine, code, description) VALUES (?,?,?,?)",
                                 (g.get("title", ""), g.get("machine", ""), g.get("code", ""), g.get("description", "")))
                
                # 가공 메모 복원
                for m in db_data.get("memos", []):
                    conn.execute("INSERT OR IGNORE INTO memos (id, title, content, date) VALUES (?,?,?,?)",
                                 (m.get("id", ""), m.get("title", ""), m.get("content", ""), m.get("date", "")))
                
                conn.commit()
                os.rename(machining_json, str(machining_json) + ".backup")
            except Exception as e:
                st.error(f"machining_db.json 불러오기 오류: {e}")

init_db()
migrate_old_json_data()


# =====================================================================
# [SECTION 3] 순수 Python 기반 영수증 텍스트 판독 엔진 (Vision AI 대체)
# =====================================================================
def extract_text_from_file(file_path):
    text = ""
    try:
        suffix = file_path.suffix.lower()
        if suffix == '.pdf':
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    text += page.extract_text() or "" + "\n"
        elif suffix in ['.png', '.jpg', '.jpeg']:
            # pytesseract를 이용한 이미지 OCR
            img = Image.open(file_path)
            text = pytesseract.image_to_string(img, lang='kor+eng')
    except Exception as e:
        return f"문서 읽기 오류: {str(e)}"
    return text

def parse_receipt_with_python(file_path):
    text = extract_text_from_file(file_path)
    item = {
        "quote_date": datetime.now().strftime("%Y-%m-%d"),
        "company": "미상",
        "category": "공구",
        "part_name": "판독 필요 (장부 센터에서 수정)",
        "unit_price": 0,
        "quantity": 1
    }
    
    if not text or "문서 읽기 오류" in text:
        return {"items": [item]}

    try:
        date_match = re.search(r'(20\d{2})[-./](\d{1,2})[-./](\d{1,2})', text)
        if date_match:
            item["quote_date"] = f"{date_match.group(1)}-{date_match.group(2).zfill(2)}-{date_match.group(3).zfill(2)}"
        
        company_match = re.search(r'(상\s*호|공급자|업\s*체\s*명|귀하)\s*[:;]?\s*([가-힣a-zA-Z0-9\(\)]+)', text)
        if company_match:
            item["company"] = company_match.group(2).strip()
            
        money_matches = re.findall(r'[0-9]{1,3}(?:,[0-9]{3})+', text)
        if money_matches:
            amounts = [int(m.replace(',', '')) for m in money_matches]
            item["unit_price"] = max(amounts)
    except Exception:
        pass 

    return {"items": [item]}


# =====================================================================
# [SECTION 4] 스마트 검색 엔진 및 AI 요약 보고
# =====================================================================
def extract_keywords(prompt_text):
    current_month = str(datetime.now().month)
    
    text_with_month = prompt_text.replace("이번달", f"{current_month}월") \
                                 .replace("이번 달", f"{current_month}월") \
                                 .replace("요번달", f"{current_month}월") \
                                 .replace("요번 달", f"{current_month}월")
                                 
    months = re.findall(r'(\d+)월', text_with_month)
    
    # 보고, 모고, 요번달 등 대거 추가
    stop_words = [
        "이번달", "이번", "달", "월", "월달", "보여줘", "알려줘", "보고해", "보고", "모고", 
        "어때", "얼마야", "얼마", "내역", "총액", "총", "합계", "싹", "다", "전부", 
        "매입", "구매", "산거", "거래", "장부", "샀어", "찾아봐", "정리해", "해줘", 
        "은", "는", "이", "가", "을", "를", "어", "요", "야", "지", "얼만지", "요번달", "요번"
    ]
    
    clean_prompt = re.sub(r'[^\w\s]', '', text_with_month)
    words = clean_prompt.split()
    
    keywords = []
    for w in words:
        if w in stop_words:
            continue
        if re.match(r'^\d+(월|월달|월에)?$', w):
            continue
        keywords.append(w)
        
    return months, keywords

def get_ai_briefing(user_prompt, total_sum, count):
    system_fact = f"[파이썬 코드 제공 팩트: 조건에 맞는 데이터는 총 {count}건, 합계 금액은 {total_sum:,.0f}원입니다.]" if count > 0 else "[파이썬 코드 제공 팩트: 해당 조건에 맞는 데이터가 존재하지 않습니다.]"
    
    payload = {
        "model": CHAT_MODEL_NAME, 
        "messages": [
            {
                "role": "system",
                "content": f"당신은 '지에스이정밀'의 베테랑 사무장입니다.\n{system_fact}\n사장님의 지시({user_prompt})에 대해 위 [팩트]만을 사용하여 짧고 명확하게 '보고'하십시오.\n🚨 절대 지켜야 할 규칙: 상세 내역은 이미 화면에 표로 출력되었으니, 품목을 나열하거나 임의로 숫자를 지어내지 마십시오. '사장님, 보고드립니다.'로 시작하여 2~3줄로 깔끔하게 요약만 하세요."
            },
            {
                "role": "user",
                "content": user_prompt
            }
        ],
        "stream": True,
        "keep_alive": "5m", 
        "options": {
            "temperature": 0.2,
            "num_gpu": -1,
            "num_predict": 1000,
            "num_ctx": 4096
        }
    }
    
    try:
        def stream_generator():
            with requests.post("http://localhost:11434/api/chat", json=payload, stream=True) as response:
                if response.status_code != 200:
                    yield f"Ollama 연결 에러 (모델명: {CHAT_MODEL_NAME} 설치 확인요망): {response.status_code}"
                    return
                for line in response.iter_lines():
                    if line: 
                        data = json.loads(line.decode('utf-8'))
                        if "message" in data:
                            yield data["message"].get("content", "")
                        
        return st.write_stream(stream_generator())
    except Exception as e: 
        return f"AI 연결 오류: {str(e)}"


# =====================================================================
# [SECTION 5] 앱 화면(UI) 구성 및 라우팅
# =====================================================================
st.sidebar.divider()
menu = st.sidebar.radio("업무 메뉴", ["💬 1. 스마트 사무장", "📊 2. 통합 장부 센터", "🏭 3. 셋업 시트 매니저"])
st.sidebar.divider()

clicked_prompt = None

# --- 사이드바 퀵 버튼 (현장 지시) ---
st.sidebar.subheader("💡 퀵 버튼 (현장 지시)")
tab_q_tool, tab_q_mat, tab_q_con, tab_q_etc, tab_q_add = st.sidebar.tabs(["🛠️ 공구", "🧪 소재", "🛢 소모품", "📁 기타", "➕ 등록"])

with sqlite3.connect(str(DB_PATH)) as conn:
    prompts_df = pd.read_sql_query("SELECT * FROM quick_prompts", conn)

def render_quick_prompts(tab, category):
    global clicked_prompt
    with tab:
        df_cat = prompts_df[prompts_df['category'] == category]
        if df_cat.empty: st.caption("등록된 질문이 없습니다.")
        for _, row in df_cat.iterrows():
            c1, c2 = st.columns([5, 1])
            if c1.button(row['prompt_text'], key=f"btn_q_{row['id']}", use_container_width=True):
                clicked_prompt = row['prompt_text']
            if c2.button("❌", key=f"del_q_{row['id']}"):
                with sqlite3.connect(str(DB_PATH)) as conn: conn.execute("DELETE FROM quick_prompts WHERE id=?", (row['id'],))
                st.rerun()

render_quick_prompts(tab_q_tool, "공구")
render_quick_prompts(tab_q_mat, "소재")
render_quick_prompts(tab_q_con, "소모품")
render_quick_prompts(tab_q_etc, "기타")

with tab_q_add:
    st.caption("새로운 퀵 질문 등록")
    new_q_cat = st.radio("분류", ["공구", "소재", "소모품", "기타"], horizontal=True, label_visibility="collapsed")
    new_q_text = st.text_area("내용", placeholder="예: 이번 달 원영공구 결제 대금 얼마야?", label_visibility="collapsed")
    if st.button("💾 등록", use_container_width=True, type="primary", disabled=is_backup_mode):
        if new_q_text.strip():
            try:
                with sqlite3.connect(str(DB_PATH)) as conn:
                    conn.execute("INSERT INTO quick_prompts (prompt_text, category) VALUES (?,?)", (new_q_text.strip(), new_q_cat))
                st.rerun()
            except sqlite3.IntegrityError: st.error("이미 동일한 질문이 있습니다.")

# -----------------------------------------------------
# 메뉴 1: 스마트 사무장
# -----------------------------------------------------
if menu == "💬 1. 스마트 사무장":
    st.header("💬 사무장 실시간 보고 터미널")
    
    with st.expander("📄 서류/영수증 간편 자동 등록 (파이썬 OCR 적용)", expanded=False):
        if is_backup_mode:
            st.error("⚠️ 과거 장부를 열람 중이므로 새로운 서류를 등록할 수 없습니다.")
        else:
            up_file = st.file_uploader("영수증이나 거래명세서 이미지/PDF를 올려주세요.", type=['png', 'jpg', 'jpeg', 'pdf'])
            if up_file and st.button("🚀 서류 자동 분석 및 등록", type="primary"):
                t_path = INBOX_DIR / up_file.name
                with open(t_path, "wb") as f: f.write(up_file.getbuffer())
                
                with st.status("🔍 파이썬 OCR 및 정규식 엔진으로 텍스트를 추출 중입니다...", expanded=True) as status:
                    data = parse_receipt_with_python(t_path)
                    
                    if data and data.get('items'):
                        items_list = data['items']
                        st.dataframe(pd.DataFrame(items_list), use_container_width=True)
                        with sqlite3.connect(str(DB_PATH)) as conn:
                            for item in items_list:
                                u_price = int(re.sub(r'[^0-9]', '', str(item.get('unit_price', '0'))))
                                qty = int(re.sub(r'[^0-9]', '', str(item.get('quantity', '1'))))
                                conn.execute("INSERT INTO tool_ledger (quote_date, company, category, part_name, unit_price, quantity, total_price) VALUES (?,?,?,?,?,?,?)", 
                                             (item.get('quote_date'), item.get('company'), item.get('category', '공구'), item.get('part_name'), u_price, qty, u_price * qty))
                        st.success("✅ 장부 임시 저장 완료! (장부 센터 탭에서 정확한 값으로 수정해 주세요.)")
                        status.update(state="complete")
                    else: 
                        st.error("⚠️ 데이터 추출 실패")
                        status.update(state="error")

    st.divider()
    
    # 대화 기록 출력 (텍스트 먼저, 표는 상세내역으로 접어서 출력)
    for chat in st.session_state.chat_history:
        with st.chat_message(chat["role"]):
            st.markdown(chat["content"])
            if chat.get("dataframe") is not None: 
                with st.expander("📄 보고 근거 상세 내역"):
                    st.dataframe(chat["dataframe"], hide_index=True, use_container_width=True)

    user_input = st.chat_input("사무장에게 장부 내역을 물어보세요 (또는 왼쪽 퀵 버튼 클릭)")
    final_prompt = user_input or clicked_prompt
    
    if final_prompt:
        st.session_state.chat_history.append({"role": "user", "content": final_prompt})
        with st.chat_message("user"): st.markdown(final_prompt)
        
        with st.chat_message("assistant"):
            months, keywords = extract_keywords(final_prompt)
            is_sales = any(w in final_prompt for w in ["매출", "발주서", "제작"])
            table_name = "quotes" if is_sales else "tool_ledger"
            
            with sqlite3.connect(str(DB_PATH)) as conn:
                df = pd.read_sql_query(f"SELECT * FROM {table_name} ORDER BY quote_date DESC", conn)
            
            if not df.empty:
                if months:
                    df['clean_date'] = pd.to_datetime(df['quote_date'], errors='coerce')
                    df = df[df['clean_date'].dt.month.isin([int(m) for m in months])]
                    df = df.drop(columns=['clean_date'], errors='ignore')
                
                if keywords:
                    mask = pd.Series([True] * len(df), index=df.index)
                    for kw in keywords:
                        kw_mask = (
                            df['company'].astype(str).str.contains(kw, case=False, na=False) |
                            df['part_name'].astype(str).str.contains(kw, case=False, na=False) |
                            df['category'].astype(str).str.contains(kw, case=False, na=False)
                        )
                        mask = mask & kw_mask
                    df = df[mask]
            
            if not df.empty:
                df_display = df.drop(columns=['id', 'file_path', 'raw_text'], errors='ignore')
                total_sum = df['total_price'].sum()
                count = len(df)
            else:
                df_display, total_sum, count = None, 0, 0
                
            # 실시간 AI 타이핑
            briefing_text = get_ai_briefing(final_prompt, total_sum, count)
            
            # 보고 후 상세 내역 출력
            if df_display is not None:
                with st.expander("📄 보고 근거 상세 내역"):
                    st.dataframe(df_display, hide_index=True, use_container_width=True)

            st.session_state.chat_history.append({"role": "assistant", "dataframe": df_display, "content": briefing_text})


# -----------------------------------------------------
# 메뉴 2: 통합 장부 센터
# -----------------------------------------------------
elif menu == "📊 2. 통합 장부 센터":
    st.header("📊 통합 매입 장부 센터")
    
    if is_backup_mode:
        st.warning(f"⚠️ 현재 [{selected_db_name}] 과거 장부를 열람하고 있습니다. 덮어쓰기 저장이 비활성화됩니다.")
    
    # 💡 "거래처별 정산서 출력" 탭 추가
    tab_view, tab_edit, tab_print, tab_backup = st.tabs(["📖 전체 장부 열람", "✏ 데이터 직접 수정", "🖨️ 거래처별 정산서 출력", "💾 장부 안전 백업"])
    
    # 💡 장부용 한글 컬럼 설정 추가
    ledger_column_config = {
        "quote_date": "일자",
        "company": "거래처",
        "category": "분류",
        "part_name": "품목명",
        "unit_price": "단가",
        "quantity": "수량",
        "total_price": "총액"
    }

    with tab_view:
        st.subheader("통합 매입 장부 목록")
        sort_opt = st.radio("🔄 뷰(View) 정렬 기준", ["업체별", "날짜별(최신순)", "품목별(가나다순)"], horizontal=True)
        with sqlite3.connect(str(DB_PATH)) as conn: df_view = pd.read_sql_query("SELECT * FROM tool_ledger", conn)
            
        if not df_view.empty:
            df_view['clean_date'] = pd.to_datetime(df_view['quote_date'], errors='coerce')
            if sort_opt == "업체별": df_view = df_view.sort_values(by=['company', 'clean_date'], ascending=[True, False])
            elif sort_opt == "날짜별(최신순)": df_view = df_view.sort_values(by=['clean_date'], ascending=[False])
            else: df_view = df_view.sort_values(by=['part_name', 'clean_date'], ascending=[True, False])
                
            df_view = df_view.drop(columns=['clean_date', 'id'], errors='ignore')
            show_print_button()
            
            # 💡 한글 컬럼명 매핑 적용
            st.dataframe(df_view, hide_index=True, use_container_width=True, column_config=ledger_column_config)
        else: st.info("기록된 데이터가 없습니다.")

    with tab_edit:
        st.subheader("🛠️ 장부 데이터 직접 편집")
        sort_opt_edit = st.radio("🔄 편집 뷰(View) 정렬 기준", ["업체별", "날짜별(최신순)", "품목별(가나다순)"], horizontal=True, key="sort_edit_radio")
        
        with sqlite3.connect(str(DB_PATH)) as conn: 
            df_edit = pd.read_sql_query("SELECT * FROM tool_ledger", conn)
            
        if not df_edit.empty:
            df_edit['clean_date'] = pd.to_datetime(df_edit['quote_date'], errors='coerce')
            if sort_opt_edit == "업체별": df_edit = df_edit.sort_values(by=['company', 'clean_date'], ascending=[True, False])
            elif sort_opt_edit == "날짜별(최신순)": df_edit = df_edit.sort_values(by=['clean_date'], ascending=[False])
            else: df_edit = df_edit.sort_values(by=['part_name', 'clean_date'], ascending=[True, False])
            df_edit = df_edit.drop(columns=['clean_date'], errors='ignore')
            
        # 💡 한글 컬럼명 매핑 적용
        edited_tools = st.data_editor(
            df_edit, 
            hide_index=True, 
            use_container_width=True, 
            num_rows="dynamic" if not is_backup_mode else "fixed",
            column_config=ledger_column_config
        )
        
        if st.button("💾 매입 장부 덮어쓰기 저장", type="primary", disabled=is_backup_mode):
            with sqlite3.connect(str(DB_PATH)) as conn: edited_tools.to_sql("tool_ledger", conn, if_exists="replace", index=False)
            st.success("✅ 장부 업데이트 완료!"); st.rerun()

    with tab_print:
        st.subheader("🖨️ 거래처별 정산서 출력")
        with sqlite3.connect(str(DB_PATH)) as conn: 
            df_print = pd.read_sql_query("SELECT * FROM tool_ledger", conn)
            
        if not df_print.empty:
            df_print['clean_date'] = pd.to_datetime(df_print['quote_date'], errors='coerce')
            
            # 1단계: 거래처 및 월 선택 필터 생성
            companies = ["전체"] + sorted(df_print['company'].dropna().unique().tolist())
            months = ["전체"] + sorted(df_print['clean_date'].dt.month.dropna().unique().astype(int).astype(str).tolist())
            
            c1, c2 = st.columns(2)
            sel_company = c1.selectbox("🏢 정산할 거래처 선택", companies)
            sel_month = c2.selectbox("📅 정산할 월(Month) 선택", months)
            
            # 2단계: 데이터 필터링
            df_filtered = df_print.copy()
            if sel_company != "전체":
                df_filtered = df_filtered[df_filtered['company'] == sel_company]
            if sel_month != "전체":
                df_filtered = df_filtered[df_filtered['clean_date'].dt.month == int(sel_month)]
                
            df_filtered = df_filtered.drop(columns=['clean_date', 'id'], errors='ignore')
            
            # 3단계: 자동 합계 계산 및 화면 구성
            if not df_filtered.empty:
                total_amount = df_filtered['total_price'].sum()
                
                # 인쇄 시 보여질 보고서 헤더 타이틀
                report_title_company = sel_company if sel_company != "전체" else "전체 거래처"
                report_title_month = f"{sel_month}월" if sel_month != "전체" else "전체 기간"
                
                st.markdown(f"### 🧾 [{report_title_company}] {report_title_month} 정산 내역")
                st.markdown(f"#### 💰 총 결제 대상 금액: **{total_amount:,.0f} 원**")
                
                # 4단계: 깔끔한 인쇄 버튼 제공
                show_print_button()
                
                # 💡 한글 컬럼명 매핑 적용
                st.dataframe(df_filtered, hide_index=True, use_container_width=True, column_config=ledger_column_config)
            else:
                st.info("해당 조건(업체/월)에 일치하는 데이터가 없습니다.")
        else:
            st.info("기록된 데이터가 없습니다.")

    with tab_backup:
        st.subheader("💾 월별 장부 안전 백업 (스냅샷)")
        st.write("월말 결산 후 아래 버튼을 눌러 장부 전체를 백업해 두시면, 다음 달에도 과거 기록을 안전하게 열람할 수 있습니다.")
        
        custom_backup_name = st.text_input("📝 백업 파일명 지정 (선택사항)", placeholder="예: 9월달 원영공구 매입장부 (빈칸이면 날짜로 자동 저장)")
        
        c1, c2 = st.columns(2)
        with c1:
            if st.button("🚀 월말 장부 수동/자동 백업 생성", type="primary", use_container_width=True, disabled=is_backup_mode):
                now_str = datetime.now().strftime('%Y%m%d_%H%M%S') 
                input_name = custom_backup_name.strip()
                if input_name:
                    backup_filename = input_name if input_name.endswith(".db") else f"{input_name}.db"
                else:
                    backup_filename = f"장부백업_{now_str}.db"
                    
                shutil.copy2(str(DB_PATH), str(BACKUP_DIR / backup_filename))
                st.success(f"✅ [{backup_filename}] 스냅샷 생성 완료! (좌측 사이드바에서 즉시 열람 가능)")
                st.rerun()
                
        with c2:
            try:
                with open(DB_PATH, "rb") as f: db_bytes = f.read()
                st.download_button("💾 외부 저장소(USB)로 직접 다운로드", db_bytes, f"gse_장부_{datetime.now().strftime('%Y%m%d')}.db", "application/octet-stream", use_container_width=True)
            except Exception: st.warning("다운로드 할 수 없습니다.")

# -----------------------------------------------------
# 메뉴 3: 셋업 시트 매니저 (더블클릭 데이터 편집 기능 적용)
# -----------------------------------------------------
elif menu == "🏭 3. 셋업 시트 매니저":
    st.header("📋 스마트 셋업 시트 관리")
    
    # 셋업 시트, G코드, 메모 탭 구성
    tab_sheet, tab_gcode, tab_memo = st.tabs(["📋 셋업 시트", "⚙️ G코드 보관함", "📝 가공 메모"])
    
    # --- 1. 셋업 시트 탭 ---
    with tab_sheet:
        with st.expander("➕ 신규 셋업 시트 작성"):
            with st.form("setup_form", clear_on_submit=True):
                c1, c2 = st.columns(2)
                part_name = c1.text_input("📦 품번/품명")
                machine = c1.selectbox("📟 장비", ["두산 DNM500", "두산 DNM650", "두산 GT2100"])
                material = c2.text_input("🧪 재질")
                g_code_coord = c2.text_input("📍 좌표계 (G54 등)")
                tool_info = st.text_area("공구 리스트")
                knowhow = st.text_area("💡 가공 노하우 / 주의사항")
                images = st.file_uploader("📷 세팅 사진", accept_multiple_files=True)
                
                if st.form_submit_button("💾 셋업 시트 저장", disabled=is_backup_mode):
                    if part_name.strip():
                        sheet_id = datetime.now().strftime("%Y%m%d%H%M%S")
                        imgs_b64 = json.dumps([base64.b64encode(i.read()).decode() for i in images]) if images else "[]"
                        with sqlite3.connect(str(DB_PATH)) as conn:
                            conn.execute("INSERT INTO setup_sheets (id, quote_date, part_name, machine, category, material, g_code_coord, tool_info, knowhow, images_b64) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                        (sheet_id, datetime.now().strftime("%Y-%m-%d"), part_name, machine, "방산/일반", material, g_code_coord, tool_info, knowhow, imgs_b64))
                        st.success("✅ 저장 완료!")
                        st.rerun()
                    else:
                        st.error("⚠️ 품번/품명을 입력해주세요.")

        with sqlite3.connect(str(DB_PATH)) as conn: 
            # 💡 기존에는 일부 컬럼만 불러왔으나, 덮어쓰기 저장을 위해 전체 데이터를 불러오도록 수정
            df_setup = pd.read_sql_query("SELECT * FROM setup_sheets ORDER BY id DESC", conn)
        
        if not df_setup.empty:
            st.write("🔍 **저장된 셋업 시트 기록 (표의 셀을 더블 클릭하여 수정하세요)**")
            
            edited_setup = st.data_editor(
                df_setup, 
                hide_index=True, 
                use_container_width=True,
                num_rows="dynamic" if not is_backup_mode else "fixed",
                column_config={
                    "id": st.column_config.TextColumn("ID (수정불가)", disabled=True),
                    "quote_date": "등록일자",
                    "part_name": "품번/품명",
                    "machine": "장비",
                    "category": "분류",
                    "material": "재질",
                    "g_code_coord": "좌표계",
                    "tool_info": "공구정보",
                    "knowhow": "노하우/주의사항",
                    "images_b64": st.column_config.TextColumn("이미지 데이터 (수정불가)", disabled=True)
                }
            )
            
            if st.button("💾 셋업 시트 덮어쓰기 저장", key="save_setup", disabled=is_backup_mode):
                with sqlite3.connect(str(DB_PATH)) as conn:
                    edited_setup.to_sql("setup_sheets", conn, if_exists="replace", index=False)
                st.success("✅ 셋업 시트 업데이트 완료!")
                st.rerun()

    # --- 2. G코드 보관함 탭 ---
    with tab_gcode:
        st.subheader("⚙️ 저장된 G코드 매크로")
        
        with st.expander("➕ 신규 G코드 작성"):
            with st.form("gcode_form", clear_on_submit=True):
                c1, c2 = st.columns(2)
                g_title = c1.text_input("📌 G코드 제목", placeholder="예: MCT 주축 예열 프로그램")
                g_machine = c2.selectbox("📟 적용 장비", ["공통", "두산 DNM500", "두산 DNM650", "두산 GT2100"])
                g_code = st.text_area("💻 G코드 내용", height=150, placeholder="G코드를 여기에 붙여넣으세요.")
                g_desc = st.text_area("💡 설명 / 주의사항", placeholder="이 코드를 사용할 때 주의할 점을 적어주세요.")
                
                if st.form_submit_button("💾 G코드 저장", disabled=is_backup_mode):
                    if g_title.strip() and g_code.strip():
                        with sqlite3.connect(str(DB_PATH)) as conn:
                            conn.execute("INSERT INTO gcodes (title, machine, code, description) VALUES (?,?,?,?)",
                                         (g_title, g_machine, g_code, g_desc))
                        st.success("✅ G코드 저장 완료!")
                        st.rerun()
                    else:
                        st.error("⚠️ 제목과 G코드 내용을 모두 입력해주세요.")
        
        with sqlite3.connect(str(DB_PATH)) as conn: 
            # 💡 덮어쓰기 저장을 위해 전체 데이터를 불러오도록 수정
            df_gcode = pd.read_sql_query("SELECT * FROM gcodes", conn)
            
        if not df_gcode.empty:
            st.write("🔍 **저장된 G코드 기록 (표의 셀을 더블 클릭하여 수정하세요)**")
            
            edited_gcode = st.data_editor(
                df_gcode, 
                hide_index=True, 
                use_container_width=True,
                num_rows="dynamic" if not is_backup_mode else "fixed",
                column_config={
                    "id": st.column_config.NumberColumn("ID (수정불가)", disabled=True),
                    "title": "제목",
                    "machine": "적용 장비",
                    "code": "G코드",
                    "description": "설명"
                }
            )
            
            if st.button("💾 G코드 덮어쓰기 저장", key="save_gcode", disabled=is_backup_mode):
                with sqlite3.connect(str(DB_PATH)) as conn: 
                    edited_gcode.to_sql("gcodes", conn, if_exists="replace", index=False)
                st.success("✅ G코드 업데이트 완료!")
                st.rerun()
        else:
            st.info("등록된 G코드가 없습니다.")
            
    # --- 3. 가공 메모 탭 ---
    with tab_memo:
        st.subheader("📝 가공 노하우 및 메모")
        
        with st.expander("➕ 신규 메모 작성"):
            with st.form("memo_form", clear_on_submit=True):
                m_title = st.text_input("📌 메모 제목", placeholder="예: 평면 가공 시 유의사항")
                m_content = st.text_area("📝 메모 내용", height=200, placeholder="노하우나 메모할 내용을 적어주세요.")
                
                if st.form_submit_button("💾 메모 저장", disabled=is_backup_mode):
                    if m_title.strip() and m_content.strip():
                        m_id = datetime.now().strftime("%Y%m%d%H%M%S")
                        m_date = datetime.now().strftime("%Y-%m-%d %H:%M")
                        with sqlite3.connect(str(DB_PATH)) as conn:
                            conn.execute("INSERT INTO memos (id, title, content, date) VALUES (?,?,?,?)",
                                         (m_id, m_title, m_content, m_date))
                        st.success("✅ 메모 저장 완료!")
                        st.rerun()
                    else:
                        st.error("⚠️ 제목과 메모 내용을 모두 입력해주세요.")
                        
        with sqlite3.connect(str(DB_PATH)) as conn: 
            # 💡 덮어쓰기 저장을 위해 전체 데이터를 불러오도록 수정
            df_memo = pd.read_sql_query("SELECT * FROM memos ORDER BY date DESC", conn)
            
        if not df_memo.empty:
            st.write("🔍 **저장된 메모 기록 (표의 셀을 더블 클릭하여 수정하세요)**")
            
            edited_memo = st.data_editor(
                df_memo, 
                hide_index=True, 
                use_container_width=True,
                num_rows="dynamic" if not is_backup_mode else "fixed",
                column_config={
                    "id": st.column_config.TextColumn("ID (수정불가)", disabled=True),
                    "title": "제목",
                    "content": "메모 내용",
                    "date": "작성/수정일"
                }
            )
            
            if st.button("💾 메모 덮어쓰기 저장", key="save_memo", disabled=is_backup_mode):
                with sqlite3.connect(str(DB_PATH)) as conn: 
                    edited_memo.to_sql("memos", conn, if_exists="replace", index=False)
                st.success("✅ 메모 업데이트 완료!")
                st.rerun()
        else:
            st.info("등록된 메모가 없습니다.")
