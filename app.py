import os
os.environ["STREAMLIT_GATHER_USAGE_STATS"] = "false"
os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"

import streamlit as st
import json
from datetime import datetime

st.set_page_config(page_title="스마트 가공 매니저", page_icon="⚙️", layout="wide")

DB_FILE = "machining_db.json"

GM_DICTIONARY = {
    "G00": "급속 이송 (가공 없이 목표 위치로 빠르게 이동)",
    "G01": "직선 절삭 이송 (F값에 지정된 속도로 직선 가공)",
    "G02": "원호 가공 CW (시계 방향으로 둥글게 깎기)",
    "G03": "원호 가공 CCW (반시계 방향으로 둥글게 깎기)",
    "G04": "일시 정지 / Dwell (X 또는 P값만큼 제자리에서 대기)",
    "G28": "기계 원점 복귀",
    "G40": "공구 인선 반경 보정 취소 (G41, G42 취소)",
    "G41": "공구 인선 반경 보정 - 좌측 (진행 방향 기준 왼쪽 보정)",
    "G42": "공구 인선 반경 보정 - 우측 (진행 방향 기준 오른쪽 보정)",
    "G43": "공구 길이 보정 + (MCT 주로 사용)",
    "G49": "공구 길이 보정 취소",
    "G54": "공작물 좌표계 1 (가장 많이 쓰는 기본 좌표계)",
    "G55": "공작물 좌표계 2",
    "G56": "공작물 좌표계 3",
    "G70": "정삭 사이클 (CNC 선반)",
    "G71": "외경/내경 황삭 사이클 (CNC 선반)",
    "G76": "나사 절삭 사이클 (CNC 선반)",
    "G80": "고정 사이클 취소 (드릴링 등 구멍 가공 끝날 때 필수)",
    "G81": "스폿 드릴링 사이클 (MCT)",
    "G83": "심공 드릴링 사이클 (MCT - 펙드릴)",
    "G84": "태핑 사이클 (MCT - 탭 가공)",
    "G90": "절대 지령 (MCT) / 내외경 절삭 사이클 (선반)",
    "G91": "증분 지령 (MCT 상대좌표)",
    "G94": "분당 이송 (mm/min)",
    "G95": "회전당 이송 (mm/rev)",
    "G96": "주속 일정 제어 (선반 - 파이 크기에 따라 RPM 자동 변환)",
    "G97": "주속 일정 제어 취소 / RPM 고정 (선반)",
    "M00": "프로그램 정지 (무조건 기계 멈춤, 시작 버튼 누르면 재시작)",
    "M01": "선택적 정지 (기계 조작반의 '옵셔널 스톱' 켜져 있을 때만 멈춤)",
    "M02": "프로그램 종료",
    "M03": "주축 정회전",
    "M04": "주축 역회전",
    "M05": "주축 정지",
    "M06": "공구 교환 (MCT)",
    "M08": "절삭유 켜기 (ON)",
    "M09": "절삭유 끄기 (OFF)",
    "M19": "주축 정위치 정지 (스핀들 오리엔테이션)",
    "M29": "리지드 탭 (MCT - G84 탭 가공 직전 주축 회전과 Z축 이송을 정확히 동기화)",
    "M30": "프로그램 종료 및 메모리 선두로 복귀 (작업 완료)",
    "M98": "서브 프로그램 호출",
    "M99": "서브 프로그램 종료 및 메인으로 복귀"
}

