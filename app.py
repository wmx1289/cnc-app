import os
import base64
os.environ["STREAMLIT_GATHER_USAGE_STATS"] = "false"
os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"

import streamlit as st
import json
from datetime import datetime
import re
import pandas as pd

# 모바일 화면에서도 꽉 차게 보이도록 layout="wide", 사이드바 항상 열린 상태로 시작
st.set_page_config(
    page_title="현장 통합 관리 시스템", 
    page_icon="⚙️", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# 사이드바를 접는 '<' 버튼을 완전히 숨겨서 메뉴바를 고정시키는 커스텀 CSS
st.markdown(
    """
    <style>
        [data-testid="collapsedControl"] {
            display: none;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# ==========================================
# 1. 스마트 가공 매니저용 데이터베이스 설정
# ==========================================
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

def save_data(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def load_data():
    initial_data = {"setup_sheets": [], "gcodes": [], "memos": [], "work_logs": []}
    if not os.path.exists(DB_FILE):
        save_data(initial_data)
        return initial_data
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                raise ValueError("File is empty")
            data = json.loads(content)
            needs_save = False
            for key in initial_data.keys():
                if key not in data:
                    data[key] = []
                    needs_save = True
            if needs_save: save_data(data)
            return data
    except Exception:
        save_data(initial_data)
        return initial_data

db = load_data()


# ==========================================
# 2. 공구/자재 장부용 공통 데이터 함수
# ==========================================

# 원영공구 기본 제공 데이터 (파일이 없을 때 최초 1회 생성용)
DEFAULT_WONYOUNG_DATA = [
    {"일자": "2026-06-08", "분류": "공구", "품목명": "롱드릴 1.5", "단가": 6700, "수량": 2, "총액": 13400},
    {"일자": "2026-06-08", "분류": "공구", "품목명": "나찌코발트 5.0", "단가": 2950, "수량": 5, "총액": 14750},
    {"일자": "2026-06-08", "분류": "공구", "품목명": "나찌코발트 6.0", "단가": 3850, "수량": 5, "총액": 19250},
    {"일자": "2026-06-08", "분류": "공구", "품목명": "엔드밀코렛트 C32-18", "단가": 11000, "수량": 1, "총액": 11000},
    {"일자": "2026-06-11", "분류": "공구", "품목명": "SP탭(YAMAWA)11x1.0", "단가": 44400, "수량": 1, "총액": 44400},
    {"일자": "2026-06-23", "분류": "공구", "품목명": "ALU-CUT 3날라핑(E5D73100B)", "단가": 0, "수량": 2, "총액": 0},
    {"일자": "2026-07-02", "분류": "공구", "품목명": "인디케이터", "단가": 87000, "수량": 1, "총액": 87000},
    {"일자": "2026-07-07", "분류": "공구", "품목명": "SUS-CUT 3파이", "단가": 18500, "수량": 1, "총액": 18500},
    {"일자": "2026-07-07", "분류": "공구", "품목명": "SUS-CUT 4파이", "단가": 18500, "수량": 1, "총액": 18500},
    {"일자": "2026-07-07", "분류": "공구", "품목명": "SUS-CUT 6파이", "단가": 18500, "수량": 2, "총액": 37000},
    {"일자": "2026-07-07", "분류": "공구", "품목명": "SUS-CUT 5파이", "단가": 18100, "수량": 1, "총액": 18100},
    {"일자": "2026-07-07", "분류": "공구", "품목명": "SUS-CUT 8파이", "단가": 26100, "수량": 2, "총액": 52200},
    {"일자": "2026-07-07", "분류": "공구", "품목명": "SUS-CUT 10파이", "단가": 35800, "수량": 2, "총액": 71600},
    {"일자": "2026-07-07", "분류": "공구", "품목명": "파인라핑(TANK-POWEP) 10파이", "단가": 22200, "수량": 2, "총액": 44400},
    {"일자": "2026-07-07", "분류": "공구", "품목명": "나찌코발트 드릴 2.6파이", "단가": 1800, "수량": 2, "총액": 3600},
    {"일자": "2026-07-07", "분류": "공구", "품목명": "나찌코발트 드릴 3.4파이", "단가": 2100, "수량": 2, "총액": 4200},
    {"일자": "2026-07-07", "분류": "공구", "품목명": "나찌코발트 드릴 4.3파이", "단가": 2900, "수량": 2, "총액": 5800},
    {"일자": "2026-07-07", "분류": "공구", "품목명": "나찌코발트 드릴 5.2파이", "단가": 3800, "수량": 2, "총액": 7600},
    {"일자": "2026-07-07", "분류": "공구", "품목명": "나찌코발트 드릴 5.5파이", "단가": 3800, "수량": 2, "총액": 7600},
    {"일자": "2026-07-07", "분류": "공구", "품목명": "나찌코발드 드릴 11.5파이", "단가": 15600, "수량": 1, "총액": 15600},
    {"일자": "2026-07-13", "분류": "공구", "품목명": "SUS-CUT 10파이", "단가": 35800, "수량": 2, "총액": 71600},
    {"일자": "2026-07-13", "분류": "공구", "품목명": "초경 T더블앵글캇타", "단가": 31100, "수량": 2, "총액": 62200},
    {"일자": "2026-07-14", "분류": "공구", "품목명": "나찌코발트드릴 4.0파이", "단가": 2400, "수량": 2, "총액": 4800},
    {"일자": "2026-07-14", "분류": "공구", "품목명": "SUS-탭 M3", "단가": 6700, "수량": 2, "총액": 13400},
    {"일자": "2026-07-14", "분류": "공구", "품목명": "SUS-탭 M4", "단가": 6200, "수량": 2, "총액": 12400},
    {"일자": "2026-07-14", "분류": "공구", "품목명": "SUS-탭M5", "단가": 6500, "수량": 2, "총액": 13000},
    {"일자": "2026-07-14", "분류": "공구", "품목명": "SUS-탭 M6", "단가": 7000, "수량": 2, "총액": 14000},
    {"일자": "2026-07-14", "분류": "공구", "품목명": "로콜  탭핑유", "단가": 20500, "수량": 1, "총액": 20500},
    {"일자": "2026-07-14", "분류": "공구", "품목명": "SUS- 탭 M4 다노이", "단가": 8400, "수량": 2, "총액": 16800},
    {"일자": "2026-07-14", "분류": "공구", "품목명": "SUS-탭 M5 다노이", "단가": 8700, "수량": 2, "총액": 17400},
    {"일자": "2026-07-14", "분류": "공구", "품목명": "코발트드릴 3.4파이", "단가": 2100, "수량": 2, "총액": 4200},
    {"일자": "2026-07-21", "분류": "공구", "품목명": "오일 토나#68", "단가": 154000, "수량": 1, "총액": 154000},
    {"일자": "2026-07-21", "분류": "공구", "품목명": "알루미늄 SP 탭 M2.6*0.45", "단가": 10400, "수량": 2, "총액": 20800},
    {"일자": "2026-07-22", "분류": "공구", "품목명": "T 캇타10 3T", "단가": 11600, "수량": 2, "총액": 23200},
    {"일자": "2026-07-09", "분류": "공구", "품목명": "t컷다10 3t", "단가": 23200, "수량": 2, "총액": 46400},
    {"일자": "2026-07-09", "분류": "공구", "품목명": "부품함(101 노란색)", "단가": 5000, "수량": 15, "총액": 75000},
    {"일자": "2026-07-09", "분류": "공구", "품목명": "보루", "단가": 12000, "수량": 2, "총액": 24000},
    {"일자": "2026-07-09", "분류": "공구", "품목명": "말링팁 TT7078", "단가": 10220, "수량": 10, "총액": 102200},
    {"일자": "2026-07-09", "분류": "공구", "품목명": "3날초경SUS엔드밀", "단가": 26100, "수량": 1, "총액": 26100},
    {"일자": "2026-07-09", "분류": "공구", "품목명": "3날초경SUS엔드밀10파이", "단가": 35800, "수량": 1, "총액": 35800},
    {"일자": "2026-07-09", "분류": "공구", "품목명": "초경NC드릴", "단가": 15100, "수량": 2, "총액": 30200},
    {"일자": "2026-07-09", "분류": "공구", "품목명": "초경NC드릴", "단가": 15600, "수량": 2, "총액": 31200},
    {"일자": "2026-07-09", "분류": "공구", "품목명": "포인트탭3*0.5", "단가": 8300, "수량": 2, "총액": 16600},
    {"일자": "2026-07-09", "분류": "공구", "품목명": "포인트탭4*0.7", "단가": 8000, "수량": 2, "총액": 16000},
    {"일자": "2026-07-09", "분류": "공구", "품목명": "포인트탭5*0.8", "단가": 8100, "수량": 1, "총액": 8100},
    {"일자": "2026-07-09", "분류": "공구", "품목명": "포인트탭6*1.0", "단가": 8700, "수량": 1, "총액": 8700},
    {"일자": "2026-07-09", "분류": "공구", "품목명": "코발트드릴 5파이", "단가": 6600, "수량": 1, "총액": 6600},
    {"일자": "2026-08-19", "분류": "공구", "품목명": "ALU-CUT 2.5파이", "단가": 13500, "수량": 2, "총액": 27000},
    {"일자": "2026-08-20", "분류": "공구", "품목명": "SP 탭 M24x1.0", "단가": 62500, "수량": 1, "총액": 62500},
    {"일자": "2026-08-20", "분류": "공구", "품목명": "ALU-CUT 라핑 6파이  ", "단가": 17500, "수량": 2, "총액": 35000},
    {"일자": "2026-08-20", "분류": "공구", "품목명": "3날초경알미늄엔드밀6파이", "단가": 17500, "수량": 2, "총액": 35000},
    {"일자": "2026-08-25", "분류": "공구", "품목명": "메탈캇타75 1.5T ", "단가": 11500, "수량": 2, "총액": 23000},
    {"일자": "2026-08-25", "분류": "공구", "품목명": "3날 SUS엔드밀 5파이", "단가": 18100, "수량": 1, "총액": 18100},
    {"일자": "2026-08-25", "분류": "공구", "품목명": "3날 SUS엔드밀 8파이", "단가": 26100, "수량": 2, "총액": 52200},
    {"일자": "2026-08-25", "분류": "공구", "품목명": "메탈캇타75 1.0T", "단가": 10200, "수량": 2, "총액": 20400},
    {"일자": "2026-09-01", "분류": "공구", "품목명": "X POWER 8파이", "단가": 26100, "수량": 2, "총액": 52200},
    {"일자": "2026-09-01", "분류": "공구", "품목명": "4날초경SUS 8파이", "단가": 26100, "수량": 1, "총액": 26100},
    {"일자": "2026-09-01", "분류": "공구", "품목명": "4날초경 SUS 8*0.3R", "단가": 32300, "수량": 3, "총액": 96900},
    {"일자": "2026-09-01", "분류": "공구", "품목명": "폐파(사슴포)#220", "단가": 680, "수량": 50, "총액": 34000},
    {"일자": "2026-09-01", "분류": "공구", "품목명": "메탈캇타 75 1T", "단가": 10200, "수량": 2, "총액": 20400}
]

def 장부_저장하기(파일이름, 데이터):
    with open(파일이름, 'w', encoding='utf-8') as f:
        json.dump(데이터, f, ensure_ascii=False, indent=4)

def 장부_불러오기(파일이름):
    if os.path.exists(파일이름):
        try:
            with open(파일이름, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
            
    # 클라우드 등에서 '원영공구_장부.json' 파일이 아예 없을 때 기본 데이터 주입
    if 파일이름 == "원영공구_장부.json":
        장부_저장하기(파일이름, DEFAULT_WONYOUNG_DATA)
        return DEFAULT_WONYOUNG_DATA
        
    return []

def 장부_화면_출력(제목, 파일이름, 분류목록):
    st.title(제목)
    if 'current_menu' not in st.session_state or st.session_state.current_menu != 제목:
        st.session_state.current_menu = 제목
        st.session_state.ledger = 장부_불러오기(파일이름)

    tab1, tab2, tab3 = st.tabs(["월별 구매내역", "품목 통계", "새 구매 등록"])

    with tab1:
        st.subheader("구매내역 조회")
        검색어 = st.text_input("🔍 품목명 검색:", key=f"search_{제목}")
        if st.session_state.ledger:
            df = pd.DataFrame(st.session_state.ledger)
            df.insert(0, 'No.', range(1, len(df) + 1))
            
            if 검색어: 
                df = df[df['품목명'].str.contains(검색어)]
                
            st.dataframe(df.style.format({"단가": "₩{:,}", "총액": "₩{:,}"}), hide_index=True, use_container_width=True)
            
            st.markdown("---")
            st.subheader("⚙️ 기록 수정 및 삭제")
            st.info("💡 수정이나 삭제를 원하시는 기록의 **No.** 를 입력해주세요.")
            
            선택번호 = st.number_input("수정/삭제할 행 번호 (위 표의 No. 입력)", min_value=1, max_value=len(st.session_state.ledger), step=1, key=f"target_{제목}")
            선택인덱스 = 선택번호 - 1
            
            if 0 <= 선택인덱스 < len(st.session_state.ledger):
                선택데이터 = st.session_state.ledger[선택인덱스]
                col_edit, col_del = st.columns([2, 1])
                
                with col_edit:
                    with st.expander(f"✏️ No.{선택번호} 기록 수정하기"):
                        with st.form(f"edit_form_{제목}"):
                            try:
                                edit_date = datetime.strptime(선택데이터.get("일자", datetime.now().strftime("%Y-%m-%d")), "%Y-%m-%d").date()
                            except:
                                edit_date = datetime.now().date()
                                
                            수정_일자 = st.date_input("구매 일자", value=edit_date, key=f"e_date_{제목}")
                            cat_idx = 분류목록.index(선택데이터.get("분류")) if 선택데이터.get("분류") in 분류목록 else 0
                            수정_분류 = st.radio("분류 선택", 분류목록, index=cat_idx, horizontal=True, key=f"e_cat_{제목}")
                            수정_품목명 = st.text_input("품목명", value=선택데이터.get("품목명", ""), key=f"e_name_{제목}")
                            
                            c1, c2 = st.columns(2)
                            with c1: 
                                수정_단가 = st.text_input("단가", value=str(선택데이터.get("단가", "")), key=f"e_price_{제목}")
                            with c2: 
                                수정_수량 = st.text_input("수량", value=str(선택데이터.get("수량", "")), key=f"e_qty_{제목}")
                                
                            if st.form_submit_button("💾 수정 완료", type="primary"):
                                if not 수정_품목명 or not 수정_단가 or not 수정_수량:
                                    st.warning("모든 항목을 입력해 주세요.")
                                else:
                                    try:
                                        단가_숫자 = int(re.sub(r'[^0-9]', '', 수정_단가))
                                        수량_숫자 = int(re.sub(r'[^0-9]', '', 수정_수량))
                                        
                                        st.session_state.ledger[선택인덱스] = {
                                            "일자": 수정_일자.strftime("%Y-%m-%d"),
                                            "분류": 수정_분류,
                                            "품목명": 수정_품목명,
                                            "단가": 단가_숫자,
                                            "수량": 수량_숫자,
                                            "총액": 단가_숫자 * 수량_숫자
                                        }
                                        장부_저장하기(파일이름, st.session_state.ledger)
                                        st.success("기록이 성공적으로 수정되었습니다!")
                                        st.rerun()
                                    except ValueError:
                                        st.error("단가와 수량은 숫자만 입력해 주세요.")
                                        
                with col_del:
                    st.markdown("<br>", unsafe_allow_html=True)
                    if st.button("🗑️ 선택 기록 영구 삭제", type="primary", key=f"btn_del_{제목}"):
                        del st.session_state.ledger[선택인덱스]
                        장부_저장하기(파일이름, st.session_state.ledger)
                        st.success("기록이 삭제되었습니다.")
                        st.rerun()
        else:
            st.info("기록된 데이터가 없습니다.")

    with tab2:
        st.subheader("분류별 구매 통계")
        if st.session_state.ledger:
            df = pd.DataFrame(st.session_state.ledger)
            cat_tabs = st.tabs(분류목록)
            for i, cat in enumerate(분류목록):
                with cat_tabs[i]:
                    cat_df = df[df['분류'] == cat]
                    if not cat_df.empty:
                        stats = cat_df.groupby('품목명').agg({'수량': 'sum', '총액': 'sum'}).reset_index()
                        st.dataframe(stats.style.format({"총액": "₩{:,}"}), use_container_width=True)
                    else:
                        st.write(f"등록된 '{cat}' 내역이 없습니다.")
        else:
            st.info("데이터가 없습니다.")

    with tab3:
        st.subheader("📝 새 구매 내역 등록")
        with st.form(f"form_{제목}"):
            입력_일자 = st.date_input("구매 일자", value=datetime.now().date())
            입력_분류 = st.radio("분류 선택", 분류목록, horizontal=True)
            입력_품목명 = st.text_input("품목명")
            col1, col2 = st.columns(2)
            with col1: 입력_단가 = st.text_input("단가", placeholder="예: 25000")
            with col2: 입력_수량 = st.text_input("수량", value="1")
                
            if st.form_submit_button("장부에 등록하기", type="primary"):
                if not 입력_품목명 or not 입력_단가 or not 입력_수량:
                    st.warning("모두 입력해 주세요.")
                else:
                    try:
                        단가_숫자 = int(re.sub(r'[^0-9]', '', 입력_단가))
                        수량_숫자 = int(re.sub(r'[^0-9]', '', 입력_수량))
                        새기록 = {
                            "일자": 입력_일자.strftime("%Y-%m-%d"), 
                            "분류": 입력_분류, 
                            "품목명": 입력_품목명, 
                            "단가": 단가_숫자, 
                            "수량": 수량_숫자, 
                            "총액": 단가_숫자 * 수량_숫자
                        }
                        st.session_state.ledger.append(새기록)
                        장부_저장하기(파일이름, st.session_state.ledger)
                        st.success("저장 완료!")
                        st.rerun()
                    except ValueError: 
                        st.error("단가와 수량에는 숫자만 적어주세요.")


# ==========================================
# 3. 사이드바 메인 라우팅 (메뉴 구성)
# ==========================================
st.sidebar.title("🗂️ 통합 시스템")
메인메뉴 = st.sidebar.radio(
    "대분류 선택:", 
    ["스마트 가공 매니저", "원영공구 (공구/소재)", "익산비철 (비철/자재)"]
)

if 메인메뉴 == "스마트 가공 매니저":
    st.sidebar.markdown("---")
    st.sidebar.markdown("**메뉴를 선택하세요**")
    가공메뉴 = st.sidebar.radio(
        "스마트 가공 매니저 하위메뉴",
        [
            "📋 스마트 셋업 시트 작성", 
            "🔍 셋업 시트 검색/조회", 
            "💾 자주 쓰는 G코드 매니저", 
            "📝 현장 수기 노트 / 자유 메모", 
            "📅 일일 작업 일지"
        ],
        label_visibility="collapsed"
    )

    if 가공메뉴 == "📋 스마트 셋업 시트 작성":
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
            tool_info = st.text_area("공구 리스트 (번호 / 공구명 / 조건 등)", height=150)
            knowhow = st.text_area("💡 마스터캠 툴패스 설정 및 현장 가공 노하우 (주의사항)", height=170)
            setup_images = st.file_uploader("📷 세팅 사진 첨부 (여러 장 가능)", type=["png", "jpg", "jpeg"], accept_multiple_files=True)
            
            submitted = st.form_submit_button("💾 셋업 시트 저장하기")
            if submitted:
                if not part_name:
                    st.error("품명/품번은 필수 입력 항목입니다.")
                else:
                    images_b64_list = []
                    if setup_images:
                        for img in setup_images:
                            images_b64_list.append(base64.b64encode(img.read()).decode('utf-8'))

                    new_sheet = {
                        "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                        "part_name": part_name,
                        "machine": machine,
                        "category": category,
                        "material": material,
                        "g_code_coord": g_code_coord,
                        "tool_info": tool_info,
                        "knowhow": knowhow,
                        "images_b64": images_b64_list,
                        "image_b64": "",
                        "date": datetime.now().strftime("%Y-%m-%d %H:%M")
                    }
                    db["setup_sheets"].append(new_sheet)
                    save_data(db)
                    st.success(f"🎉 [{part_name}] 셋업 시트가 성공적으로 저장되었습니다!")

    elif 가공메뉴 == "🔍 셋업 시트 검색/조회":
        st.header("🔍 저장된 셋업 시트 검색")
        search_query = st.text_input("🔍 품명, 재질, 또는 노하우 키워드로 검색하세요", placeholder="검색어 입력...")
        
        col1, col2 = st.columns(2)
        with col1: filter_machine = st.multiselect("장비 필터", ["두산 DNM500", "두산 DNM650", "두산 GT2100"])
        with col2: filter_cat = st.multiselect("분류 필터", ["방산 부품", "반도체 장비부품", "산업 부품", "기타"])
            
        filtered_sheets = db["setup_sheets"]
        if search_query:
            filtered_sheets = [s for s in filtered_sheets if search_query.lower() in s["part_name"].lower() or search_query.lower() in s["material"].lower() or search_query.lower() in s["knowhow"].lower()]
        if filter_machine: filtered_sheets = [s for s in filtered_sheets if s["machine"] in filter_machine]
        if filter_cat: filtered_sheets = [s for s in filtered_sheets if s["category"] in filter_cat]
            
        st.markdown("---")
        st.subheader(f"📊 검색 결과 (총 {len(filtered_sheets)}건)")
        
        if not filtered_sheets:
            st.info("조건에 맞는 셋업 시트가 없습니다.")
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
                    
                    imgs_to_show = sheet.get("images_b64", [])
                    if not imgs_to_show and sheet.get("image_b64"):
                        imgs_to_show = [sheet["image_b64"]]
                    
                    if imgs_to_show:
                        st.markdown("---")
                        num_cols = min(len(imgs_to_show), 3) 
                        cols = st.columns(num_cols)
                        for i, img_b64 in enumerate(imgs_to_show):
                            cols[i % num_cols].image(base64.b64decode(img_b64), use_column_width=True)
                    
                    st.markdown("<br>", unsafe_allow_html=True)
                    col_btn1, col_btn2 = st.columns([1, 1])
                    with col_btn1:
                        with st.expander("✏️ 시트 수정하기"):
                            with st.form(f"edit_sheet_form_{sheet['id']}"):
                                edit_part_name = st.text_input("품명 / 품번 수정", value=sheet.get('part_name', ''))
                                machines = ["두산 DNM500", "두산 DNM650", "두산 GT2100"]
                                edit_machine = st.selectbox("가공 장비 수정", machines, index=machines.index(sheet['machine']) if sheet.get('machine') in machines else 0)
                                categories = ["방산 부품", "반도체 장비부품", "산업 부품", "기타"]
                                edit_category = st.selectbox("제품 분류 수정", categories, index=categories.index(sheet['category']) if sheet.get('category') in categories else 0)
                                edit_material = st.text_input("소재 재질 수정", value=sheet.get('material', ''))
                                edit_g_code_coord = st.text_input("작업 좌표계 수정", value=sheet.get('g_code_coord', ''))
                                edit_tool_info = st.text_area("공구 세팅 정보 수정", value=sheet.get('tool_info', ''), height=150)
                                edit_knowhow = st.text_area("가공 노하우 수정", value=sheet.get('knowhow', ''), height=150)
                                edit_images = st.file_uploader("📷 새 사진 첨부", type=["png", "jpg", "jpeg"], accept_multiple_files=True, key=f"edit_img_{sheet['id']}")
                                delete_image = st.checkbox("🗑️ 기존 사진 모두 삭제", key=f"del_img_{sheet['id']}")
                                
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
                                            if edit_images:
                                                db["setup_sheets"][idx]["images_b64"] = [base64.b64encode(img.read()).decode('utf-8') for img in edit_images]
                                                db["setup_sheets"][idx]["image_b64"] = ""
                                            elif delete_image:
                                                db["setup_sheets"][idx]["images_b64"] = []
                                                db["setup_sheets"][idx]["image_b64"] = ""
                                            db["setup_sheets"][idx]["date"] = datetime.now().strftime("%Y-%m-%d %H:%M") + " (수정됨)"
                                            break
                                    save_data(db)
                                    st.rerun()
                                    
                    with col_btn2:
                        if st.button("❌ 시트 삭제", key=f"del_{sheet['id']}"):
                            db["setup_sheets"] = [s for s in db["setup_sheets"] if s["id"] != sheet["id"]]
                            save_data(db)
                            st.rerun()

    elif 가공메뉴 == "💾 자주 쓰는 G코드 매니저":
        st.header("💾 자주 쓰는 G코드 & 수기 매크로")
        st.subheader("📖 현장 G/M코드 기본 사전")
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
        with st.expander("➕ 새로운 매크로 패턴 등록하기"):
            with st.form("gcode_form"):
                g_title = st.text_input("코드 명칭", placeholder="예: 나사 가공 G76 사이클 기본 형태")
                g_machine = st.selectbox("해당 장비", ["공통", "두산 DNM500", "두산 DNM650", "두산 GT2100"])
                g_code = st.text_area("G코드 내용", placeholder="G코드 블록을 입력하세요", height=150)
                g_desc = st.text_input("코드 설명", placeholder="간단한 활용 팁이나 설명을 적어주세요")
                if st.form_submit_button("저장하기"):
                    if not g_title or not g_code:
                        st.error("명칭과 코드 내용은 필수입니다.")
                    else:
                        db["gcodes"].append({"title": g_title, "machine": g_machine, "code": g_code, "description": g_desc})
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

    elif 가공메뉴 == "📝 현장 수기 노트 / 자유 메모":
        st.header("📝 현장 수기 노트 및 자유 메모")
        with st.expander("➕ 새 노트 작성하기"):
            with st.form("memo_form", clear_on_submit=True):
                memo_title = st.text_input("노트 제목")
                memo_content = st.text_area("노트 내용", height=150)
                uploaded_images = st.file_uploader("📷 현장 사진 첨부", type=["png", "jpg", "jpeg"], accept_multiple_files=True)
                if st.form_submit_button("💾 메모 저장하기"):
                    if not memo_title or not memo_content:
                        st.error("제목과 내용은 필수 입력 항목입니다.")
                    else:
                        images_b64_list = [base64.b64encode(img.read()).decode('utf-8') for img in uploaded_images] if uploaded_images else []
                        new_memo = {
                            "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                            "title": memo_title,
                            "content": memo_content,
                            "images_b64": images_b64_list,
                            "image_b64": "",
                            "date": datetime.now().strftime("%Y-%m-%d %H:%M")
                        }
                        db["memos"].append(new_memo)
                        save_data(db)
                        st.success("새로운 노트가 저장되었습니다!")
                        
        st.markdown("---")
        st.subheader("📚 저장된 수기 노트 목록")
        search_memo = st.text_input("🔍 노트 검색", placeholder="검색어 입력...")
        filtered_memos = db["memos"]
        if search_memo:
            filtered_memos = [m for m in filtered_memos if search_memo.lower() in m["title"].lower() or search_memo.lower() in m["content"].lower()]
            
        if not filtered_memos:
            st.info("등록된 노트가 없습니다.")
        else:
            for memo in reversed(filtered_memos):
                with st.expander(f"📔 {memo['title']} ({memo['date']})"):
                    st.write(memo['content'])
                    imgs_to_show = memo.get("images_b64", [])
                    if not imgs_to_show and memo.get("image_b64"):
                        imgs_to_show = [memo["image_b64"]]
                    if imgs_to_show:
                        st.markdown("---")
                        num_cols = min(len(imgs_to_show), 3)
                        cols = st.columns(num_cols)
                        for i, img_b64 in enumerate(imgs_to_show):
                            cols[i % num_cols].image(base64.b64decode(img_b64), use_column_width=True)
                    st.markdown("<br>", unsafe_allow_html=True)
                    col_btn1, col_btn2 = st.columns([1, 1])
                    with col_btn1:
                        with st.expander("✏️ 문서 수정하기"):
                            with st.form(f"edit_memo_form_{memo['id']}"):
                                edit_title = st.text_input("제목 수정", value=memo['title'])
                                edit_content = st.text_area("내용 수정", value=memo['content'], height=200)
                                edit_images = st.file_uploader("📷 새 사진 첨부", type=["png", "jpg", "jpeg"], accept_multiple_files=True, key=f"edit_img_{memo['id']}")
                                delete_image = st.checkbox("🗑️ 기존 사진 모두 삭제", key=f"del_img_{memo['id']}")
                                if st.form_submit_button("💾 수정 저장"):
                                    for idx, m in enumerate(db["memos"]):
                                        if m["id"] == memo["id"]:
                                            db["memos"][idx]["title"] = edit_title
                                            db["memos"][idx]["content"] = edit_content
                                            if edit_images:
                                                db["memos"][idx]["images_b64"] = [base64.b64encode(img.read()).decode('utf-8') for img in edit_images]
                                                db["memos"][idx]["image_b64"] = ""
                                            elif delete_image:
                                                db["memos"][idx]["images_b64"] = []
                                                db["memos"][idx]["image_b64"] = ""
                                            db["memos"][idx]["date"] = datetime.now().strftime("%Y-%m-%d %H:%M") + " (수정됨)"
                                            break
                                    save_data(db)
                                    st.rerun()
                    with col_btn2:
                        if st.button("❌ 문서 삭제", key=f"del_memo_{memo['id']}"):
                            db["memos"] = [m for m in db["memos"] if m["id"] != memo["id"]]
                            save_data(db)
                            st.rerun()

    elif 가공메뉴 == "📅 일일 작업 일지":
        st.header("📅 일일 작업 일지")
        with st.expander("➕ 새 작업 일지 작성하기"):
            with st.form("work_log_form", clear_on_submit=True):
                col1, col2, col3 = st.columns(3)
                with col1: log_date = st.date_input("작업 일자")
                with col2: worker_name = st.text_input("작업자명")
                with col3: shift = st.selectbox("근무조", ["주간", "야간", "특근/기타"])
                machine_used = st.selectbox("가공 장비", ["전체/공통", "두산 DNM500", "두산 DNM650", "두산 GT2100"])
                tasks_done = st.text_area("생산 내역 (품명 및 수량 등)", height=100)
                issues_notes = st.text_area("특이사항 및 인수인계", height=100)
                log_images = st.file_uploader("📷 작업/현장 사진 첨부", type=["png", "jpg", "jpeg"], accept_multiple_files=True)
                if st.form_submit_button("💾 일지 저장하기"):
                    if not worker_name or not tasks_done:
                        st.error("작업자명과 생산 내역은 필수 입력 항목입니다.")
                    else:
                        images_b64_list = [base64.b64encode(img.read()).decode('utf-8') for img in log_images] if log_images else []
                        new_log = {
                            "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                            "date": log_date.strftime("%Y-%m-%d"),
                            "worker": worker_name,
                            "shift": shift,
                            "machine": machine_used,
                            "tasks": tasks_done,
                            "issues": issues_notes,
                            "images_b64": images_b64_list,
                            "image_b64": "",
                            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
                        }
                        db["work_logs"].append(new_log)
                        save_data(db)
                        st.success("작업 일지가 성공적으로 저장되었습니다!")
                        
        st.markdown("---")
        st.subheader("📋 지난 작업 일지 조회")
        search_log = st.text_input("🔍 일지 검색", placeholder="검색어 입력...")
        filtered_logs = db["work_logs"]
        if search_log:
            filtered_logs = [l for l in filtered_logs if search_log.lower() in l["worker"].lower() or search_log.lower() in l["machine"].lower() or search_log.lower() in l["tasks"].lower() or search_log.lower() in l["issues"].lower()]
            
        if not filtered_logs:
            st.info("등록된 작업 일지가 없습니다.")
        else:
            for log in reversed(filtered_logs):
                with st.expander(f"📅 [{log['date']}] {log['worker']} ({log['shift']}) - {log['machine']}"):
                    st.markdown("**🛠️ 생산 내역:**")
                    st.write(log['tasks'])
                    st.markdown("**⚠️ 특이사항 및 인수인계:**")
                    st.info(log['issues'] if log['issues'] else "특이사항 없음")
                    imgs_to_show = log.get("images_b64", [])
                    if not imgs_to_show and log.get("image_b64"):
                        imgs_to_show = [log["image_b64"]]
                    if imgs_to_show:
                        st.markdown("---")
                        num_cols = min(len(imgs_to_show), 3)
                        cols = st.columns(num_cols)
                        for i, img_b64 in enumerate(imgs_to_show):
                            cols[i % num_cols].image(base64.b64decode(img_b64), use_column_width=True)
                    st.caption(f"작성 일시: {log['created_at']}")
                    col_btn1, col_btn2 = st.columns([1, 1])
                    with col_btn1:
                        with st.expander("✏️ 일지 수정하기"):
                            with st.form(f"edit_log_form_{log['id']}"):
                                edit_worker = st.text_input("작업자명 수정", value=log['worker'])
                                edit_tasks = st.text_area("생산 내역 수정", value=log['tasks'], height=100)
                                edit_issues = st.text_area("특이사항 수정", value=log['issues'], height=100)
                                edit_images = st.file_uploader("📷 새 사진 첨부", type=["png", "jpg", "jpeg"], accept_multiple_files=True, key=f"edit_img_{log['id']}")
                                delete_image = st.checkbox("🗑️ 기존 사진 모두 삭제", key=f"del_img_{log['id']}")
                                if st.form_submit_button("💾 수정 저장"):
                                    for idx, l in enumerate(db["work_logs"]):
                                        if l["id"] == log["id"]:
                                            db["work_logs"][idx]["worker"] = edit_worker
                                            db["work_logs"][idx]["tasks"] = edit_tasks
                                            db["work_logs"][idx]["issues"] = edit_issues
                                            if edit_images:
                                                db["work_logs"][idx]["images_b64"] = [base64.b64encode(img.read()).decode('utf-8') for img in edit_images]
                                                db["work_logs"][idx]["image_b64"] = ""
                                            elif delete_image:
                                                db["work_logs"][idx]["images_b64"] = []
                                                db["work_logs"][idx]["image_b64"] = ""
                                            break
                                    save_data(db)
                                    st.rerun()
                    with col_btn2:
                        if st.button("❌ 일지 삭제", key=f"del_log_{log['id']}"):
                            db["work_logs"] = [l for l in db["work_logs"] if l["id"] != log["id"]]
                            save_data(db)
                            st.rerun()

elif 메인메뉴 == "원영공구 (공구/소재)":
    장부_화면_출력("🛠️ 원영공구 구매 장부", "원영공구_장부.json", ["공구", "소재"])

elif 메인메뉴 == "익산비철 (비철/자재)":
    장부_화면_출력("⛓️ 익산비철 구매 장부", "익산비철_장부.json", ["비철", "기타 자재"])
