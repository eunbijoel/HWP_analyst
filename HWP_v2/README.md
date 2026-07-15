# HWP v2 (Experimental)

**Status: Experimental prototype — not production ready**

## Purpose

- Workspace UI (document pane + AI pane)
- Multi-document editing workflow (HWP / HWPX / Excel)
- Inline editing experiment (select · rewrite · accept/reject)
- Local Ollama (not Claude)

## Not intended to replace the current Streamlit application

| Production (stable) | This branch (experiment) |
|---------------------|---------------------------|
| `app.py` (Streamlit) | `HWP_v2/` (Flask) |
| Review rules · fill · Q&A | Inline UI · multi-doc workspace |

Current production version: **`app.py`** on `main`.

Do **not** merge this branch into `main` until a product decision is made (v1 stays vs v2 replaces / merges).

---

## Run

```bash
cd "/home/eunbi/HWP analysis"
python3 HWP_v2/server.py
```

→ http://127.0.0.1:8765

## What works (as of 2026-07-15)

- HWPX open · direct edit · export (save must match on-screen text)
- HWP open via convert; working HWP mirror on edit when possible
- Excel as reference · multi-doc Q&A / “보완해줘” fill path
- Paragraph/cell select · Ollama rewrite · accept/reject
- Settings (Ollama URL / model list) collapsed in header

## What does **not** (or poorly)

- Native Hangul.exe rendering (Linux → HTML preview only)
- Fake placeholder names/titles (removed on purpose)
- Polished UX comparable to commercial Inline AI
- Clear long-term product direction vs v1

## Notes

- Engine reuses `hwp_core`, `DocFillPipeline`, `QAEngine`
- HWPX is a ZIP (`PK…`) — save as file and open in Hangul
- See `EXPERIMENT.md` for the day log
