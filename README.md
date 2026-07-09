# HWP 문서 분석기

한글(HWP/HWPX)·엑셀 문서를 업로드해 표·숫자를 구조화하고, 로컬 LLM으로 질의응답·AI 편집·자동 검토를 지원하는 내부 문서 분석 도구.

- **HWP/HWPX/Excel 문서에서 표·숫자·본문 사실을 추출하고, 질의응답 및 일관성 검토 지원**
- **예산서·사업계획서·성과지표 등 내부 문서를 로컬 환경에서 안전하게 분석/활용**

0709 ver.
<img width="1810" height="812" alt="image" src="https://github.com/user-attachments/assets/bc3e34f0-ebc8-4810-ac42-da78e4982143" />


## 주요 기능

- **Excel 분석/HWP/HWPX 편집** — 파일 업로드, 미리보기·직접 수정·다운로드
- **통합 작업 화면** — 파일 형식별 동일 레이아웃: 왼쪽 미리보기/직접편집, 오른쪽 **💬 이 파일 | 💬 전체** 채팅
- **자동 검토** — 표 행 합계·합계 행·본문↔표 예산·문서 간 총액 교차 검증 (이슈 있을 때만 패널 표시)
- **미리보기 diff** — 🟡 AI 제안, 🔴 적용된 수정, 🟢 새 내용

## 프로젝트 구조

```
HWP analysis/
├── app.py                      # Streamlit 실행
├── requirements.txt
├── hwpilot/                    # Node CLI — .hwp 읽기·변환·편집 (dist/ 번들 또는 npm)
│
├── hwp_core/                   # 핵심 로직
│   ├── hwp_parser.py           # HWP/HWPX 파싱
│   ├── hwp_backends.py         # hwpilot / pyhwp / LibreOffice / olefile
│   ├── table_extractor.py      # 표·숫자 구조화 + 표 그리드·병합셀
│   ├── qa_engine.py            # 2-Stage LLM 질의응답
│   ├── llm_client.py           # Ollama 연결·일반 질문
│   ├── hwpx_editor.py          # HWPX 편집, pending/applied diff
│   ├── fact_extractor.py       # 표·본문 Fact 추출
│   ├── consistency_checker.py  # 합계·교차 일관성 검사
│   └── intel_pipeline.py       # 문서/워크스페이스 intel 조립
│
├── ui/                         # Streamlit UI
│   ├── document_workspace.py   # HWP/HWPX/Excel 통합 분할 UI
│   ├── document_preview.py     # HTML 미리보기·diff
│   ├── command_router.py       # 채팅 의도 분류·편집 실행
│   ├── canvas_editor.py        # 직접 편집·HWPX 다운로드
│   ├── intel_panel.py          # 자동 검토 패널
│   └── session_store.py        # 세션·편집 상태
│
├── additional/
│   ├── ai_editor.py            # LLM 빈칸/초안/리라이트
│   └── reference_parser.py     # Excel·PDF·DOCX 등 파싱 + 참고자료
└── 
```

## 설치 및 실행

```bash
pip install -r requirements.txt  # pyhwp CLI: hwp5txt, hwp5html

# hwpilot — .hwp 업로드·편집 시 필요 (repo 번들 또는 전역 설치)
npm install -g hwpilot
# 또는: cd hwpilot && npm install   # repo 내 dist/ 사용

streamlit run app.py #실행
```

### 분석·Q&A

```
┌────────────────────────────────────────────────────────────────────────────────────┐
│                            app.py (Streamlit UI)                                   │
│  파일 업로드 → process_document() → 세션 캐시 → 2분할 UI (미리보기 + 채팅)           │
└──────┬──────────────────┬────────────────────┬────────────────────┬─────────────────┘
       │                  │                    │                    │
┌──────▼─────────┐ ┌──────▼──────────┐ ┌─────▼────────────┐ ┌────▼──────────────┐
│ 파싱            │ │ table_extractor │ │ intel_pipeline   │ │ qa_engine         │
│                │ │                 │ │                  │ │                   │
│ Excel:         │ │ rows→DataFrame  │ │ fact_extractor   │ │ Stage 1: gemma3:4b│
│  reference_    │ │ NumberInfo 탐지 │ │ consistency_     │ │  의도·엔티티 추출  │
│  parser        │─▶│ TableSummary    │─▶│ checker          │─▶│ Pre-compute /    │
│ HWPX: ZIP→XML  │ │                 │ │ → intel_panel    │ │  Rule-based       │
│ HWP: hwpilot   │ │                 │ │  (이슈 시만 UI)  │ │ Stage 2: gemma4   │
│  fallback chain│ │                 │ │                  │ │  (스트리밍)       │
└────────────────┘ └─────────────────┘ └──────────────────┘ └───────────────────┘
```

```mermaid
flowchart LR
    subgraph Input
        B[file_bytes]
        FN[filename.ext]
    end

    subgraph Router["process_document()"]
        EXT{ext?}
    end

    subgraph Excel_Path["Excel — openpyxl"]
        X1[reference_parser]
        X2[tables_raw + full_text]
    end

    subgraph HWPX_Path["HWPX — Pure Python"]
        Z1[ZipFile.read]
        Z2[section*.xml]
        Z3[ElementTree parse]
        Z4[paragraphs + tables_raw]
    end

    subgraph HWP_Path["HWP — Fallback Chain"]
        F1["① hwpilot read"]
        F2["② pyhwp hwp5html"]
        F3["③ hwpilot convert → parse_hwpx"]
        F4["④ LibreOffice convert"]
        F5["⑤ olefile + zlib"]
    end

    subgraph Enrich["Post-process (CPU)"]
        ET[extract_tables]
        NT[detect_numbers_in_text]
        NB[detect_numbers_in_tables]
    end

    subgraph Intel["자동 검토"]
        FE[fact_extractor]
        CC[consistency_checker]
        IP[intel_pipeline]
    end

    subgraph Output["Session"]
        C["doc_payload<br/>tables · numbers · intel"]
        QA[qa_engine 2-Stage]
    end

    B --> EXT
    FN --> EXT
    EXT -->|.xlsx .xls| Excel_Path
    EXT -->|.hwpx| HWPX_Path
    EXT -->|.hwp 등| HWP_Path
    Excel_Path --> Enrich
    HWPX_Path --> Enrich
    F1 -->|success| Enrich
    F1 -->|fail| F2
    F2 -->|fail| F3
    F3 -->|fail| F4
    F4 -->|fail| F5
    F5 --> Enrich
    Enrich --> Intel
    FE --> CC --> IP
    Intel --> C
    C --> QA
```

0702 ver.
<img width="1838" height="797" alt="image" src="https://github.com/user-attachments/assets/945bea83-4f5a-42b8-9e66-e586ed982fa7" />

<img width="1861" height="775" alt="image" src="https://github.com/user-attachments/assets/9a2b66dc-f027-4d26-a2e5-6ff7ed40c948" />
