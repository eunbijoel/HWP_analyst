# Experiment Log — HWP v2

## 2026-07-15

### Goal

Inline AI–style UX feasibility on a Flask workspace (local Ollama), separate from Streamlit `app.py`.

### Built / fixed

- Workspace UI: left document · right AI · settings collapsible
- Multi-document slots + Excel as reference
- Direct edit commit path aligned with v1 (XML in place, export verification)
- HWP working-bytes mirror on edit; HWPX export dirty-section harden
- Chat: selection rewrite · no-selection fill/Q&A across docs
- Removed hardcoded fake name/title fills from `cell_ai.py`

### Learned

- Multi-document + Excel wiring is feasible by reusing v1 `QAEngine` / `DocFillPipeline`
- Editing/export can work if we stop mixing propose/track-changes and “replay onto original HWP”
- UX is still rough vs commercial Inline AI; paragraph numbers feel like a code view
- Product direction is unclear if v1 and v2 are both “semi-working”

### Next

- Decide whether v2 replaces v1, stays experiment, or merges selected UX into Streamlit
- Keep `main` stable (`app.py`) until that decision
- Optional: Draft PR from this branch for review only — **do not merge** without decision
