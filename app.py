import os
import json
import re
import io
import shutil
import sqlite3
import requests
import time
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path
import pandas as pd
import base64
import streamlit as st
import streamlit.components.v1 as components

# =====================================================================
# [SECTION 1] 시스템 환경 및 UI 기본 설정
# =====================================================================
st.set_page_config(page_title="GSE 현장 기록노트 (로컬PC)", layout="wide", page_icon="🛠️", initial_sidebar_state="expanded")

# Streamlit Cloud에서는 쓰기 가능한 임시 경로 사용
if Path("/mount/src/cnc-app").exists():
    BASE_DIR = Path("/tmp/cnc-app")
else:
    BASE_DIR = Path(__file__).resolve().parent

ALL_DATA_DIR = BASE_DIR / "all_data"
INBOX_DIR = BASE_DIR / "inbox"
DATA_DIR = BASE_DIR / "data"
BACKUP_DIR = BASE_DIR / "backup"

try:
    KNOWLEDGE_DIR = Path(r"\\GBS\web_knowledge")
except Exception:
    KNOWLEDGE_DIR = DATA_DIR / "web_knowledge"

@st.cache_resource(show_spinner=False)
def _init_system_env():
    for d in [ALL_DATA_DIR, INBOX_DIR, DATA_DIR, BACKUP_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    try:
        KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

_init_system_env()

OLLAMA_MODELS_DIR = r"D:\ollama_models"
OLLAMA_HOST = "http://127.0.0.1:11434"
CHAT_MODEL_DEFAULT = "hf.co/bartowski/Qwen2.5-7B-Instruct-GGUF:Q4_K_M"

if 'chat_model' not in st.session_state:
    st.session_state.chat_model = CHAT_MODEL_DEFAULT
CHAT_MODEL_NAME = st.session_state.chat_model

if 'chat_history' not in st.session_state: 
    st.session_state.chat_history = []

@st.cache_data(ttl=60, show_spinner=False)
def check_ollama_status(timeout_sec=1):
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=timeout_sec)
        if r.status_code != 200:
            return False, [], f"HTTP {r.status_code}"
        models = [m.get("name", "") for m in r.json().get("models", [])]
        return True, models, "OK"
    except Exception as e:
        return False, [], str(e)

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

# ==========================================================================
# [공용함수 - AI 발주 이메일] 발주서 이메일 전송 (계정 연동 전: 시뮬레이션 모드)
# ==========================================================================
def send_order_email(to_email, company_name, order_details):
    sender_email = "your_company@gmail.com"
    subject = f"[{datetime.now().strftime('%m/%d')}] 지에스이정밀 발주서 - {company_name} 귀하"
    body = f"안녕하세요 {company_name} 담당자님,\n\n아래 품목 발주 요청드립니다.\n\n[발주 내용]\n{order_details}\n\n감사합니다.\n지에스이정밀 드림"
    
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = sender_email
    msg['To'] = to_email
    
    try:
        return True, "✅ 이메일 발송이 성공적으로 완료되었습니다. (현재는 테스트/시뮬레이션 모드)"
    except Exception as e:
        return False, f"❌ 이메일 발송 실패: {str(e)}"

# ==========================================================================
# [공용함수 - AI 발주 팩스] 발주서 웹팩스 전송 (실제 API 연동 전: 시뮬레이션)
# ==========================================================================
def send_order_fax(to_fax, company_name, order_details):
    try:
        time.sleep(1)
        return True, f"📠 팩스 발송 완료 시뮬레이션: {to_fax} 번호로 발주서 전송 성공 (실제 API 연동 대기중)"
    except Exception as e:
        return False, f"❌ 팩스 발송 실패: {str(e)}"

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
# [SECTION 2] 데이터베이스 경로 지정 및 백업 모드 확인
# =====================================================================
backup_files = list(BACKUP_DIR.glob("*.db"))
backup_names = [f.name for f in sorted(backup_files, reverse=True)]
db_options = ["현재 사용 중인 장부 (gse_precision.db)"] + backup_names

selected_db_name = st.sidebar.selectbox("📂 열어볼 장부 파일(백업) 선택", db_options)
is_backup_mode = selected_db_name != "현재 사용 중인 장부 (gse_precision.db)"

if is_backup_mode:
    DB_PATH = BACKUP_DIR / selected_db_name
    st.sidebar.error("⚠️ 과거 장부를 열람 중입니다. (조회 전용/저장 불가)")
else:
    DB_PATH = DATA_DIR / "gse_precision.db"

# =====================================================================
# [SECTION 3] 네트워크 백업 동기화 (거래내역 JSON 변환 추출)
# =====================================================================
@st.cache_data(ttl=300, show_spinner=False)
def load_backup_config():
    USER_DESKTOP_TMP = os.path.join(os.path.expanduser("~"), "Desktop")
    LOG_PATH_TMP = os.path.join(USER_DESKTOP_TMP, "작업일지")
    config_path = os.path.join(LOG_PATH_TMP, "config.json")
    
    if not os.path.exists(config_path):
        if not os.path.exists(LOG_PATH_TMP):
            os.makedirs(LOG_PATH_TMP)
        default_config = {"SHARED_FOLDER_PATH": ""}
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=4)
        return default_config
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"SHARED_FOLDER_PATH": ""}

APP_CONFIG = load_backup_config()
SHARED_FOLDER_PATH = APP_CONFIG.get("SHARED_FOLDER_PATH", "").strip()

def _sync_ledger_to_shared_folder(trigger_name="앱 자동 동기화", allow_past_db=False):
    if is_backup_mode and not allow_past_db:
        return False, "과거 장부 모드에서는 자동 동기화되지 않습니다."
        
    if not SHARED_FOLDER_PATH:
        return False, "공유 폴더가 설정되지 않음 (config.json 확인)"
        
    try:
        if os.path.exists(SHARED_FOLDER_PATH):
            with sqlite3.connect(str(DB_PATH)) as conn:
                df_ledger = pd.read_sql_query("SELECT * FROM tool_ledger", conn)
                
            if df_ledger.empty:
                return True, "전송할 장부 데이터가 없습니다."

            grouped = df_ledger.groupby('company')
            
            for company, group in grouped:
                safe_company = "".join(c for c in str(company) if c.isalnum() or c in (' ', '_', '-')).strip()
                if not safe_company:
                    safe_company = "이름미상"
                
                if is_backup_mode:
                    db_name_clean = selected_db_name.replace(".db", "").replace(" ", "")
                    json_filename = f"{safe_company}_{db_name_clean}_과거전송.json"
                else:
                    today_str = datetime.now().strftime("%Y%m%d")
                    json_filename = f"{safe_company}_장부_{today_str}.json"
                    
                target_path = os.path.join(SHARED_FOLDER_PATH, json_filename)
                
                records = []
                for _, row in group.iterrows():
                    records.append({
                        "일자": str(row.get('quote_date', '')),
                        "거래처": str(row.get('company', '')),
                        "분류": str(row.get('category', '미분류')),
                        "품목명": str(row.get('part_name', '')),
                        "단가": int(row.get('unit_price', 0)),
                        "수량": int(row.get('quantity', 0)),
                        "총액": int(row.get('total_price', 0))
                    })
                
                with open(target_path, 'w', encoding='utf-8') as f:
                    json.dump(records, f, ensure_ascii=False, indent=4)
                    
            return True, f"✅ [{trigger_name}] 장부 데이터 거래처별 JSON 전송 완료!"
        else:
            return False, f"⚠️ 공유 폴더 연결 실패: {SHARED_FOLDER_PATH}"
    except Exception as e:
        return False, f"❌ 동기화 에러: {str(e)}"

st.sidebar.divider()
st.sidebar.subheader("🔄 거래내역 전송 (현장PC→메인서버)")
if st.sidebar.button("🚀 메인 서버로 즉시 전송", use_container_width=True):
    with st.spinner(f"'{selected_db_name}' 데이터를 JSON으로 변환하여 전송 중..."):
        success, msg = _sync_ledger_to_shared_folder("수동 전송 버튼", allow_past_db=True)
        if success:
            st.sidebar.success(msg)
        else:
            st.sidebar.error(msg)


# =====================================================================
# [SECTION 4] 데이터베이스(SQLite) 초기화 및 마이그레이션
# =====================================================================
def _connect_db():
    if is_backup_mode:
        uri = Path(DB_PATH).resolve().as_uri() + "?mode=ro"
        return sqlite3.connect(uri, uri=True)
    return sqlite3.connect(str(DB_PATH))

def _safe_int_val(v, default=0):
    try:
        if pd.isna(v) or str(v).strip() == "":
            return default
        return int(float(str(v).replace(",", "").strip()))
    except Exception:
        return default

def _save_df_preserving_schema(df_edited, table, pk="id", text_pk=False):
    if is_backup_mode:
        raise RuntimeError("과거 장부 모드에서는 저장할 수 없습니다.")
    df = df_edited.copy()
    with sqlite3.connect(str(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        schema_cols = [r[1] for r in cur.fetchall()]
        if not schema_cols:
            raise RuntimeError(f"테이블 없음: {table}")
        df = df[[c for c in df.columns if c in schema_cols]]
        for c in schema_cols:
            if c not in df.columns:
                df[c] = "" if c != pk else None
        cur.execute(f"SELECT {pk} FROM {table}")
        existing_ids = set(str(r[0]) for r in cur.fetchall() if r[0] is not None)
        if not text_pk:
            cur.execute(f"SELECT MAX({pk}) FROM {table}")
            max_id = cur.fetchone()[0] or 0
            next_id = int(max_id) + 1
        new_ids, keep_ids = [], set()
        rows = []
        for _, row in df.iterrows():
            v = row.get(pk)
            is_new = (v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "")
            if not is_new and not text_pk:
                try:
                    v = int(float(v))
                except ValueError:
                    pass
            if is_new:
                if text_pk:
                    v = datetime.now().strftime("%Y%m%d%H%M%S%f")
                else:
                    v = next_id; next_id += 1
                new_ids.append(str(v))
            keep_ids.add(str(v))
            rows.append([v if c == pk else (None if pd.isna(row.get(c)) else row.get(c)) for c in schema_cols])
        to_delete = existing_ids - keep_ids
        for did in to_delete:
            if text_pk:
                cur.execute(f"DELETE FROM {table} WHERE {pk}=?", (did,))
            else:
                try:
                    cur.execute(f"DELETE FROM {table} WHERE {pk}=?", (int(did),))
                except ValueError:
                    cur.execute(f"DELETE FROM {table} WHERE {pk}=?", (did,))
        placeholders = ",".join(["?"] * len(schema_cols))
        cur.executemany(f"INSERT OR REPLACE INTO {table} ({','.join(schema_cols)}) VALUES ({placeholders})", rows)
        conn.commit()

def init_db():
    if is_backup_mode:
        return
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, company TEXT, 
            part_name TEXT, material TEXT, unit_price INTEGER, quantity INTEGER, total_price INTEGER,
            quote_date TEXT, file_path TEXT, raw_text TEXT, payment_status TEXT, due_date TEXT, estimated_hours TEXT)""")
        
        conn.execute("""CREATE TABLE IF NOT EXISTS tool_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT, quote_date TEXT, company TEXT, 
            category TEXT, part_name TEXT, unit_price INTEGER, quantity INTEGER, total_price INTEGER)""")
        
        conn.execute("""CREATE TABLE IF NOT EXISTS purchase_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT,
            category TEXT DEFAULT '공구',
            part_name TEXT,
            quantity INTEGER DEFAULT 1,
            unit_price INTEGER DEFAULT 0,
            order_date TEXT,
            is_received INTEGER DEFAULT 0,
            receive_date TEXT DEFAULT '',
            moved_to_ledger INTEGER DEFAULT 0,
            memo TEXT DEFAULT ''
        )""")
        
        conn.execute("""CREATE TABLE IF NOT EXISTS setup_sheets (
            id TEXT PRIMARY KEY, quote_date TEXT, part_name TEXT, machine TEXT, category TEXT, 
            material TEXT, g_code_coord TEXT, tool_info TEXT, knowhow TEXT, images_b64 TEXT)""")
        
        conn.execute("""CREATE TABLE IF NOT EXISTS quick_prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, prompt_text TEXT UNIQUE, category TEXT DEFAULT '기타')""")
        
        conn.execute("""CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, company_name TEXT UNIQUE, contact_name TEXT, 
            phone TEXT, email TEXT, fax TEXT, memo TEXT)""")
            
        conn.execute("""CREATE TABLE IF NOT EXISTS gcodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, machine TEXT, code TEXT, description TEXT)""")
        
        conn.execute("""CREATE TABLE IF NOT EXISTS memos (
            id TEXT PRIMARY KEY, title TEXT, content TEXT, date TEXT)""")
            
        conn.execute("""CREATE TABLE IF NOT EXISTS work_logs (
            id TEXT PRIMARY KEY, date TEXT, item TEXT, note TEXT)""")
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(work_logs)")
        cols = [r[1] for r in cur.fetchall()]
        if 'total' in cols:
            cur.execute("CREATE TABLE IF NOT EXISTS work_logs_new (id TEXT PRIMARY KEY, date TEXT, item TEXT, note TEXT)")
            cur.execute("INSERT OR IGNORE INTO work_logs_new (id, date, item, note) SELECT id, date, item, '' AS note FROM work_logs")
            cur.execute("DROP TABLE work_logs")
            cur.execute("ALTER TABLE work_logs_new RENAME TO work_logs")
        if 'note' not in [r[1] for r in cur.execute("PRAGMA table_info(work_logs)").fetchall()]:
            cur.execute("ALTER TABLE work_logs ADD COLUMN note TEXT DEFAULT ''")
        conn.commit()

def _ensure_purchase_orders_table():
    if is_backup_mode:
        return
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS purchase_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT,
            category TEXT DEFAULT '공구',
            part_name TEXT,
            quantity INTEGER DEFAULT 1,
            unit_price INTEGER DEFAULT 0,
            order_date TEXT,
            is_received INTEGER DEFAULT 0,
            receive_date TEXT DEFAULT '',
            moved_to_ledger INTEGER DEFAULT 0,
            memo TEXT DEFAULT ''
        )""")
        conn.commit()

