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
    platformRowTemplate: document.getElementById("platform-row-template"),
  };

  const PLATFORM_KEYS = ["android", "ios", "pc"];
  const PLATFORM_FLAGS = ["owned", "wishlisted", "preordered", "monthly_subscription"];
  const PLATFORM_STORE_OPTIONS = {
    android: ["Play Store", "BGG", "Humble", "Amazon Appstore", "Samsung Galaxy Store", "itch.io"],
    ios: ["App Store", "TestFlight", "itch.io"],
    pc: ["Steam", "Web", "Tabletop Simulator", "Tabletopia", "Yucata", "BGA", "Epic", "EA app", "Ubisoft Connect", "GOG", "Microsoft Store", "itch.io", "Humble", "Amazon"],
  };

  function getFaviconUrl(domain) {
    return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64`;
  }

  function normalizeStoreKey(value) {
    return String(value || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  }

  function normalizeUrl(url) {
    const trimmed = String(url || "").trim();
    if (!trimmed) return "";
    if (/^https?:\/\//i.test(trimmed)) return trimmed;
    if (/^www\./i.test(trimmed)) return `https://${trimmed}`;
    return trimmed;
  }

  function detectStoreKeyFromUrl(url) {
    const normalizedUrl = normalizeUrl(url);
    if (!normalizedUrl) return "";

    try {
      const parsedUrl = new URL(normalizedUrl);
      const host = parsedUrl.hostname.toLowerCase();
      const path = parsedUrl.pathname.toLowerCase();
      const fullUrl = normalizedUrl.toLowerCase();

      if (
        host.includes("tabletopsimulator.com") ||
        fullUrl.includes("tabletopsimulator") ||
        path.includes("/app/286160")
      ) return "tabletop simulator";

      if (host.includes("tabletopia.com")) return "tabletopia";
      if (host.includes("yucata.de")) return "yucata";
      if (host.includes("boardgamearena")) return "board game arena";
      if (host.includes("steampowered") || host.includes("steamcommunity")) return "steam";
      if (host.includes("epicgames")) return "epic";
      if (host.includes("gog.com")) return "gog";
      if (host.includes("itch.io")) return "itch io";
      if (host.includes("play.google.com")) return "play store";
      if (host.includes("apps.apple.com")) return "app store";
      if (host.includes("microsoft.com") || host.includes("xbox.com")) return "microsoft store";
      if (host.includes("humblebundle")) return "humble";
      if (host.includes("amazon.")) return "amazon";
      if (host.includes("boardgamegeek.com")) return "bgg";
    } catch (_error) {
      // Ignore parse errors and fall through.
    }

    return "";
  }

  function getStoreIconMeta(store, url) {
    const storeKey = normalizeStoreKey(store) || detectStoreKeyFromUrl(url);
    if (!storeKey) return null;

    const knownStores = {
      "board game arena": { label: "BGA", title: "Board Game Arena", iconUrl: "https://boardgamearena.com/favicon.ico" },
      "bga": { label: "BGA", title: "Board Game Arena", iconUrl: "https://boardgamearena.com/favicon.ico" },
      "steam": { label: "STEAM", title: "Steam", iconUrl: getFaviconUrl("store.steampowered.com") },
      "epic": { label: "EPIC", title: "Epic Games", iconUrl: getFaviconUrl("www.epicgames.com") },
      "epic games": { label: "EPIC", title: "Epic Games", iconUrl: getFaviconUrl("www.epicgames.com") },
      "gog": { label: "GOG", title: "GOG", iconUrl: getFaviconUrl("www.gog.com") },
      "itch io": { label: "ITCH", title: "itch.io", iconUrl: getFaviconUrl("itch.io") },
      "itchio": { label: "ITCH", title: "itch.io", iconUrl: getFaviconUrl("itch.io") },
      "tabletop simulator": { label: "TTS", title: "Tabletop Simulator", iconUrl: "https://cdn2.steamgriddb.com/icon/68230fb510baa246a67bf901c7f895ea/32/256x256.png" },
      "table top simulator": { label: "TTS", title: "Tabletop Simulator", iconUrl: "https://cdn2.steamgriddb.com/icon/68230fb510baa246a67bf901c7f895ea/32/256x256.png" },
      "tts": { label: "TTS", title: "Tabletop Simulator", iconUrl: "https://cdn2.steamgriddb.com/icon/68230fb510baa246a67bf901c7f895ea/32/256x256.png" },
      "tabletopia": { label: "TTOP", title: "Tabletopia", iconUrl: "https://tabletopia.com/favicon.ico" },
      "yucata": { label: "YUC", title: "Yucata", iconUrl: "https://www.yucata.de/favicon.ico" },
      "yucata de": { label: "YUC", title: "Yucata", iconUrl: "https://www.yucata.de/favicon.ico" },
      "play store": { label: "PLAY", title: "Google Play", iconUrl: getFaviconUrl("play.google.com") },
      "google play": { label: "PLAY", title: "Google Play", iconUrl: getFaviconUrl("play.google.com") },
      "app store": { label: "APPLE", title: "App Store", iconUrl: getFaviconUrl("apps.apple.com") },
      "microsoft store": { label: "MS", title: "Microsoft Store", iconUrl: getFaviconUrl("www.microsoft.com") },
      "humble": { label: "HUMBLE", title: "Humble", iconUrl: "https://cdn.simpleicons.org/humblebundle" },
      "amazon": { label: "AMZ", title: "Amazon", iconUrl: getFaviconUrl("www.amazon.com") },
      "ea app": { label: "EA", title: "EA app", iconUrl: getFaviconUrl("www.ea.com") },
      "ubisoft connect": { label: "UBI", title: "Ubisoft Connect", iconUrl: getFaviconUrl("www.ubisoft.com") },
      "bgg": { label: "BGG", title: "BoardGameGeek", iconUrl: "https://cdn.simpleicons.org/boardgamegeek" },
      "web": { label: "WEB", title: "Web", iconUrl: getFaviconUrl("www.google.com") },
    };

    if (knownStores[storeKey]) {
      return knownStores[storeKey];
    }

    const compact = storeKey
      .split(" ")
      .filter(Boolean)
      .map((part) => part[0])
      .join("")
      .slice(0, 4)
      .toUpperCase() || "D";

    return {
      label: compact,
      title: String(store || storeKey).trim(),
      iconUrl: "",
    };
  }

  function updatePlatformRowIcon(row) {
    if (!row) return;

    const iconSlot = row.querySelector('[data-key="store-icon"]');
    const storeInput = row.querySelector('[data-key="store"]');
    const urlInput = row.querySelector('[data-key="url"]');
    if (!iconSlot || !storeInput || !urlInput) return;

    const meta = getStoreIconMeta(storeInput.value, urlInput.value);
    iconSlot.innerHTML = "";
    iconSlot.classList.remove("store-icon-badge");

    if (!meta) {
      iconSlot.title = "";
      iconSlot.removeAttribute("aria-label");
      return;
    }

    iconSlot.title = meta.title || "Store";
    iconSlot.setAttribute("aria-label", meta.title || "Store");

    if (meta.iconUrl) {
      const img = document.createElement("img");
      img.className = "store-icon-logo";
      img.src = meta.iconUrl;
      img.alt = meta.title || "Store icon";
      img.loading = "lazy";
      img.referrerPolicy = "no-referrer";
      img.addEventListener("error", () => {
        img.remove();
        iconSlot.textContent = meta.label || "D";
        iconSlot.classList.add("store-icon-badge");
      });
      iconSlot.appendChild(img);
    } else {
      iconSlot.textContent = meta.label || "D";
      iconSlot.classList.add("store-icon-badge");
    }
  }

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

  function normalizePlatformEntry(raw) {
    if (!raw || typeof raw !== "object") return null;

    const out = {};
    const store = String(raw.store || raw.service || raw.name || raw.label || "").trim();
    const url = String(raw.url || "").trim();

    // URL is required for digital implementations.
    if (!url) return null;

    if (store) out.store = store;
    if (url) out.url = url;

    const note = String(raw.note || raw.description || "").trim();
    if (note) out.note = note;

    for (const flag of PLATFORM_FLAGS) {
      if (raw[flag] === true) {
        out[flag] = true;
      }
    }

    if (raw.support_app === true || raw.state === "support_app") {
      out.support_app = true;
    }
    if (raw.monthly_subscription === true || raw.state === "monthly_subscription" || raw.state === "subscription") {
      out.monthly_subscription = true;
    }

    if (raw.state === "wishlisted") {
      out.wishlisted = true;
    } else if (raw.state === "preordered") {
      out.preordered = true;
    } else if (raw.state === "owned") {
      out.owned = true;
    }

    return Object.keys(out).length > 0 ? out : null;
  }

  function normalizePlatformEntries(raw) {
    const items = Array.isArray(raw) ? raw : (raw && typeof raw === "object" ? [raw] : []);
    return items.map(normalizePlatformEntry).filter(Boolean);
  }

  function getStoreOptions(platform) {
    return PLATFORM_STORE_OPTIONS[platform] || [];
  }

  function populateStoreSelect(select, platform, currentValue = "") {
    if (!select) return;

    const normalizedCurrentValue = String(currentValue || "").trim();
    const options = getStoreOptions(platform);
    select.innerHTML = "";

    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Select store...";
    select.appendChild(placeholder);

    options.forEach((optionValue) => {
      const option = document.createElement("option");
      option.value = optionValue;
      option.textContent = optionValue;
      select.appendChild(option);
    });

    if (normalizedCurrentValue && !options.includes(normalizedCurrentValue)) {
      const custom = document.createElement("option");
      custom.value = normalizedCurrentValue;
      custom.textContent = normalizedCurrentValue;
      select.appendChild(custom);
    }

    select.value = normalizedCurrentValue || options[0] || "";
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
      const p = normalizePlatformEntries(entry[platform]);
      if (p.length > 0) {
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
      if (Array.isArray(p)) {
        count += p.length;
      } else if (p && typeof p === "object" && Object.keys(p).length > 0) {
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

  function renderPlatformList(platform, entries) {
    const target = document.querySelector(`[data-platform-list="${platform}"]`);
    if (!target || !els.platformRowTemplate) return;

    target.innerHTML = "";
    const list = Array.isArray(entries) ? entries : [];

    if (list.length === 0) return;

    for (const entry of list) {
      addPlatformRow(platform, entry);
    }
  }

  function getPlatformState(entry) {
    if (!entry || typeof entry !== "object") return "";
    if (entry.monthly_subscription === true) return "monthly_subscription";
    if (entry.support_app === true) return "support_app";
    if (entry.preordered === true) return "preordered";
    if (entry.wishlisted === true) return "wishlisted";
    return "";
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

  function readPlatformList(platform) {
    const target = document.querySelector(`[data-platform-list="${platform}"]`);
    if (!target) return [];

    const entries = [];
    target.querySelectorAll('.platform-row').forEach((row) => {
      const store = String(row.querySelector('[data-key="store"]').value || "").trim();
      const url = String(row.querySelector('[data-key="url"]').value || "").trim();
      const state = String(row.querySelector('[data-key="state"]').value || "").trim();
      const note = String(row.querySelector('[data-key="note"]').value || "").trim();

      // Ignore rows without a URL.
      if (!url) return;

      const entry = {};
      if (store) entry.store = store;
      entry.url = url;
      if (note) entry.note = note;
      if (state === "monthly_subscription") {
        entry.monthly_subscription = true;
      } else if (state === "support_app") {
        entry.support_app = true;
      } else if (state === "wishlisted") {
        entry.wishlisted = true;
      } else if (state === "preordered") {
        entry.preordered = true;
      } else {
        entry.owned = true;
      }

      if (Object.keys(entry).length > 0) {
        entries.push(entry);
      }
    });

    return entries;
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
      renderPlatformList(platform, normalizePlatformEntries(entry[platform]));
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
      const platformEntries = readPlatformList(platform);
      if (platformEntries.length > 0) {
        entry[platform] = platformEntries;
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

  async function loadFromDefaultFile(options = {}) {
    const silent = !!options.silent;
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
      if (!silent) {
        alert("Could not load game_metadata_overrides.json. Use Import JSON instead.");
      }
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
    triggerJsonDownload();
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
    return saveJsonToFile();
  }

  function buildJsonPayload() {
    return JSON.stringify(exportSortedData(), null, 2) + "\n";
  }

  function triggerJsonDownload() {
    const payload = buildJsonPayload();
    const blob = new Blob([payload], { type: "application/json" });
    const url = URL.createObjectURL(blob);

    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "game_metadata_overrides.json";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();

    URL.revokeObjectURL(url);
    setDirty(false);
  }

  async function saveJsonToFile() {
    if (!window.showSaveFilePicker) {
      triggerJsonDownload();
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
      const payload = buildJsonPayload();
      await writable.write(payload);
      await writable.close();
      setDirty(false);
    } catch (err) {
      if (err && err.name === "AbortError") return;
      console.error(err);
      triggerJsonDownload();
    }
  }

  function addDocRow(target) {
    const row = els.docRowTemplate.content.firstElementChild.cloneNode(true);
    target.appendChild(row);
  }

  function addPlatformRow(platform, entry = {}) {
    const target = document.querySelector(`[data-platform-list="${platform}"]`);
    if (!target || !els.platformRowTemplate) return;

    const row = els.platformRowTemplate.content.firstElementChild.cloneNode(true);
    row.dataset.platform = platform;
    populateStoreSelect(row.querySelector('[data-key="store"]'), platform, entry.store || "");
    row.querySelector('[data-key="url"]').value = entry.url || "";
    row.querySelector('[data-key="state"]').value = getPlatformState(entry);
    row.querySelector('[data-key="note"]').value = entry.note || "";
    target.appendChild(row);
    updatePlatformRowIcon(row);
  }

  function onDocListClick(event) {
    const btn = event.target.closest("button[data-action='remove']");
    if (!btn) return;
    btn.closest(".doc-row").remove();
    commitEditorToState();
  }

  function onPlatformListClick(event) {
    const btn = event.target.closest("button[data-action='remove']");
    if (!btn) return;
    btn.closest(".platform-row").remove();
    commitEditorToState();
  }

  function onFormInput(event) {
    if (!state.selectedId) return;

    const target = event.target;
    if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement)) {
      return;
    }

    if (target.closest(".platform-row") && (target.matches('[data-key="store"]') || target.matches('[data-key="url"]'))) {
      updatePlatformRowIcon(target.closest(".platform-row"));
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
    els.loadDefault.addEventListener("click", () => loadFromDefaultFile());

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

    document.querySelectorAll('button[data-action="add-platform-entry"]').forEach((button) => {
      button.addEventListener('click', () => {
        addPlatformRow(button.dataset.platform);
        commitEditorToState();
      });
    });

    els.rulebooksList.addEventListener("click", onDocListClick);
    els.supplementalList.addEventListener("click", onDocListClick);
    document.querySelectorAll('[data-platform-list]').forEach((target) => {
      target.addEventListener('click', onPlatformListClick);
    });
    els.editorForm.addEventListener("input", onFormInput);
    els.editorForm.addEventListener("change", onFormInput);

    window.addEventListener("beforeunload", (event) => {
      if (!state.dirty) return;
      event.preventDefault();
      event.returnValue = "";
    });
  }

  async function init() {
    state.data = { games: {} };
    setDirty(false);
    bindEvents();
    showEditor(false);
    renderGameList();
    await loadFromDefaultFile({ silent: true });
  }

  init();
})();
