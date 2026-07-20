# 진행 기록

새 기능은 아래 표에 **한 줄씩만** 추가. 설계 배경은 [ARCHITECTURE.md](ARCHITECTURE.md).

## 완료 (P1–P16)

| # | 뭐가 생겼나 | 계산 (코드) | 설정 (파일) | LLM | 한 줄 포인트 |
|---|-------------|-------------|-------------|-----|--------------|
| P1 | ontology → Fact → 검증 | 라벨매칭·Fact·합계/예실/교차 | `ontology/budget_concepts.yaml` | Q&A 설명만 | **맞다/틀다 = 코드** |
| P2 | 사이드바 파일 체크 | 업로드 시 1회 파싱, 체크는 필터만 | `session_state file_active_*` | 없음 | **업로드 ≠ 활성** |
| P3 | PromptRegistry + memory 구멍 | Stage조립·변수치환 | `prompts/*.md` + `catalog.yaml` | Stage1/2 호출 | **프롬프트 고치려면 MD** |
| P4 | 이슈 → 이동 / 채팅 설명 | 위치 점프·행 하이라이트·질문 자동전송 | `ui/issue_panel.py` | 설명만 (숫자 수정 X) | **카드: 이동 / 채팅** |
| P5 | MemoryStore (백엔드만) | SQLite·Stage2 주입 (UI 없음) | `data/memory/` | 참고만 | **화면에는 안 냄** |
| P6 | 규칙 YAML + ontology 큐 API | on/off·tol·concept / 승인 API | `rules/*.yaml` | 없음 | **정책=파일, UI 최소** |
| P7 | Issue → QAEngine 구조 연결 | Issue dict·결정적 설명·`issues=` | Stage2 `{issue_section}` | 설명만 (숫자 수정 X) | **채팅=문자열+구조** |
| P8 | concept별 tol·confidence | `resolve_tol` / `resolve_min_confidence` | `concept_tol` in `budget_checks.yaml` | 없음 | **총액 엄격·증감 여유** |
| P9 | 양식 자동 채우기 vertical slice | inspect→plan→propose→승인→apply→verify | `doc_fill_concepts.yaml` | 글 초안만 (숫자·쓰기는 코드) | **분석기 + 실행 1업무** |
| P10 | 빈 칸 채우기 UI 1차 정리 | 검사+제안 합침, 기술어 축소 | `ui/doc_work_panel.py` | 없음 | **사람말 버튼** |
| P11 | 상용 AI 제품형 UX 재설계 | UI/플로우만 (백엔드 무변경) | `ui/brand.py` · review · fill | 없음 | **한 모드 · 한 CTA · 진행만 노출** |
| P12 | 채우기 = 채팅 자연어만 | DocFillPipeline을 chat intent로 호출 | `doc_work_panel` · `command_router` | 초안만 | **모드/CTA 제거 · 검토+대화** |
| P12.1 | 채우기 실동작 보강 | 예실 Excel→HWPX **표 삽입** · 서식빈칸은 참고라벨 매칭만 · 가짜값 금지 | `doc_agent` · `hwpx_editor` | 글 초안만 | **표는 표 · 없는 값은 비움** |
| P13 | 예산 규칙 확대 + 이슈 Q&A 본선 연결 | 합계/예실/교차 규칙 확장 · Issue 구조→Stage2 | `budget_checks.yaml` · `qa_engine` · prompts | 설명만 | **검토→채팅 설명 한 줄** |
| P14 | 셸 레이아웃 · 로고 | wide 메인 · 사이드바/히어로 브랜드 | `ui/brand.py` · `ui/logo.png` | 없음 | **화면 = 제품** |
| P15 | DocFill 신뢰성 + institution 내부 도구 | 라벨거절·trace·Evidence-only 기관 채우기 | `doc_agent/*` · `workflows/institution_fill` | 없음 | **가짜값 금지 · 근거 추적** |
| P16 | Completion Planner (최소) | 상태→갭→계획→내부 fill 도구 (complete/fill만) | `doc_reasoner/` · B `chat_route`/`server` | 없음 | **완성해줘 = Planner가 도구 고름** |
| P17 | A 일반 지식 분리 | 문서 섹션 + 문서 외 섹션 (기본: 문서+일반지식) | `knowledge_mode` · `qa_engine` · intelligence UI | 보충만 | **근거와 일반지식 안 섞음** |

```text
업로드 → 검토 홈(요약·이슈) → 문서 + 채팅
  B 예: 「이 문서 완성해줘」
    → Completion Planner → FactFill(기관) 등 → 제안 pending → 반영 → 다운로드
```

### P12에서 바꾼 것

| 이전 | 이후 |
|------|------|
| 「이 문서 검토하기 / 빈 칸 채우기」모드 | **검토만** — 채우기는 채팅 |
| 「이 문서 채워 주세요」버튼·요청 기본문구 | **자연어 한 줄** (예: 참고 자료로 채워줘) |
| 후보/제안/검토 KPI 3카드 | **숨김** — 답변 + 제안 카드만 |

백엔드(`hwp_core/doc_agent`) 파이프는 그대로, 진입점만 채팅.

### 2026-07-14 요약

| 커밋 / 범위 | 내용 |
|-------------|------|
| `15881d1` | P12 — 검토 홈 + 채팅 채우기 |
| `0e956a1` 등 | P13 예산·이슈 Q&A · P12.1/P14 보강 |

### 오늘 (2026-07-20) 요약

| 범위 | 내용 |
|------|------|
| push `76c9bf1` | P15 — DocFill label-as-value 수정 · fill trace · institution 도구 |
| 로컬 (Completion Planner) | P16 — 「이 문서 완성해줘」→ DocumentState/Gap/Plan → FactFill · 용어: Document Reasoner→Completion Planner |

원칙: **숫자·쓰기는 코드 · LLM은 초안/설명 · 원본 보존 · complete/fill만 Planner · Document Reasoner는 예약(상위 태스크 선택).**

## 빈 구멍 → 다음

| 우선 | 내용 | 상태 |
|------|------|------|
| — | Completion Planner gap 확대 (필요성 초안 실행 등) — **chat 워크플로 추가 금지** | 다음 |
| — | B deep dive (대상 범위 · 사실/서술 분리 · 항목 수락 · 저장 보존) | **다음 집중** |
| — | A 동결 (일반지식 분리·편집말투 완화·활성문서 표시까지 반영) | 안정화 후 |
| — | KMX 실문서로 「완성해줘」E2E | 사용자 |
| — | `.gitignore` · logo 등 저장소 정리 | 선택 |

원칙: **숫자·쓰기는 코드 · LLM은 초안/설명 · 원본 보존 · 화면은 제품.**