def _parse_order_items_text(items_text):
    if not items_text or not str(items_text).strip():
        return []
    raw_lines = re.split(r'[\n,;]+', str(items_text))
    parsed = []
    for line in raw_lines:
        clean = line.strip()
        if not clean:
            continue
        m = re.search(r'^(.*?)\s+(\d+)\s*(?:개|EA|ea|본|세트|박스|장|통|롤|건|캔|조)?$', clean)
        if m and m.group(1).strip():
            p_name = m.group(1).strip()
            qty = max(1, _safe_int_val(m.group(2), 1))
        else:
            p_name = clean
            qty = 1
        parsed.append({"part_name": p_name, "quantity": qty})
    return parsed

def register_order_to_status(company, items_text, category="공구", memo="AI 사무장 발주"):
    if is_backup_mode:
        return 0
    _ensure_purchase_orders_table()
    parsed_items = _parse_order_items_text(items_text)
    if not parsed_items:
        parsed_items = [{"part_name": str(items_text).strip() or "발주품목", "quantity": 1}]
    today_str = datetime.now().strftime("%Y-%m-%d")
    comp_clean = str(company).strip() or "미상"
    cat_clean = str(category).strip() or "공구"
    cnt = 0
    with sqlite3.connect(str(DB_PATH)) as conn:
        for it in parsed_items:
            conn.execute("""
                INSERT INTO purchase_orders 
                (company, category, part_name, quantity, unit_price, order_date, is_received, receive_date, moved_to_ledger, memo)
                VALUES (?, ?, ?, ?, 0, ?, 0, '', 0, ?)
            """, (comp_clean, cat_clean, it["part_name"], it["quantity"], today_str, memo))
            cnt += 1
        conn.commit()
    return cnt

def migrate_old_json_data():
    if is_backup_mode: return
    with sqlite3.connect(str(DB_PATH)) as conn:
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

        machining_json = BASE_DIR / "machining_db.json"
        if machining_json.exists():
            try:
                with open(machining_json, 'r', encoding='utf-8') as f:
                    db_data = json.load(f)
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
                
                for g in db_data.get("gcodes", []):
                    conn.execute("INSERT INTO gcodes (title, machine, code, description) VALUES (?,?,?,?)",
                                 (g.get("title", ""), g.get("machine", ""), g.get("code", ""), g.get("description", "")))
                
                for m in db_data.get("memos", []):
                    conn.execute("INSERT OR IGNORE INTO memos (id, title, content, date) VALUES (?,?,?,?)",
                                 (m.get("id", ""), m.get("title", ""), m.get("content", ""), m.get("date", "")))
                
                conn.commit()
                os.rename(machining_json, str(machining_json) + ".backup")
            except Exception as e:
                st.error(f"machining_db.json 불러오기 오류: {e}")

if st.session_state.get("_db_initialized_for") != selected_db_name:
    init_db()
    migrate_old_json_data()
    st.session_state["_db_initialized_for"] = selected_db_name


# =====================================================================
# [SECTION 6] 스마트 검색 및 AI 요약 보고 엔진
# =====================================================================
CHAT_CATEGORY_MAP = {
    "🧠 지식창고(오프라인)": {"table": None, "date_col": None, "search_cols": [], "mode": "rag"},
    "💰 매입장부": {"table": "tool_ledger", "date_col": "quote_date", "search_cols": ["company", "part_name", "category"], "mode": "ledger"},
    "📦 발주현황": {"table": "purchase_orders", "date_col": "order_date", "search_cols": ["company", "part_name", "category", "memo"], "mode": "tech"},
    "📈 매출/견적": {"table": "quotes", "date_col": "quote_date", "search_cols": ["company", "part_name", "category", "material"], "mode": "ledger"},
    "📋 셋업시트": {"table": "setup_sheets", "date_col": "quote_date", "search_cols": ["part_name", "machine", "category", "material", "g_code_coord", "tool_info", "knowhow"], "mode": "tech"},
    "⚙️ G코드": {"table": "gcodes", "date_col": None, "search_cols": ["title", "machine", "code", "description"], "mode": "tech"},
    "📝 가공메모": {"table": "memos", "date_col": "date", "search_cols": ["title", "content"], "mode": "tech"},
    "📅 작업일지": {"table": "work_logs", "date_col": "date", "search_cols": ["item", "note"], "mode": "tech"},
}

def extract_keywords(prompt_text):
    current_month = str(datetime.now().month)
    text_with_month = prompt_text.replace("이번달", f"{current_month}월") \
                                 .replace("이번 달", f"{current_month}월") \
                                 .replace("요번달", f"{current_month}월") \
                                 .replace("요번 달", f"{current_month}월")
                                 
    months = re.findall(r'(\d+)월', text_with_month)
    stop_words = [
        "이번달", "이번", "달", "월", "월달", "보여줘", "알려줘", "보고해", "보고", "모고", 
        "어때", "얼마야", "얼마", "내역", "총액", "총", "합계", "싹", "다", "전부", 
        "매입", "구매", "산거", "거래", "장부", "샀어", "찾아봐", "정리해", "해줘", 
        "은", "는", "이", "가", "을", "를", "어", "요", "야", "지", "얼만지", "요번달", "요번",
        "셋업", "세팅", "시트", "G코드", "지코드", "매크로", "메모", "노하우", "작업일지", "일지",
        "장비", "어떻게", "뭐야", "내용", "기록", "찾아줘",
        "최근", "회사별", "업체별", "품목별", "분류별", "월별", "정리", "정리해", "보고",
        "내역", "거래내역", "매입장부", "매출장부", "목록", "현황", "상세", "분석해", "계산해", "보여",
        "재발주", "구매기록", "구매이력", "요약해줘", "요약", "찾아봤습니다",
    ]
    
    clean_prompt = re.sub(r'[^\w\s]', '', text_with_month)
    words = clean_prompt.split()
    
    keywords = []
    for w in words:
        if w in stop_words: continue
        if re.match(r'^\d+(월|월달|월에)?$', w): continue
        keywords.append(w)
        
    return months, keywords

def filter_ledger_smart(df, months, keywords, date_col="quote_date", search_cols=None):
    if search_cols is None:
        search_cols = ["company", "part_name", "category"]
    if df is None or df.empty:
        return df
    if months and date_col is not None and date_col in df.columns:
        try:
            df = df.copy()
            df['clean_date'] = pd.to_datetime(df[date_col], errors='coerce')
            df = df[df['clean_date'].dt.month.isin([int(m) for m in months])]
            df = df.drop(columns=['clean_date'], errors='ignore')
        except Exception:
            pass
    if keywords and not df.empty:
        valid_cols = [c for c in search_cols if c in df.columns]
        if valid_cols:
            scores = pd.Series(0, index=df.index)
            for kw in keywords:
                try:
                    kw_mask = None
                    for c in valid_cols:
                        m = df[c].astype(str).str.contains(kw, case=False, na=False)
                        kw_mask = m if kw_mask is None else (kw_mask | m)
                    scores = scores + kw_mask.astype(int)
                except Exception:
                    continue
            if scores.sum() > 0:
                df = df.copy()
                df['__score'] = scores
                df = df[df['__score'] > 0]
                if not df.empty:
                    if date_col is not None and date_col in df.columns:
                        df = df.sort_values(by=['__score', date_col], ascending=[False, False])
                    else:
                        sort_key = 'id' if 'id' in df.columns else df.columns[0]
                        df = df.sort_values(by=['__score', sort_key], ascending=[False, False])
                df = df.drop(columns=['__score'], errors='ignore')
    return df

def search_local_knowledge(query, knowledge_dir):
    kdir = Path(knowledge_dir)
    empty = ("", [])
    if not query or not str(query).strip():
        return empty
    if not kdir.exists():
        return empty

    raw = re.sub(r"[^\w\s가-힣]", " ", str(query))
    tokens = [t for t in raw.split() if t]
    stop_words = {
        "알려줘", "보여줘", "해줘", "뭐야", "어떻게", "내용", "찾아줘", "찾아봐",
        "은", "는", "이", "가", "을", "를", "의", "에", "요", "야", "지",
        "지식창고", "지식", "창고", "오프라인", "문서",
    }
    keywords = [t for t in tokens if t not in stop_words and len(t) >= 1]
    if not keywords:
        keywords = tokens[:] if tokens else [str(query).strip()]

    scored = []
    try:
        txt_files = list(kdir.rglob("*.txt"))
    except Exception:
        return empty

    for path in txt_files:
        if not path.is_file():
            continue
        text = None
        for enc in ("utf-8", "cp949", "euc-kr"):
            try:
                text = path.read_text(encoding=enc)
                break
            except UnicodeDecodeError:
                continue
            except Exception:
                text = None
                break
        if text is None:
            continue
        blob = f"{path.stem} {path.name}\n{text}"
        blob_l = blob.lower()
        score = 0
        for kw in keywords:
            if kw.lower() in blob_l:
                score += 1
        if score > 0:
            scored.append((score, path.name, text, str(path)))

    scored.sort(key=lambda x: (-x[0], x[1]))
    top = scored[:2]
    docs = [{"filename": name, "content": content, "path": pth} for _, name, content, pth in top]
    combined = "\n\n".join(f"[문서: {d['filename']}]\n{d['content']}" for d in docs)
    return combined, docs

