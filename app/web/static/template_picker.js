/* ASGL v13.5 — Template Picker (web)
 *
 * Features
 * - Upload workbook to tenant-scoped staging area
 * - Render a light sheet preview grid
 * - Click to select a start and end cell (A1 range)
 * - Add mappings: label + sheet + range + aggregation
 * - Preview extraction result before saving
 * - Save template to DB + local JSON audit trail
 */

(function () {
  let apiKey = "";
  let uploadId = null;
  let sheet = null;

  let startCell = null;
  let endCell = null;

  let mappings = [];

  const $ = (id) => document.getElementById(id);

  function normalizeKey(raw) {
    const k = (raw || "").trim();
    if (!k) return "";
    // If user pastes "Bearer <key>", keep it
    if (k.toLowerCase().startsWith("bearer ")) return k;
    return k;
  }

  function headers() {
    const k = apiKey;
    if (!k) return {};
    if (k.toLowerCase().startsWith("bearer ")) {
      return { "Authorization": k };
    }
    return { "X-API-Key": k };
  }

  function setStatus(el, msg) {
    $(el).textContent = msg;
  }

  function cellLabel(cell) {
    if (!cell) return "—";
    return `${cell.c}${cell.r}`;
  }

  function rangeLabel() {
    if (!startCell) return "—";
    if (!endCell) return cellLabel(startCell);
    return `${cellLabel(startCell)}:${cellLabel(endCell)}`;
  }

  function clearGridSelection() {
    startCell = null;
    endCell = null;
    document.querySelectorAll(".cell-selected,.cell-range").forEach((n) => {
      n.classList.remove("cell-selected");
      n.classList.remove("cell-range");
    });
    $("sel").textContent = "—";
  }

  function computeRange(a, b) {
    const r1 = Math.min(a.r, b.r), r2 = Math.max(a.r, b.r);
    const c1 = Math.min(a.ci, b.ci), c2 = Math.max(a.ci, b.ci);
    return { r1, r2, c1, c2 };
  }

  function paintRange() {
    document.querySelectorAll(".cell-selected,.cell-range").forEach((n) => {
      n.classList.remove("cell-selected");
      n.classList.remove("cell-range");
    });

    if (!startCell) {
      $("sel").textContent = "—";
      return;
    }

    const startEl = document.querySelector(`[data-row="${startCell.r}"][data-col="${startCell.c}"]`);
    if (startEl) startEl.classList.add("cell-selected");

    if (!endCell) {
      $("sel").textContent = rangeLabel();
      return;
    }

    const rng = computeRange(startCell, endCell);
    for (let r = rng.r1; r <= rng.r2; r++) {
      for (let ci = rng.c1; ci <= rng.c2; ci++) {
        const col = indexToCol(ci);
        const el = document.querySelector(`[data-row="${r}"][data-col="${col}"]`);
        if (el) el.classList.add("cell-range");
      }
    }
    $("sel").textContent = rangeLabel();
  }

  function colToIndex(col) {
    let s = (col || "").trim().toUpperCase();
    if (!s) return 0;
    let n = 0;
    for (let i = 0; i < s.length; i++) {
      const code = s.charCodeAt(i);
      if (code < 65 || code > 90) return 0;
      n = n * 26 + (code - 65 + 1);
    }
    return n;
  }

  function indexToCol(idx) {
    let n = idx;
    if (!n || n <= 0) return "";
    let s = "";
    while (n > 0) {
      let r = (n - 1) % 26;
      s = String.fromCharCode(65 + r) + s;
      n = Math.floor((n - 1) / 26);
    }
    return s;
  }

  function renderMappings() {
    const box = $("mappings");
    box.innerHTML = "";

    if (!mappings.length) {
      box.innerHTML = '<div class="muted">No mappings yet.</div>';
      return;
    }

    mappings.forEach((m, idx) => {
      const div = document.createElement("div");
      div.style.display = "flex";
      div.style.justifyContent = "space-between";
      div.style.alignItems = "center";
      div.style.gap = "10px";
      div.style.padding = "8px 0";
      div.style.borderBottom = "1px solid rgba(255,255,255,0.06)";

      const left = document.createElement("div");
      const a1 = `${m.range.start.c}${m.range.start.r}:${m.range.end.c}${m.range.end.r}`;
      left.innerHTML = `
        <div style="font-weight:900">${escapeHtml(m.label)}</div>
        <div class="muted" style="font-size:12px">${escapeHtml(m.sheet)} • ${escapeHtml(a1)} • ${escapeHtml(m.agg || "auto")}</div>
      `;

      const actions = document.createElement("div");
      actions.style.display = "flex";
      actions.style.gap = "8px";

      const btnEdit = document.createElement("button");
      btnEdit.className = "btn";
      btnEdit.textContent = "Edit";
      btnEdit.onclick = () => {
        $("label").value = m.label;
        $("agg").value = m.agg || "auto";
        // restore selection in grid if current sheet matches
        if (sheet === m.sheet) {
          startCell = { r: m.range.start.r, c: m.range.start.c, ci: colToIndex(m.range.start.c) };
          endCell = { r: m.range.end.r, c: m.range.end.c, ci: colToIndex(m.range.end.c) };
          paintRange();
        }
        // remove old, user re-adds
        mappings = mappings.filter((_, i) => i !== idx);
        renderMappings();
        setStatus("saveStatus", "Loaded mapping into editor. Adjust and click Add mapping.");
      };

      const btn = document.createElement("button");
      btn.className = "btn";
      btn.textContent = "Remove";
      btn.onclick = () => {
        mappings = mappings.filter((_, i) => i !== idx);
        renderMappings();
      };

      actions.appendChild(btnEdit);
      actions.appendChild(btn);
      div.appendChild(left);
      div.appendChild(actions);
      box.appendChild(div);
    });
  }

  function renderGrid(grid) {
    const table = $("grid");
    table.innerHTML = "";
    clearGridSelection();

    // header
    const thead = document.createElement("tr");
    const th0 = document.createElement("th");
    th0.textContent = "";
    thead.appendChild(th0);

    (grid.cols || []).forEach((c) => {
      const th = document.createElement("th");
      th.textContent = c;
      thead.appendChild(th);
    });
    table.appendChild(thead);

    // body
    (grid.rows || []).forEach((r, ri) => {
      const tr = document.createElement("tr");
      const th = document.createElement("th");
      th.textContent = r;
      tr.appendChild(th);

      (grid.cols || []).forEach((c, ci0) => {
        const ci = ci0 + 1;
        const td = document.createElement("td");
        td.textContent = (grid.values && grid.values[ri]) ? (grid.values[ri][ci0] ?? "") : "";
        td.dataset.row = r;
        td.dataset.col = c;

        td.onclick = () => {
          if (!startCell) {
            startCell = { r: r, c: c, ci: ci };
            endCell = null;
          } else if (!endCell) {
            endCell = { r: r, c: c, ci: ci };
          } else {
            // third click starts a new selection
            startCell = { r: r, c: c, ci: ci };
            endCell = null;
          }
          paintRange();
        };
        tr.appendChild(td);
      });

      table.appendChild(tr);
    });
  }

  async function doUpload() {
    apiKey = normalizeKey($("apiKey").value);
    if (!apiKey) {
      setStatus("uploadStatus", "Please paste an API key first.");
      return;
    }

    const f = $("xlsx").files[0];
    if (!f) {
      setStatus("uploadStatus", "Choose an .xlsx file first.");
      return;
    }

    setStatus("uploadStatus", "Uploading...");
    const fd = new FormData();
    fd.append("file", f);

    const res = await fetch("/api/templates/upload", { method: "POST", headers: headers(), body: fd });
    if (!res.ok) {
      setStatus("uploadStatus", "Upload failed: " + (await res.text()));
      return;
    }
    const meta = await res.json();
    uploadId = meta.upload_id;
    setStatus("uploadStatus", `Uploaded: ${meta.filename}  |  upload_id=${uploadId}`);

    // populate sheets
    const sel = $("sheet");
    sel.innerHTML = "";
    (meta.sheets || []).forEach((s) => {
      const o = document.createElement("option");
      o.value = s;
      o.textContent = s;
      sel.appendChild(o);
    });
    sheet = sel.value || null;
    setStatus("saveStatus", "—");
  }

  async function loadSheet() {
    apiKey = normalizeKey($("apiKey").value);
    if (!apiKey) {
      setStatus("uploadStatus", "Please paste an API key first.");
      return;
    }
    if (!uploadId) {
      setStatus("uploadStatus", "Upload a workbook first.");
      return;
    }
    sheet = $("sheet").value;
    if (!sheet) {
      setStatus("uploadStatus", "Select a sheet.");
      return;
    }

    setStatus("uploadStatus", `Loading preview for ${sheet}...`);
    const res = await fetch(`/api/templates/sheet?upload_id=${encodeURIComponent(uploadId)}&sheet=${encodeURIComponent(sheet)}`, { headers: headers() });
    if (!res.ok) {
      setStatus("uploadStatus", "Load sheet failed: " + (await res.text()));
      return;
    }
    const data = await res.json();
    renderGrid(data.grid);
    setStatus("uploadStatus", `Preview loaded for ${sheet}. Select cells to create mappings.`);
  }

  function addMapping() {
    const label = ($("label").value || "").trim();
    const agg = ($("agg").value || "auto").trim();

    if (!label) {
      setStatus("saveStatus", "Provide a label.");
      return;
    }
    if (!uploadId || !sheet || !startCell) {
      setStatus("saveStatus", "Upload + load a sheet and select cells.");
      return;
    }

    const end = endCell || startCell;
    const mapping = {
      label: label,
      sheet: sheet,
      agg: agg,
      range: { start: { c: startCell.c, r: startCell.r }, end: { c: end.c, r: end.r } }
    };

    mappings.push(mapping);
    $("label").value = "";
    renderMappings();
    setStatus("saveStatus", `Added mapping: ${label} → ${rangeLabel()}`);
    clearGridSelection();
    hidePreview();
  }

  function hidePreview() {
    $("previewBox").style.display = "none";
    $("previewTable").innerHTML = "";
  }

  function renderPreview(preview) {
    const table = $("previewTable");
    table.innerHTML = "";

    const head = document.createElement("tr");
    ["Label", "Sheet", "Range", "Agg", "Value", "Notes"].forEach((h) => {
      const th = document.createElement("th");
      th.textContent = h;
      head.appendChild(th);
    });
    table.appendChild(head);

    (preview || []).forEach((r) => {
      const tr = document.createElement("tr");

      const value = (r.error) ? ("Error: " + r.error) : (r.value === null || r.value === undefined ? "" : (typeof r.value === "object" ? JSON.stringify(r.value) : String(r.value)));
      const notes = r.notes || "";

      [r.label || "", r.sheet || "", r.a1 || "", r.agg || "", value, notes].forEach((v) => {
        const td = document.createElement("td");
        td.textContent = String(v);
        tr.appendChild(td);
      });
      table.appendChild(tr);
    });

    $("previewBox").style.display = "block";
  }

  async function previewExtraction() {
    apiKey = normalizeKey($("apiKey").value);
    if (!apiKey) {
      setStatus("saveStatus", "Please paste an API key first.");
      return;
    }
    if (!uploadId) {
      setStatus("saveStatus", "Upload a workbook first.");
      return;
    }
    if (!mappings.length) {
      setStatus("saveStatus", "Add at least one mapping.");
      return;
    }

    setStatus("saveStatus", "Previewing extraction...");
    const res = await fetch("/api/templates/preview", {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, headers()),
      body: JSON.stringify({ upload_id: uploadId, mappings: mappings })
    });

    if (!res.ok) {
      setStatus("saveStatus", "Preview failed: " + (await res.text()));
      return;
    }
    const out = await res.json();
    renderPreview(out.preview || []);
    setStatus("saveStatus", "Preview ready.");
  }

  async function saveTemplate() {
    apiKey = normalizeKey($("apiKey").value);
    if (!apiKey) {
      setStatus("saveStatus", "Please paste an API key first.");
      return;
    }
    const name = ($("templateName").value || "").trim() || "template";
    if (!uploadId) {
      setStatus("saveStatus", "Upload a workbook first.");
      return;
    }
    if (!mappings.length) {
      setStatus("saveStatus", "Add at least one mapping.");
      return;
    }

    setStatus("saveStatus", "Saving template...");
    const res = await fetch("/api/templates/save", {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, headers()),
      body: JSON.stringify({ name: name, upload_id: uploadId, mappings: mappings })
    });
    if (!res.ok) {
      setStatus("saveStatus", "Save failed: " + (await res.text()));
      return;
    }
    const out = await res.json();
    setStatus("saveStatus", `Saved. template_id=${out.template_id}  |  local_template_id=${out.local_template_id}`);
  }

  function escapeHtml(s) {
    return String(s || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  $("btnUpload").onclick = doUpload;
  $("btnLoadSheet").onclick = loadSheet;
  $("btnAdd").onclick = addMapping;
  $("btnClearSel").onclick = clearGridSelection;
  $("btnSave").onclick = saveTemplate;
  $("btnPreview").onclick = previewExtraction;

  $("btnReset").onclick = () => {
    uploadId = null;
    sheet = null;
    mappings = [];
    $("sheet").innerHTML = "";
    $("grid").innerHTML = "";
    hidePreview();
    clearGridSelection();
    renderMappings();
    setStatus("uploadStatus", "No workbook uploaded.");
    setStatus("saveStatus", "—");
  };

  renderMappings();
})();