def load_data():
    if not os.path.exists(DB_FILE):
        initial_data = {
            "setup_sheets": [],
            "gcodes": [],
            "memos": [],
            "work_logs": []
        }
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(initial_data, f, ensure_ascii=False, indent=4)
        return initial_data
    
    with open(DB_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        needs_save = False
        if "memos" not in data:
            data["memos"] = []
            needs_save = True
        if "work_logs" not in data:
            data["work_logs"] = []
            needs_save = True
            
        if needs_save:
            save_data(data)
        return data

def save_data(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

db = load_data()

st.sidebar.title("⚙️ 가공 노하우 매니저")
st.sidebar.markdown("---")
menu = st.sidebar.radio(
    "메뉴를 선택하세요",
    ["📋 스마트 셋업 시트 작성", "🔍 셋업 시트 검색/조회", "💾 자주 쓰는 G코드 매니저", "📝 현장 수기 노트 / 자유 메모", "📅 일일 작업 일지"]
)

if menu == "📋 스마트 셋업 시트 작성":
    st.header("📋 신규 스마트 셋업 시트 작성")
    st.write("현장에서 가공한 제품의 세팅 정보와 마스터캠 주의사항을 기록합니다.")
    
    with st.form("setup_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        
        with col1:
            part_name = st.text_input("📦 품명 / 품번", placeholder="예: 반도체 챔버 커버 A형")
            machine = st.selectbox("📟 가공 장비", ["두산 DNM500", "두산 DNM650", "두산 GT2100"])
            category = st.selectbox("🛡️ 제품 분류", ["방산 부품", "반도체 장비부품", "산업 부품", "기타"])
            
        with col2:
            material = st.text_input("🧪 소재 재질", placeholder="예: AL6061, SUS304, S45C 등")
            g_code_coord = st.text_input("📍 작업 좌표계", placeholder="예: G54 (바이스 중앙 중심, 소재 상면 Z0)")
            
        st.markdown("---")
        st.subheader("🛠️ 공구 세팅 및 가공 정보")
        
        tool_info = st.text_area(
            "공구 리스트 (번호 / 공구명 / 조건 등)", 
            placeholder="예:\nT01: 10파이 황삭 엔드밀 (S4000 / F1200)\nT02: 6파이 정삭 엔드밀 (S6000 / F800)\nT03: M6 탭",
            height=150
        )
        
        knowhow = st.text_area(
            "💡 마스터캠 툴패스 설정 및 현장 가공 노하우 (주의사항)", 
            placeholder="예:\n- 코너 부위에 잔삭이 남으므로 T02 정삭 진입 시 에어컷 확인 필수.\n- SUS304 재질 특성상 절삭유 공급 방향 유의하고 황삭 시 부하 체크할 것.",
            height=170
        )
        
        submitted = st.form_submit_button("💾 셋업 시트 저장하기")
        
        if submitted:
            if not part_name:
                st.error("품명/품번은 필수 입력 항목입니다.")
            else:
                new_sheet = {
                    "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                    "part_name": part_name,
                    "machine": machine,
                    "category": category,
                    "material": material,
                    "g_code_coord": g_code_coord,
                    "tool_info": tool_info,
                    "knowhow": knowhow,
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M")
                }
                db["setup_sheets"].append(new_sheet)
                save_data(db)
                st.success(f"🎉 [{part_name}] 셋업 시트가 성공적으로 저장되었습니다!")

elif menu == "🔍 셋업 시트 검색/조회":
    st.header("🔍 저장된 셋업 시트 검색")
    
    search_query = st.text_input("🔍 품명, 재질, 또는 노하우 키워드로 검색하세요", placeholder="검색어 입력...")
    
    col1, col2 = st.columns(2)
    with col1:
        filter_machine = st.multiselect("장비 필터", ["두산 DNM500", "두산 DNM650", "두산 GT2100"])
    with col2:
        filter_cat = st.multiselect("분류 필터", ["방산 부품", "반도체 장비부품", "산업 부품", "기타"])
        
    filtered_sheets = db["setup_sheets"]
    
    if search_query:
        filtered_sheets = [
            s for s in filtered_sheets 
            if search_query.lower() in s["part_name"].lower() or 
               search_query.lower() in s["material"].lower() or 
               search_query.lower() in s["knowhow"].lower()
        ]
        
    if filter_machine:
        filtered_sheets = [s for s in filtered_sheets if s["machine"] in filter_machine]
        
    if filter_cat:
        filtered_sheets = [s for s in filtered_sheets if s["category"] in filter_cat]
        
    st.markdown("---")
    st.subheader(f"📊 검색 결과 (총 {len(filtered_sheets)}건)")
    
    if not filtered_sheets:
        st.info("조건에 맞는 셋업 시트가 없습니다. 새로운 노하우를 등록해 보세요!")
    else:
        for sheet in reversed(filtered_sheets):
            with st.expander(f"📦 [{sheet['machine']}] {sheet['part_name']} ({sheet['date']})"):
                c1, c2, c3 = st.columns(3)
                c1.markdown(f"**제품 분류:** {sheet['category']}")
                c2.markdown(f"**소재 재질:** {sheet['material']}")
                c3.markdown(f"**작업 좌표계:** {sheet['g_code_coord']}")
                
                st.markdown("**🛠️ 공구 세팅 정보**")
                st.code(sheet['tool_info'] if sheet['tool_info'] else "등록된 공구 정보 없음")
                
                st.markdown("**💡 가공 노하우 및 마스터캠 주의점**")
                st.info(sheet['knowhow'] if sheet['knowhow'] else "등록된 노하우 정보 없음")
                
                # --- 추가 및 수정된 부분: 수정하기 기능과 삭제 버튼 배치 ---
                col_btn1, col_btn2 = st.columns([1, 1])
                
                with col_btn1:
                    with st.expander("✏️ 시트 수정하기"):
                        with st.form(f"edit_sheet_form_{sheet['id']}"):
                            edit_part_name = st.text_input("품명 / 품번 수정", value=sheet.get('part_name', ''))
                            
                            machines = ["두산 DNM500", "두산 DNM650", "두산 GT2100"]
                            machine_idx = machines.index(sheet['machine']) if sheet.get('machine') in machines else 0
                            edit_machine = st.selectbox("가공 장비 수정", machines, index=machine_idx)
                            
                            categories = ["방산 부품", "반도체 장비부품", "산업 부품", "기타"]
                            category_idx = categories.index(sheet['category']) if sheet.get('category') in categories else 0
                            edit_category = st.selectbox("제품 분류 수정", categories, index=category_idx)
                            
                            edit_material = st.text_input("소재 재질 수정", value=sheet.get('material', ''))
                            edit_g_code_coord = st.text_input("작업 좌표계 수정", value=sheet.get('g_code_coord', ''))
                            edit_tool_info = st.text_area("공구 세팅 정보 수정", value=sheet.get('tool_info', ''), height=150)
                            edit_knowhow = st.text_area("가공 노하우 수정", value=sheet.get('knowhow', ''), height=150)
                            
                            if st.form_submit_button("💾 수정 저장"):
                                for idx, s in enumerate(db["setup_sheets"]):
                                    if s["id"] == sheet["id"]:
                                        db["setup_sheets"][idx]["part_name"] = edit_part_name
                                        db["setup_sheets"][idx]["machine"] = edit_machine
                                        db["setup_sheets"][idx]["category"] = edit_category
                                        db["setup_sheets"][idx]["material"] = edit_material
                                        db["setup_sheets"][idx]["g_code_coord"] = edit_g_code_coord
                                        db["setup_sheets"][idx]["tool_info"] = edit_tool_info
                                        db["setup_sheets"][idx]["knowhow"] = edit_knowhow
                                        db["setup_sheets"][idx]["date"] = datetime.now().strftime("%Y-%m-%d %H:%M") + " (수정됨)"
                                        break
                                save_data(db)
                                st.rerun()
                                
                with col_btn2:
                    if st.button("❌ 시트 삭제", key=f"del_{sheet['id']}"):
                        db["setup_sheets"] = [s for s in db["setup_sheets"] if s["id"] != sheet["id"]]
                        save_data(db)
                        st.rerun()

elif menu == "💾 자주 쓰는 G코드 매니저":
    st.header("💾 자주 쓰는 G코드 & 수기 매크로")
    
    st.subheader("📖 현장 G/M코드 기본 사전")
    st.write("작업 중 뜻이 헷갈리는 코드나 기능을 검색해 보세요. (예: G76, M08, 절삭유, 나사, 탭 등)")
    
    dict_query = st.text_input("🔍 모르는 코드 번호나 한글 기능 검색", placeholder="예: M30, M29, 리지드, 탭...").strip()
    
    if dict_query:
        found = False
        q_upper = dict_query.upper()
        
        if len(q_upper) == 2 and q_upper[0] in ['G', 'M']:
            q_upper = f"{q_upper[0]}0{q_upper[1]}"
            
        for code, desc in GM_DICTIONARY.items():
            if q_upper in code or dict_query in desc:
                st.info(f"**{code}** : {desc}")
                found = True
                
        if not found:
            st.warning("사전에 등록되지 않은 코드이거나 검색어가 없습니다.")
            
    st.markdown("---")
    
    st.subheader("⚙️ 내 전용 매크로 & 세팅 블록 관리")
    st.write("장비 조작 시 자주 수기로 입력하거나 마스터캠 출력 후 자주 수정하는 패턴을 관리합니다.")
    
    with st.expander("➕ 새로운 매크로 패턴 등록하기"):
        with st.form("gcode_form"):
            g_title = st.text_input("코드 명칭", placeholder="예: 나사 가공 G76 사이클 기본 형태")
            g_machine = st.selectbox("해당 장비", ["공통", "두산 DNM500", "두산 DNM650", "두산 GT2100"])
            g_code = st.text_area("G코드 내용", placeholder="G코드 블록을 입력하세요", height=150)
            g_desc = st.text_input("코드 설명", placeholder="간단한 활용 팁이나 설명을 적어주세요")
            
            g_submitted = st.form_submit_button("저장하기")
            if g_submitted:
                if not g_title or not g_code:
                    st.error("명칭과 코드 내용은 필수입니다.")
                else:
                    new_gcode = {
                        "title": g_title,
                        "machine": g_machine,
                        "code": g_code,
                        "description": g_desc
                    }
                    db["gcodes"].append(new_gcode)
                    save_data(db)
                    st.success("새로운 매크로가 등록되었습니다!")
                    
    st.markdown("<br>", unsafe_allow_html=True)
    
    for idx, g in enumerate(db["gcodes"]):
        col_g1, col_g2 = st.columns([3, 1])
        with col_g1:
            st.markdown(f"#### 📌 {g['title']}")
            st.caption(f"장비: {g['machine']} | 설명: {g['description']}")
        with col_g2:
            if st.button("🗑️ 삭제", key=f"del_g_{idx}"):
                db["gcodes"].pop(idx)
                save_data(db)
                st.rerun()
                
        st.code(g["code"], language="glsl")
        st.markdown("<br>", unsafe_allow_html=True)

elif menu == "📝 현장 수기 노트 / 자유 메모":
    st.header("📝 현장 수기 노트 및 자유 메모")
    st.write("기존에 수첩이나 볼펜으로 적어두었던 현장 세팅 방법, 마스터캠 주의사항 등을 자유롭게 기록하세요.")
    
    with st.expander("➕ 새 노트 작성하기", expanded=True):
        with st.form("memo_form", clear_on_submit=True):
            memo_title = st.text_input("노트 제목", placeholder="예: 마스터캠 황삭 툴패스 설정 시 주의점, SUS 재질 탭 가공 노하우 등")
            memo_content = st.text_area("노트 내용", placeholder="수첩에 기록해둔 내용이나 잊지 말아야 할 세부 사항을 자유롭게 적어주세요.", height=250)
            
            memo_submitted = st.form_submit_button("💾 메모 저장하기")
            
            if memo_submitted:
                if not memo_title or not memo_content:
                    st.error("제목과 내용은 필수 입력 항목입니다.")
                else:
                    new_memo = {
                        "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                        "title": memo_title,
                        "content": memo_content,
                        "date": datetime.now().strftime("%Y-%m-%d %H:%M")
                    }
                    db["memos"].append(new_memo)
                    save_data(db)
                    st.success("새로운 노트가 저장되었습니다!")
                    
    st.markdown("---")
    st.subheader("📚 저장된 수기 노트 목록")
    
    search_memo = st.text_input("🔍 노트 제목이나 내용으로 검색", placeholder="검색어 입력...")
    
    filtered_memos = db["memos"]
    if search_memo:
        filtered_memos = [
            m for m in filtered_memos 
            if search_memo.lower() in m["title"].lower() or search_memo.lower() in m["content"].lower()
        ]
        
    if not filtered_memos:
        st.info("등록된 노트가 없습니다. 수첩에 있는 내용들을 하나씩 옮겨보세요!")
    else:
        for memo in reversed(filtered_memos):
            with st.expander(f"📔 {memo['title']} ({memo['date']})"):
                st.write(memo['content'])
                st.markdown("<br>", unsafe_allow_html=True)
                
                col_btn1, col_btn2 = st.columns([1, 1])
                
                with col_btn1:
                    with st.expander("✏️ 문서 수정하기"):
                        with st.form(f"edit_memo_form_{memo['id']}"):
                            edit_title = st.text_input("제목 수정", value=memo['title'])
                            edit_content = st.text_area("내용 수정", value=memo['content'], height=200)
                            
                            if st.form_submit_button("💾 수정 저장"):
                                for idx, m in enumerate(db["memos"]):
                                    if m["id"] == memo["id"]:
                                        db["memos"][idx]["title"] = edit_title
                                        db["memos"][idx]["content"] = edit_content
                                        db["memos"][idx]["date"] = datetime.now().strftime("%Y-%m-%d %H:%M") + " (수정됨)"
                                        break
                                save_data(db)
                                st.rerun()
                                
                with col_btn2:
                    if st.button("❌ 문서 삭제", key=f"del_memo_{memo['id']}"):
                        db["memos"] = [m for m in db["memos"] if m["id"] != memo["id"]]
                        save_data(db)
                        st.rerun()

elif menu == "📅 일일 작업 일지":
    st.header("📅 일일 작업 일지")
    st.write("그날그날의 생산량, 특이사항, 인수인계 내용을 기록하고 관리합니다.")
    
    with st.expander("➕ 새 작업 일지 작성하기", expanded=True):
        with st.form("work_log_form", clear_on_submit=True):
            col1, col2, col3 = st.columns(3)
            with col1:
                log_date = st.date_input("작업 일자")
            with col2:
                worker_name = st.text_input("작업자명", placeholder="예: 홍길동")
            with col3:
                shift = st.selectbox("근무조", ["주간", "야간", "특근/기타"])
                
            machine_used = st.selectbox("가공 장비", ["전체/공통", "두산 DNM500", "두산 DNM650", "두산 GT2100"])
            
            tasks_done = st.text_area("생산 내역 (품명 및 수량 등)", placeholder="예:\n- 반도체 챔버 A형: 50개 완료\n- 하우징 커버 B형: 20개 황삭 진행", height=100)
            issues_notes = st.text_area("특이사항 및 인수인계", placeholder="예:\n- DNM500 절삭유 보충 필요함\n- 야간조 작업 시 T02 인서트 팁 교체 후 작업할 정", height=100)
            
            log_submitted = st.form_submit_button("💾 일지 저장하기")
            
            if log_submitted:
                if not worker_name or not tasks_done:
                    st.error("작업자명과 생산 내역은 필수 입력 항목입니다.")
                else:
                    new_log = {
                        "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                        "date": log_date.strftime("%Y-%m-%d"),
                        "worker": worker_name,
                        "shift": shift,
                        "machine": machine_used,
                        "tasks": tasks_done,
                        "issues": issues_notes,
                        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
                    }
                    db["work_logs"].append(new_log)
                    save_data(db)
                    st.success("작업 일지가 성공적으로 저장되었습니다!")
                    
    st.markdown("---")
    st.subheader("📋 지난 작업 일지 조회")
    
    search_log = st.text_input("🔍 작업자명, 장비명, 또는 내용으로 검색", placeholder="검색어 입력...")
    
    filtered_logs = db["work_logs"]
    if search_log:
        filtered_logs = [
            l for l in filtered_logs 
            if search_log.lower() in l["worker"].lower() or 
               search_log.lower() in l["machine"].lower() or 
               search_log.lower() in l["tasks"].lower() or 
               search_log.lower() in l["issues"].lower()
        ]
        
    if not filtered_logs:
        st.info("등록된 작업 일지가 없습니다. 오늘의 작업을 기록해 보세요!")
    else:
        for log in reversed(filtered_logs):
            with st.expander(f"📅 [{log['date']}] {log['worker']} ({log['shift']}) - {log['machine']}"):
                st.markdown("**🛠️ 생산 내역:**")
                st.write(log['tasks'])
                st.markdown("**⚠️ 특이사항 및 인수인계:**")
                st.info(log['issues'] if log['issues'] else "특이사항 없음")
                st.caption(f"작성 일시: {log['created_at']}")
                
                col_btn1, col_btn2 = st.columns([1, 1])
                
                with col_btn1:
                    with st.expander("✏️ 일지 수정하기"):
                        with st.form(f"edit_log_form_{log['id']}"):
                            edit_worker = st.text_input("작업자명 수정", value=log['worker'])
                            edit_tasks = st.text_area("생산 내역 수정", value=log['tasks'], height=100)
                            edit_issues = st.text_area("특이사항 수정", value=log['issues'], height=100)
                            
                            if st.form_submit_button("💾 수정 저장"):
                                for idx, l in enumerate(db["work_logs"]):
                                    if l["id"] == log["id"]:
                                        db["work_logs"][idx]["worker"] = edit_worker
                                        db["work_logs"][idx]["tasks"] = edit_tasks
                                        db["work_logs"][idx]["issues"] = edit_issues
                                        break
                                save_data(db)
                                st.rerun()
                                
                with col_btn2:
                    if st.button("❌ 일지 삭제", key=f"del_log_{log['id']}"):
                        db["work_logs"] = [l for l in db["work_logs"] if l["id"] != log["id"]]
                        save_data(db)
                        st.rerun()