def get_ai_briefing(user_prompt, category_label, df_result):
    model_name = st.session_state.get("chat_model", CHAT_MODEL_DEFAULT)
    is_reorder = "[재발주]" in (user_prompt or "")
    if is_reorder:
        if df_result is None or (hasattr(df_result, 'empty') and df_result.empty):
            system_content = (
                "당신은 '지에스이정밀'의 친절한 사수입니다.\n"
                f"[파이썬 팩트 체크: '{user_prompt}'에 일치하는 과거 구매 기록이 장부에 존재하지 않습니다. (0건)]\n"
                "초보 작업자(아드님)에게 '해당 품목의 구매 기록이 장부에 없습니다. 검색어(품명)를 다르게 입력하거나 직접 신규 발주를 진행해 주세요.'라고 친절하게 알려주세요.\n"
                "🚨 절대 지켜야 할 규칙: 데이터가 없으므로 가상의 거래처나 품명을 절대 지어내지 마십시오."
            )
        else:
            try:
                _rq_cols = [c for c in ['quote_date', 'company', 'part_name', 'unit_price', 'quantity', 'total_price']
                            if c in df_result.columns]
                _rq_rows = df_result.head(10)[_rq_cols].to_dict('records')
                for _rw in _rq_rows:
                    for _k in list(_rw.keys()):
                        try:
                            if pd.isna(_rw[_k]):
                                _rw[_k] = ""
                        except Exception:
                            pass
                _reorder_facts = json.dumps(_rq_rows, ensure_ascii=False)
            except Exception:
                _reorder_facts = ""
            if len(_reorder_facts) > 3000:
                _reorder_facts = _reorder_facts[:3000] + "..."
            system_content = (
                "당신은 '지에스이정밀'의 친절한 사수입니다. 초보 작업자가 과거에 샀던 공구를 재발주하기 위해 기록을 검색했습니다.\n"
                f"[파이썬 코드 제공 팩트: 총 {len(df_result)}건의 데이터가 있습니다.]\n"
                f"[실제 장부 데이터 (최근 {len(_rq_rows)}건, 아래 값 외에는 절대 생성 금지)] {_reorder_facts}\n"
                f"사장님의 지시({user_prompt})에 대해, 이미 화면에 출력된 상세 내역 표를 참고하여 최근 구매했던 공구의 [품명/규격], [거래처], [단가]를 보기 쉽게 글머리 기호로 요약해서 나열해 주세요.\n"
                "🚨 절대 지켜야 할 규칙: 파이썬 시스템이 제공한 표(데이터)에 없는 가상의 공구나 가격을 절대 지어내지 마십시오. 아드님이 발주서에 복사하기 편하도록 명확하게 브리핑하고, 시작은 '과거 구매 기록을 찾아봤습니다. 아래 내역을 참고해 발주해 보세요!'로 시작하세요."
            )
    elif category_label == "🧠 지식창고(오프라인)":
        docs_text = df_result if isinstance(df_result, str) else ""
        if not str(docs_text).strip():
            system_content = (
                "당신은 '지에스이정밀'의 베테랑 현장 사무장입니다.\n"
                "[파이썬 코드 제공 팩트: 오프라인 지식 창고에서 질문에 맞는 문서를 찾지 못했습니다.]\n"
                "사장님께 해당 문서가 없다고 짧게 보고하고, 규격표를 참고하라고 안내하십시오.\n"
                "🚨 절대 지켜야 할 규칙: 문서에 없는 내용을 임의로 지어내지 마십시오."
            )
        else:
            if len(docs_text) > 6000:
                docs_text = docs_text[:6000] + "..."
            system_content = (
                "당신은 '지에스이정밀'의 베테랑 현장 사무장입니다.\n"
                "다음 제공된 [오프라인 지식 창고 문서]만을 바탕으로 질문에 정확히 답변해. 문서에 없으면 규격표를 참고하라고 해.\n"
                f"[오프라인 지식 창고 문서]\n{docs_text}\n"
                "🚨 절대 지켜야 할 규칙: 제공된 문서에 없는 내용을 임의로 지어내지 마십시오. "
                "문서 내용을 왜곡 없이 앵무새처럼 브리핑하십시오."
            )
    elif df_result is None or (hasattr(df_result, 'empty') and df_result.empty):
        system_fact = "[파이썬 코드 제공 팩트: 해당 조건에 맞는 데이터가 존재하지 않습니다.]"
        system_role = "당신은 '지에스이정밀'의 베테랑 사무장입니다."
        system_content = f"{system_role}\n{system_fact}\n사장님의 지시({user_prompt})에 대해 위 [팩트]만을 사용하여 짧고 명확하게 보고하십시오. '사장님, 보고드립니다.'로 시작하세요."
    elif category_label in ("💰 매입장부", "📈 매출/견적"):
        try:
            count = len(df_result)
            total_sum = float(df_result['total_price'].sum()) if 'total_price' in df_result.columns else 0
        except Exception:
            count, total_sum = len(df_result), 0
        system_content = (
            f"당신은 '지에스이정밀'의 베테랑 사무장입니다.\n"
            f"[파이썬 코드 제공 팩트: ({category_label}) 조건에 맞는 데이터는 총 {count}건, 합계 금액은 {total_sum:,.0f}원입니다.]\n"
            f"사장님의 지시({user_prompt})에 대해 위 [팩트]만을 사용하여 짧고 명확하게 '보고'하십시오.\n"
            f"🚨 절대 지켜야 할 규칙: 상세 내역은 이미 화면에 표로 출력되었으니, 품목을 나열하거나 임의로 숫자를 지어내지 마십시오. "
            f"'사장님, 보고드립니다.'로 시작하여 2~3줄로 깔끔하게 요약만 하세요."
        )
    else:
        try:
            top = df_result.head(5)
            exclude = {'id', 'images_b64', 'file_path', 'raw_text'}
            lines = []
            for _, row in top.iterrows():
                parts = []
                for c in top.columns:
                    if c in exclude:
                        continue
                    try:
                        v = "" if pd.isna(row[c]) else str(row[c])
                    except Exception:
                        v = str(row.get(c, ""))
                    if len(v) > 300:
                        v = v[:300] + "..."
                    parts.append(f"{c}:{v}")
                lines.append(" | ".join(parts))
            records_summary = " // ".join(lines)
            if len(records_summary) > 4000:
                records_summary = records_summary[:4000] + "..."
        except Exception:
            records_summary = ""
        system_content = (
            f"당신은 '지에스이정밀'의 베테랑 현장 사무장입니다.\n"
            f"[파이썬 코드 제공 팩트: ({category_label}) 총 {len(df_result)}건이 검색되었습니다. 상위 핵심 내역: {records_summary}]\n"
            f"사장님의 지시({user_prompt})에 대해 위 [팩트] 데이터에 적힌 내용만을 근거로 "
            f"왜곡 없이 핵심만 3~5줄 이내로 명확하게 요약 보고하십시오.\n"
            f"🚨 절대 지켜야 할 규칙: 데이터에 없는 내용을 임의로 지어내지 마십시오. '사장님, 보고드립니다.'로 시작하세요."
        )
    payload = {
        "model": model_name, 
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_prompt}
        ],
        "stream": True,
        "keep_alive": "0m", 
        "options": {
            "temperature": 0.2,
            "num_predict": 1024,  # 출력 메모리 낭비 방지 (-1에서 1024로 변경)
            "num_ctx": 4096,      # VRAM 확보를 위해 8192에서 4096으로 다이어트
            "num_thread": max(1, (os.cpu_count() or 4) // 2)  # ◀ [2안] 물리 코어 최대 할당
        }
    }
    
    try:
        def stream_generator():
            with requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, stream=True) as response:
                if response.status_code != 200:
                    yield f"Ollama 연결 에러 (모델: {model_name}, 경로: {OLLAMA_MODELS_DIR} 확인요망): {response.status_code}"
                    return
                for line in response.iter_lines():
                    if line: 
                        data = json.loads(line.decode('utf-8'))
                        if "message" in data:
                            yield data["message"].get("content", "")
                        
        return st.write_stream(stream_generator())
    except Exception as e: 
        return f"AI 연결 오류 ({OLLAMA_HOST} 접속 실패): {str(e)}"

# ==========================================================================
# [발주 파이프라인 1번] 발주 데이터 추출 전용 함수 (Ollama JSON Mode, 비스트리밍)
# ==========================================================================
def extract_order_info(user_prompt):
    payload = {
        "model": st.session_state.get("chat_model", CHAT_MODEL_DEFAULT),
        "messages": [
            {
                "role": "system",
                "content": "사용자의 지시에서 업체명(company)과 품목/수량(items)을 추출하여 오직 JSON 형식으로만 응답하라. 응답은 반드시 {\"company\": \"업체명\", \"items\": \"품목 및 수량\"} 형태의 JSON 객체만이며 다른 텍스트는 절대 포함하지 마라."
            },
            {"role": "user", "content": user_prompt}
        ],
        "format": "json",
        "stream": False,
        "keep_alive": "5m",
        "options": {
            "temperature": 0.1,
            "num_predict": 300,
            "num_ctx": 4096,
            "num_thread": max(1, (os.cpu_count() or 4) // 2)  # ◀ [2안] 물리 코어 최대 할당 추가
        }
    }
    try:
        r = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=90)
        if r.status_code != 200:
            return None
        content = r.json().get("message", {}).get("content", "")
        data = json.loads(content)
        if isinstance(data, dict):
            return {"company": str(data.get("company", "") or ""), "items": str(data.get("items", "") or "")}
        return None
    except Exception:
        return None

# =====================================================================
# [SECTION 7] 사이드바 메뉴 및 퀵 프롬프트 구성
# =====================================================================
st.sidebar.divider()
st.sidebar.subheader("🤖 로컬 AI 상태")
_oll_ok, _oll_models, _oll_msg = check_ollama_status()
if _oll_ok:
    st.sidebar.success(f"🟢 Ollama 연결됨 ({len(_oll_models)}개 모델)")
    _opts = [CHAT_MODEL_DEFAULT]
    try:
        _idx = _opts.index(st.session_state.get("chat_model", CHAT_MODEL_DEFAULT))
    except ValueError:
        _idx = 0
    sel = st.sidebar.selectbox("챗봇 모델 선택", _opts, index=_idx,
    help=f"로컬 경로: {OLLAMA_MODELS_DIR}", key="chat_model_select")
    st.session_state.chat_model = sel
    if sel not in _oll_models and not any(m.startswith(sel) for m in _oll_models):
        st.sidebar.warning(f"⚠️ '{sel}' 미설치 → `ollama pull {sel}` 필요.")
else:
    st.sidebar.error(f"🔴 Ollama 미연결: {_oll_msg}")
    st.sidebar.caption(f"모델 경로: {OLLAMA_MODELS_DIR} / {OLLAMA_HOST}")
st.sidebar.divider()

menu = st.sidebar.radio("업무 메뉴", ["💬 1. 스마트 사무장", "📊 2. 통합 장부 센터", "🏭 3. 셋업 시트 매니저"])
clicked_prompt = None
st.sidebar.subheader("💡 퀵 버튼 (현장 지시)")

selected_quick_menu = st.sidebar.selectbox(
    "📂 지시 분류 선택",
    ["🛠️ 공구", "🧪 소재", "🛢 소모품", "🛒 재발주", "📁 기타", "➕ 신규 등록"],
    key="quick_menu_select",
)

_QUICK_CAT_MAP = {
    "🛠️ 공구": "공구",
    "🧪 소재": "소재",
    "🛢 소모품": "소모품",
    "🛒 재발주": "재발주",
    "📁 기타": "기타",
}

with _connect_db() as conn:
    prompts_df = pd.read_sql_query("SELECT * FROM quick_prompts", conn)

with st.sidebar:
    def render_quick_prompts(category):
        global clicked_prompt
        df_cat = prompts_df[prompts_df['category'] == category]
        if df_cat.empty:
            st.caption("등록된 항목이 없습니다.")
        
        for _, row in df_cat.iterrows():
            _btn_label = str(row['prompt_text'])
            if category == "재발주":
                _short = _btn_label.replace("🛒", "").replace("[재발주]", "").replace("최근", "").replace("구매기록 요약해줘", "").strip()
                _btn_label = f"🛒 {_short if _short else '재발주'}"
            
            c1, c2 = st.columns([4, 1])
            
            if c1.button(_btn_label, key=f"btn_q_{row['id']}", use_container_width=True, help=row['prompt_text']):
                clicked_prompt = row['prompt_text']
                
            if c2.button("❌", key=f"del_q_{row['id']}", help="이 항목 삭제", use_container_width=True, disabled=is_backup_mode):
                if is_backup_mode:
                    st.error("불가")
                else:
                    with sqlite3.connect(str(DB_PATH)) as conn:
                        conn.execute("DELETE FROM quick_prompts WHERE id=?", (row['id'],))
                    st.rerun()

    if selected_quick_menu in _QUICK_CAT_MAP:
        render_quick_prompts(_QUICK_CAT_MAP[selected_quick_menu])
    else:
        st.caption("새로운 퀵 질문 등록")
        new_q_cat = st.radio("분류", ["공구", "소재", "소모품", "재발주", "기타"], horizontal=True, label_visibility="collapsed")
        
        _is_reorder_cat = (new_q_cat == "재발주")
        if _is_reorder_cat:
            st.caption("🛒 재발주 선택 시 품명만 입력하세요")
            _ph_text = "품명만 입력하세요"
        else:
            _ph_text = "예: 이번 달 원영공구 결제 대금 얼마야?"
            
        new_q_text = st.text_area("내용", placeholder=_ph_text, label_visibility="collapsed")
        
        if st.button("💾 등록", use_container_width=True, type="primary", disabled=is_backup_mode):
            if new_q_text.strip():
                _raw_text = new_q_text.strip()
                if _is_reorder_cat:
                    if "[재발주]" in _raw_text:
                        _final_text = _raw_text if _raw_text.startswith("🛒") else f"🛒 {_raw_text}"
                    else:
                        _core = _raw_text.replace("🛒", "").replace("[재발주]", "").replace("최근", "").strip()
                        _core = re.sub(r'\s*구매기록\s*요약해줘\s*$', '', _core).strip()
                        _final_text = f"🛒 [재발주] 최근 {_core} 구매기록 요약해줘"
                else:
                    _final_text = _raw_text
                    
                try:
                    with sqlite3.connect(str(DB_PATH)) as conn:
                        conn.execute("INSERT INTO quick_prompts (prompt_text, category) VALUES (?,?)", (_final_text, new_q_cat))
                    st.rerun()
                except sqlite3.IntegrityError: 
                    st.error("이미 동일한 질문이 있습니다.")

