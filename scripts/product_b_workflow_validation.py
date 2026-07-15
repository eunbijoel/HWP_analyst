#!/usr/bin/env python3
"""
Real-world Product B workflow validation (API-level researcher simulation).

Does not change architecture. Writes JSON report under data/validation/.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hwp_core.doc_agent.fixtures import make_minimal_hwpx, make_staff_xlsx  # noqa: E402
from hwp_core.hwpx_editor import HWPXEditor  # noqa: E402

BASE = os.environ.get("HWP_B_URL", "http://127.0.0.1:8765")
MODEL = os.environ.get("HWP_B_MODEL", "gemma3:4b")  # faster for bulk rewrite UX timing
OUT = ROOT / "data" / "validation"
OUT.mkdir(parents=True, exist_ok=True)


PARAS = [
    "데이터 스페이스 기반 공공 연구데이터 활용체계 연구",
    "1. 연구개발의 필요성",
    "공공·민간 연구데이터가 기관별로 분산되어 있어 재활용과 검증이 어렵다.",
    "데이터 스페이스는 신뢰 가능한 데이터 교환·활용을 위한 공통 공간이다.",
    "본 연구는 HWP 계획서·예산서의 오류를 줄이기 위한 문서 지능을 목표로 한다.",
    "2. 연구개발 목표",
    "목표는 HWP/HWPX 문서에서 사실을 추출하고 규칙으로 검증하는 체계를 구축한다.",
    "□",
    "3. 연구 내용 및 방법",
    "문서 파싱, 온톨로지 그라운딩, 규칙 검증, 멀티문서 비교를 단계적으로 수행한다.",
    "표 합계·소계 일치와 인건비·연구비 항목 정합성을 점검한다.",
    "4. 기대효과",
    "□",
    "연구행정 문서의 재작성 부담을 줄인다.",
    "5. 추진체계",
    "연구책임자와 실무연구원이 역할 분담하여 문서 워크플로를 운영한다.",
    "6. 예산 개요",
    "총사업비는 단계별 연구비와 인건비로 구성된다.",
    "연구비 세부 내역은 표1을 기준으로 한다.",
    "7. 향후 계획",
    "파일럿 문서에 편집 보조를 적용하고 사용성 피드백을 수집한다.",
    "최종적으로 분석 제품과 편집 제품을 분리 운영한다.",
]

TABLE = [
    ["항목", "금액(원)", "비고"],
    ["인건비", "48000000", ""],
    ["연구활동비", "12000000", ""],
    ["재료비", "8000000", ""],
    ["여비", "3000000", ""],
    ["간접비", "5000000", ""],
    ["합계", "", "자동 확인"],
]

REF_PARAS = [
    "참고: 기관 내부 지침",
    "데이터 스페이스란 참여자 간 합의된 규칙으로 데이터를 공유·활용하는 연합 공간이다.",
    "연구개발 목표는 문서 오류 자동탐지와 편집 보조의 상용화 가능성을 검증하는 것이다.",
    "기대효과는 검토 시간 50% 단축과 재작업 비용 절감이다.",
    "인건비 합계는 48,000,000원, 연구비(활동+재료+여비)는 23,000,000원으로 책정한다.",
]


def timed(label: str, fn):
    t0 = time.perf_counter()
    try:
        result = fn()
        ok, detail = True, result
    except Exception as e:
        ok, detail = False, str(e)
    ms = (time.perf_counter() - t0) * 1000
    return {"label": label, "ok": ok, "ms": round(ms, 1), "detail": detail}


def api(method: str, path: str, **kwargs):
    r = requests.request(method, BASE + path, timeout=kwargs.pop("timeout", 180), **kwargs)
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:500]}
    if not r.ok:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {data}")
    return data


def main():
    report = {
        "base": BASE,
        "model": MODEL,
        "steps": [],
        "timings_ms": [],
        "counts": {},
        "persistence": {},
        "failures": [],
        "ux_observations": [],
    }

    # Health
    h = timed("health", lambda: api("GET", "/api/health"))
    report["timings_ms"].append({k: h[k] for k in ("label", "ok", "ms")})
    if not h["ok"]:
        report["failures"].append(h)
        _write(report)
        return report

    target = make_minimal_hwpx(PARAS, [TABLE])
    ref = make_minimal_hwpx(REF_PARAS)
    xlsx = make_staff_xlsx()
    (OUT / "src_plan.hwpx").write_bytes(target)
    (OUT / "src_ref.hwpx").write_bytes(ref)
    (OUT / "src_staff.xlsx").write_bytes(xlsx)

    # Upload multi-doc
    def upload():
        files = [
            ("files", ("rd_plan.hwpx", target, "application/octet-stream")),
            ("files", ("ref_guide.hwpx", ref, "application/octet-stream")),
            ("files", ("staff.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
        ]
        return api("POST", "/api/upload", files=files)

    u = timed("upload_three_docs", upload)
    report["timings_ms"].append({k: u[k] for k in ("label", "ok", "ms")})
    if not u["ok"]:
        report["failures"].append(u)
        _write(report)
        return report
    state = u["detail"]
    sid = state["session_id"]
    docs = state.get("docs") or []
    report["counts"]["uploaded_docs"] = len(docs)
    report["ux_observations"].append(
        f"업로드 후 docs={len(docs)}, active={state.get('filename')}, "
        f"pending={len(state.get('pending') or [])}"
    )

    # Activate editable plan
    plan_id = next((d["id"] for d in docs if d.get("filename") == "rd_plan.hwpx"), None)
    if plan_id:
        act = timed(
            "set_active_plan",
            lambda: api(
                "POST",
                "/api/set_active",
                json={"session_id": sid, "doc_id": plan_id},
            ),
        )
        report["timings_ms"].append({k: act[k] for k in ("label", "ok", "ms")})
        state = act["detail"] if act["ok"] else state

    # --- Explain without modify (selection + question) ---
    api(
        "POST",
        "/api/select",
        json={"session_id": sid, "kind": "paragraph", "paragraph_index": 3},
    )
    explain = timed(
        "explain_selected_no_edit",
        lambda: api(
            "POST",
            "/api/chat",
            json={
                "session_id": sid,
                "message": "데이터 스페이스가 뭐야?",
                "model": MODEL,
                "ollama_url": "http://localhost:11434",
            },
            timeout=240,
        ),
    )
    report["timings_ms"].append({k: explain[k] for k in ("label", "ok", "ms")})
    if explain["ok"]:
        st = explain["detail"]
        pend_n = len(st.get("pending") or [])
        reply = ""
        chat = st.get("chat") or []
        if chat:
            reply = chat[-1].get("content") or ""
        report["steps"].append({
            "case": "explain_selected",
            "pending_after": pend_n,
            "reply_preview": reply[:180],
            "mutated": pend_n > 0,
        })
        if pend_n > 0:
            report["failures"].append("explain created pending edits (should not)")
            report["ux_observations"].append(
                "CRITICAL: explanatory question created edit proposals"
            )
        else:
            report["ux_observations"].append(
                "Explain-on-selection answered without pending proposals"
            )
        # Clear selection for next steps
        state = st
    else:
        report["failures"].append(explain)

    # --- AI rewrite 20 paragraphs (select + short rewrite command) ---
    rewrite_ok = 0
    rewrite_times = []
    for i in range(20):
        # ensure selection on para i
        api(
            "POST",
            "/api/select",
            json={"session_id": sid, "kind": "paragraph", "paragraph_index": i},
        )
        msg = "문장을 더 명확하고 짧게 다듬어줘" if i % 2 == 0 else "공문체로 짧게 수정해줘"

        def do_rw(idx=i, message=msg):
            return api(
                "POST",
                "/api/chat",
                json={
                    "session_id": sid,
                    "message": message,
                    "model": MODEL,
                    "ollama_url": "http://localhost:11434",
                },
                timeout=300,
            )

        rw = timed(f"ai_rewrite_para_{i+1}", do_rw)
        rewrite_times.append(rw["ms"])
        report["timings_ms"].append({k: rw[k] for k in ("label", "ok", "ms")})
        if rw["ok"]:
            st = rw["detail"]
            pend = st.get("pending") or []
            # Accept immediately to accumulate real XML commits like a careful researcher
            if pend:
                acc = api("POST", "/api/accept_all", json={"session_id": sid})
                st = acc
                rewrite_ok += 1
            else:
                report["failures"].append(f"rewrite para {i+1}: no pending proposal")
            state = st
        else:
            report["failures"].append(rw)
        # small pacing
        time.sleep(0.05)

    report["counts"]["ai_rewrites_ok"] = rewrite_ok
    report["counts"]["ai_rewrite_median_ms"] = (
        sorted(rewrite_times)[len(rewrite_times) // 2] if rewrite_times else None
    )
    report["counts"]["ai_rewrite_mean_ms"] = (
        round(sum(rewrite_times) / len(rewrite_times), 1) if rewrite_times else None
    )
    report["ux_observations"].append(
        f"AI rewrite×20: ok={rewrite_ok}/20 mean={report['counts']['ai_rewrite_mean_ms']}ms "
        f"median={report['counts']['ai_rewrite_median_ms']}ms "
        "(each needs: click select + type + send + accept — 4 actions)"
    )

    # --- Edit 10 table cells (direct inline API = dblclick blur path) ---
    cell_ok = 0
    cell_times = []
    # edit data cells in table 0: rows 1..5 col1, and a few notes
    targets = [
        (0, 1, 1, "50,000,000"),
        (0, 2, 1, "15,000,000"),
        (0, 3, 1, "9,000,000"),
        (0, 4, 1, "3,500,000"),
        (0, 5, 1, "5,500,000"),
        (0, 1, 2, "책임급 기준"),
        (0, 2, 2, "실험·장비"),
        (0, 3, 2, "시약"),
        (0, 4, 2, "국내출장"),
        (0, 6, 1, "83,000,000"),  # 합계
    ]
    for t, r, c, text in targets:
        def do_cell(tt=t, rr=r, cc=c, tx=text):
            return api(
                "POST",
                "/api/edit_cell",
                json={"session_id": sid, "t": tt, "r": rr, "c": cc, "text": tx},
            )

        ce = timed(f"edit_cell_{t}_{r}_{c}", do_cell)
        cell_times.append(ce["ms"])
        report["timings_ms"].append({k: ce[k] for k in ("label", "ok", "ms")})
        if ce["ok"]:
            cell_ok += 1
            state = ce["detail"]
        else:
            report["failures"].append(ce)
    report["counts"]["cell_edits_ok"] = cell_ok
    report["counts"]["cell_edit_mean_ms"] = (
        round(sum(cell_times) / len(cell_times), 1) if cell_times else None
    )
    report["ux_observations"].append(
        f"Direct cell edits {cell_ok}/10 mean={report['counts']['cell_edit_mean_ms']}ms "
        "(UI path: click + dblclick + type + blur; no accept button)"
    )

    # --- Fill missing fields with reference docs ---
    # Clear selection so fill route runs
    # (API has no clear-select; select a dummy then chat without relying on selection —
    #  decide_chat_route uses has_selection from session. Select nothing by selecting
    #  and then sending fill — server uses sess.selected_* . Workaround: select cell then
    #  user said fill without selection. Call select with invalid? Use set_active to keep
    #  selection. Looking at select API...)
    # Force no selection via selecting paragraph then overwriting session is hard.
    # Call chat with fill — if selection still set, may rewrite instead.
    # Re-select none: POST select with kind that clears? read select_target.
    fill = timed(
        "docfill_with_refs",
        lambda: _fill_without_selection(sid),
    )
    report["timings_ms"].append({k: fill[k] for k in ("label", "ok", "ms")})
    if fill["ok"]:
        st = fill["detail"]
        pend = st.get("pending") or []
        reply = (st.get("chat") or [{}])[-1].get("content", "")
        report["steps"].append({
            "case": "docfill",
            "pending": len(pend),
            "reply": reply[:240],
            "auto_applied_hint": ("반영" in reply) or ("✅" in reply),
        })
        if pend:
            api("POST", "/api/accept_all", json={"session_id": sid})
            report["ux_observations"].append(
                f"DocFill produced {len(pend)} proposals; accepted"
            )
        elif "반영" in reply or "✅" in reply:
            report["ux_observations"].append(
                "DocFill auto-applied without propose/accept — researcher cannot review before write"
            )
        else:
            report["ux_observations"].append(
                "DocFill returned no pending proposals — researcher may think fill failed"
            )
        state = api("GET", f"/api/session/{sid}")
    else:
        report["failures"].append(fill)

    # --- Export HWPX and reopen ---
    exp = timed(
        "export_hwpx_b64",
        lambda: api("GET", f"/api/export/{sid}?fmt=hwpx", timeout=60),
    )
    report["timings_ms"].append({k: exp[k] for k in ("label", "ok", "ms")})
    if not exp["ok"]:
        report["failures"].append(exp)
        _write(report)
        return report

    b64 = exp["detail"].get("b64") or ""
    exported = base64.b64decode(b64)
    (OUT / "exported_after_edits.hwpx").write_bytes(exported)
    report["persistence"]["export_bytes"] = len(exported)

    # Verify content inside export
    ed = HWPXEditor(exported)
    paras = [p["text"] for p in ed.get_paragraphs()]
    rows = ed.get_table_as_rows(0) if ed.get_table_count() else []
    report["persistence"]["reopened_para_count"] = len(paras)
    report["persistence"]["sample_paras"] = paras[:5]
    report["persistence"]["table_row_1"] = rows[1] if len(rows) > 1 else None
    report["persistence"]["table_total"] = rows[6] if len(rows) > 6 else None

    # Re-upload exported file as new session
    def reopen():
        return api(
            "POST",
            "/api/upload",
            files=[("files", ("rd_plan_reopen.hwpx", exported, "application/octet-stream"))],
        )

    reo = timed("reupload_exported", reopen)
    report["timings_ms"].append({k: reo[k] for k in ("label", "ok", "ms")})
    if reo["ok"]:
        st2 = reo["detail"]
        html = st2.get("html") or ""
        # Check some edited markers appear in preview / paragraphs via session
        report["persistence"]["reopen_session"] = st2.get("session_id")
        report["persistence"]["reopen_filename"] = st2.get("filename")
        # Direct parse checks
        checks = {
            "cell_50000000_or_formatted": any(
                "50" in str(c) and "000" in str(c).replace(",", "")
                for row in rows for c in row
            ),
            "total_83000000": any("83" in str(c) for row in rows for c in row),
            "para_count_ge_20": len(paras) >= 20,
            "paras_differ_from_original": sum(
                1 for a, b in zip(paras, PARAS) if a != b
            ),
        }
        report["persistence"]["checks"] = checks
        if not checks["para_count_ge_20"]:
            report["failures"].append("reopen lost paragraphs")
        if checks["paras_differ_from_original"] < 10:
            report["failures"].append(
                f"few paragraphs changed after rewrites: {checks['paras_differ_from_original']}"
            )
        report["ux_observations"].append(
            f"Save/reopen: paras_changed={checks['paras_differ_from_original']}/"
            f"{len(list(zip(paras, PARAS)))}, cell50={checks['cell_50000000_or_formatted']}, "
            f"total83={checks['total_83000000']}"
        )

    # Friction notes from UI contract
    report["ux_observations"].extend([
        "AI pane copy still says '멀티문서 Q&A' though Product B redirects analysis to Product A",
        "Accept is only all-or-nothing (전체 수락/거절) — no per-proposal accept",
        "Selection required before AI rewrite; no-selection edits use search but UI does not show pickable candidate buttons",
        "Dual save buttons (HWPX/HWP) without indicating which one is 'the' research artifact",
        "Empty state examples still show analysis questions ('사업비는?') that now redirect",
        "Pending badge does not show before/after diff inline; must hunt yellow highlights in paper",
        "Ctrl+S saves HWPX silently via download — easy to miss confirmation",
        "Ollama model buried under Settings details — easy to run on huge model by accident",
    ])

    _write(report)
    return report


def _fill_without_selection(sid: str):
    # Clear selection so fill is not treated as rewrite-on-selection
    api(
        "POST",
        "/api/select",
        json={"session_id": sid, "kind": "paragraph", "paragraph_index": None},
    )
    return api(
        "POST",
        "/api/chat",
        json={
            "session_id": sid,
            "message": "빈칸을 참고 문서로 채워줘",
            "model": MODEL,
            "ollama_url": "http://localhost:11434",
        },
        timeout=360,
    )


def _write(report: dict):
    path = OUT / "product_b_workflow_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "wrote": str(path),
        "failures": len(report.get("failures") or []),
        "counts": report.get("counts"),
        "persistence": report.get("persistence", {}).get("checks"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
