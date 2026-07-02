# HWP 문서 분석기

한글 문서(HWP/HWPX)를 업로드해 표·숫자를 구조화하고 로컬 LLM으로 질의응답+AI 편집하는 분석 도구.

- **한글(HWP/HWPX) 문서를 로컬 LLM 기반으로 분석하여, 문서 내용·표·숫자를 자동 추출하고 질의응답을 지원하는 SW 구현**
- **향후 예산서, 사업계획서, 성과지표 등 내부 문서를 안전하게 분석·활용할 수 있는 시스템으로 확장**

0702 ver.
<img width="1838" height="797" alt="image" src="https://github.com/user-attachments/assets/945bea83-4f5a-42b8-9e66-e586ed982fa7" />

<img width="1861" height="775" alt="image" src="https://github.com/user-attachments/assets/9a2b66dc-f027-4d26-a2e5-6ff7ed40c948" />


## 주요 기능

- **문서 분석 / 질의응답** — 표·숫자 자동 추출 후 Ollama 기반 2-Stage Q&A
- **HWPX 채팅 편집** — 빈칸 채우기, 초안 작성, 표 셀 숫자 수정
- **미리보기 diff** — 🟡 노란색 = AI 제안, 🔴 빨간색 = 적용된 수정, 🟢 초록 = 새 내용

## 프로젝트 구조

```
HWP analysis/
├── app.py                  # Streamlit 진입점 (streamlit run app.py)
├── requirements.txt
│
├── main/                   # 핵심 로직: 파싱, Q&A, 표 그리드, HWPX 편집 엔진
│   ├── hwp_parser.py       # HWP/HWPX 파싱
│   ├── table_extractor.py  # 표·숫자 구조화 (Q&A용)
│   ├── qa_engine.py        # 2-Stage LLM 질의응답
│   ├── table_grid.py       # 표 그리드·병합셀 파싱 (공통)
│   └── hwpx_editor.py      # HWPX 편집, pending/applied diff
│
├── ui/                     # 화면·명령 라우팅
│   ├── document_preview.py # 왼쪽 HTML 미리보기
│   └── command_router.py   # 채팅 의도 분류·편집 실행
│
└── additional/             # 부가 기능: AI 편집·참고자료·Windows 연동
    ├── ai_editor.py        # LLM 빈칸/초안/리라이트
    ├── reference_parser.py # 참고자료(PDF·DOCX 등) 파싱
    └── windows_agent/      # Windows 한글 COM 브리지 (선택)
        └── hwp_bridge.py
```


## 아키텍처

설계 원칙: **계산은 코드가, 해석은 LLM이**

LLM은 표 안 숫자를 잘못 읽거나 계산을 틀리는 경우가 많습니다.  
이 시스템은 코드가 먼저 표에서 숫자를 찾고 계산한 뒤, LLM은 그 결과를 자연어로 설명합니다.

### 분석·Q&A

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          app.py (Streamlit UI)                           │
│  파일 업로드 → 세션 캐시 → 2분할 UI (미리보기 + 채팅, 스트리밍 지원)        │
└──────┬────────────────────┬────────────────────────┬─────────────────────┘
       │                    │                        │
 ┌─────▼──────────┐  ┌──────▼───────────┐  ┌─────────▼────────────────────┐
 │ main/          │  │ main/            │  │ main/                        │
 │ hwp_parser     │  │ table_extractor  │  │ qa_engine                    │
 │                │  │                  │  │                              │
 │ HWPX: ZIP→XML  │  │ rows→DataFrame   │  │ Stage 1: gemma3:4b           │
 │  병합셀 복원   │──▶│ NumberInfo 탐지  │──▶│  의도·엔티티 추출 + 검증      │
 │ HWP: OLE/변환  │  │ TableSummary     │  │ Pre-compute / Rule-based     │
 │                │  │                  │  │ Stage 2: gemma4 (스트리밍)   │
 └────────────────┘  └──────────────────┘  └──────────────────────────────┘
```

### 편집

HWPX 업로드 시 오른쪽 채팅에서 편집 명령을내면, 제안은 왼쪽 미리보기에 표시되고 **「모두 적용」** 후 HWPX로 저장합니다.

```
app.py
  └─ ui/command_router.py     의도 분류 (fill / draft / replace / qa)
        ├─ additional/ai_editor.py      LLM 빈칸·초안·리라이트
        └─ main/hwpx_editor.py          propose_* → pending 변경
              └─ ui/document_preview.py  노란(제안) / 빨강(적용) HTML 미리보기
```

| 색상 | 의미 |
|------|------|
| 🟡 노란 테두리 | AI가 제안한 변경 (대기) |
| 🔴 빨간 글씨 | 문서에 적용된 수정 (한글 Track Changes와 유사) |
| 🟢 초록 글씨 | 추가·수정된 새 텍스트 |

**제한:** HWP는 읽기 전용 Q&A만 지원. 편집·diff는 **HWPX** 로 변환 후 활용.

이전 버전:
<img width="1820" height="771" alt="image" src="https://github.com/user-attachments/assets/02ce6981-37bd-4538-b2e9-2a99b8538b20" />
<img width="549" height="231" alt="image" src="https://github.com/user-attachments/assets/c1e8d1c0-9404-4f7d-8015-b1adf038b778" />