# =====================================================================
# [SECTION 8] 화면 구성: 1. 스마트 사무장
# =====================================================================
if menu == "💬 1. 스마트 사무장":
    st.header("🛠️ [현장PC] 기록노트 & 사무장 터미널")
    
    with st.expander("📝 스마트 텍스트 복붙 등록기", expanded=False):
        st.caption("💡 윈도우 캡처나 스마트폰에서 복사한 명세표/영수증 글씨를 붙여넣으세요.")
        c1, c2, c3 = st.columns([1, 1, 2])
        r_company = c1.text_input("🏢 거래처명 (입력 필수)", placeholder="예: 원영공구")
        r_category = c2.selectbox("📂 기본 분류", ["공구", "소재", "소모품", "기타"], index=0)
        r_text = c3.text_area("📋 복사한 텍스트 붙여넣기", height=130, placeholder="롱드릴9.5 2 20000...")
        
        if st.button("🤖 AI 텍스트 분석 및 표 생성", type="primary", disabled=is_backup_mode):
            if not r_text.strip():
                st.warning("텍스트를 붙여넣어 주세요.")
            else:
                with st.spinner("AI가 텍스트를 분류 중입니다..."):
                    payload = {
                        "model": st.session_state.get("chat_model", CHAT_MODEL_DEFAULT),
                        "messages": [
                            {"role": "system", "content": "사용자가 입력한 명세표 텍스트에서 각 항목을 분리해 오직 JSON 형식으로만 응답하라. 응답 형식: {\"items\": [{\"part_name\": \"품명\", \"quantity\": 수량숫자, \"unit_price\": 단가숫자}]}"},
                            {"role": "user", "content": r_text}
                        ],
                        "format": "json",
                        "stream": False,  # ◀ 이 쉼표가 빠졌거나
                        "options": {
                            "num_thread": max(1, (os.cpu_count() or 4) // 2)
                        }  # ◀ 이 닫는 괄호들이 누락되었을 확률이 높습니다.
                    }
                    try:
                        res = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=30)
                        if res.status_code == 200:
                            content = res.json().get("message", {}).get("content", "{}")
                            parsed_data = json.loads(content).get("items", [])
                            if parsed_data:
                                st.session_state['parsed_receipt'] = parsed_data
                                st.rerun()
                            else:
                                st.error("추출된 항목이 없습니다.")
                    except Exception as e:
                        fallback_items = _parse_order_items_text(r_text)
                        if fallback_items:
                            for fb in fallback_items:
                                fb["unit_price"] = 0
                            st.session_state['parsed_receipt'] = fallback_items
                            st.warning(f"기본 텍스트 파서로 표를 생성했습니다: {e}")
                            st.rerun()
                        else:
                            st.error(f"AI 분석 실패: {e}")

        if 'parsed_receipt' in st.session_state:
            st.write("🔍 **추출된 데이터 확인 및 수정**")
            df_parsed = pd.DataFrame(st.session_state['parsed_receipt'])
            if 'part_name' not in df_parsed.columns: df_parsed['part_name'] = ""
            if 'quantity' not in df_parsed.columns: df_parsed['quantity'] = 1
            if 'unit_price' not in df_parsed.columns: df_parsed['unit_price'] = 0
            
            edited_df = st.data_editor(
                df_parsed[['part_name', 'quantity', 'unit_price']],
                num_rows="dynamic",
                use_container_width=True,
                column_config={
                    "part_name": "발주명(품목명)",
                    "quantity": st.column_config.NumberColumn("수량", min_value=1, step=1),
                    "unit_price": st.column_config.NumberColumn("단가(원)", min_value=0, step=100)
                }
            )
            
            if st.button("💾 발주현황 입고매칭 및 통합 매입 장부 일괄 저장", type="primary", disabled=is_backup_mode):
                if not r_company.strip():
                    st.error("거래처명을 입력해 주세요!")
                else:
                    _ensure_purchase_orders_table()
                    company_val = r_company.strip()
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    matched_order_cnt = 0
                    saved_ledger_cnt = 0

                    with sqlite3.connect(str(DB_PATH)) as conn:
                        cur = conn.cursor()
                        cur.execute("""
                            SELECT id, part_name, quantity, category 
                            FROM purchase_orders 
                            WHERE moved_to_ledger = 0 AND company LIKE ?
                            ORDER BY id ASC
                        """, (f"%{company_val}%",))
                        pending_orders = cur.fetchall()
                        used_order_ids = set()

                        for _, row in edited_df.iterrows():
                            p_name = str(row.get('part_name', '') or '').strip()
                            if not p_name:
                                continue
                            qty = max(1, _safe_int_val(row.get('quantity', 1), 1))
                            u_price = _safe_int_val(row.get('unit_price', 0), 0)
                            t_price = qty * u_price
                            cat_to_use = r_category

                            matched_po_id = None
                            p_norm = re.sub(r'\s+', '', p_name).lower()
                            for po_id, po_part, po_qty, po_cat in pending_orders:
                                if po_id in used_order_ids:
                                    continue
                                po_norm = re.sub(r'\s+', '', str(po_part or '')).lower()
                                if po_norm and (po_norm in p_norm or p_norm in po_norm):
                                    matched_po_id = po_id
                                    if po_cat:
                                        cat_to_use = po_cat
                                    break

                            if matched_po_id is not None:
                                used_order_ids.add(matched_po_id)
                                matched_order_cnt += 1
                                cur.execute("""
                                    UPDATE purchase_orders
                                    SET quantity=?, unit_price=?, is_received=1, receive_date=?, moved_to_ledger=1
                                    WHERE id=?
                                """, (qty, u_price, today_str, matched_po_id))
                            else:
                                cur.execute("""
                                    INSERT INTO purchase_orders
                                    (company, category, part_name, quantity, unit_price, order_date, is_received, receive_date, moved_to_ledger, memo)
                                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, 1, '스마트영수증 직등록')
                                """, (company_val, cat_to_use, p_name, qty, u_price, today_str, today_str))

                            cur.execute("""
                                SELECT 1 FROM tool_ledger 
                                WHERE company = ? AND part_name = ? AND quote_date = ? AND unit_price = ? AND quantity = ?
                            """, (company_val, p_name, today_str, u_price, qty))

                            if not cur.fetchone():
                                cur.execute("""
                                    INSERT INTO tool_ledger (quote_date, company, category, part_name, unit_price, quantity, total_price)
                                    VALUES (?, ?, ?, ?, ?, ?, ?)
                                """, (today_str, company_val, cat_to_use, p_name, u_price, qty, t_price))
                                saved_ledger_cnt += 1

                        conn.commit()

                    _sync_ok, _sync_msg = _sync_ledger_to_shared_folder("스마트 텍스트 복붙 등록")
                    if _sync_ok: st.toast(_sync_msg)
                    st.success(f"✅ 통합 매입 장부에 {saved_ledger_cnt}건 저장 완료!")
                    del st.session_state['parsed_receipt']
                    time.sleep(1)
                    st.rerun()

    st.divider()
    
    for chat in st.session_state.chat_history:
        with st.chat_message(chat["role"]):
            st.markdown(chat["content"])
            if chat.get("knowledge_docs"):
                with st.expander("📄 참고한 지식 창고 원본 문서"):
                    for _kd in chat["knowledge_docs"]:
                        st.markdown(f"**{_kd.get('filename', '문서')}**")
                        st.text(_kd.get("content", ""))
            if chat.get("dataframe") is not None: 
                with st.expander("📄 보고 근거 상세 내역"):
                    st.dataframe(chat["dataframe"], hide_index=True, use_container_width=True)
                    if chat.get("setup_images"):
                        st.markdown("📷 세팅 사진")
                        _hcols = st.columns(4)
                        for _i, _b64 in enumerate(chat["setup_images"][:8]):
                            try:
                                _hcols[_i % 4].image(base64.b64decode(_b64), use_container_width=True)
                            except Exception:
                                pass

    user_input = st.chat_input("사무장에게 장부 내역을 물어보세요")
    selected_chat_cat = st.radio(
        "🎯 조회할 장부/현장 자료 카테고리 선택",
        ["🧠 지식창고(오프라인)", "💰 매입장부", "📦 발주현황", "📈 매출/견적", "📋 셋업시트", "⚙️ G코드", "📝 가공메모", "📅 작업일지"],
        horizontal=True,
        key="smart_chat_category_radio"
    )
    final_prompt = user_input or clicked_prompt
    
    if final_prompt:
        st.session_state.chat_history.append({"role": "user", "content": final_prompt})
        with st.chat_message("user"): st.markdown(final_prompt)
        
        is_reorder = "[재발주]" in final_prompt
        is_order = (not is_reorder) and any(w in final_prompt for w in ["발주", "주문", "신청"])
        
        if is_order:
            with st.spinner("🔍 발주 내용을 분석 중..."):
                order_data = extract_order_info(final_prompt)
            if not order_data:
                order_data = {"company": "", "items": final_prompt}
            st.session_state["_active_order_draft"] = {
                "company": str(order_data.get("company", "") or "").strip(),
                "items": str(order_data.get("items", "") or "").strip(),
                "prompt": final_prompt
            }
            briefing_text = "✅ 사장님, 발주서를 준비했습니다."
            with st.chat_message("assistant"):
                st.markdown(briefing_text)
            st.session_state.chat_history.append({"role": "assistant", "dataframe": None, "setup_images": [], "category": "🚀 발주", "content": briefing_text})
        else:
            with st.chat_message("assistant"):
                _brief_cat = "💰 매입장부" if is_reorder else selected_chat_cat
                if (not is_reorder) and selected_chat_cat == "🧠 지식창고(오프라인)":
                    with st.spinner("📚 오프라인 지식 창고를 검색 중..."):
                        knowledge_text, knowledge_docs = search_local_knowledge(final_prompt, KNOWLEDGE_DIR)
                    briefing_text = get_ai_briefing(final_prompt, _brief_cat, knowledge_text)
                    if knowledge_docs:
                        with st.expander("📄 참고한 지식 창고 원본 문서"):
                            for _kd in knowledge_docs:
                                st.markdown(f"**{_kd.get('filename', '문서')}**")
                                st.text(_kd.get("content", ""))
                    st.session_state.chat_history.append({
                        "role": "assistant",
                        "dataframe": None,
                        "setup_images": [],
                        "knowledge_docs": knowledge_docs,
                        "category": _brief_cat,
                        "content": briefing_text,
                    })
                else:
                    _ensure_purchase_orders_table()
                    cat_cfg = CHAT_CATEGORY_MAP.get(_brief_cat, CHAT_CATEGORY_MAP["💰 매입장부"])
                    table_name, date_col, search_cols = cat_cfg["table"], cat_cfg["date_col"], cat_cfg["search_cols"]
                    months, keywords = extract_keywords(final_prompt)
                    
                    with _connect_db() as conn:
                        try:
                            if date_col is not None:
                                df = pd.read_sql_query(f"SELECT * FROM {table_name} ORDER BY {date_col} DESC", conn)
                            else:
                                df = pd.read_sql_query(f"SELECT * FROM {table_name} ORDER BY id DESC", conn)
                        except Exception:
                            df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
                    
                    df_all = df
                    df = filter_ledger_smart(df, months, keywords, date_col=date_col, search_cols=search_cols)
                    if not keywords and not months:
                        if df is None or df.empty:
                            df = df_all.head(20) if df_all is not None else df
                        else:
                            df = df.head(20)
                    if df is not None and not df.empty and len(df) > 30:
                        df = df.head(30)
                    if is_reorder and keywords and df is not None and not df.empty:
                        _strict_mask = None
                        for _kw in keywords:
                            for _sc in search_cols:
                                if _sc in df.columns:
                                    _m = df[_sc].astype(str).str.contains(_kw, case=False, na=False)
                                    _strict_mask = _m if _strict_mask is None else (_strict_mask | _m)
                        if _strict_mask is not None and int(_strict_mask.sum()) > 0:
                            df = df[_strict_mask]
                        else:
                            df = df.iloc[0:0]
                    
                    if df is not None and not df.empty:
                        df_display = df.drop(columns=['id', 'file_path', 'raw_text', 'images_b64'], errors='ignore')
                    else:
                        df_display = None
                    setup_images = []
                    if _brief_cat == "📋 셋업시트" and df is not None and not df.empty and 'images_b64' in df.columns:
                        for _, _r in df.iterrows():
                            try:
                                _imgs = json.loads(_r['images_b64']) if isinstance(_r['images_b64'], str) else []
                            except Exception:
                                _imgs = []
                            for _b in _imgs:
                                if _b:
                                    setup_images.append(_b)
                                if len(setup_images) >= 8:
                                    break
                            if len(setup_images) >= 8:
                                break
                    briefing_text = get_ai_briefing(final_prompt, _brief_cat, df)
                    
                    if df_display is not None:
                        with st.expander("📄 보고 근거 상세 내역"):
                            st.dataframe(df_display, hide_index=True, use_container_width=True)
                            if setup_images:
                                st.markdown("📷 세팅 사진")
                                _cols = st.columns(4)
                                for _i, _b64 in enumerate(setup_images[:8]):
                                    try:
                                        _cols[_i % 4].image(base64.b64decode(_b64), use_container_width=True)
                                    except Exception:
                                        pass
                    
                    st.session_state.chat_history.append({"role": "assistant", "dataframe": df_display, "setup_images": setup_images, "category": _brief_cat, "content": briefing_text})

    if st.session_state.get("_active_order_draft"):
        draft = st.session_state["_active_order_draft"]
        _order_company = draft.get("company", "")
        order_email_found, order_fax_found = "", ""
        if _order_company:
            try:
                with _connect_db() as conn:
                    _order_df = pd.read_sql_query(
                        "SELECT email, fax, contact_name FROM suppliers WHERE company_name LIKE ?", conn,
                        params=(f"%{_order_company}%",))
                if _order_df is not None and not _order_df.empty:
                    _email_val = _order_df.iloc[0].get('email', '')
                    _fax_val = _order_df.iloc[0].get('fax', '')
                    if isinstance(_email_val, str) and "@" in _email_val:
                        order_email_found = _email_val.strip()
                    if isinstance(_fax_val, str):
                        order_fax_found = _fax_val.strip()
            except Exception:
                order_email_found, order_fax_found = "", ""

        st.info("📦 **[AI 사무장 발주서 연동]** 아래 발주서를 전송하면 발주현황에 즉시 자동 기록됩니다.")
        with st.form("order_confirm_persistent_form", clear_on_submit=False):
            st.markdown("### 🚀 발주서 최종 확인 및 발주현황 자동 등록")
            fc1, fc2 = st.columns([2, 1])
            of_company = fc1.text_input("🏢 발주처(수신 업체명)", value=_order_company)
            of_category = fc2.selectbox("📂 품목 분류", ["공구", "소재", "소모품", "기타"], index=0)
            send_method = st.radio("🚀 처리 방식 선택", ["팩스 발송 + 발주현황 등록", "이메일 발송 + 발주현황 등록", "발주현황에만 즉시 등록"], horizontal=True)
            fc3, fc4 = st.columns(2)
            fax_input = fc3.text_input("📠 수신자 팩스번호", value=order_fax_found)
            email_input = fc4.text_input("📧 수신자 이메일", value=order_email_found)
            of_items = st.text_area("📦 발주명 및 수량", value=draft.get("items", ""))

            b_col1, b_col2 = st.columns([3, 1])
            submit_order = b_col1.form_submit_button("🚀 발주 실행 및 발주현황에 자동 기록", type="primary", use_container_width=True, disabled=is_backup_mode)
            cancel_order = b_col2.form_submit_button("✖ 발주창 닫기", use_container_width=True)

            if cancel_order:
                del st.session_state["_active_order_draft"]
                st.rerun()

            if submit_order:
                if not of_company.strip() or not of_items.strip():
                    st.error("❌ 발주처와 발주명을 모두 입력해 주세요.")
                else:
                    _detail = f"업체: {of_company}\n품목/내용: {of_items}\n요청일: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                    send_ok = False
                    send_msg = ""
                    if send_method.startswith("팩스"):
                        if not fax_input.strip():
                            st.error("❌ 팩스번호를 입력해 주세요.")
                        else:
                            send_ok, send_msg = send_order_fax(fax_input.strip(), of_company.strip(), _detail)
                    elif send_method.startswith("이메일"):
                        if not email_input.strip() or "@" not in email_input:
                            st.error("❌ 수신자 이메일이 올바르지 않습니다.")
                        else:
                            send_ok, send_msg = send_order_email(email_input.strip(), of_company.strip(), _detail)
                    else:
                        send_ok = True
                        send_msg = "✅ 전화/직접 발주 건으로 발주현황에 등록합니다."

                    if send_ok:
                        reg_cnt = register_order_to_status(
                            company=of_company.strip(),
                            items_text=of_items.strip(),
                            category=of_category,
                            memo=f"AI사무장"
                        )
                        st.success(f"{send_msg}")
                        del st.session_state["_active_order_draft"]
                        time.sleep(1.2)
                        st.rerun()

