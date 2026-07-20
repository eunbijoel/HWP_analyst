# HWP Editing Assistant (B)

Flask 워크스페이스 UI — **Product B (Modify)**.  
문서를 보며 선택 · 직접 수정 · Ollama 지시 · 저장합니다.


|     | Product A                                      | Product B (이 폴더)                               |
| --- | ---------------------------------------------- | ---------------------------------------------- |
| 이름  | HWP Document Intelligence (A)                  | HWP Editing Assistant (B)                      |
| 역할  | 분석 · 검토 · Q&A (읽기 전용)                          | 선택 · 수정 · 보완 · 저장                              |
| 실행  | `streamlit run apps/intelligence/app.py`       | `python3 HWP_v2/server.py`                     |
| 주소  | [http://127.0.0.1:8501](http://127.0.0.1:8501) | [http://127.0.0.1:8765](http://127.0.0.1:8765) |
| UI  | Streamlit (`apps/intelligence` + `ui/`)        | `templates/` · `static/`                       |


엔진은 `hwp_core`를 공유합니다. 동일 엔트리: `python3 apps/editor/server.py`.

---

## Run

```bash
cd "/home/eunbi/HWP analysis"
python3 HWP_v2/server.py
# 또는: python3 apps/editor/server.py
```

→ [http://127.0.0.1:8765](http://127.0.0.1:8765)

---

## Layout

```
[ 워크스페이스 ] [ 문서 미리보기/편집 ] [ AI ]
```


| 영역  | 역할                                |
| --- | --------------------------------- |
| 왼쪽  | 연 파일 목록 · 클릭=활성 · **× = 목록에서 삭제** |
| 가운데 | HTML 미리보기 · 클릭=선택 · 더블클릭=직접 편집    |
| 오른쪽 | AI 채팅 · 제안 수락/거절                  |
| 상단  | 파일 추가 · 저장(HWPX/HWP) · 설정(Ollama) |


미리보기는 한글.exe가 아닙니다. Linux에서는 HWPX XML → HTML로 재구성합니다.

**지원 파일: HWP / HWPX만** (Excel 업로드·분석은 UI에서 제외).

---

## Folder structure

```
HWP_v2/
  server.py           # Flask API · 세션 · 채팅 · export
  chat_route.py       # 채팅 의도 분기
  workspace_docs.py   # DocSlot (HWP/HWPX 로드)
  cell_ai.py          # 선택 문단/영역 프롬프트 · 로컬 축약
  convert_hwp.py      # HWP ↔ HWPX
  templates/index.html
  static/js/app.js
  static/css/app.css
  README.md
```

**Reused**


| Module                                 | Role                 |
| -------------------------------------- | -------------------- |
| `hwp_core.hwpx_editor.HWPXEditor`      | HWPX XML 편집 · export |
| `hwp_core.qa_engine.QAEngine`          | 멀티문서 Q&A             |
| `hwp_core.doc_agent.DocFillPipeline`   | 채우기 엔진               |
| `hwp_core.doc_reasoner`                | Completion Planner (complete/fill만) |
| `hwp_core.editing` / `hwp_core.shared` | 편집·공유 레이어            |


---

## Features

### Documents

- **파일 추가**: `.hwp` / `.hwpx` 다중 업로드 (세션에 계속 추가)
- **삭제**: 워크스페이스 카드 옆 **×** → 확인 후 제거 (`POST /api/remove_doc`)
- **HWPX**: 편집 가능
- **HWP**: 변환 후 편집 · 가능하면 HWP 저장에 미러
- **저장 · HWPX / HWP** · Ctrl+S ≈ HWPX

### Editing

- 클릭 = 선택, **Ctrl+클릭 = 다중 선택** (문단끼리 또는 셀끼리; 혼합 불가)
- 더블클릭 = 직접 수정 → 서버에 즉시 commit
- 선택 후 채팅 한 번 → 선택한 곳마다 AI 제안
- 제안: **항목별 수락/거절** + **전체 수락 / 전체 거절**
  - `POST /api/select` — `mode: replace|toggle`
  - `POST /api/accept_one` / `POST /api/reject_one` — `change_id`

### DocFill (채우기)

1. **Evidence Fill** (우선): 참고 문서에서 근거를 찾아 제안 · 출처 표시
2. **Context Fill** (폴백): 참고가 없거나 근거가 없으면 **현재 문서만**으로 초안 · `AI Draft (Generated from current document context)` 표시
3. 두 모드 모두 실패할 때만 오류 · **자동 반영 없음** (제안 → 검토 → 수락)

---

## Notes

- HWPX는 ZIP(`PK…`). 파일로 저장해 한글에서 열기.
- DocFill은 Evidence → Context 순으로 자동 선택. Context 초안은 수락 전까지 문서에 쓰지 않음.
- Product A/B 안내는 저장소의 `PRODUCT_B_UX_VALIDATION.md` 참고.

