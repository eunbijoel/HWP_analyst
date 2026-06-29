# HWP 문서 분석기

한글 문서(.hwp/.hwpx)를 업로드하여 구조화·수치 분석한 뒤, 로컬 LLM으로 질의응답하는 문서 분석 도구입니다.

- **한글(HWP/HWPX) 문서를 로컬 LLM(Ollama Gemma 4) 기반으로 분석하여, 문서 내용·표·숫자를 자동 추출하고 질의응답을 지원하는 SW를 구현**
- **향후 예산서, 사업계획서, 성과지표 등 내부 문서를 안전하게 분석·활용할 수 있는 기반 시스템으로 확장**

<img width="1820" height="771" alt="image" src="https://github.com/user-attachments/assets/02ce6981-37bd-4538-b2e9-2a99b8538b20" />
<img width="549" height="231" alt="image" src="https://github.com/user-attachments/assets/c1e8d1c0-9404-4f7d-8015-b1adf038b778" />

## 파일 구조

```
HWP analysis/
├── app.py              # Streamlit UI 메인
├── hwp_parser.py       # HWP/HWPX 문서 파싱
├── table_extractor.py  # 표 추출 및 숫자 분석
├── qa_engine.py        # 질의응답 (rule-based + Ollama)
├── requirements.txt    # 의존성 목록
```

## 문서 처리 파이프라인

업로드된 파일은 Streamlit UI에서 **한 번 파싱되면 세션에 캐시**됩니다. 이후 질문마다 아래 3단계가 실행됩니다.

```
┌─────────────┐    ┌──────────────────┐    ┌─────────────────────┐
│  HWP/HWPX   │───▶│  구조화·수치 분석  │───▶│  질의응답 (QAEngine)  │
│  hwp_parser │    │  table_extractor  │    │  ① 사전 계산         │
└─────────────┘    └──────────────────┘    │  ② Rule-based       │
                                            │  ③ Ollama (선택)    │
                                            └─────────────────────┘
```

### Stage 1 — 문서 파싱 (`hwp_parser.py`)

| 형식 | 방식 | 산출물 |
|------|------|--------|
| **HWPX** | ZIP 내 `section*.xml` 순회, 문단(`p`)·표(`tbl`) **문서 순서대로** 추출 | `ParsedDocument`: `paragraphs`, `tables_raw`, `full_text` |
| **HWP** | OLE `BodyText` 스트림, zlib 압축 해제 후 HWP 레코드(tag 67) 텍스트 디코딩 | `paragraphs` (표 구조 미지원) |

HWPX 표 파싱 시:
- `cellAddr` / `cellSpan` 기반 그리드 복원 (병합 셀 1차 지원)
- 표 직전 문단에서 **캡션·단위** 추출 — `(단위: 천원)` 패턴

### Stage 2 — 표·숫자 구조화 (`table_extractor.py`)

파싱 결과를 **분석 가능한 데이터 모델**로 변환합니다.

1. **DataFrame 변환** — 단위 전용 첫 행 자동 스킵, 중복 헤더 처리
2. **컬럼 프로파일링** — 숫자·금액·연도 컬럼, 합계 행(합계/총계/소계 등) 탐지
3. **NumberInfo 추출** — 본문·셀 단위로 금액(원~조원), %, 연도, 기간 정규식 매칭 및 `numeric_value` 환산

각 표는 `TableSummary` 객체로 관리됩니다 (캡션, 단위, 헤더, `dataframe`, `money_columns` 등).

### Stage 3 — 질의응답 (`qa_engine.py`)
LLM을 부르기 전에 아래를 순서대로 수행합니다.

① 사전 계산 (_pre_compute_analysis)
② Rule-based 분석 (_rule_based_answer)
③ LLM 보강 (_llm_answer, Ollama 선택)


## 현재 한계 (HWP 제한)

- **권장**: 가능하면 한글 프로그램에서 HWPX로 다시 저장한 후 사용하세요.

1. **HWP 표 추출 불가**: HWP 바이너리에서 표 구조를 파싱하는 것은 복잡한 리버스 엔지니어링이 필요합니다.
2. **병합 셀 처리 미흡**: HWPX에서도 복잡한 셀 병합이 있는 표는 정확도가 떨어질 수 있습니다.
3. **이미지/차트 미지원**: 문서 내 이미지나 차트는 분석하지 않습니다.

## 향후 개선 방향

1. **pyhwp 라이브러리 연동**: HWP 파일의 표 추출 정확도 향상
2. **병합 셀 처리**: rowspan/colspan 처리 로직 추가
3. **문서 비교**: 복수 문서 간 예산 비교 기능
4. **차트 생성**: 추출된 숫자 데이터를 시각화
5. **RAG 파이프라인**: 문서 임베딩을 통한 정확한 검색 기반 답변