# =====================================================================
# [SECTION 9] 화면 구성: 2. 통합 장부 센터
# =====================================================================
elif menu == "📊 2. 통합 장부 센터":
    st.header("📊 통합 매입 장부 센터")
    _ensure_purchase_orders_table()
    
    if is_backup_mode:
        st.warning(f"⚠️ 현재 [{selected_db_name}] 과거 장부를 열람하고 있습니다.")
    
    tab_view, tab_order_and_edit, tab_print, tab_address, tab_backup = st.tabs([
        "📖 통합 매입 장부 목록",
        "📦 발주현황 (입고관리 및 직접수정)",
        "🖨️ 거래처별 정산서 출력",
        "📞 거래처 주소록",
        "💾 장부 안전 백업"
    ])
    
    ledger_column_config = {
        "id": st.column_config.NumberColumn("ID(수정불가)", disabled=True),
        "quote_date": "일자(입고일)", "company": "거래처", "category": "분류", 
        "part_name": "품목명", "unit_price": "단가", "quantity": "수량", "total_price": "총액"
    }

    with _connect_db() as conn: 
        df_ledger_all = pd.read_sql_query("SELECT * FROM tool_ledger", conn)
        try:
            df_orders_all = pd.read_sql_query("SELECT * FROM purchase_orders ORDER BY id DESC", conn)
        except Exception:
            df_orders_all = pd.DataFrame(columns=[
                'id', 'company', 'category', 'part_name', 'quantity',
                'unit_price', 'order_date', 'is_received', 'receive_date', 'moved_to_ledger', 'memo'
            ])

    if not df_ledger_all.empty:
        df_ledger_all['clean_date'] = pd.to_datetime(df_ledger_all['quote_date'], errors='coerce')

    with tab_view:
        st.subheader("📖 통합 매입 장부 목록")
        sort_opt = st.radio("🔄 뷰 정렬 기준", ["날짜별(최신순)", "업체별", "품목별(가나다순)"], horizontal=True)
        df_view = df_ledger_all.copy()
            
        if not df_view.empty:
            if sort_opt == "업체별": df_view = df_view.sort_values(by=['company', 'clean_date'], ascending=[True, False])
            elif sort_opt == "날짜별(최신순)": df_view = df_view.sort_values(by=['clean_date', 'id'], ascending=[False, False])
            else: df_view = df_view.sort_values(by=['part_name', 'clean_date'], ascending=[True, False])
                
            df_view = df_view.drop(columns=['clean_date', 'id'], errors='ignore')
            show_print_button()
            
            df_print_view_v1 = df_view.copy()
            cols_order_v1 = ['quote_date', 'company', 'category', 'part_name', 'unit_price', 'quantity', 'total_price']
            df_print_view_v1 = df_print_view_v1[[c for c in cols_order_v1 if c in df_print_view_v1.columns]]
            for col in ['unit_price', 'quantity', 'total_price']:
                if col in df_print_view_v1.columns:
                    df_print_view_v1[col] = df_print_view_v1[col].apply(lambda x: f"{_safe_int_val(x, 0):,}")
            df_print_view_v1 = df_print_view_v1.rename(columns={
                "quote_date": "일자(입고일)", "company": "거래처", "category": "분류", 
                "part_name": "품목명", "unit_price": "단가(원)", "quantity": "수량", "total_price": "총액(원)"
            })
            html_table_v1 = df_print_view_v1.to_html(index=False, escape=True, classes="gse-print-table")
            html_table_clean_v1 = "\n".join([line.lstrip() for line in html_table_v1.splitlines()])
            custom_style_v1 = (
                "<style>"
                ".gse-print-table { width: 100% !important; border-collapse: collapse !important; background-color: #ffffff !important; color: #000000 !important; font-size: 13px !important; margin-top: 10px !important; }"
                ".gse-print-table th, .gse-print-table td { border: 1px solid #333333 !important; padding: 7px 10px !important; color: #000000 !important; background-color: #ffffff !important; word-break: keep-all; }"
                ".gse-print-table th { background-color: #f0f0f0 !important; font-weight: bold !important; text-align: center !important; }"
                ".gse-print-table td:nth-child(5), .gse-print-table td:nth-child(6), .gse-print-table td:nth-child(7) { text-align: right !important; }"
                ".gse-print-table td:nth-child(1), .gse-print-table td:nth-child(3) { text-align: center !important; }"
                "</style>"
            )
            st.markdown(custom_style_v1 + html_table_clean_v1, unsafe_allow_html=True)
        else:
            st.info("기록된 통합 매입 장부 데이터가 없습니다.")

    with tab_order_and_edit:
        st.subheader("📦 발주현황 및 입고 관리")
        
        with st.expander("➕ 신규 발주 직접 등록", expanded=False):
            with st.form("quick_new_order_form", clear_on_submit=True):
                oc1, oc2, oc3, oc4, oc5 = st.columns([1.5, 1, 2.5, 1, 1.2])
                no_company = oc1.text_input("🏢 발주처 (필수)", placeholder="예: 원영공구")
                no_category = oc2.selectbox("📂 분류", ["공구", "소재", "소모품", "기타"])
                no_part = oc3.text_input("📦 발주명 (품목/규격)", placeholder="예: 초경엔드밀 10파이")
                no_qty = oc4.number_input("🔢 발주수량", min_value=1, value=1, step=1)
                no_date = oc5.date_input("📅 발주일", value=datetime.now())
                if st.form_submit_button("📥 발주현황에 추가", type="primary", disabled=is_backup_mode):
                    if not no_company.strip() or not no_part.strip():
                        st.error("⚠️ 발주처와 발주명을 모두 입력해 주세요.")
                    else:
                        with sqlite3.connect(str(DB_PATH)) as conn:
                            conn.execute("""
                                INSERT INTO purchase_orders 
                                (company, category, part_name, quantity, unit_price, order_date, is_received, receive_date, moved_to_ledger, memo)
                                VALUES (?, ?, ?, ?, 0, ?, 0, '', 0, '직접발주등록')
                            """, (no_company.strip(), no_category, no_part.strip(), int(no_qty), no_date.strftime("%Y-%m-%d")))
                            conn.commit()
                        st.success(f"✅ 발주 등록 완료!")
                        time.sleep(0.5)
                        st.rerun()

        filter_col1, filter_col2 = st.columns([2, 2])
        order_filter_opt = filter_col1.radio(
            "🔎 발주현황 표시 범위",
            ["⏳ 미입고 (입고 대기중)", "📋 전체 보기", "✅ 입고완료 내역만"],
            horizontal=True,
            key="order_status_filter_radio"
        )

        df_po_work = df_orders_all.copy()
        if not df_po_work.empty:
            df_po_work['is_received'] = df_po_work['is_received'].apply(lambda x: bool(_safe_int_val(x, 0)))
            df_po_work['moved_to_ledger'] = df_po_work['moved_to_ledger'].apply(lambda x: _safe_int_val(x, 0))
            df_po_work['status_badge'] = df_po_work['moved_to_ledger'].apply(lambda x: "✅ 장부이동완료" if x == 1 else "⏳ 대기중")
        else:
            df_po_work = pd.DataFrame(columns=[
                'id', 'company', 'category', 'part_name', 'quantity', 'unit_price',
                'order_date', 'is_received', 'receive_date', 'status_badge', 'moved_to_ledger'
            ])

        if order_filter_opt == "⏳ 미입고 (입고 대기중)":
            df_po_view = df_po_work[df_po_work['moved_to_ledger'] == 0].copy()
        elif order_filter_opt == "✅ 입고완료 내역만":
            df_po_view = df_po_work[df_po_work['moved_to_ledger'] == 1].copy()
        else:
            df_po_view = df_po_work.copy()

        po_display_cols = [
            'id', 'company', 'category', 'part_name', 'quantity',
            'unit_price', 'order_date', 'is_received', 'receive_date', 'status_badge'
        ]
        for c in po_display_cols:
            if c not in df_po_view.columns:
                df_po_view[c] = False if c == 'is_received' else (0 if c in ('quantity', 'unit_price') else "")

        po_column_config = {
            "id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
            "company": st.column_config.TextColumn("발주처", required=True),
            "category": st.column_config.SelectboxColumn("분류", options=["공구", "소재", "소모품", "기타"], default="공구", width="small"),
            "part_name": st.column_config.TextColumn("발주명 (품목/규격)", required=True),
            "quantity": st.column_config.NumberColumn("발주수량", min_value=1, default=1, step=1, width="small"),
            "unit_price": st.column_config.NumberColumn("입고단가(원)", min_value=0, default=0, step=100),
            "order_date": st.column_config.TextColumn("발주일", default=datetime.now().strftime("%Y-%m-%d")),
            "is_received": st.column_config.CheckboxColumn("입고완료", default=False),
            "receive_date": st.column_config.TextColumn("입고날짜", default=""),
            "status_badge": st.column_config.TextColumn("장부이관 상태", disabled=True),
        }

        edited_po = st.data_editor(
            df_po_view[po_display_cols],
            hide_index=True,
            use_container_width=True,
            num_rows="dynamic" if not is_backup_mode else "fixed",
            column_config=po_column_config,
            key="purchase_orders_editor"
        )

        if st.button("💾 발주현황 저장 및 입고완료 항목 장부 이동", type="primary", use_container_width=True, disabled=is_backup_mode):
            today_str = datetime.now().strftime("%Y-%m-%d")
            moved_count = 0

            with sqlite3.connect(str(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("SELECT id, moved_to_ledger, memo FROM purchase_orders")
                existing_po_map = {int(r[0]): {"moved": int(r[1] or 0), "memo": str(r[2] or "")} for r in cur.fetchall()}

                visible_orig_ids = set(
                    int(x) for x in df_po_view['id'].dropna().tolist()
                    if str(x).strip() != ""
                )
                kept_ids = set()

                for _, row in edited_po.iterrows():
                    raw_id = row.get('id')
                    is_new_row = (raw_id is None or (isinstance(raw_id, float) and pd.isna(raw_id)) or str(raw_id).strip() == "")
                    comp = str(row.get('company', '') or '').strip()
                    pname = str(row.get('part_name', '') or '').strip()
                    if not comp and not pname:
                        continue

                    cat = str(row.get('category', '공구') or '공구').strip()
                    qty = max(1, _safe_int_val(row.get('quantity', 1), 1))
                    u_price = _safe_int_val(row.get('unit_price', 0), 0)
                    o_date = str(row.get('order_date', '') or '').strip() or today_str
                    is_rec = 1 if bool(row.get('is_received', False)) else 0
                    r_date = str(row.get('receive_date', '') or '').strip()

                    if is_rec == 1 and not r_date:
                        r_date = today_str

                    prev_moved = 0
                    prev_memo = "직접입력"
                    if not is_new_row:
                        po_id = int(float(raw_id))
                        kept_ids.add(po_id)
                        prev_info = existing_po_map.get(po_id, {"moved": 0, "memo": ""})
                        prev_moved = prev_info["moved"]
                        prev_memo = prev_info["memo"]

                    new_moved = prev_moved
                    if is_rec == 1 and prev_moved == 0:
                        total_p = qty * u_price
                        cur.execute("""
                            SELECT 1 FROM tool_ledger 
                            WHERE company = ? AND part_name = ? AND quote_date = ? AND unit_price = ? AND quantity = ?
                        """, (comp, pname, r_date, u_price, qty))
                        
                        if not cur.fetchone():
                            cur.execute("""
                                INSERT INTO tool_ledger (quote_date, company, category, part_name, unit_price, quantity, total_price)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            """, (r_date, comp, cat, pname, u_price, qty, total_p))
                            moved_count += 1
                        new_moved = 1
                    elif is_rec == 0:
                        new_moved = 0

                    if is_new_row:
                        cur.execute("""
                            INSERT INTO purchase_orders
                            (company, category, part_name, quantity, unit_price, order_date, is_received, receive_date, moved_to_ledger, memo)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (comp, cat, pname, qty, u_price, o_date, is_rec, r_date, new_moved, prev_memo))
                    else:
                        cur.execute("""
                            UPDATE purchase_orders
                            SET company=?, category=?, part_name=?, quantity=?, unit_price=?,
                                order_date=?, is_received=?, receive_date=?, moved_to_ledger=?
                            WHERE id=?
                        """, (comp, cat, pname, qty, u_price, o_date, is_rec, r_date, new_moved, po_id))

                ids_to_delete = visible_orig_ids - kept_ids
                for did in ids_to_delete:
                    cur.execute("DELETE FROM purchase_orders WHERE id=?", (did,))

                conn.commit()

            if moved_count > 0:
                sync_ok, sync_msg = _sync_ledger_to_shared_folder("발주현황 입고완료 장부이관")
                if sync_ok:
                    st.toast(sync_msg)
                st.success(f"✅ 저장 완료! {moved_count}건이 통합 매입 장부로 이동되었습니다.")
            else:
                st.success("✅ 저장되었습니다.")
            time.sleep(0.7)
            st.rerun()

        st.divider()

        st.subheader("🛠️ 2. 통합 매입 장부 데이터 직접 수정")
        sort_opt_edit = st.radio("🔄 편집 뷰 정렬 기준", ["날짜별(최신순)", "업체별", "품목별(가나다순)"], horizontal=True, key="sort_edit_radio")
        
        df_edit = df_ledger_all.copy()
        if not df_edit.empty:
            if sort_opt_edit == "업체별": df_edit = df_edit.sort_values(by=['company', 'clean_date'], ascending=[True, False])
            elif sort_opt_edit == "날짜별(최신순)": df_edit = df_edit.sort_values(by=['clean_date', 'id'], ascending=[False, False])
            else: df_edit = df_edit.sort_values(by=['part_name', 'clean_date'], ascending=[True, False])
            df_edit = df_edit.drop(columns=['clean_date'], errors='ignore')
            
        edited_tools = st.data_editor(
            df_edit, hide_index=True, use_container_width=True, 
            num_rows="dynamic" if not is_backup_mode else "fixed",
            column_config=ledger_column_config,
            key="tool_ledger_direct_editor"
        )
        
        if st.button("💾 통합 매입 장부 수정본 덮어쓰기 저장", type="primary", disabled=is_backup_mode):
            if not edited_tools.empty:
                for idx, r in edited_tools.iterrows():
                    up = _safe_int_val(r.get('unit_price', 0), 0)
                    qt = _safe_int_val(r.get('quantity', 1), 1)
                    tp = _safe_int_val(r.get('total_price', 0), 0)
                    if up >= 0 and qt > 0 and tp != (up * qt):
                        edited_tools.at[idx, 'total_price'] = up * qt
            try:
                _save_df_preserving_schema(edited_tools, "tool_ledger", pk="id", text_pk=False)
            except RuntimeError as e:
                st.error(str(e))
                st.stop()
                
            sync_ok, sync_msg = _sync_ledger_to_shared_folder("매입 장부 편집본")
            if sync_ok: st.toast(sync_msg)
            
            st.success("✅ 저장 및 동기화 완료!")
            time.sleep(0.5)
            st.rerun()

    with tab_print:
        st.subheader("🖨️ 거래처별 정산서 출력")
        df_print = df_ledger_all.copy()
        if not df_print.empty:
            companies = ["전체"] + sorted([str(c) for c in df_print['company'].dropna().unique() if str(c).strip()])
            
            if 'clean_date' in df_print.columns:
                years = ["전체"] + sorted([str(int(y)) for y in df_print['clean_date'].dt.year.dropna().unique()])
            else:
                years = ["전체"]

            valid_months = df_print['clean_date'].dt.month.dropna().unique()
            months = ["전체"] + sorted([str(int(m)) for m in valid_months])
            
            c1, c2, c3 = st.columns(3)
            sel_company = c1.selectbox("🏢 정산할 거래처 선택", companies)
            sel_year = c2.selectbox("📅 정산할 연도(Year) 선택", years)
            sel_month = c3.selectbox("📅 정산할 월(Month) 선택", months)
            
            df_filtered = df_print.copy()
            if sel_company != "전체": 
                df_filtered = df_filtered[df_filtered['company'] == sel_company]
                
            if sel_year != "전체":
                df_filtered = df_filtered[df_filtered['clean_date'].notna() & (df_filtered['clean_date'].dt.year == int(sel_year))]

            if sel_month != "전체": 
                df_filtered = df_filtered[df_filtered['clean_date'].notna() & (df_filtered['clean_date'].dt.month == int(sel_month))]
            
            if not df_filtered.empty:
                total_amount = df_filtered['total_price'].sum()
                report_title_company = sel_company if sel_company != "전체" else "전체 거래처"
                report_title_year = f"{sel_year}년" if sel_year != "전체" else ""
                report_title_month = f"{sel_month}월" if sel_month != "전체" else "전체 기간"
                
                st.markdown(f"### 🧾 [{report_title_company}] {report_title_year} {report_title_month} 정산 내역")
                st.markdown(f"#### 💰 총 결제 대상 금액: **{total_amount:,.0f} 원**")
                show_print_button()
                
                df_print_view = df_filtered.copy()
                cols_order = ['quote_date', 'company', 'category', 'part_name', 'unit_price', 'quantity', 'total_price']
                df_print_view = df_print_view[[c for c in cols_order if c in df_print_view.columns]]
                
                for col in ['unit_price', 'quantity', 'total_price']:
                    if col in df_print_view.columns:
                        df_print_view[col] = df_print_view[col].apply(lambda x: f"{_safe_int_val(x, 0):,}")
                
                df_print_view = df_print_view.rename(columns={
                    "quote_date": "일자(입고일)", "company": "거래처", "category": "분류", 
                    "part_name": "품목명", "unit_price": "단가(원)", "quantity": "수량", "total_price": "총액(원)"
                })
                
                html_table = df_print_view.to_html(index=False, escape=True, classes="gse-print-table")
                html_table_clean = "\n".join([line.lstrip() for line in html_table.splitlines()])
                
                custom_style = (
                    "<style>"
                    ".gse-print-table { width: 100% !important; border-collapse: collapse !important; background-color: #ffffff !important; color: #000000 !important; font-size: 13px !important; margin-top: 10px !important; }"
                    ".gse-print-table th, .gse-print-table td { border: 1px solid #333333 !important; padding: 7px 10px !important; color: #000000 !important; background-color: #ffffff !important; word-break: keep-all; }"
                    ".gse-print-table th { background-color: #f0f0f0 !important; font-weight: bold !important; text-align: center !important; }"
                    ".gse-print-table td:nth-child(5), .gse-print-table td:nth-child(6), .gse-print-table td:nth-child(7) { text-align: right !important; }"
                    ".gse-print-table td:nth-child(1), .gse-print-table td:nth-child(3) { text-align: center !important; }"
                    "</style>"
                )
                
                st.markdown(custom_style + html_table_clean, unsafe_allow_html=True)
            else: st.info("해당 조건에 일치하는 데이터가 없습니다.")
        else: st.info("기록된 데이터가 없습니다.")

    with tab_address:
        st.subheader("📞 거래처 주소록")
        if is_backup_mode:
            st.warning("⚠️ 과거 장부 열람 모드입니다.")
        with st.expander("➕ 신규 거래처 등록", expanded=False):
            with st.form("supplier_form", clear_on_submit=True):
                s_company = st.text_input("🏢 업체명")
                s_contact = st.text_input("👤 담당자")
                sc1, sc2 = st.columns(2)
                s_phone = sc1.text_input("☎️ 연락처")
                s_fax = sc2.text_input("🖨️ 팩스번호")
                s_email = st.text_input("📧 이메일")
                s_memo = st.text_area("📝 메모")
                
                if st.form_submit_button("💾 거래처 등록", disabled=is_backup_mode):
                    if s_company.strip():
                        try:
                            with sqlite3.connect(str(DB_PATH)) as conn:
                                conn.execute("""INSERT OR IGNORE INTO suppliers 
                                    (company_name, contact_name, phone, email, fax, memo) VALUES (?,?,?,?,?,?)""",
                                    (s_company.strip(), s_contact.strip(), s_phone.strip(), s_email.strip(), s_fax.strip(), s_memo.strip()))
                                if conn.total_changes == 0:
                                    st.error(f"⚠️ 이미 등록된 업체명입니다.")
                                else:
                                    st.success(f"✅ 등록 완료!")
                                    time.sleep(0.5)
                                    st.rerun()
                        except Exception as e:
                            st.error(f"❌ 오류: {str(e)}")
                    else:
                        st.error("⚠️ 업체명을 입력해주세요.")
        
        with _connect_db() as conn: 
            try:
                df_addr = pd.read_sql_query("SELECT * FROM suppliers ORDER BY company_name ASC", conn)
            except Exception:
                df_addr = pd.DataFrame(columns=['id', 'company_name', 'contact_name', 'phone', 'email', 'fax', 'memo'])
        
        addr_column_config = {
            "id": st.column_config.NumberColumn("ID", disabled=True),
            "company_name": "업체명", "contact_name": "담당자", "phone": "연락처",
            "email": "이메일", "fax": "팩스번호", "memo": "메모"
        }
        
        edited_addr = st.data_editor(
            df_addr, hide_index=True, use_container_width=True, num_rows="dynamic" if not is_backup_mode else "fixed",
            column_config=addr_column_config, key="supplier_editor"
        )
        
        if st.button("💾 주소록 덮어쓰기 로컬 저장", key="save_supplier", type="primary", disabled=is_backup_mode):
            try:
                _save_df_preserving_schema(edited_addr, "suppliers", pk="id", text_pk=False)
            except RuntimeError as e:
                st.error(str(e)); st.stop()
            st.success("✅ 주소록 업데이트 완료!")
            time.sleep(0.5)
            st.rerun()

    with tab_backup:
        st.subheader("💾 월말 스냅샷 안전 백업")
        custom_backup_name = st.text_input("📝 백업 파일명 지정", placeholder="예: 9월장부백업")
        
        c1, c2 = st.columns(2)
        with c1:
            if st.button("🚀 월말 장부 스냅샷 생성", type="primary", use_container_width=True, disabled=is_backup_mode):
                now_str = datetime.now().strftime('%Y%m%d_%H%M%S') 
                input_name = custom_backup_name.strip()
                backup_filename = input_name if input_name.endswith(".db") else f"{input_name}.db" if input_name else f"장부백업_{now_str}.db"
                    
                shutil.copy2(str(DB_PATH), str(BACKUP_DIR / backup_filename))
                st.success(f"✅ 스냅샷 생성 완료!")
                time.sleep(0.5)
                st.rerun()
                
        with c2:
            usb_ready = st.checkbox("🔌 USB 다운로드 파일 준비", key="usb_download_gate")
            if usb_ready:
                try:
                    with open(DB_PATH, "rb") as f: db_bytes = f.read()
                    st.download_button("💾 외부 저장소 다운로드", db_bytes, f"gse_장부_{datetime.now().strftime('%Y%m%d')}.db", "application/octet-stream", use_container_width=True)
                except Exception: st.warning("다운로드 할 수 없습니다.")
            else:
                st.caption("체크 시 다운로드 버튼이 표시됩니다.")

# =====================================================================
# [SECTION 10] 화면 구성: 3. 셋업 시트 매니저
# =====================================================================
elif menu == "🏭 3. 셋업 시트 매니저":
    st.header("📋 스마트 셋업 시트 관리")
    
    tab_sheet, tab_gcode, tab_memo, tab_work = st.tabs(["📋 셋업 시트", "⚙️ G코드 보관함", "📝 가공 메모", "📈 작업일지"])
    
    @st.cache_data(show_spinner=False)
    def _decode_img_bytes(b64_str):
        return base64.b64decode(b64_str)

    with tab_sheet:
        with st.expander("➕ 신규 셋업 시트 작성", expanded=False):
            with st.form("setup_form", clear_on_submit=True):
                c1, c2 = st.columns(2)
                part_name = c1.text_input("📦 품번/품명")
                machine = c1.selectbox("📟 장비", ["두산 DNM500", "두산 DNM650", "두산 GT2100", "공통/기타"])
                material = c2.text_input("🧪 재질")
                g_code_coord = c2.text_input("📍 좌표계")
                tool_info = st.text_area("🛠️ 공구 리스트", height=100)
                knowhow = st.text_area("💡 가공 노하우", height=120)
                images = st.file_uploader("📷 세팅 사진", accept_multiple_files=True)
                
                if st.form_submit_button("💾 셋업 시트 로컬 저장", type="primary", disabled=is_backup_mode):
                    if part_name.strip():
                        sheet_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
                        imgs_b64 = json.dumps([base64.b64encode(i.read()).decode() for i in images]) if images else "[]"
                        with sqlite3.connect(str(DB_PATH)) as conn:
                            conn.execute(
                                "INSERT INTO setup_sheets (id, quote_date, part_name, machine, category, material, g_code_coord, tool_info, knowhow, images_b64) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (sheet_id, datetime.now().strftime("%Y-%m-%d"), part_name.strip(), machine, "방산/일반", material.strip(), g_code_coord.strip(), tool_info, knowhow, imgs_b64)
                            )
                            conn.commit()
                        st.session_state["selected_setup_id"] = sheet_id
                        st.success("✅ 저장 완료!")
                        time.sleep(0.5)
                        st.rerun()
                    else:
                        st.error("⚠️ 품번/품명을 입력해주세요.")

        with _connect_db() as conn: 
            df_setup = pd.read_sql_query("SELECT * FROM setup_sheets ORDER BY id DESC", conn)
        
        if not df_setup.empty:
            df_setup['id'] = df_setup['id'].astype(str)
            valid_sids = df_setup['id'].tolist()
            if st.session_state.get("selected_setup_id") not in valid_sids:
                st.session_state["selected_setup_id"] = valid_sids[0]

            col_left, col_right = st.columns([1.15, 1], gap="large")

            with col_left:
                st.markdown("##### 📂 보관된 셋업 시트 목록")
                df_s_list = df_setup[['id', 'quote_date', 'part_name', 'machine', 'material']].copy()
                df_s_list.insert(0, "열람", df_s_list['id'] == st.session_state["selected_setup_id"])
                df_s_list.insert(1, "삭제", False)

                edited_s_list = st.data_editor(
                    df_s_list,
                    hide_index=True,
                    use_container_width=True,
                    num_rows="fixed",
                    column_config={
                        "열람": st.column_config.CheckboxColumn("👀 열람", width="small"),
                        "삭제": st.column_config.CheckboxColumn("🗑️ 삭제", width="small"),
                        "id": None,
                        "quote_date": st.column_config.TextColumn("등록일", width="small", disabled=True),
                        "part_name": st.column_config.TextColumn("품번/품명"),
                        "machine": st.column_config.TextColumn("장비", width="small"),
                        "material": st.column_config.TextColumn("재질", width="small"),
                    },
                    key=f"setup_list_ed_{st.session_state['selected_setup_id']}"
                )

                checked_s = edited_s_list[edited_s_list["열람"] == True]
                for _, r in checked_s.iterrows():
                    if str(r["id"]) != st.session_state["selected_setup_id"]:
                        st.session_state["selected_setup_id"] = str(r["id"])
                        st.rerun()

                if st.button("💾 목록 변경사항 적용", key="btn_list_setup", type="primary", disabled=is_backup_mode):
                    with sqlite3.connect(str(DB_PATH)) as conn:
                        cur = conn.cursor()
                        for _, r in edited_s_list.iterrows():
                            sid = str(r["id"])
                            if bool(r["삭제"]):
                                cur.execute("DELETE FROM setup_sheets WHERE id=?", (sid,))
                            else:
                                cur.execute(
                                    "UPDATE setup_sheets SET part_name=?, machine=?, material=? WHERE id=?",
                                    (str(r["part_name"]).strip(), str(r["machine"]).strip(), str(r["material"]).strip(), sid)
                                )
                        conn.commit()
                    st.success("✅ 반영되었습니다!")
                    time.sleep(0.5)
                    st.rerun()

            with col_right:
                st.markdown("##### 🔎 셋업 시트 상세 미리보기 및 편집")
                sel_sid = st.session_state["selected_setup_id"]
                s_row = df_setup[df_setup['id'] == sel_sid]

                if not s_row.empty:
                    sr = s_row.iloc[0]
                    rc1, rc2, rc3 = st.columns(3)
                    ed_machine = rc1.text_input("📟 장비", value=str(sr.get('machine', '') or ''), key=f"s_mach_{sel_sid}")
                    ed_mat = rc2.text_input("🧪 재질", value=str(sr.get('material', '') or ''), key=f"s_mat_{sel_sid}")
                    ed_coord = rc3.text_input("📍 좌표계", value=str(sr.get('g_code_coord', '') or ''), key=f"s_coord_{sel_sid}")

                    ed_tools = st.text_area(
                        "🛠️ 공구 리스트",
                        value=str(sr.get('tool_info', '') or ''),
                        height=140,
                        key=f"s_tools_{sel_sid}"
                    )
                    ed_knowhow = st.text_area(
                        "💡 가공 노하우",
                        value=str(sr.get('knowhow', '') or ''),
                        height=180,
                        key=f"s_know_{sel_sid}"
                    )

                    try:
                        curr_imgs = json.loads(sr.get('images_b64', '[]')) if isinstance(sr.get('images_b64'), str) else []
                    except Exception:
                        curr_imgs = []

                    new_imgs = st.file_uploader("📷 세팅 사진 추가", accept_multiple_files=True, key=f"s_up_{sel_sid}")
                    clear_imgs = st.checkbox(f"🗑️ 기존 사진 비우기", value=False, key=f"s_clr_{sel_sid}")

                    if st.button("💾 셋업 시트 내용 덮어쓰기", key="btn_detail_setup", type="primary", disabled=is_backup_mode):
                        final_imgs = [] if clear_imgs else list(curr_imgs)
                        if new_imgs:
                            final_imgs.extend([base64.b64encode(i.read()).decode() for i in new_imgs])
                        with sqlite3.connect(str(DB_PATH)) as conn:
                            conn.execute(
                                """UPDATE setup_sheets 
                                   SET machine=?, material=?, g_code_coord=?, tool_info=?, knowhow=?, images_b64=? 
                                   WHERE id=?""",
                                (ed_machine.strip(), ed_mat.strip(), ed_coord.strip(), ed_tools, ed_knowhow, json.dumps(final_imgs), sel_sid)
                            )
                            conn.commit()
                        st.success("✅ 저장되었습니다!")
                        time.sleep(0.5)
                        st.rerun()

                    if curr_imgs:
                        st.markdown(f"**📷 등록된 세팅 사진 ({len(curr_imgs)}장)**")
                        img_cols = st.columns(3)
                        for idx_img, b64_str in enumerate(curr_imgs[:9]):
                            try:
                                img_cols[idx_img % 3].image(_decode_img_bytes(b64_str), use_container_width=True)
                            except Exception:
                                pass
        else:
            st.info("등록된 셋업 시트가 없습니다.")

    with tab_gcode:
        with st.expander("➕ 신규 G코드 작성", expanded=False):
            with st.form("gcode_form", clear_on_submit=True):
                c1, c2 = st.columns(2)
                g_title = c1.text_input("📌 G코드 제목")
                g_machine = c2.selectbox("📟 적용 장비", ["공통", "두산 DNM500", "두산 DNM650", "두산 GT2100"])
                g_code = st.text_area("💻 G코드 내용", height=150)
                g_desc = st.text_area("💡 설명", height=100)
                
                if st.form_submit_button("💾 G코드 로컬 저장", type="primary", disabled=is_backup_mode):
                    if g_title.strip() and g_code.strip():
                        with sqlite3.connect(str(DB_PATH)) as conn:
                            cur = conn.cursor()
                            cur.execute("INSERT INTO gcodes (title, machine, code, description) VALUES (?,?,?,?)", (g_title.strip(), g_machine, g_code, g_desc))
                            new_gid = cur.lastrowid
                            conn.commit()
                        st.session_state["selected_gcode_id"] = int(new_gid)
                        st.success("✅ 저장 완료!")
                        time.sleep(0.5)
                        st.rerun()
                    else:
                        st.error("⚠️ 제목과 내용을 입력해주세요.")
        
        with _connect_db() as conn: 
            df_gcode = pd.read_sql_query("SELECT * FROM gcodes ORDER BY id DESC", conn)
            
        if not df_gcode.empty:
            df_gcode['id'] = df_gcode['id'].astype(int)
            valid_gids = df_gcode['id'].tolist()
            if st.session_state.get("selected_gcode_id") not in valid_gids:
                st.session_state["selected_gcode_id"] = valid_gids[0]

            col_left_g, col_right_g = st.columns([1.15, 1], gap="large")

            with col_left_g:
                st.markdown("##### 📂 G코드 목록")
                df_g_list = df_gcode[['id', 'title', 'machine']].copy()
                df_g_list.insert(0, "열람", df_g_list['id'] == st.session_state["selected_gcode_id"])
                df_g_list.insert(1, "삭제", False)

                edited_g_list = st.data_editor(
                    df_g_list,
                    hide_index=True,
                    use_container_width=True,
                    num_rows="fixed",
                    column_config={
                        "열람": st.column_config.CheckboxColumn("👀 열람", width="small"),
                        "삭제": st.column_config.CheckboxColumn("🗑️ 삭제", width="small"),
                        "id": None,
                        "title": st.column_config.TextColumn("G코드 제목"),
                        "machine": st.column_config.TextColumn("장비", width="small"),
                    },
                    key=f"gcode_list_ed_{st.session_state['selected_gcode_id']}"
                )

                checked_g = edited_g_list[edited_g_list["열람"] == True]
                for _, r in checked_g.iterrows():
                    if int(r["id"]) != st.session_state["selected_gcode_id"]:
                        st.session_state["selected_gcode_id"] = int(r["id"])
                        st.rerun()

                if st.button("💾 목록 변경사항 적용", key="btn_list_gcode", type="primary", disabled=is_backup_mode):
                    with sqlite3.connect(str(DB_PATH)) as conn:
                        cur = conn.cursor()
                        for _, r in edited_g_list.iterrows():
                            gid = int(r["id"])
                            if bool(r["삭제"]):
                                cur.execute("DELETE FROM gcodes WHERE id=?", (gid,))
                            else:
                                cur.execute(
                                    "UPDATE gcodes SET title=?, machine=? WHERE id=?",
                                    (str(r["title"]).strip(), str(r["machine"]).strip(), gid)
                                )
                        conn.commit()
                    st.success("✅ 반영되었습니다!")
                    time.sleep(0.5)
                    st.rerun()

            with col_right_g:
                st.markdown("##### 🔎 G코드 본문 편집")
                sel_gid = st.session_state["selected_gcode_id"]
                g_row = df_gcode[df_gcode['id'] == sel_gid]

                if not g_row.empty:
                    gr = g_row.iloc[0]
                    ed_g_code = st.text_area(
                        "💻 G코드 본문",
                        value=str(gr.get('code', '') or ''),
                        height=240,
                        key=f"g_code_{sel_gid}"
                    )
                    ed_g_desc = st.text_area(
                        "💡 설명",
                        value=str(gr.get('description', '') or ''),
                        height=130,
                        key=f"g_desc_{sel_gid}"
                    )

                    if st.button("💾 G코드 내용 덮어쓰기", key="btn_detail_gcode", type="primary", disabled=is_backup_mode):
                        with sqlite3.connect(str(DB_PATH)) as conn:
                            conn.execute(
                                "UPDATE gcodes SET code=?, description=? WHERE id=?",
                                (ed_g_code, ed_g_desc, sel_gid)
                            )
                            conn.commit()
                        st.success("✅ 저장되었습니다!")
                        time.sleep(0.5)
                        st.rerun()
        else:
            st.info("등록된 G코드가 없습니다.")
            
    with tab_memo:
        with st.expander("➕ 신규 메모 작성", expanded=False):
            with st.form("memo_form", clear_on_submit=True):
                m_title = st.text_input("📌 메모 제목")
                m_content = st.text_area("📝 메모 내용", height=200)
                
                if st.form_submit_button("💾 메모 로컬 저장", type="primary", disabled=is_backup_mode):
                    if m_title.strip() and m_content.strip():
                        m_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
                        m_date = datetime.now().strftime("%Y-%m-%d %H:%M")
                        with sqlite3.connect(str(DB_PATH)) as conn:
                            conn.execute("INSERT INTO memos (id, title, content, date) VALUES (?,?,?,?)", (m_id, m_title.strip(), m_content, m_date))
                            conn.commit()
                        st.session_state["selected_memo_id"] = m_id
                        st.success("✅ 저장 완료!")
                        time.sleep(0.5)
                        st.rerun()
                    else:
                        st.error("⚠️ 제목과 내용을 입력해주세요.")
                        
        with _connect_db() as conn: 
            df_memo = pd.read_sql_query("SELECT * FROM memos ORDER BY date DESC, id DESC", conn)
            
        if not df_memo.empty:
            df_memo['id'] = df_memo['id'].astype(str)
            valid_mids = df_memo['id'].tolist()
            if st.session_state.get("selected_memo_id") not in valid_mids:
                st.session_state["selected_memo_id"] = valid_mids[0]

            col_left_m, col_right_m = st.columns([1.15, 1], gap="large")

            with col_left_m:
                st.markdown("##### 📂 가공 메모 목록")
                df_m_list = df_memo[['id', 'title', 'date']].copy()
                df_m_list.insert(0, "열람", df_m_list['id'] == st.session_state["selected_memo_id"])
                df_m_list.insert(1, "삭제", False)

                edited_m_list = st.data_editor(
                    df_m_list,
                    hide_index=True,
                    use_container_width=True,
                    num_rows="fixed",
                    column_config={
                        "열람": st.column_config.CheckboxColumn("👀 열람", width="small"),
                        "삭제": st.column_config.CheckboxColumn("🗑️ 삭제", width="small"),
                        "id": None,
                        "title": st.column_config.TextColumn("메모 제목"),
                        "date": st.column_config.TextColumn("작성일", width="small", disabled=True),
                    },
                    key=f"memo_list_ed_{st.session_state['selected_memo_id']}"
                )

                checked_m = edited_m_list[edited_m_list["열람"] == True]
                for _, r in checked_m.iterrows():
                    if str(r["id"]) != st.session_state["selected_memo_id"]:
                        st.session_state["selected_memo_id"] = str(r["id"])
                        st.rerun()

                if st.button("💾 목록 변경사항 적용", key="btn_list_memo", type="primary", disabled=is_backup_mode):
                    with sqlite3.connect(str(DB_PATH)) as conn:
                        cur = conn.cursor()
                        for _, r in edited_m_list.iterrows():
                            mid = str(r["id"])
                            if bool(r["삭제"]):
                                cur.execute("DELETE FROM memos WHERE id=?", (mid,))
                            else:
                                cur.execute(
                                    "UPDATE memos SET title=? WHERE id=?",
                                    (str(r["title"]).strip(), mid)
                                )
                        conn.commit()
                    st.success("✅ 반영되었습니다!")
                    time.sleep(0.5)
                    st.rerun()

            with col_right_m:
                st.markdown("##### 🔎 메모 본문 편집")
                sel_mid = st.session_state["selected_memo_id"]
                m_row = df_memo[df_memo['id'] == sel_mid]

                if not m_row.empty:
                    mr = m_row.iloc[0]
                    ed_m_content = st.text_area(
                        "메모 본문",
                        value=str(mr.get('content', '') or ''),
                        height=360,
                        key=f"m_content_{sel_mid}",
                        label_visibility="collapsed"
                    )

                    if st.button("💾 메모 내용 덮어쓰기", key="btn_detail_memo", type="primary", disabled=is_backup_mode):
                        now_date = datetime.now().strftime("%Y-%m-%d %H:%M")
                        with sqlite3.connect(str(DB_PATH)) as conn:
                            conn.execute(
                                "UPDATE memos SET content=?, date=? WHERE id=?",
                                (ed_m_content, now_date, sel_mid)
                            )
                            conn.commit()
                        st.success("✅ 저장되었습니다!")
                        time.sleep(0.5)
                        st.rerun()
        else:
            st.info("등록된 메모가 없습니다.")

    with tab_work:
        st.subheader("📈 작업일지")
        with st.expander("➕ 신규 작업일지 작성"):
            with st.form("work_form", clear_on_submit=True):
                c1, c2 = st.columns([1, 2])
                t_date = c1.date_input("📅 일자")
                t_item = c2.text_input("📦 품목/내용")
                t_note = st.text_input("📝 비고")
                
                if st.form_submit_button("💾 작업일지 로컬 저장", disabled=is_backup_mode):
                    if t_item.strip():
                        t_id = datetime.now().strftime("%Y%m%d%H%M%S")
                        with sqlite3.connect(str(DB_PATH)) as conn:
                            conn.execute("INSERT INTO work_logs (id, date, item, note) VALUES (?,?,?,?)",
                                         (t_id, t_date.strftime("%Y-%m-%d"), t_item, t_note))
                            conn.commit()
                            
                        st.success("✅ 저장 완료!")
                        time.sleep(0.5)
                        st.rerun()
                    else: 
                        st.error("⚠️ 품목/내용을 입력해주세요.")
                        
        with _connect_db() as conn: 
            try:
                df_work = pd.read_sql_query("SELECT id, date, item, note FROM work_logs ORDER BY date DESC, id DESC", conn)
            except Exception:
                df_work = pd.DataFrame()
            
        if not df_work.empty:
            display_cols = ['id', 'date', 'item', 'note']
            df_work_view = df_work[[c for c in display_cols if c in df_work.columns]]
            
            edited_work = st.data_editor(
                df_work_view, hide_index=True, use_container_width=True, num_rows="dynamic" if not is_backup_mode else "fixed",
                column_config={
                    "id": st.column_config.TextColumn("ID", disabled=True), 
                    "date": "일자", "item": "품목/내용", 
                    "note": "비고"
                }
            )
            
            if st.button("💾 작업일지 덮어쓰기 로컬 저장", key="save_work", disabled=is_backup_mode):
                try:
                    _save_df_preserving_schema(edited_work, "work_logs", pk="id", text_pk=True)
                except RuntimeError as e:
                    st.error(str(e)); st.stop()
                st.success("✅ 업데이트 완료!")
                time.sleep(0.5)
                st.rerun()
        else: 
            st.info("등록된 작업일지가 없습니다.")
