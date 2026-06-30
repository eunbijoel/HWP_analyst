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
├── qa_engine.py        # 2-Stage LLM 질의응답 엔진
└── requirements.txt    # 의존성 목록
```


## 문서 처리 파이프라인

설계 원칙: 계산은 코드가, 해석은 LLM이

LLM은 표 안의 숫자를 잘못 읽거나 계산을 틀리는 경우가 많습니다.
그래서 이 시스템은 코드가 먼저 표에서 숫자를 찾고 계산한 뒤, LLM은 그 결과를 자연어로 설명하는 역할만 합니다.

또한, 질문에서 키워드를 정규식으로 찾는 방식은 줄임말이나 변형 표현에 취약하기 때문에,
소형 LLM(gemma3:4b)이 먼저 질문 의도를 파악하고, 그 결과로 코드가 정확한 데이터를 추출한 뒤,
대형 LLM(gemma4)이 최종 답변을 생성하는 2단계 구조를 사용합니다.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          app.py (Streamlit UI)                           │
│  다중 파일 업로드 → 세션 캐시 → 문서 메트릭 → 채팅 (스트리밍 지원)           │
└──────┬────────────────────┬────────────────────────┬─────────────────────┘
       │                    │                        │
 ┌─────▼───────┐   ┌────────▼─────────┐   ┌──────────▼───────────────────┐
 │ hwp_parser  │   │ table_extractor  │   │        qa_engine             │
 │             │   │                  │   │                              │
 │ HWPX:       │   │ rows→DataFrame   │   │ Stage 1: gemma3:4b           │
 │  ZIP→XML    │   │ NumberInfo 탐지  │   │  질문 의도 + 엔티티 추출       │
 │  병합셀     │──▶│ TableSummary    │──▶│  ↓ 검증 (환각 필터링)         │
 │             │   │  컬럼 프로파일링 │   │                               │
 │ HWP:        │   │  금액/연도/비율  │   │ Pre-compute: DataFrame 계산   │
 │  LibreOffice│   │                 │   │ Rule-based: 키워드 패턴 매칭   │
 │  변환→HWPX  │   │                 │   │                               │
 │  (실패시 OLE)│  │                 │   │ Stage 2: gemma4 (스트리밍)     │
 └─────────────┘   └─────────────────┘   │  해석 + 자연어 답변            │
                                         └───────────────────────────────┘
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
| 순서 | 단계 | 역할 |
|------|------|------|
| 1 | **Stage 1** (gemma3:4b) | 질문에서 의도(lookup/sum/max 등), 엔티티(기관명), 지표(예산/연구개발비)를 추출. 추출 결과는 실제 표 헤더와 대조하여 검증 |
| 2 | **Pre-compute** | Stage 1 결과로 DataFrame에서 코드 기반 계산 (교차 조회, 합계, 필터링). 숫자 정확도 보장 |
| 3 | **Rule-based** | 키워드 패턴 매칭으로 빠른 답변 (예산 표 찾기, 최댓값, 연도 필터링, 합계 계산 등 12개 패턴) |
| 4 | **Stage 2** (gemma4) | 위 결과를 받아 자연어로 정리. 스트리밍으로 토큰 단위 실시간 출력 |


