(() => {
  const $ = (id) => document.getElementById(id);

  let sessionId = null;
  let selectedParas = []; // number[]
  let selectedCells = []; // {t,r,c}[]
  let pendingSave = Promise.resolve();

  const els = {
    fileInput: $("fileInput"),
    btnDownload: $("btnDownload"),
    btnDownloadHwp: $("btnDownloadHwp"),
    docName: $("docName"),
    pendingBadge: $("pendingBadge"),
    hwpNote: $("hwpNote"),
    emptyState: $("emptyState"),
    docRoot: $("docRoot"),
    docList: $("docList"),
    chatLog: $("chatLog"),
    chatForm: $("chatForm"),
    chatInput: $("chatInput"),
    btnSend: $("btnSend"),
    btnAccept: $("btnAccept"),
    btnReject: $("btnReject"),
    pendingList: $("pendingList"),
    selInfo: $("selInfo"),
    ollamaUrl: $("ollamaUrl"),
    modelSelect: $("modelSelect"),
    btnOllama: $("btnOllama"),
    ollamaStatus: $("ollamaStatus"),
  };

  function cellKey(c) {
    return `${c.t},${c.r},${c.c}`;
  }

  function syncSelectionFromState(state) {
    selectedParas = Array.isArray(state.selected_paras)
      ? state.selected_paras.map(Number)
      : (state.selected_para != null ? [Number(state.selected_para)] : []);
    selectedCells = Array.isArray(state.selected_cells)
      ? state.selected_cells.map((x) => ({ t: x.t, r: x.r, c: x.c }))
      : (state.selected_cell
        ? [{ t: state.selected_cell.t, r: state.selected_cell.r, c: state.selected_cell.c }]
        : []);
  }

  function updateSelInfo() {
    if (!els.selInfo) return;
    if (selectedCells.length) {
      if (selectedCells.length === 1) {
        const { t, r, c } = selectedCells[0];
        const el = els.docRoot.querySelector(`[data-t="${t}"][data-r="${r}"][data-c="${c}"]`);
        const orig = (el && el.getAttribute("data-cell-orig")) || "";
        els.selInfo.textContent =
          `${r + 1}행 ${c + 1}열\n${orig.slice(0, 280) || "(비어 있음)"}`;
      } else {
        const lines = selectedCells
          .slice(0, 6)
          .map(({ r, c }) => `${r + 1}행 ${c + 1}열`);
        const more = selectedCells.length > 6 ? `\n…외 ${selectedCells.length - 6}곳` : "";
        els.selInfo.textContent = `셀 ${selectedCells.length}곳 선택 (Ctrl+클릭)\n${lines.join(" · ")}${more}`;
      }
      return;
    }
    if (selectedParas.length) {
      if (selectedParas.length === 1) {
        const idx = selectedParas[0];
        const el = els.docRoot.querySelector(`.para[data-para-idx="${idx}"]`);
        const orig = (el && el.getAttribute("data-para-orig")) || "";
        els.selInfo.textContent = `문단 ${idx + 1}\n${orig.slice(0, 280)}`;
      } else {
        const lines = selectedParas.slice(0, 8).map((i) => `문단 ${i + 1}`);
        const more = selectedParas.length > 8 ? `\n…외 ${selectedParas.length - 8}` : "";
        els.selInfo.textContent = `문단 ${selectedParas.length}곳 선택 (Ctrl+클릭)\n${lines.join(" · ")}${more}`;
      }
      return;
    }
    els.selInfo.textContent = "없으면 전체 문서로 질문/보완\nCtrl+클릭으로 여러 곳 선택";
  }

  function paintSelection() {
    clearSelectionUI();
    selectedParas.forEach((idx) => {
      const el = els.docRoot.querySelector(`.para[data-para-idx="${idx}"]`);
      if (el) el.classList.add("selected-v2");
    });
    selectedCells.forEach(({ t, r, c }) => {
      const el = els.docRoot.querySelector(`[data-t="${t}"][data-r="${r}"][data-c="${c}"]`);
      if (el) el.classList.add("selected-v2");
    });
    updateSelInfo();
  }

  function renderPending(pending) {
    if (!els.pendingList) return;
    els.pendingList.innerHTML = "";
    const rows = pending || [];
    if (!rows.length) {
      els.pendingList.hidden = true;
      return;
    }
    els.pendingList.hidden = false;
    rows.forEach((p) => {
      const card = document.createElement("div");
      card.className = "pending-item";
      const loc = document.createElement("div");
      loc.className = "pending-loc";
      loc.textContent = p.location || p.type || "제안";
      const body = document.createElement("div");
      body.className = "pending-body";
      body.textContent = (p.new || "").slice(0, 160) || "(내용 없음)";
      const actions = document.createElement("div");
      actions.className = "pending-actions";
      const ok = document.createElement("button");
      ok.type = "button";
      ok.className = "primary small";
      ok.textContent = "수락";
      ok.addEventListener("click", async () => {
        try {
          applyState(await api("/api/accept_one", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: sessionId, change_id: p.id }),
          }));
        } catch (err) {
          alert(err.message);
        }
      });
      const no = document.createElement("button");
      no.type = "button";
      no.className = "danger small";
      no.textContent = "거절";
      no.addEventListener("click", async () => {
        try {
          applyState(await api("/api/reject_one", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: sessionId, change_id: p.id }),
          }));
        } catch (err) {
          alert(err.message);
        }
      });
      actions.appendChild(ok);
      actions.appendChild(no);
      card.appendChild(loc);
      card.appendChild(body);
      card.appendChild(actions);
      els.pendingList.appendChild(card);
    });
  }

  function currentModel() {
    return (els.modelSelect && els.modelSelect.value) || "gemma4";
  }

  function fillModelSelect(models, prefer) {
    const sel = els.modelSelect;
    if (!sel) return;
    const prev = prefer || sel.value;
    sel.innerHTML = "";
    (models || []).forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      sel.appendChild(opt);
    });
    if (!models || !models.length) {
      const opt = document.createElement("option");
      opt.value = "gemma4";
      opt.textContent = "gemma4 (목록 없음)";
      sel.appendChild(opt);
      return;
    }
    const pick =
      (prev && models.includes(prev) && prev) ||
      models.find((m) => m.includes("gemma4")) ||
      models[0];
    sel.value = pick;
  }

  async function api(url, opts = {}) {
    const res = await fetch(url, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }

  function renderChat(chat) {
    els.chatLog.innerHTML = "";
    (chat || []).forEach((m) => {
      const div = document.createElement("div");
      div.className = `bubble ${m.role === "user" ? "user" : "assistant"}`;
      div.textContent = m.content || "";
      els.chatLog.appendChild(div);
    });
    els.chatLog.scrollTop = els.chatLog.scrollHeight;
  }

  function clearSelectionUI() {
    els.docRoot.querySelectorAll(".selected-v2").forEach((el) => el.classList.remove("selected-v2"));
  }

  async function postSelect(payload) {
    if (!sessionId) return;
    try {
      const data = await api("/api/select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, ...payload }),
      });
      syncSelectionFromState(data);
      paintSelection();
    } catch (_) {}
  }

  function wireDocument() {
    const root = els.docRoot;

    root.querySelectorAll(".para-clickable").forEach((p) => {
      p.addEventListener("click", async (e) => {
        if (e.target.closest(".cell-clickable")) return;
        if (e.target.classList.contains("para-editable") && document.activeElement === e.target) return;
        const idx = p.getAttribute("data-para-idx");
        if (idx == null) return;
        const multi = e.ctrlKey || e.metaKey;
        await postSelect({
          kind: "paragraph",
          mode: multi ? "toggle" : "replace",
          paragraph_index: Number(idx),
        });
      });
      p.addEventListener("dblclick", (e) => {
        e.preventDefault();
        const span = p.querySelector(".para-editable");
        if (!span) return;
        span.setAttribute("contenteditable", "true");
        span.focus();
        const range = document.createRange();
        range.selectNodeContents(span);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      });
    });

    root.querySelectorAll(".para-editable").forEach((span) => {
      span.setAttribute("contenteditable", "true");
      span.addEventListener("mousedown", (e) => e.stopPropagation());
      span.addEventListener("blur", () => {
        const p = span.closest(".para");
        const idx = p && p.getAttribute("data-para-idx");
        if (idx == null || !sessionId) return;
        const text = span.innerText.replace(/\u00a0/g, " ").trim();
        const job = api("/api/edit_paragraph", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: sessionId,
            paragraph_index: Number(idx),
            text,
          }),
        }).then((st) => applyState(st)).catch((err) => alert(err.message));
        pendingSave = pendingSave.then(() => job, () => job);
      });
    });

    root.querySelectorAll(".cell-clickable").forEach((td) => {
      td.addEventListener("click", async (e) => {
        e.stopPropagation();
        const t = Number(td.getAttribute("data-t"));
        const r = Number(td.getAttribute("data-r"));
        const c = Number(td.getAttribute("data-c"));
        const multi = e.ctrlKey || e.metaKey;
        await postSelect({
          kind: "cell",
          mode: multi ? "toggle" : "replace",
          t, r, c,
        });
      });
      td.addEventListener("dblclick", (e) => {
        e.preventDefault();
        e.stopPropagation();
        let span = td.querySelector(".cell-editable");
        if (!span) {
          span = document.createElement("span");
          span.className = "cell-editable";
          span.textContent = (td.getAttribute("data-cell-orig") || "").replace(/^\(비어 있음\)$/, "");
          td.textContent = "";
          td.appendChild(span);
        }
        span.setAttribute("contenteditable", "true");
        span.focus();
      });
    });

    root.querySelectorAll(".cell-editable").forEach((span) => {
      span.setAttribute("contenteditable", "true");
      span.addEventListener("click", (e) => e.stopPropagation());
      span.addEventListener("mousedown", (e) => e.stopPropagation());
      span.addEventListener("blur", () => {
        const td = span.closest("[data-t]");
        if (!td || !sessionId) return;
        let text = span.innerText.replace(/\u00a0/g, " ");
        if (text.trim() === "(비어 있음)") text = "";
        const job = api("/api/edit_cell", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: sessionId,
            t: Number(td.getAttribute("data-t")),
            r: Number(td.getAttribute("data-r")),
            c: Number(td.getAttribute("data-c")),
            text,
          }),
        }).then((st) => applyState(st)).catch((err) => alert(err.message));
        pendingSave = pendingSave.then(() => job, () => job);
      });
    });
  }

  async function flushEdits() {
    const active = document.activeElement;
    if (active && active.isContentEditable) active.blur();
    await pendingSave;
  }

  function renderDocList(docs) {
    if (!els.docList) return;
    if (!docs || !docs.length) {
      els.docList.innerHTML =
        '<div class="doc-list-empty">HWP · HWPX를<br>여러 개 올려 보세요</div>';
      return;
    }
    els.docList.innerHTML = "";
    docs.forEach((d) => {
      const row = document.createElement("div");
      row.className = "doc-row";

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "doc-item" + (d.active ? " active" : "");
      const kind = (d.kind || "").toLowerCase();
      const label = kind === "xlsx" ? "DOC" : kind.toUpperCase() || "DOC";
      const meta = d.excel
        ? "참고"
        : d.editable
          ? "편집 가능"
          : "읽기 전용";
      btn.innerHTML =
        `<span class="doc-kind ${kind}">${label}</span>` +
        `<span class="doc-fname">${d.filename || ""}</span>` +
        `<span class="doc-meta">${meta}</span>`;
      btn.addEventListener("click", async () => {
        if (!sessionId || d.active) return;
        try {
          applyState(await api("/api/set_active", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: sessionId, doc_id: d.id }),
          }));
        } catch (err) {
          alert(err.message);
        }
      });

      const del = document.createElement("button");
      del.type = "button";
      del.className = "doc-del";
      del.title = "목록에서 삭제";
      del.setAttribute("aria-label", `${d.filename || "문서"} 삭제`);
      del.textContent = "×";
      del.addEventListener("click", async (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        if (!sessionId) return;
        const name = d.filename || "이 문서";
        if (!confirm(`「${name}」을(를) 워크스페이스에서 제거할까요?`)) return;
        try {
          applyState(await api("/api/remove_doc", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: sessionId, doc_id: d.id }),
          }));
        } catch (err) {
          alert(err.message);
        }
      });

      row.appendChild(btn);
      row.appendChild(del);
      els.docList.appendChild(row);
    });
  }

  function applyState(state) {
    if (!state) return;
    sessionId = state.session_id;

    const hasDocs = (state.doc_count || 0) > 0 || !!(state.html || state.filename);
    els.emptyState.hidden = hasDocs;
    els.docRoot.hidden = !hasDocs;
    els.docRoot.innerHTML = state.html || "";
    els.docName.textContent = state.filename
      ? `${state.filename}${state.doc_count > 1 ? ` · 외 ${state.doc_count - 1}` : ""}`
      : "문서를 열어 주세요";
    els.btnDownload.disabled = !sessionId || !!state.read_only;
    els.btnDownloadHwp.disabled = !sessionId;
    els.btnSend.disabled = !sessionId;

    if (state.source_was_hwp && !state.read_only) {
      els.hwpNote.classList.remove("hidden");
      els.hwpNote.textContent = `HWP→HWPX (${state.convert_note || "변환됨"})`;
    } else if (state.read_only && state.filename) {
      els.hwpNote.classList.remove("hidden");
      els.hwpNote.textContent = "읽기 전용";
    } else {
      els.hwpNote.classList.add("hidden");
    }

    const n = state.pending_count || 0;
    if (n > 0) {
      els.pendingBadge.classList.remove("hidden");
      els.pendingBadge.textContent = `제안 ${n}`;
      els.btnAccept.disabled = false;
      els.btnReject.disabled = false;
    } else {
      els.pendingBadge.classList.add("hidden");
      els.btnAccept.disabled = true;
      els.btnReject.disabled = true;
    }

    if (state.ollama_url) els.ollamaUrl.value = state.ollama_url;
    if (state.model && els.modelSelect) {
      const existing = [...els.modelSelect.options].map((o) => o.value).filter(Boolean);
      if (!existing.includes(state.model)) {
        fillModelSelect(existing.length ? existing.concat(state.model) : [state.model], state.model);
      } else {
        els.modelSelect.value = state.model;
      }
    }

    renderDocList(state.docs || []);
    renderChat(state.chat);
    renderPending(state.pending || []);
    wireDocument();
    syncSelectionFromState(state);
    paintSelection();
  }

  els.fileInput.addEventListener("change", async () => {
    const files = els.fileInput.files;
    if (!files || !files.length) return;
    const fd = new FormData();
    for (let i = 0; i < files.length; i++) {
      fd.append("files", files[i]);
    }
    if (sessionId) fd.append("session_id", sessionId);
    fd.append("ollama_url", els.ollamaUrl.value.trim());
    fd.append("model", currentModel());
    els.docName.textContent = "여는 중…";
    try {
      applyState(await api("/api/upload", { method: "POST", body: fd }));
    } catch (err) {
      alert(err.message);
      els.docName.textContent = "문서를 열어 주세요";
    }
    els.fileInput.value = "";
  });

  els.chatInput.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
    e.preventDefault();
    if (els.btnSend.disabled) return;
    els.chatForm.requestSubmit();
  });

  els.chatForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!sessionId) return;
    const message = els.chatInput.value.trim();
    if (!message) return;
    els.btnSend.disabled = true;
    els.chatInput.value = "";
    try {
      applyState(await api("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          message,
          ollama_url: els.ollamaUrl.value.trim(),
          model: currentModel(),
        }),
      }));
    } catch (err) {
      alert(err.message);
    } finally {
      els.btnSend.disabled = !sessionId;
      els.chatInput.focus();
    }
  });

  els.btnAccept.addEventListener("click", async () => {
    if (!sessionId) return;
    try {
      applyState(await api("/api/accept_all", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      }));
    } catch (err) {
      alert(err.message);
    }
  });

  els.btnReject.addEventListener("click", async () => {
    if (!sessionId) return;
    try {
      applyState(await api("/api/reject_all", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      }));
    } catch (err) {
      alert(err.message);
    }
  });

  async function downloadFmt(fmt) {
    if (!sessionId) return;
    await flushEdits();
    const res = await fetch(`/api/export/${sessionId}?fmt=${fmt}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.error) {
      throw new Error(data.error || `${fmt.toUpperCase()} 저장 실패`);
    }
    if (!data.b64) throw new Error("파일 데이터가 비어 있습니다");
    if (fmt === "hwpx" && data.magic !== "PK") {
      throw new Error("HWPX가 아닙니다. 서버를 재시작하세요.");
    }
    const bin = atob(data.b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const blob = new Blob([bytes], { type: "application/octet-stream" });
    const fname = data.filename || `edited.${fmt === "hwp" ? "hwp" : "hwpx"}`;

    // Chrome: 저장 대화상자 (탭에 PK 안 뜸)
    if (window.showSaveFilePicker) {
      try {
        const handle = await window.showSaveFilePicker({
          suggestedName: fname,
          types: [{
            description: fmt === "hwp" ? "HWP" : "HWPX",
            accept: { "application/octet-stream": [`.${fmt === "hwp" ? "hwp" : "hwpx"}`] },
          }],
        });
        const writable = await handle.createWritable();
        await writable.write(blob);
        await writable.close();
        return;
      } catch (e) {
        if (e && e.name === "AbortError") return; // 사용자가 취소
        // fall through
      }
    }

    // 일반 다운로드
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = fname;
    a.rel = "noopener";
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
  }

  els.btnDownload.addEventListener("click", async () => {
    try {
      await downloadFmt("hwpx");
    } catch (err) {
      alert(err.message);
    }
  });

  els.btnDownloadHwp.addEventListener("click", async () => {
    try {
      await downloadFmt("hwp");
    } catch (err) {
      alert(err.message);
    }
  });

  document.addEventListener("keydown", async (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      if (!sessionId) return;
      try {
        await downloadFmt("hwpx");
      } catch (err) {
        alert(err.message);
      }
    }
  });

  els.btnOllama.addEventListener("click", async () => {
    const url = encodeURIComponent(els.ollamaUrl.value.trim());
    try {
      const st = await api(`/api/ollama?url=${url}`);
      if (st.status === "running") {
        els.ollamaStatus.textContent = `연결 · ${st.models.length}개`;
        fillModelSelect(st.models || [], currentModel());
      } else {
        els.ollamaStatus.textContent = "미연결";
      }
    } catch (err) {
      els.ollamaStatus.textContent = err.message;
    }
  });

  els.btnOllama.click();
})();
