(function () {
  "use strict";

  const state = {
    data: { games: {} },
    selectedId: null,
    dirty: false,
    handle: null,
  };

  const els = {
    loadDefault: document.getElementById("load-default"),
    fileInput: document.getElementById("file-input"),
    downloadJson: document.getElementById("download-json"),
    savePickedFile: document.getElementById("save-picked-file"),
    newId: document.getElementById("new-id"),
    createGame: document.getElementById("create-game"),
    search: document.getElementById("search"),
    gameList: document.getElementById("game-list"),
    dirtyStatus: document.getElementById("dirty-status"),
    countStatus: document.getElementById("count-status"),
    emptyState: document.getElementById("empty-state"),
    editorForm: document.getElementById("editor-form"),
    fieldId: document.getElementById("field-id"),
    fieldName: document.getElementById("field-name"),
    fieldShortDescription: document.getElementById("field-short-description"),
    rulebooksList: document.getElementById("rulebooks-list"),
    supplementalList: document.getElementById("supplemental-list"),
    addRulebook: document.getElementById("add-rulebook"),
    addSupplemental: document.getElementById("add-supplemental"),
    deleteGame: document.getElementById("delete-game"),
    docRowTemplate: document.getElementById("doc-row-template"),
  };

  const PLATFORM_KEYS = ["android", "ios", "pc"];
  const PLATFORM_FLAGS = ["owned", "wishlisted", "preordered"];

  function ensureGamesContainer(data) {
    if (!data || typeof data !== "object") {
      return { games: {} };
    }

    if (!data.games || typeof data.games !== "object" || Array.isArray(data.games)) {
      data.games = {};
    }

    return data;
  }

  function normalizeDocuments(raw) {
    if (!Array.isArray(raw)) return [];

    const docs = [];
    for (const item of raw) {
      let url = "";
      let name = "";

      if (typeof item === "string") {
        url = item.trim();
      } else if (item && typeof item === "object") {
        url = String(item.url || "").trim();
        name = String(item.name || "").trim();
      }

      if (!url) continue;
      const doc = { url };
      if (name) doc.name = name;
      docs.push(doc);
    }

    return docs;
  }

  function normalizePlatform(raw) {
    if (!raw || typeof raw !== "object") return {};

    const out = {};
    const url = String(raw.url || "").trim();
    if (url) out.url = url;

    for (const flag of PLATFORM_FLAGS) {
      if (raw[flag] === true) {
        out[flag] = true;
      }
    }

    return out;
  }

  function normalizeEntry(raw, fallbackName) {
    const entry = raw && typeof raw === "object" ? raw : {};

    const name = String(entry.name || fallbackName || "").trim();
    const shortDescription = String(entry.short_description || "").trim();
    const rulebooks = normalizeDocuments(entry.rulebooks);
    const supplementalFiles = normalizeDocuments(entry.supplemental_files);

    const normalized = {
      name,
      short_description: shortDescription,
      rulebooks,
      supplemental_files: supplementalFiles,
    };

    for (const platform of PLATFORM_KEYS) {
      const p = normalizePlatform(entry[platform]);
      if (Object.keys(p).length > 0) {
        normalized[platform] = p;
      }
    }

    return normalized;
  }

  function getSortedIds() {
    const ids = Object.keys(state.data.games || {});
    ids.sort((a, b) => {
      const aEntry = state.data.games[a] || {};
      const bEntry = state.data.games[b] || {};
      const aName = String(aEntry.name || "").toLowerCase();
      const bName = String(bEntry.name || "").toLowerCase();

      if (aName && bName && aName !== bName) return aName.localeCompare(bName);
      if (aName && !bName) return -1;
      if (!aName && bName) return 1;

      const aNum = Number(a);
      const bNum = Number(b);
      if (Number.isFinite(aNum) && Number.isFinite(bNum)) return aNum - bNum;
      return a.localeCompare(b);
    });
    return ids;
  }

  function platformStats(entry) {
    let count = 0;
    for (const platform of PLATFORM_KEYS) {
      const p = entry[platform];
      if (p && typeof p === "object" && Object.keys(p).length > 0) {
        count += 1;
      }
    }
    return count;
  }

  function gameSummary(entry) {
    const rb = Array.isArray(entry.rulebooks) ? entry.rulebooks.length : 0;
    const sf = Array.isArray(entry.supplemental_files) ? entry.supplemental_files.length : 0;
    const p = platformStats(entry);
    return `rb:${rb} sup:${sf} plat:${p}`;
  }

  function setDirty(nextDirty) {
    state.dirty = !!nextDirty;
    els.dirtyStatus.textContent = state.dirty ? "Unsaved changes" : "Saved";
    els.dirtyStatus.classList.toggle("dirty", state.dirty);
    els.dirtyStatus.classList.toggle("clean", !state.dirty);
  }

  function updateCountStatus() {
    const count = Object.keys(state.data.games || {}).length;
    els.countStatus.textContent = `${count} game${count === 1 ? "" : "s"}`;
  }

  function renderGameList() {
    const term = String(els.search.value || "").trim().toLowerCase();
    const ids = getSortedIds();

    els.gameList.innerHTML = "";

    const fragment = document.createDocumentFragment();
    for (const id of ids) {
      const entry = normalizeEntry(state.data.games[id], "");
      const name = entry.name || "(unnamed)";
      const haystack = `${id} ${name}`.toLowerCase();
      if (term && !haystack.includes(term)) continue;

      const li = document.createElement("li");
      li.className = "game-item";
      if (state.selectedId === id) li.classList.add("active");
      li.dataset.id = id;

      const nameDiv = document.createElement("div");
      nameDiv.className = "name";
      nameDiv.textContent = `${name}`;

      const metaDiv = document.createElement("div");
      metaDiv.className = "meta";
      metaDiv.textContent = `${id} • ${gameSummary(entry)}`;

      li.appendChild(nameDiv);
      li.appendChild(metaDiv);
      fragment.appendChild(li);
    }

    els.gameList.appendChild(fragment);
    updateCountStatus();
  }

  function renderDocList(target, docs) {
    target.innerHTML = "";

    for (const doc of docs) {
      const row = els.docRowTemplate.content.firstElementChild.cloneNode(true);
      row.querySelector('[data-key="name"]').value = doc.name || "";
      row.querySelector('[data-key="url"]').value = doc.url || "";
      target.appendChild(row);
    }
  }

  function readDocList(target) {
    const docs = [];
    const rows = target.querySelectorAll(".doc-row");

    for (const row of rows) {
      const name = String(row.querySelector('[data-key="name"]').value || "").trim();
      const url = String(row.querySelector('[data-key="url"]').value || "").trim();
      if (!url) continue;
      const doc = { url };
      if (name) doc.name = name;
      docs.push(doc);
    }

    return docs;
  }

  function getSelectedEntry() {
    if (!state.selectedId) return null;
    return normalizeEntry(state.data.games[state.selectedId], "");
  }

  function showEditor(show) {
    els.emptyState.classList.toggle("hidden", show);
    els.editorForm.classList.toggle("hidden", !show);
  }

  function populateEditor(id) {
    const rawEntry = state.data.games[id];
    const entry = normalizeEntry(rawEntry, "");

    els.fieldId.value = id;
    els.fieldName.value = entry.name || "";
    els.fieldShortDescription.value = entry.short_description || "";

    renderDocList(els.rulebooksList, entry.rulebooks || []);
    renderDocList(els.supplementalList, entry.supplemental_files || []);

    for (const platform of PLATFORM_KEYS) {
      const card = document.querySelector(`.platform-card[data-platform="${platform}"]`);
      const p = normalizePlatform(entry[platform]);
      card.querySelector('[data-role="url"]').value = p.url || "";
      for (const flag of PLATFORM_FLAGS) {
        card.querySelector(`[data-role="${flag}"]`).checked = p[flag] === true;
      }
    }

    showEditor(true);
  }

  function collectEditorEntry() {
    const entry = {
      name: String(els.fieldName.value || "").trim(),
      short_description: String(els.fieldShortDescription.value || "").trim(),
      rulebooks: readDocList(els.rulebooksList),
      supplemental_files: readDocList(els.supplementalList),
    };

    for (const platform of PLATFORM_KEYS) {
      const card = document.querySelector(`.platform-card[data-platform="${platform}"]`);
      const url = String(card.querySelector('[data-role="url"]').value || "").trim();
      const out = {};
      if (url) out.url = url;
      for (const flag of PLATFORM_FLAGS) {
        if (card.querySelector(`[data-role="${flag}"]`).checked) {
          out[flag] = true;
        }
      }
      if (Object.keys(out).length > 0) {
        entry[platform] = out;
      }
    }

    return normalizeEntry(entry, "");
  }

  function commitEditorToState() {
    if (!state.selectedId) return;
    state.data.games[state.selectedId] = collectEditorEntry();
    setDirty(true);
    renderGameList();
  }

  function selectGame(id) {
    state.selectedId = id;
    populateEditor(id);
    renderGameList();
  }

  function createOrOpenById() {
    const raw = String(els.newId.value || "").trim();
    if (!/^\d+$/.test(raw)) {
      alert("BGG id must be numeric.");
      return;
    }

    const id = String(Number(raw));
    if (!state.data.games[id]) {
      state.data.games[id] = normalizeEntry({ name: "" }, "");
      setDirty(true);
    }

    selectGame(id);
    els.newId.value = "";
  }

  async function loadFromDefaultFile() {
    try {
      const resp = await fetch("game_metadata_overrides.json", { cache: "no-store" });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = await resp.json();
      state.data = ensureGamesContainer(data);
      state.selectedId = null;
      state.handle = null;
      setDirty(false);
      showEditor(false);
      renderGameList();
    } catch (err) {
      alert("Could not load game_metadata_overrides.json automatically. Use Import JSON instead.");
      console.error(err);
    }
  }

  async function importJsonFile(file) {
    const text = await file.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch (err) {
      alert("Invalid JSON file.");
      return;
    }

    state.data = ensureGamesContainer(data);
    state.selectedId = null;
    state.handle = null;
    setDirty(false);
    showEditor(false);
    renderGameList();
  }

  function downloadJson() {
    const sorted = exportSortedData();
    const payload = JSON.stringify(sorted, null, 2) + "\n";
    const blob = new Blob([payload], { type: "application/json" });
    const url = URL.createObjectURL(blob);

    const a = document.createElement("a");
    a.href = url;
    a.download = "game_metadata_overrides.json";
    document.body.appendChild(a);
    a.click();
    a.remove();

    URL.revokeObjectURL(url);
    setDirty(false);
  }

  function exportSortedData() {
    const output = { games: {} };
    const ids = Object.keys(state.data.games || {}).sort((a, b) => Number(a) - Number(b));
    for (const id of ids) {
      output.games[id] = normalizeEntry(state.data.games[id], "");
    }
    return output;
  }

  async function saveToPickedFile() {
    if (!window.showSaveFilePicker) {
      alert("Save to File is not supported in this browser. Use Download JSON instead.");
      return;
    }

    try {
      if (!state.handle) {
        state.handle = await window.showSaveFilePicker({
          suggestedName: "game_metadata_overrides.json",
          types: [{
            description: "JSON",
            accept: { "application/json": [".json"] },
          }],
        });
      }

      const writable = await state.handle.createWritable();
      const payload = JSON.stringify(exportSortedData(), null, 2) + "\n";
      await writable.write(payload);
      await writable.close();
      setDirty(false);
    } catch (err) {
      if (err && err.name === "AbortError") return;
      alert("Could not write file. Try Download JSON instead.");
      console.error(err);
    }
  }

  function addDocRow(target) {
    const row = els.docRowTemplate.content.firstElementChild.cloneNode(true);
    target.appendChild(row);
  }

  function onDocListClick(event) {
    const btn = event.target.closest("button[data-action='remove']");
    if (!btn) return;
    btn.closest(".doc-row").remove();
    commitEditorToState();
  }

  function onFormInput(event) {
    if (!state.selectedId) return;

    const target = event.target;
    if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)) {
      return;
    }

    commitEditorToState();
  }

  function onListClick(event) {
    const item = event.target.closest(".game-item");
    if (!item) return;
    selectGame(item.dataset.id);
  }

  function deleteSelectedGame() {
    if (!state.selectedId) return;
    const id = state.selectedId;
    if (!confirm(`Delete entry ${id}?`)) return;

    delete state.data.games[id];
    state.selectedId = null;
    showEditor(false);
    setDirty(true);
    renderGameList();
  }

  function bindEvents() {
    els.loadDefault.addEventListener("click", loadFromDefaultFile);

    els.fileInput.addEventListener("change", async (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      await importJsonFile(file);
      event.target.value = "";
    });

    els.downloadJson.addEventListener("click", downloadJson);
    els.savePickedFile.addEventListener("click", saveToPickedFile);
    els.createGame.addEventListener("click", createOrOpenById);
    els.search.addEventListener("input", renderGameList);
    els.gameList.addEventListener("click", onListClick);
    els.deleteGame.addEventListener("click", deleteSelectedGame);

    els.addRulebook.addEventListener("click", () => {
      addDocRow(els.rulebooksList);
      commitEditorToState();
    });

    els.addSupplemental.addEventListener("click", () => {
      addDocRow(els.supplementalList);
      commitEditorToState();
    });

    els.rulebooksList.addEventListener("click", onDocListClick);
    els.supplementalList.addEventListener("click", onDocListClick);
    els.editorForm.addEventListener("input", onFormInput);

    window.addEventListener("beforeunload", (event) => {
      if (!state.dirty) return;
      event.preventDefault();
      event.returnValue = "";
    });
  }

  function init() {
    state.data = { games: {} };
    setDirty(false);
    renderGameList();
    showEditor(false);
    bindEvents();
  }

  init();
})();
