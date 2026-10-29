/* ==========================================================================
   CodeDetector — Ön Yüz Uygulama Mantığı (Vanilla JS)
   ========================================================================== */
(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // ------------------------------ Durum -----------------------------------
  const state = {
    mode: "code", // "code" | "diff" | "upload"
    uploadKind: "pair", // "pair" | "patch"
    modelStatus: null,
    sampleData: null,
    currentReport: null,
    abortCtrl: null,
    timerId: null,
  };

  // ------------------------------ Sabitler --------------------------------
  const SEV_META = {
    critical: { label: "Kritik", group: "Kritik Riskler" },
    high: { label: "Yüksek", group: "Yüksek Riskler" },
    medium: { label: "Orta", group: "Orta Düzey Riskler" },
    low: { label: "Düşük", group: "Düşük Düzey / Uç Durumlar" },
    info: { label: "Bilgi", group: "Bilgilendirici Gözlemler" },
    none: { label: "Belirgin Risk Yok", group: "" },
  };

  const EV_META = {
    confirmed: { label: "Doğrulanmış gözlem", cls: "ev-confirmed" },
    plausible: { label: "Doğrulanmalı risk", cls: "ev-plausible" },
    unknown: { label: "Bilinmeyen etki", cls: "ev-unknown" },
  };

  const CONF_META = {
    high: { label: "Yüksek güven", bars: 3 },
    medium: { label: "Orta güven", bars: 2 },
    low: { label: "Düşük güven", bars: 1 },
  };

  const PRIO_META = {
    high: "Yüksek Öncelik",
    medium: "Orta Öncelik",
    low: "Düşük Öncelik",
  };

  const CAT_META = {
    regression: "Regresyon",
    unit: "Birim",
    integration: "Entegrasyon",
    edge_case: "Uç Durum",
    security: "Güvenlik",
    manual: "Manuel Doğrulama",
  };

  const STATUS_META = {
    pending: "Bekliyor",
    passed: "Geçti",
    failed: "Kaldı",
    blocked: "Engellendi",
  };

  const KIND_META = {
    function: "Fonksiyon",
    class: "Sınıf",
    method: "Metot",
    module: "Modül",
    interface: "Arayüz",
    api: "API",
    database: "Veritabanı",
    config: "Yapılandırma",
    variable: "Değişken",
    other: "Bileşen",
  };

  // ------------------------- Güvenli HTML / Yardımcılar -------------------
  function esc(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  let toastTimer = null;
  function showToast(msg) {
    const el = $("#toast");
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      el.hidden = true;
    }, 3200);
  }

  // ----------------------- Hafif Sözdizimi Vurgulama ----------------------
  const KW_RE =
    /\b(def|class|return|if|elif|else|for|while|try|except|finally|raise|import|from|as|with|pass|break|continue|lambda|yield|async|await|function|const|let|var|new|switch|case|default|throw|catch|interface|type|extends|implements|public|private|protected|static|final|void|int|float|double|boolean|char|string|struct|enum|impl|fn|pub|mut|use|mod|match|package|func|defer|go|select|namespace|using|SELECT|INSERT|UPDATE|DELETE|FROM|WHERE|JOIN|AND|OR|NOT|CREATE|TABLE)\b/g;
  const LIT_RE = /\b(True|False|None|true|false|null|undefined|nil|self|this)\b/g;
  const NUM_RE = /\b(\d+(?:\.\d+)?)\b/g;
  const FN_RE = /\b([A-Za-z_]\w*)(?=\s*\()/g;

  const TOKEN_MASTER =
    /(#[^\n]*|\/\/[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)/g;

  function highlightSegment(seg) {
    let s = esc(seg);
    s = s.replace(KW_RE, '<span class="tok-kw">$1</span>');
    s = s.replace(LIT_RE, '<span class="tok-lit">$1</span>');
    s = s.replace(NUM_RE, '<span class="tok-num">$1</span>');
    s = s.replace(FN_RE, '<span class="tok-fn">$1</span>');
    return s;
  }

  function highlightCode(text, lang) {
    if (!text) return "";
    if (lang === "diff") {
      return text
        .split("\n")
        .map((line) => {
          const e = esc(line);
          if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff "))
            return `<span class="tok-dmeta">${e}</span>`;
          if (line.startsWith("@@")) return `<span class="tok-dhunk">${e}</span>`;
          if (line.startsWith("+")) return `<span class="tok-dadd">${e}</span>`;
          if (line.startsWith("-")) return `<span class="tok-ddel">${e}</span>`;
          return e;
        })
        .join("\n");
    }
    let out = "";
    let last = 0;
    TOKEN_MASTER.lastIndex = 0;
    let m;
    while ((m = TOKEN_MASTER.exec(text)) !== null) {
      if (m.index > last) {
        out += highlightSegment(text.slice(last, m.index));
      }
      if (m[1]) {
        out += `<span class="tok-com">${esc(m[1])}</span>`;
      } else if (m[2]) {
        out += `<span class="tok-str">${esc(m[2])}</span>`;
      }
      last = TOKEN_MASTER.lastIndex;
    }
    if (last < text.length) {
      out += highlightSegment(text.slice(last));
    }
    return out;
  }

  // ---------------------------- Kod Editörleri ----------------------------
  function initEditors() {
    $$("[data-editor]").forEach((wrap) => {
      const ta = $("textarea", wrap);
      const code = $("pre.highlight code", wrap);
      const pre = $("pre.highlight", wrap);
      const gutter = $(".gutter", wrap);
      const fixedLang = wrap.dataset.lang || null;

      function update() {
        const val = ta.value;
        const lines = val.split("\n").length;
        const nums = [];
        for (let i = 1; i <= lines; i++) nums.push(i);
        gutter.textContent = nums.join("\n");

        if (val.length > 40000) {
          wrap.classList.add("plain");
        } else {
          wrap.classList.remove("plain");
          const lang = fixedLang || $("#language").value;
          code.innerHTML = highlightCode(val, lang) + (val.endsWith("\n") ? " " : "");
        }
        syncScroll();
      }

      function syncScroll() {
        pre.scrollTop = ta.scrollTop;
        pre.scrollLeft = ta.scrollLeft;
        gutter.scrollTop = ta.scrollTop;
      }

      ta.addEventListener("input", () => {
        update();
        checkSampleDirty();
      });
      ta.addEventListener("scroll", syncScroll);
      ta.addEventListener("keydown", (e) => {
        if (e.key === "Tab" && !e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          const start = ta.selectionStart;
          const end = ta.selectionEnd;
          ta.setRangeText("    ", start, end, "end");
          update();
        }
      });

      wrap._updateEditor = update;
      update();
    });

    $("#language").addEventListener("change", () => {
      $$("[data-editor]").forEach((w) => w._updateEditor && w._updateEditor());
    });
  }

  function refreshAllEditors() {
    $$("[data-editor]").forEach((w) => w._updateEditor && w._updateEditor());
  }

  function isSampleCurrentlyUnmodified() {
    if (!state.sampleData || state.mode !== "code") return false;
    const norm = (s) => (s || "").replace(/\r\n/g, "\n").trim();
    return (
      norm($("#old-code").value) === norm(state.sampleData.old_code) &&
      norm($("#new-code").value) === norm(state.sampleData.new_code)
    );
  }

  function checkSampleDirty() {
    const lbl = $("#sample-label");
    if (!lbl) return;
    lbl.hidden = !isSampleCurrentlyUnmodified();
  }

  // ------------------------------ Sekmeler --------------------------------
  function switchTab(mode) {
    state.mode = mode;
    $$(".tab").forEach((btn) => {
      const active = btn.dataset.mode === mode;
      btn.setAttribute("aria-selected", active ? "true" : "false");
      btn.tabIndex = active ? 0 : -1;
    });
    $("#panel-code").hidden = mode !== "code";
    $("#panel-diff").hidden = mode !== "diff";
    $("#panel-upload").hidden = mode !== "upload";
    $("#filename-field").hidden = mode !== "code";
    hideInputError();
    checkSampleDirty();
  }

  function initTabs() {
    const tabs = $$(".tab");
    tabs.forEach((btn, idx) => {
      btn.addEventListener("click", () => switchTab(btn.dataset.mode));
      btn.addEventListener("keydown", (e) => {
        if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
          e.preventDefault();
          const next = (idx + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
          tabs[next].focus();
          switchTab(tabs[next].dataset.mode);
        }
      });
    });
  }

  // --------------------------- Dosya Yükleme ------------------------------
  function initUploads() {
    $$('input[name="upload-kind"]').forEach((radio) => {
      radio.addEventListener("change", () => {
        state.uploadKind = radio.value;
        $("#upload-pair").hidden = state.uploadKind !== "pair";
        $("#upload-patch").hidden = state.uploadKind !== "patch";
        hideInputError();
      });
    });

    $$(".dropzone").forEach((dz) => {
      const input = $("input[type=file]", dz);
      const nameEl = $(".dz-name", dz);

      function updateFileDisplay() {
        const file = input.files && input.files[0];
        if (file) {
          const kb = Math.max(1, Math.round(file.size / 1024));
          nameEl.textContent = `${file.name} (${kb} KB)`;
          dz.classList.add("has-file");
        } else {
          nameEl.textContent = "Dosya seçin veya buraya sürükleyin";
          dz.classList.remove("has-file");
        }
        hideInputError();
      }

      input.addEventListener("change", updateFileDisplay);
      dz.addEventListener("dragover", (e) => {
        e.preventDefault();
        dz.classList.add("dragover");
      });
      dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
      dz.addEventListener("drop", (e) => {
        e.preventDefault();
        dz.classList.remove("dragover");
        if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
          input.files = e.dataTransfer.files;
          updateFileDisplay();
        }
      });
      dz._resetDisplay = () => {
        input.value = "";
        updateFileDisplay();
      };
    });
  }

  // ------------------------ Model Durumu (Gemma 4) ------------------------
  async function checkModelStatus(manual = false) {
    const btn = $("#engine-status");
    const dot = $(".dot", btn);
    const detail = $(".engine-detail", btn);
    dot.className = "dot dot-checking";
    detail.textContent = "kontrol ediliyor…";

    try {
      const res = await fetch("/api/model/status", { headers: { Accept: "application/json" } });
      const st = await res.json();
      state.modelStatus = st;

      if (st.ready) {
        dot.className = "dot dot-ready";
        detail.textContent = `${st.configured_model} · hazır`;
        $("#setup-banner").hidden = true;
        if (manual) showToast(`Gemma 4 (${st.configured_model}) bağlantısı doğrulandı.`);
      } else {
        dot.className = "dot dot-down";
        detail.textContent = `${st.configured_model || "model"} · bağlı değil`;
        const err = st.error || {};
        $("#setup-title").textContent = "Gemma 4 çalışma ortamı hazır değil";
        $("#setup-message").textContent = err.message || "Gemma 4 modeline ulaşılamadı.";
        $("#setup-hint").textContent = err.hint || "";
        $("#setup-pull").textContent = `ollama pull ${st.configured_model || "gemma4:e2b"}`;
        $("#setup-banner").hidden = false;
        if (manual) showToast("Gemma 4 henüz hazır değil; kurulum adımlarını kontrol edin.");
      }
    } catch (_e) {
      dot.className = "dot dot-down";
      detail.textContent = "sunucu yanıt vermiyor";
    }
  }

  // ------------------------- Girdi Doğrulama & İstek ----------------------
  function showInputError(msg) {
    const el = $("#input-error");
    el.textContent = msg;
    el.hidden = false;
  }

  function hideInputError() {
    $("#input-error").hidden = true;
  }

  function validateInputClient() {
    hideInputError();
    if (state.mode === "code") {
      const oldC = $("#old-code").value;
      const newC = $("#new-code").value;
      if (!oldC.trim() && !newC.trim()) {
        showInputError("Eski ve yeni kod alanları boş. Karşılaştırmak için kod yapıştırın veya 'Örnek senaryoyu yükle' düğmesine tıklayın.");
        return false;
      }
      if (oldC.replace(/\r\n/g, "\n") === newC.replace(/\r\n/g, "\n")) {
        showInputError("Eski ve yeni kod birebir aynı; analiz edilecek bir değişiklik yok.");
        return false;
      }
      return true;
    }
    if (state.mode === "diff") {
      const d = $("#diff-text").value;
      if (!d.trim()) {
        showInputError("Diff alanı boş. Bir `git diff` veya unified diff çıktısı yapıştırın.");
        return false;
      }
      return true;
    }
    if (state.mode === "upload") {
      if (state.uploadKind === "pair") {
        const f1 = $("#old-file").files[0];
        const f2 = $("#new-file").files[0];
        if (!f1 || !f2) {
          showInputError("Karşılaştırma için hem önceki sürüm hem de değiştirilmiş sürüm dosyasını seçin.");
          return false;
        }
      } else {
        const fp = $("#patch-file").files[0];
        if (!fp) {
          showInputError("Analiz için bir .diff veya .patch dosyası seçin.");
          return false;
        }
      }
      return true;
    }
    return false;
  }

  function buildUploadForm(preview = false) {
    const fd = new FormData();
    fd.append("language", $("#language").value);
    fd.append("description", $("#description").value);
    if (preview) fd.append("preview", "1");
    if (state.uploadKind === "pair") {
      fd.append("old_file", $("#old-file").files[0]);
      fd.append("new_file", $("#new-file").files[0]);
    } else {
      fd.append("patch_file", $("#patch-file").files[0]);
    }
    return fd;
  }

  function buildJsonPayload() {
    return {
      mode: state.mode,
      filename: $("#filename").value.trim(),
      language: $("#language").value,
      description: $("#description").value.trim(),
      old_code: $("#old-code").value,
      new_code: $("#new-code").value,
      diff: $("#diff-text").value,
    };
  }

  // --------------------------- Diff Görselleştirme ------------------------
  function condenseForTable(rows, context = 5) {
    const changed = [];
    rows.forEach((r, idx) => {
      if (r.type !== "context") changed.push(idx);
    });
    if (!changed.length) return rows.map((r, i) => ({ kind: "row", row: r, idx: i }));

    const keep = new Set();
    changed.forEach((c) => {
      for (let k = Math.max(0, c - context); k <= Math.min(rows.length - 1, c + context); k++) {
        keep.add(k);
      }
    });

    const out = [];
    let i = 0;
    while (i < rows.length) {
      if (keep.has(i)) {
        out.push({ kind: "row", row: rows[i], idx: i });
        i++;
      } else {
        const start = i;
        while (i < rows.length && !keep.has(i)) i++;
        const hiddenRows = rows.slice(start, i);
        if (hiddenRows.length <= 3) {
          hiddenRows.forEach((r, off) => out.push({ kind: "row", row: r, idx: start + off }));
        } else {
          out.push({ kind: "fold", rows: hiddenRows });
        }
      }
    }
    return out;
  }

  function buildDiffRowTr(r, fileIdx, lang, findingLineSet) {
    const tr = document.createElement("tr");
    if (r.type === "hunk") {
      tr.className = "hunk";
      tr.innerHTML = `<td class="ln"></td><td class="ln"></td><td class="mk">@@</td><td class="code">${esc(r.text)}</td>`;
      return tr;
    }
    if (r.type === "meta") {
      tr.className = "meta";
      tr.innerHTML = `<td class="ln"></td><td class="ln"></td><td class="mk">#</td><td class="code">${esc(r.text)}</td>`;
      return tr;
    }
    const classes = [r.type];
    if (r.mod) classes.push("mod");
    if (r.new != null) {
      tr.dataset.newLine = String(r.new);
      if (findingLineSet.has(`new:${r.new}`)) classes.push("finding-line");
    }
    if (r.old != null) {
      tr.dataset.oldLine = String(r.old);
      if (findingLineSet.has(`old:${r.old}`)) classes.push("finding-line");
    }
    tr.dataset.fileIdx = String(fileIdx);
    tr.className = classes.join(" ");

    const oldCell = r.old != null ? String(r.old) : "";
    const newCell = r.new != null ? String(r.new) : "";
    const marker = r.type === "add" ? "+" : r.type === "del" ? "-" : " ";
    const codeHtml = highlightCode(r.text, lang);
    tr.innerHTML = `<td class="ln">${oldCell}</td><td class="ln">${newCell}</td><td class="mk">${marker}</td><td class="code">${codeHtml}</td>`;
    return tr;
  }

  function renderDiff(files, findings = []) {
    const sec = $("#diff-section");
    const statsEl = $("#diff-stats");
    const viewEl = $("#diff-view");
    if (!files || !files.length) {
      sec.hidden = true;
      return;
    }
    sec.hidden = false;

    const totalAdd = files.reduce((a, f) => a + (f.added || 0), 0);
    const totalDel = files.reduce((a, f) => a + (f.removed || 0), 0);
    statsEl.innerHTML = `
      <span><strong>${files.length}</strong> dosya</span>
      <span class="stat-add">+${totalAdd} eklenen</span>
      <span class="stat-del">−${totalDel} silinen</span>
    `;

    viewEl.innerHTML = "";
    files.forEach((f, fileIdx) => {
      const findingLineSet = new Set();
      (findings || []).forEach((fd) => {
        if (fd.line_verified && fd.file === f.path && fd.line_start) {
          const side = fd.line_side || "new";
          const end = fd.line_end || fd.line_start;
          for (let ln = fd.line_start; ln <= end; ln++) {
            findingLineSet.add(`${side}:${ln}`);
          }
        }
      });

      const wrap = document.createElement("div");
      wrap.className = "diff-file";
      wrap.dataset.filePath = f.path;

      const statusLabels = {
        modified: "değiştirildi",
        added: "yeni dosya",
        deleted: "silindi",
        renamed: "yeniden adlandırıldı",
      };
      const stLabel = statusLabels[f.status] || f.status;

      let noteHtml = "";
      if (f.line_mapping === "unavailable") {
        noteHtml = `<div class="diff-file-note">Bu girdi hunk başlığı (<code>@@ ... @@</code>) içermediği için kesin satır numaraları mevcut değil; satır numarası uydurulmadı.</div>`;
      }

      wrap.innerHTML = `
        <div class="diff-file-head">
          <span class="path">${esc(f.path)}</span>
          <span class="tag">${esc(f.language || "text")}</span>
          <span class="muted">${esc(stLabel)}</span>
          <span class="stat-add">+${f.added || 0}</span>
          <span class="stat-del">−${f.removed || 0}</span>
        </div>
        ${noteHtml}
        <div class="diff-scroll"><table class="diff" aria-label="${esc(f.path)} değişiklik tablosu"><tbody></tbody></table></div>
      `;

      const tbody = $("tbody", wrap);
      const items = condenseForTable(f.rows || []);
      items.forEach((item) => {
        if (item.kind === "row") {
          tbody.appendChild(buildDiffRowTr(item.row, fileIdx, f.language, findingLineSet));
        } else {
          const foldTr = document.createElement("tr");
          foldTr.className = "fold";
          const td = document.createElement("td");
          td.colSpan = 4;
          const btn = document.createElement("button");
          btn.type = "button";
          btn.textContent = `↕ Değişmeyen ${item.rows.length} satırı göster`;
          btn.addEventListener("click", () => {
            const frag = document.createDocumentFragment();
            item.rows.forEach((r) => frag.appendChild(buildDiffRowTr(r, fileIdx, f.language, findingLineSet)));
            tbody.replaceChild(frag, foldTr);
          });
          td.appendChild(btn);
          foldTr.appendChild(td);
          tbody.appendChild(foldTr);
        }
      });

      viewEl.appendChild(wrap);
    });
  }

  function jumpToDiffLine(file, line, side) {
    const files = $$(".diff-file", $("#diff-view"));
    let targetFile = files.find((el) => el.dataset.filePath === file) || files[0];
    if (!targetFile) return;

    const attr = side === "old" ? `tr[data-old-line="${line}"]` : `tr[data-new-line="${line}"]`;
    let row = $(attr, targetFile);
    if (!row) {
      // Gizlenmiş (fold) satırların içindeyse tüm katlamaları aç
      $$("tr.fold button", targetFile).forEach((b) => b.click());
      row = $(attr, targetFile);
    }
    if (row) {
      row.scrollIntoView({ behavior: "smooth", block: "center" });
      row.classList.remove("flash");
      void row.offsetWidth;
      row.classList.add("flash");
    }
  }

  // ------------------------- Diff Önizleme İsteği -------------------------
  async function handlePreviewDiff() {
    if (!validateInputClient()) return;
    try {
      let res;
      if (state.mode === "upload") {
        res = await fetch("/api/analyze/upload", {
          method: "POST",
          body: buildUploadForm(true),
        });
      } else {
        res = await fetch("/api/diff", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(buildJsonPayload()),
        });
      }
      const data = await res.json();
      if (!res.ok) {
        showInputError((data.error && data.error.message) || "Diff hesaplanamadı.");
        return;
      }
      const currentFindings = state.currentReport ? state.currentReport.result.findings : [];
      renderDiff(data.files, currentFindings);
      $("#diff-section").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (_e) {
      showInputError("Sunucuya bağlanırken hata oluştu.");
    }
  }

  // ---------------------------- Analiz Akışı ------------------------------
  function startLoadingUI() {
    $("#analysis-error").hidden = true;
    $("#loading").hidden = false;
    $("#analyze").disabled = true;
    $("#preview-diff").disabled = true;
    const elapsedEl = $("#loading-elapsed");
    const started = Date.now();
    elapsedEl.textContent = "0";
    clearInterval(state.timerId);
    state.timerId = setInterval(() => {
      elapsedEl.textContent = String(Math.floor((Date.now() - started) / 1000));
    }, 500);
  }

  function stopLoadingUI() {
    clearInterval(state.timerId);
    $("#loading").hidden = true;
    $("#analyze").disabled = false;
    $("#preview-diff").disabled = false;
  }

  async function handleAnalyze() {
    if (!validateInputClient()) return;

    // Önce diff'i hızlıca önizleyelim (kullanıcı hemen görsün)
    if (state.mode !== "upload") {
      fetch("/api/diff", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildJsonPayload()),
      })
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (d && d.files) renderDiff(d.files);
        })
        .catch(() => {});
    }

    state.abortCtrl = new AbortController();
    startLoadingUI();
    $("#loading").scrollIntoView({ behavior: "smooth", block: "nearest" });

    try {
      let res;
      if (state.mode === "upload") {
        res = await fetch("/api/analyze/upload", {
          method: "POST",
          body: buildUploadForm(false),
          signal: state.abortCtrl.signal,
        });
      } else {
        res = await fetch("/api/analyze", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(buildJsonPayload()),
          signal: state.abortCtrl.signal,
        });
      }
      const data = await res.json();
      stopLoadingUI();

      if (!res.ok) {
        showAnalysisError(data.error || { message: "Analiz başarısız oldu." });
        checkModelStatus(false);
        return;
      }

      renderReport(data);
      checkModelStatus(false);
    } catch (err) {
      stopLoadingUI();
      if (err && err.name === "AbortError") {
        showToast("Analiz iptal edildi.");
        return;
      }
      showAnalysisError({
        message: "Sunucuya bağlanılamadı veya istek kesildi.",
        hint: "Flask sunucusunun çalıştığından emin olun.",
      });
    }
  }

  function showAnalysisError(err) {
    const sec = $("#analysis-error");
    $("#analysis-error-title").textContent = "Gemma 4 analizi tamamlanamadı";
    $("#analysis-error-message").textContent = err.message || "Bilinmeyen hata.";
    $("#analysis-error-hint").textContent = err.hint || "";
    const demoBtn = $("#show-demo");
    const canDemo =
      isSampleCurrentlyUnmodified() &&
      (!state.modelStatus || state.modelStatus.demo_available !== false);
    demoBtn.hidden = !canDemo;
    sec.hidden = false;
    sec.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function handleRunDemo() {
    if (!isSampleCurrentlyUnmodified()) {
      await loadSampleScenario();
    }
    try {
      const res = await fetch("/api/demo/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildJsonPayload()),
      });
      const data = await res.json();
      if (!res.ok) {
        showToast((data.error && data.error.message) || "Demo yüklenemedi.");
        return;
      }
      $("#analysis-error").hidden = true;
      renderReport(data);
      showToast("DEMO raporu yüklendi (Gemma 4 analizi değildir).");
    } catch (_e) {
      showToast("Demo raporu alınamadı.");
    }
  }

  // ------------------------- Rapor Görselleştirme -------------------------
  function renderReport(report) {
    state.currentReport = report;
    const isDemo = Boolean(report.engine && report.engine.is_demo);
    $("#demo-indicator").hidden = !isDemo;

    if (report.files) {
      renderDiff(report.files, report.result.findings);
    }

    // Motor başlığı
    const eb = $("#engine-banner");
    if (isDemo) {
      eb.innerHTML = `
        <div class="eb eb-demo" role="status">
          <strong>DEMO MODU — Gemma 4 Analizi Değildir</strong>
          <span>${esc(report.engine.notice || "Bu sonuçlar örnek senaryo için sabit demo verisidir.")}</span>
        </div>
      `;
    } else {
      const sec = ((report.meta && report.meta.duration_ms) ? (report.meta.duration_ms / 1000).toFixed(1) : "—") + " sn";
      const paramSize =
        report.engine.model_details && report.engine.model_details.parameter_size
          ? `<span class="eb-chip">${esc(report.engine.model_details.parameter_size)}</span>`
          : "";
      eb.innerHTML = `
        <div class="eb">
          <strong>Analiz Motoru: Gemma 4</strong>
          <span class="eb-chip">${esc(report.engine.model)}</span>
          ${paramSize}
          <span class="eb-chip">Ollama ${esc(report.engine.runtime_version || "")}</span>
          <span class="muted small">Süre: ${esc(sec)} · Yalnızca statik analiz (kod çalıştırılmadı)</span>
        </div>
      `;
    }

    // Risk özeti
    const res = report.result;
    const ov = res.overall_risk || "none";
    const ovMeta = SEV_META[ov] || SEV_META.none;
    const ovEl = $("#overall-risk");
    ovEl.className = `overall-risk risk-${ov}`;
    ovEl.innerHTML = `
      <div class="label">Genel Risk Seviyesi</div>
      <div class="value">${esc(ovMeta.label)}</div>
      <div class="sub">${res.findings.length} bulgu · ${res.tests.length} test</div>
    `;

    const countsEl = $("#severity-counts");
    countsEl.innerHTML = ["critical", "high", "medium", "low", "info"]
      .map((s) => {
        const n = (res.severity_counts && res.severity_counts[s]) || 0;
        return `<span class="count-chip ${n === 0 ? "zero" : ""}"><span class="sev sev-${s}">${esc(
          SEV_META[s].label
        )}</span><b>${n}</b></span>`;
      })
      .join("");

    $("#summary-text").textContent = res.summary || "";

    const warnEl = $("#result-warnings");
    const warnings = (report.meta && report.meta.warnings) || [];
    if (warnings.length) {
      warnEl.innerHTML = `<div class="alert alert-warn"><strong>Doğrulama notları:</strong><ul>${warnings
        .map((w) => `<li>${esc(w)}</li>`)
        .join("")}</ul></div>`;
    } else {
      warnEl.innerHTML = "";
    }

    renderFindings(res.findings);
    renderComponents(res.affected_components);
    renderUnknowns(res.unknowns);
    renderTests();

    $("#results").hidden = false;
    $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderFindings(findings) {
    const container = $("#findings");
    const metaEl = $("#findings-meta");
    container.innerHTML = "";

    if (!findings || !findings.length) {
      metaEl.textContent = "0 bulgu";
      container.innerHTML = `
        <div class="empty">
          <strong>Anlamlı bir regresyon riski tespit edilmedi</strong>
          <p>Gemma 4 verilen değişiklikte somut bir kusur bulmadı. Yine de aşağıdaki doğrulama testlerini gözden geçirebilirsiniz.</p>
        </div>
      `;
      return;
    }

    metaEl.textContent = `${findings.length} bulgu (önem sırasına göre)`;

    ["critical", "high", "medium", "low", "info"].forEach((sev) => {
      const group = findings.filter((f) => f.severity === sev);
      if (!group.length) return;

      const groupDiv = document.createElement("div");
      groupDiv.className = "sev-group";
      groupDiv.innerHTML = `<h3 class="sev-group-title"><span class="sev sev-${sev}">${esc(
        SEV_META[sev].label
      )}</span> <span>${esc(SEV_META[sev].group)} (${group.length})</span></h3>`;

      group.forEach((f, idx) => {
        const det = document.createElement("details");
        det.className = `finding sev-b-${sev}`;
        det.id = `finding-${f.id}`;
        if (sev === "critical" || sev === "high" || idx === 0) det.open = true;

        const ev = EV_META[f.evidence_type] || EV_META.plausible;
        const conf = CONF_META[f.confidence] || CONF_META.low;
        const barsHtml = [1, 2, 3]
          .map((i) => `<i class="${i <= conf.bars ? "on" : ""}"></i>`)
          .join("");

        let locHtml = "";
        if (f.file) {
          if (f.line_verified && f.line_start) {
            const range =
              f.line_end && f.line_end !== f.line_start ? `${f.line_start}-${f.line_end}` : `${f.line_start}`;
            const sideLbl = f.line_side === "old" ? "eski" : "yeni";
            locHtml = `<button type="button" class="loc" data-jump-file="${esc(f.file)}" data-jump-line="${
              f.line_start
            }" data-jump-side="${esc(f.line_side || "new")}" title="Diff tablosunda bu satıra git">${esc(
              f.file
            )}:${range} (${sideLbl})</button>`;
          } else {
            locHtml = `<span class="loc">${esc(f.file)}</span>`;
          }
        }

        const snippetFlag =
          f.evidence_check === "not_found"
            ? `<span class="flag" title="Alıntılanan kod diff satırlarıyla birebir eşleşmedi">⚠ Kod alıntısı doğrulanmalı</span>`
            : "";

        const lineNoteHtml = f.line_note ? `<p class="small muted">${esc(f.line_note)}</p>` : "";
        const assumptionsHtml =
          f.assumptions && f.assumptions.length
            ? `<div><h4>Varsayımlar ve Koşullar</h4><ul class="plain-list">${f.assumptions
                .map((a) => `<li>${esc(a)}</li>`)
                .join("")}</ul></div>`
            : "";

        det.innerHTML = `
          <summary>
            <div class="finding-top">
              <span class="sev sev-${sev}">${esc(SEV_META[sev].label)}</span>
              <span class="finding-id">${esc(f.id)}</span>
              <span class="finding-title">${esc(f.title)}</span>
              <span class="chev" aria-hidden="true">▸</span>
            </div>
            <div class="finding-meta">
              ${locHtml}
              <span class="ev ${ev.cls}">${esc(ev.label)}</span>
              <span class="conf" title="${esc(conf.label)}">
                <span class="conf-bars" aria-hidden="true">${barsHtml}</span>
                <span>${esc(conf.label)}</span>
              </span>
              ${snippetFlag}
            </div>
          </summary>
          <div class="finding-body">
            ${
              f.changed_code
                ? `<div><h4>İlgili Değişen Kod</h4><pre class="codeblock"><code>${esc(
                    f.changed_code
                  )}</code></pre>${lineNoteHtml}</div>`
                : lineNoteHtml
            }
            <div>
              <h4>Neden Sorun Oluşturabilir?</h4>
              <p>${esc(f.explanation)}</p>
            </div>
            ${
              f.failure_scenario
                ? `<div><h4>Somut Hata Senaryosu</h4><div class="scenario"><p>${esc(
                    f.failure_scenario
                  )}</p></div></div>`
                : ""
            }
            ${
              f.recommendation
                ? `<div><h4>Önerilen Doğrulama / Önlem</h4><p>${esc(f.recommendation)}</p></div>`
                : ""
            }
            ${assumptionsHtml}
          </div>
        `;

        const jumpBtn = $("button.loc", det);
        if (jumpBtn) {
          jumpBtn.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            jumpToDiffLine(jumpBtn.dataset.jumpFile, Number(jumpBtn.dataset.jumpLine), jumpBtn.dataset.jumpSide);
          });
        }

        groupDiv.appendChild(det);
      });

      container.appendChild(groupDiv);
    });
  }

  function renderComponents(components) {
    const el = $("#components");
    if (!components || !components.length) {
      el.innerHTML = `<p class="muted small">Belirtilen bileşen yok.</p>`;
      return;
    }
    el.className = "components";
    el.innerHTML = components
      .map((c) => {
        const ev = EV_META[c.evidence_type] || EV_META.plausible;
        const kind = KIND_META[c.kind] || c.kind;
        return `
          <div class="component">
            <div class="component-head">
              <span class="component-name">${esc(c.name)}</span>
              <span class="component-kind">${esc(kind)}</span>
              <span class="ev ${ev.cls}">${esc(ev.label)}</span>
            </div>
            ${c.file ? `<div class="small muted">${esc(c.file)}</div>` : ""}
            <p>${esc(c.impact)}</p>
          </div>
        `;
      })
      .join("");
  }

  function renderUnknowns(unknowns) {
    const el = $("#unknowns");
    if (!unknowns || !unknowns.length) {
      el.innerHTML = `<li class="muted">Belirtilen ek bilinmeyen etki yok.</li>`;
      return;
    }
    el.innerHTML = unknowns.map((u) => `<li>${esc(u)}</li>`).join("");
  }

  // ------------------------- Test Kontrol Listesi -------------------------
  function updateTestProgress() {
    if (!state.currentReport) return;
    const tests = state.currentReport.result.tests || [];
    const total = tests.length;
    const pass = tests.filter((t) => t.status === "passed").length;
    const fail = tests.filter((t) => t.status === "failed").length;
    const block = tests.filter((t) => t.status === "blocked").length;
    const pending = total - pass - fail - block;
    const done = pass + fail + block;

    const bar = $("#test-progress");
    const pct = (n) => (total ? ((n / total) * 100).toFixed(1) + "%" : "0%");
    $(".progress-pass", bar).style.width = pct(pass);
    $(".progress-fail", bar).style.width = pct(fail);
    $(".progress-block", bar).style.width = pct(block);
    bar.setAttribute("aria-valuenow", total ? String(Math.round((done / total) * 100)) : "0");

    $("#progress-text").textContent = `${done} / ${total} işaretlendi · ${pass} geçti, ${fail} kaldı, ${block} engellendi, ${pending} bekliyor`;
  }

  function renderTests() {
    const listEl = $("#tests");
    listEl.innerHTML = "";
    if (!state.currentReport) return;

    const allTests = state.currentReport.result.tests || [];
    updateTestProgress();

    const fPrio = $("#filter-priority").value;
    const fStat = $("#filter-status").value;
    const fCat = $("#filter-category").value;

    const filtered = allTests.filter((t) => {
      if (fPrio !== "all" && t.priority !== fPrio) return false;
      if (fStat !== "all" && t.status !== fStat) return false;
      if (fCat !== "all" && t.category !== fCat) return false;
      return true;
    });

    if (!filtered.length) {
      listEl.innerHTML = `<li class="empty">Seçili filtrelere uyan test maddesi yok.</li>`;
      return;
    }

    filtered.forEach((t) => {
      const li = document.createElement("li");
      li.className = `test st-${t.status}`;

      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = t.status === "passed";
      cb.setAttribute("aria-label", `${t.name} testini geçti olarak işaretle`);

      const content = document.createElement("div");
      const findingBtn = t.finding_id
        ? `<button type="button" class="finding-link" data-finding="${esc(t.finding_id)}" title="İlgili bulguya git">→ ${esc(
            t.finding_id
          )}</button>`
        : "";

      const stepsHtml =
        t.steps && t.steps.length
          ? `<div><span class="lbl">Adımlar:</span><ol>${t.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol></div>`
          : "";

      content.innerHTML = `
        <div class="test-head">
          <span class="test-id">${esc(t.id)}</span>
          <span class="test-name">${esc(t.name)}</span>
          <span class="prio prio-${t.priority}">${esc(PRIO_META[t.priority] || t.priority)}</span>
          <span class="cat">${esc(CAT_META[t.category] || t.category)}</span>
          ${findingBtn}
        </div>
        <div class="test-body">
          ${t.behavior ? `<p><span class="lbl">Doğrulanan davranış:</span> ${esc(t.behavior)}</p>` : ""}
          ${stepsHtml}
          ${t.expected_result ? `<p><span class="lbl">Beklenen sonuç:</span> ${esc(t.expected_result)}</p>` : ""}
        </div>
        <div class="status-group" role="group" aria-label="${esc(t.id)} test durumu">
          ${["pending", "passed", "failed", "blocked"]
            .map(
              (st) =>
                `<button type="button" data-status="${st}" aria-pressed="${
                  t.status === st ? "true" : "false"
                }">${esc(STATUS_META[st])}</button>`
            )
            .join("")}
        </div>
      `;

      function setStatus(newStatus) {
        t.status = newStatus;
        renderTests();
      }

      cb.addEventListener("change", () => {
        setStatus(cb.checked ? "passed" : "pending");
      });

      $$(".status-group button", content).forEach((btn) => {
        btn.addEventListener("click", () => setStatus(btn.dataset.status));
      });

      const fLink = $(".finding-link", content);
      if (fLink) {
        fLink.addEventListener("click", () => {
          const fEl = document.getElementById(`finding-${fLink.dataset.finding}`);
          if (fEl) {
            fEl.open = true;
            fEl.scrollIntoView({ behavior: "smooth", block: "center" });
            fEl.classList.add("highlighted");
            setTimeout(() => fEl.classList.remove("highlighted"), 1800);
          }
        });
      }

      li.appendChild(cb);
      li.appendChild(content);
      listEl.appendChild(li);
    });
  }

  // ------------------------- Dışa Aktarma (MD/JSON) -----------------------
  function downloadBlob(filename, content, mime) {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function buildMarkdown(r) {
    const res = r.result;
    const isDemo = Boolean(r.engine && r.engine.is_demo);
    const lines = [];
    lines.push(`# CodeDetector — Risk Analizi Raporu`);
    lines.push("");
    if (isDemo) {
      lines.push(`> **UYARI: DEMO MODU** — Bu rapor örnek senaryo için sabit demo verisidir; Gemma 4 tarafından üretilmemiştir.`);
      lines.push("");
    }
    lines.push(`- **Rapor Kimliği:** \`${r.analysis_id}\``);
    lines.push(`- **Tarih:** ${r.created_at}`);
    lines.push(
      `- **Analiz Motoru:** ${isDemo ? "DEMO (Gemma 4 değil)" : `Gemma 4 (\`${r.engine.model}\` · Ollama ${r.engine.runtime_version || ""})`}`
    );
    lines.push(`- **Genel Risk Seviyesi:** **${(SEV_META[res.overall_risk] || SEV_META.none).label}**`);
    if (r.input && r.input.description) {
      lines.push(`- **Değişiklik Açıklaması:** ${r.input.description}`);
    }
    lines.push("");
    lines.push(`## 1. Risk Özeti`);
    lines.push("");
    lines.push(res.summary || "_Özet yok._");
    lines.push("");

    lines.push(`## 2. Bulgular (${res.findings.length})`);
    lines.push("");
    if (!res.findings.length) {
      lines.push("_Anlamlı bir risk tespit edilmedi._");
      lines.push("");
    } else {
      res.findings.forEach((f) => {
        const sevLbl = (SEV_META[f.severity] || { label: f.severity }).label;
        const evLbl = (EV_META[f.evidence_type] || { label: f.evidence_type }).label;
        const loc =
          f.file && f.line_verified && f.line_start
            ? `${f.file}:${f.line_start}${f.line_end && f.line_end !== f.line_start ? "-" + f.line_end : ""} (${f.line_side === "old" ? "eski" : "yeni"})`
            : f.file || "—";
        lines.push(`### [${f.id}] ${f.title}`);
        lines.push(`- **Önem Derecesi:** ${sevLbl}`);
        lines.push(`- **Kanıt Türü:** ${evLbl}`);
        lines.push(`- **Güven Düzeyi:** ${(CONF_META[f.confidence] || { label: f.confidence }).label}`);
        lines.push(`- **Konum:** \`${loc}\``);
        lines.push("");
        if (f.changed_code) {
          lines.push("```");
          lines.push(f.changed_code);
          lines.push("```");
          lines.push("");
        }
        lines.push(`**Neden sorun oluşturabilir?**  `);
        lines.push(f.explanation);
        lines.push("");
        if (f.failure_scenario) {
          lines.push(`**Somut hata senaryosu:**  `);
          lines.push(f.failure_scenario);
          lines.push("");
        }
        if (f.recommendation) {
          lines.push(`**Önerilen doğrulama / önlem:**  `);
          lines.push(f.recommendation);
          lines.push("");
        }
        if (f.assumptions && f.assumptions.length) {
          lines.push(`**Varsayımlar:**`);
          f.assumptions.forEach((a) => lines.push(`- ${a}`));
          lines.push("");
        }
      });
    }

    lines.push(`## 3. Etkilenen Bileşenler (${res.affected_components.length})`);
    lines.push("");
    if (!res.affected_components.length) {
      lines.push("_Belirtilmedi._");
    } else {
      res.affected_components.forEach((c) => {
        lines.push(`- **\`${c.name}\`** (${KIND_META[c.kind] || c.kind}${c.file ? ` · \`${c.file}\`` : ""}): ${c.impact}`);
      });
    }
    lines.push("");

    lines.push(`## 4. Test Kontrol Listesi (${res.tests.length})`);
    lines.push("");
    lines.push(`> Not: Testler CodeDetector tarafından otomatik çalıştırılmaz; aşağıdaki durumlar geliştirici tarafından işaretlenmiştir.`);
    lines.push("");
    res.tests.forEach((t) => {
      const mark = t.status === "passed" ? "x" : " ";
      const stLbl = STATUS_META[t.status] || t.status;
      const prioLbl = PRIO_META[t.priority] || t.priority;
      const catLbl = CAT_META[t.category] || t.category;
      lines.push(
        `- [${mark}] **[${t.id}] ${t.name}** — _Durum: ${stLbl} | ${prioLbl} | ${catLbl}${t.finding_id ? ` | Bulgu: ${t.finding_id}` : ""}_`
      );
      if (t.behavior) lines.push(`  - **Davranış:** ${t.behavior}`);
      if (t.steps && t.steps.length) {
        lines.push(`  - **Adımlar:**`);
        t.steps.forEach((s, i) => lines.push(`    ${i + 1}. ${s}`));
      }
      if (t.expected_result) lines.push(`  - **Beklenen sonuç:** ${t.expected_result}`);
    });
    lines.push("");

    if (res.unknowns && res.unknowns.length) {
      lines.push(`## 5. Bilinmeyenler`);
      lines.push("");
      res.unknowns.forEach((u) => lines.push(`- ${u}`));
      lines.push("");
    }
    return lines.join("\n");
  }

  // ------------------------ Örnek Senaryo & Sıfırlama ---------------------
  async function loadSampleScenario() {
    try {
      const res = await fetch("/api/sample");
      const data = await res.json();
      state.sampleData = data;
      switchTab("code");
      $("#filename").value = data.filename || "";
      $("#language").value = data.language || "python";
      $("#description").value = data.description || "";
      $("#old-code").value = data.old_code || "";
      $("#new-code").value = data.new_code || "";
      refreshAllEditors();
      checkSampleDirty();
      await handlePreviewDiff();
      showToast("Örnek senaryo yüklendi. 'Değişiklikleri Analiz Et' ile Gemma 4 analizini başlatın.");
    } catch (_e) {
      showToast("Örnek senaryo yüklenemedi.");
    }
  }

  function resetAll() {
    if (state.abortCtrl) state.abortCtrl.abort();
    stopLoadingUI();
    state.currentReport = null;
    $("#demo-indicator").hidden = true;
    $("#old-code").value = "";
    $("#new-code").value = "";
    $("#diff-text").value = "";
    $("#filename").value = "";
    $("#description").value = "";
    $("#language").value = "auto";
    $$(".dropzone").forEach((dz) => dz._resetDisplay && dz._resetDisplay());
    refreshAllEditors();
    checkSampleDirty();
    hideInputError();
    $("#analysis-error").hidden = true;
    $("#diff-section").hidden = true;
    $("#results").hidden = true;
    $("#workspace").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ------------------------------ Başlatma --------------------------------
  document.addEventListener("DOMContentLoaded", () => {
    initTabs();
    initEditors();
    initUploads();
    checkModelStatus(false);

    $("#engine-status").addEventListener("click", () => checkModelStatus(true));
    $("#load-sample").addEventListener("click", loadSampleScenario);
    $("#new-analysis").addEventListener("click", resetAll);
    $("#preview-diff").addEventListener("click", handlePreviewDiff);
    $("#analyze").addEventListener("click", handleAnalyze);
    $("#retry-analysis").addEventListener("click", handleAnalyze);
    $("#show-demo").addEventListener("click", handleRunDemo);
    $("#cancel-analysis").addEventListener("click", () => {
      if (state.abortCtrl) state.abortCtrl.abort();
    });

    ["#filter-priority", "#filter-status", "#filter-category"].forEach((sel) => {
      $(sel).addEventListener("change", renderTests);
    });

    $("#export-md").addEventListener("click", () => {
      if (!state.currentReport) return;
      const md = buildMarkdown(state.currentReport);
      downloadBlob(`codedetector-${state.currentReport.analysis_id}.md`, md, "text/markdown;charset=utf-8");
      showToast("Markdown raporu indirildi.");
    });

    $("#export-json").addEventListener("click", () => {
      if (!state.currentReport) return;
      const json = JSON.stringify(state.currentReport, null, 2);
      downloadBlob(`codedetector-${state.currentReport.analysis_id}.json`, json, "application/json;charset=utf-8");
      showToast("JSON raporu indirildi.");
    });

    document.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        handleAnalyze();
      }
    });
  });
})();
