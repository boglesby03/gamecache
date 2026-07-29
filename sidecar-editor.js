(function () {
  "use strict";

  const state = {
    data: { games: {} },
    selectedId: null,
    dirty: false,
    shortDescriptionEditing: false,
    handle: null,
    coverArtById: {},
    coverArtLoaded: false,
    coverArtSource: "",
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
    fieldBggLink: document.getElementById("field-bgg-link"),
    fieldCoverImage: document.getElementById("field-cover-image"),
    fieldCoverFallback: document.getElementById("field-cover-fallback"),
    fieldCoverStatus: document.getElementById("field-cover-status"),
    fieldSqliteDisplayName: document.getElementById("field-sqlite-display-name"),
    fieldSqliteYear: document.getElementById("field-sqlite-year"),
    fieldSqliteRank: document.getElementById("field-sqlite-rank"),
    fieldSqliteRating: document.getElementById("field-sqlite-rating"),
    fieldSqlitePlayingTime: document.getElementById("field-sqlite-playing-time"),
    fieldSqliteMinAge: document.getElementById("field-sqlite-min-age"),
    fieldSqliteWeight: document.getElementById("field-sqlite-weight"),
    fieldSqliteNumowned: document.getElementById("field-sqlite-numowned"),
    fieldSqliteNumplays: document.getElementById("field-sqlite-numplays"),
    fieldShortDescriptionView: document.getElementById("field-short-description-view"),
    fieldShortDescription: document.getElementById("field-short-description"),
    shortDescriptionEdit: document.getElementById("short-description-edit"),
    shortDescriptionSave: document.getElementById("short-description-save"),
    rulebooksList: document.getElementById("rulebooks-list"),
    supplementalList: document.getElementById("supplemental-list"),
    addRulebook: document.getElementById("add-rulebook"),
    addSupplemental: document.getElementById("add-supplemental"),
    deleteGame: document.getElementById("delete-game"),
    docRowTemplate: document.getElementById("doc-row-template"),
    platformRowTemplate: document.getElementById("platform-row-template"),
  };

  const PLATFORM_KEYS = ["android", "ios", "pc"];
  const PLATFORM_FLAGS = ["owned", "online", "wishlisted", "preordered", "monthly_subscription"];
  const PLATFORM_STORE_OPTIONS = {
    android: ["Play Store", "BGG", "Humble", "Amazon Appstore", "Samsung Galaxy Store", "itch.io"],
    ios: ["App Store", "TestFlight", "itch.io"],
    pc: ["Steam", "Web", "Tabletop Simulator", "Tabletopia", "Yucata", "BGA", "Epic", "EA app", "Ubisoft Connect", "GOG", "Microsoft Store", "itch.io", "Humble", "Amazon"],
  };

  const GLOBE_ICON_URL = `data:image/svg+xml;utf8,${encodeURIComponent(
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><defs><radialGradient id='o' cx='35%' cy='30%' r='70%'><stop offset='0%' stop-color='#8fe3ff'/><stop offset='100%' stop-color='#1e88e5'/></radialGradient></defs><circle cx='32' cy='32' r='30' fill='url(#o)'/><path fill='#43a047' d='M14 22c5-7 12-10 18-10 2 3 4 5 7 6 3 1 8 1 11 4 2 2 1 5-1 7-2 2-5 2-7 5-1 2 0 4-2 6-3 2-7 0-10-2-3-2-4-6-8-7-4-1-8 2-10-1-2-3 0-6 2-8z'/><path fill='#66bb6a' d='M21 46c3 2 7 5 12 5 6 0 11-3 15-7-1-2-2-5-5-6-4-1-7 2-10 3-5 2-8 1-12-2-3-2-6-1-8 1 1 2 4 4 8 6z'/><circle cx='22' cy='20' r='3' fill='#81c784'/></svg>"
  )}`;

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

  function isOnlineEntry(raw, store, url, note) {
    if (!raw || typeof raw !== "object") return false;
    if (raw.online === true || raw.state === "online") return true;

    const storeKey = normalizeStoreKey(store);
    const normalizedUrl = normalizeUrl(url).toLowerCase();

    if (storeKey === "yucata" || storeKey === "yucata de" || normalizedUrl.includes("yucata.de")) return true;
    if (storeKey === "bga" || storeKey === "board game arena" || normalizedUrl.includes("boardgamearena.com")) return true;

    return false;
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
      "web": { label: "WEB", title: "Web", iconUrl: GLOBE_ICON_URL },
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

    const storeInput = row.querySelector('[data-key="store"]');
    const urlInput = row.querySelector('[data-key="url"]');
    if (!storeInput || !urlInput) return;

    const meta = getStoreIconMeta(storeInput.value, urlInput.value);

    row.querySelectorAll('[data-key="store-icon"], [data-key="view-store-icon"]').forEach((iconSlot) => {
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
    });
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

    if (isOnlineEntry(raw, store, url, note)) {
      out.online = true;
    }

    if (raw.state === "wishlisted") {
      out.wishlisted = true;
    } else if (raw.state === "preordered") {
      out.preordered = true;
    } else if (raw.state === "online") {
      out.online = true;
    } else if (raw.state === "owned") {
      out.owned = true;
    }

    if (out.online === true && out.owned === true) {
      delete out.owned;
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

  function getCoverInfoForGameId(id) {
    if (!id) return null;
    const cover = state.coverArtById[String(id)];
    return cover && typeof cover === "object" ? cover : null;
  }

  function getCoverThumbnailForList(id) {
    const cover = getCoverInfoForGameId(id);
    if (!cover) return "";
    return String(cover.thumbnail || "").trim();
  }

  function setSelectedCoverStatus(message) {
    if (!els.fieldCoverStatus) return;
    els.fieldCoverStatus.textContent = message;
  }

  function isPresentValue(value) {
    return value !== null && value !== undefined && String(value).trim() !== "";
  }

  function formatIntegerValue(value) {
    const numberValue = Number(value);
    if (!Number.isFinite(numberValue) || numberValue <= 0) return "-";
    return Math.round(numberValue).toLocaleString();
  }

  function formatDecimalValue(value, digits = 2) {
    const numberValue = Number(value);
    if (!Number.isFinite(numberValue) || numberValue <= 0) return "-";
    return numberValue.toFixed(digits);
  }

  function setMetaValue(element, value) {
    if (!element) return;
    element.textContent = value;
  }

  function renderSelectedSqliteDetails(id) {
    const cover = getCoverInfoForGameId(id);

    if (!cover) {
      setMetaValue(els.fieldSqliteDisplayName, "-");
      setMetaValue(els.fieldSqliteYear, "-");
      setMetaValue(els.fieldSqliteRank, "-");
      setMetaValue(els.fieldSqliteRating, "-");
      setMetaValue(els.fieldSqlitePlayingTime, "-");
      setMetaValue(els.fieldSqliteMinAge, "-");
      setMetaValue(els.fieldSqliteWeight, "-");
      setMetaValue(els.fieldSqliteNumowned, "-");
      setMetaValue(els.fieldSqliteNumplays, "-");
      return;
    }

    const displayName = String(cover.name || "").trim();
    setMetaValue(els.fieldSqliteDisplayName, displayName || "-");
    setMetaValue(els.fieldSqliteYear, formatIntegerValue(cover.year));
    setMetaValue(els.fieldSqliteRank, formatIntegerValue(cover.rank));
    setMetaValue(els.fieldSqliteRating, formatDecimalValue(cover.rating, 2));

    const playTime = formatIntegerValue(cover.playing_time);
    setMetaValue(els.fieldSqlitePlayingTime, playTime === "-" ? "-" : `${playTime} min`);

    const minAge = formatIntegerValue(cover.min_age);
    setMetaValue(els.fieldSqliteMinAge, minAge === "-" ? "-" : `${minAge}+`);

    setMetaValue(els.fieldSqliteWeight, formatDecimalValue(cover.weight, 2));
    setMetaValue(els.fieldSqliteNumowned, formatIntegerValue(cover.numowned));
    setMetaValue(els.fieldSqliteNumplays, formatIntegerValue(cover.numplays));
  }

  function renderSelectedCover(id, entry) {
    if (!els.fieldCoverImage || !els.fieldCoverFallback) return;

    const cover = getCoverInfoForGameId(id);
    const imageUrl = cover ? String(cover.image || cover.thumbnail || "").trim() : "";
    renderSelectedSqliteDetails(id);

    if (imageUrl) {
      const titleName = String(entry && entry.name ? entry.name : "Game").trim() || "Game";
      els.fieldCoverImage.src = imageUrl;
      els.fieldCoverImage.alt = `${titleName} cover art`;
      els.fieldCoverImage.classList.remove("hidden");
      els.fieldCoverFallback.classList.add("hidden");
      setSelectedCoverStatus("");
      return;
    }

    els.fieldCoverImage.removeAttribute("src");
    els.fieldCoverImage.classList.add("hidden");
    els.fieldCoverFallback.classList.remove("hidden");

    if (!state.coverArtLoaded) {
      setSelectedCoverStatus("Loading cover art from SQLite...");
    } else {
      setSelectedCoverStatus("No cover found for this game in SQLite.");
    }
  }

  function parseConfigIniValue(rawValue) {
    const value = String(rawValue || "").trim();
    if (!value) return "";
    const quoteWrapped =
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"));
    return quoteWrapped ? value.slice(1, -1).trim() : value;
  }

  async function readGitHubRepoFromConfig() {
    try {
      const response = await fetch("config.ini", { cache: "no-store" });
      if (!response.ok) return "";
      const text = await response.text();
      const lines = text.split(/\r?\n/);
      for (const rawLine of lines) {
        const line = String(rawLine || "").trim();
        if (!line || line.startsWith("#")) continue;
        const match = line.match(/^github_repo\s*=\s*(.+)$/i);
        if (!match) continue;
        return parseConfigIniValue(match[1]);
      }
    } catch (error) {
      console.debug("Could not read config.ini for repo fallback.", error);
    }
    return "";
  }

  async function getDatabaseCandidates() {
    const candidates = [
      { url: "gamecache.sqlite.gz", source: "local gamecache.sqlite.gz" },
      { url: "mybgg.sqlite.gz", source: "local mybgg.sqlite.gz" },
    ];

    const repo = await readGitHubRepoFromConfig();
    if (repo) {
      candidates.push({
        url: `https://cors-proxy.mybgg.workers.dev/${repo}`,
        source: `GitHub repo ${repo}`,
      });
    }

    return candidates;
  }

  async function fetchDatabaseBytes(candidates) {
    for (const candidate of candidates) {
      try {
        const response = await fetch(candidate.url, { cache: "no-store" });
        if (!response.ok) continue;
        const rawBytes = new Uint8Array(await response.arrayBuffer());
        let bytes = rawBytes;

        if (window.fflate && typeof window.fflate.gunzipSync === "function") {
          try {
            bytes = window.fflate.gunzipSync(rawBytes);
          } catch (_error) {
            // Some sources may already return decompressed SQLite bytes.
            bytes = rawBytes;
          }
        }

        return { bytes, source: candidate.source };
      } catch (_error) {
        // Try the next candidate.
      }
    }

    return null;
  }

  async function loadCoverArtIndexFromSqlite() {
    if (!window.initSqlJs) {
      console.warn("SQL.js was not loaded; cover art lookup is disabled.");
      state.coverArtLoaded = true;
      return;
    }

    try {
      const SQL = await window.initSqlJs({
        locateFile: (file) => `sqljs/${file}`,
      });

      const candidates = await getDatabaseCandidates();
      const dbPayload = await fetchDatabaseBytes(candidates);

      if (!dbPayload) {
        state.coverArtLoaded = true;
        if (state.selectedId) {
          renderSelectedCover(state.selectedId, getSelectedEntry());
        }
        return;
      }

      const db = new SQL.Database(dbPayload.bytes);
      const covers = {};
      const statement = db.prepare(`
        SELECT id, name, image, thumbnail, year, rank, rating, playing_time, min_age, weight, numowned, numplays
        FROM games
      `);

      while (statement.step()) {
        const row = statement.getAsObject();
        const id = String(row.id || "").trim();
        const image = String(row.image || "").trim();
        const thumbnail = String(row.thumbnail || "").trim();
        if (!id) continue;
        covers[id] = {
          name: String(row.name || "").trim(),
          image,
          thumbnail,
          year: row.year,
          rank: row.rank,
          rating: row.rating,
          playing_time: row.playing_time,
          min_age: row.min_age,
          weight: row.weight,
          numowned: row.numowned,
          numplays: row.numplays,
        };
      }

      statement.free();
      db.close();

      state.coverArtById = covers;
      state.coverArtSource = dbPayload.source;
      state.coverArtLoaded = true;

      renderGameList();
      if (state.selectedId) {
        renderSelectedCover(state.selectedId, getSelectedEntry());
      }
    } catch (error) {
      console.warn("Failed to load cover art from SQLite.", error);
      state.coverArtLoaded = true;
      if (state.selectedId) {
        renderSelectedCover(state.selectedId, getSelectedEntry());
      }
    }
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

      const rowTop = document.createElement("div");
      rowTop.className = "game-item-row";

      const listCoverUrl = getCoverThumbnailForList(id);
      if (listCoverUrl) {
        const cover = document.createElement("img");
        cover.className = "game-item-cover";
        cover.src = listCoverUrl;
        cover.alt = `${name} cover`;
        cover.loading = "lazy";
        cover.referrerPolicy = "no-referrer";
        rowTop.appendChild(cover);
      }

      const textWrap = document.createElement("div");
      textWrap.className = "game-item-text";

      const nameDiv = document.createElement("div");
      nameDiv.className = "name";
      nameDiv.textContent = `${name}`;

      const metaDiv = document.createElement("div");
      metaDiv.className = "meta";
      metaDiv.textContent = `${id} • ${gameSummary(entry)}`;

      textWrap.appendChild(nameDiv);
      textWrap.appendChild(metaDiv);
      rowTop.appendChild(textWrap);
      li.appendChild(rowTop);
      fragment.appendChild(li);
    }

    els.gameList.appendChild(fragment);
    updateCountStatus();
  }

  function renderDocList(target, docs) {
    target.innerHTML = "";

    for (const doc of docs) {
      addDocRow(target, doc, { editing: false, hasSaved: true });
    }
  }

  function renderPlatformList(platform, entries) {
    const target = document.querySelector(`[data-platform-list="${platform}"]`);
    if (!target || !els.platformRowTemplate) return;

    target.innerHTML = "";
    const list = Array.isArray(entries) ? entries : [];

    if (list.length === 0) return;

    for (const entry of list) {
      addPlatformRow(platform, entry, { editing: false, hasSaved: true });
    }
  }

  function getPlatformState(entry) {
    if (!entry || typeof entry !== "object") return "";
    if (entry.monthly_subscription === true) return "monthly_subscription";
    if (entry.support_app === true) return "support_app";
    if (entry.preordered === true) return "preordered";
    if (entry.wishlisted === true) return "wishlisted";
    if (entry.online === true || isOnlineEntry(entry, entry.store || "", entry.url || "", entry.note || "")) return "online";
    return "";
  }

  function readDocList(target) {
    const docs = [];
    const rows = target.querySelectorAll(".doc-row");

    for (const row of rows) {
      if (row.dataset.editing === "true") {
        if (row.dataset.hasSaved === "true") {
          const savedDoc = readSavedRowPayload(row);
          if (savedDoc && savedDoc.url) docs.push(savedDoc);
        }
        continue;
      }

      const doc = readDocRowInputs(row);
      if (!doc) continue;
      docs.push(doc);
    }

    return docs;
  }

  function readPlatformList(platform) {
    const target = document.querySelector(`[data-platform-list="${platform}"]`);
    if (!target) return [];

    const entries = [];
    target.querySelectorAll('.platform-row').forEach((row) => {
      if (row.dataset.editing === "true") {
        if (row.dataset.hasSaved === "true") {
          const savedEntry = readSavedRowPayload(row);
          if (savedEntry && savedEntry.url) entries.push(savedEntry);
        }
        return;
      }

      const entry = readPlatformRowInputs(row);
      if (entry) entries.push(entry);
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
    if (!show && els.fieldBggLink) {
      els.fieldBggLink.classList.add("hidden");
      els.fieldBggLink.removeAttribute("href");
    }
    if (!show) {
      state.shortDescriptionEditing = false;
    }
  }

  function getSavedShortDescriptionValue() {
    return String(els.fieldShortDescription?.dataset.savedValue || "").trim();
  }

  function updateShortDescriptionViewText() {
    if (!els.fieldShortDescriptionView) return;
    const savedValue = getSavedShortDescriptionValue();
    els.fieldShortDescriptionView.textContent = savedValue || "No short description yet.";
  }

  function setShortDescriptionEditing(editing) {
    state.shortDescriptionEditing = !!editing;

    if (!els.fieldShortDescription || !els.fieldShortDescriptionView || !els.shortDescriptionEdit || !els.shortDescriptionSave) {
      return;
    }

    if (!state.shortDescriptionEditing) {
      els.fieldShortDescription.value = getSavedShortDescriptionValue();
    }

    els.fieldShortDescription.disabled = !state.shortDescriptionEditing;
    els.fieldShortDescription.classList.toggle("hidden", !state.shortDescriptionEditing);
    els.fieldShortDescriptionView.classList.toggle("hidden", state.shortDescriptionEditing);
    els.shortDescriptionEdit.classList.toggle("hidden", state.shortDescriptionEditing);
    els.shortDescriptionSave.classList.toggle("hidden", !state.shortDescriptionEditing);

    updateShortDescriptionViewText();
  }

  function initializeShortDescription(value) {
    if (!els.fieldShortDescription) return;
    const normalized = String(value || "").trim();
    els.fieldShortDescription.dataset.savedValue = normalized;
    els.fieldShortDescription.value = normalized;
    setShortDescriptionEditing(false);
  }

  function saveShortDescription() {
    if (!els.fieldShortDescription) return;
    const normalized = String(els.fieldShortDescription.value || "").trim();
    els.fieldShortDescription.dataset.savedValue = normalized;
    setShortDescriptionEditing(false);
    commitEditorToState();
  }

  function updateBggDetailLink(id) {
    if (!els.fieldBggLink) return;
    const gameId = String(id || "").trim();
    if (!/^\d+$/.test(gameId)) {
      els.fieldBggLink.classList.add("hidden");
      els.fieldBggLink.removeAttribute("href");
      return;
    }

    const href = `https://boardgamegeek.com/boardgame/${gameId}`;
    els.fieldBggLink.href = href;
    els.fieldBggLink.title = href;
    els.fieldBggLink.setAttribute("aria-label", `View game ${gameId} on BoardGameGeek`);
    els.fieldBggLink.classList.remove("hidden");
  }

  function populateEditor(id) {
    const rawEntry = state.data.games[id];
    const entry = normalizeEntry(rawEntry, "");

    els.fieldId.value = id;
    els.fieldName.value = entry.name || "";
    updateBggDetailLink(id);
    initializeShortDescription(entry.short_description || "");
    renderSelectedCover(id, entry);

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
      short_description: getSavedShortDescriptionValue(),
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

  function addDocRow(target, entry = {}, options = {}) {
    const row = els.docRowTemplate.content.firstElementChild.cloneNode(true);
    const editing = options.editing !== false;
    const hasSaved = options.hasSaved === true;

    row.querySelector('[data-key="name"]').value = entry.name || "";
    row.querySelector('[data-key="url"]').value = entry.url || "";
    if (hasSaved) {
      writeSavedRowPayload(row, readDocRowInputs(row));
    }
    row.dataset.hasSaved = hasSaved ? "true" : "false";
    setRowEditing(row, editing);

    target.appendChild(row);
    return row;
  }

  function addPlatformRow(platform, entry = {}, options = {}) {
    const target = document.querySelector(`[data-platform-list="${platform}"]`);
    if (!target || !els.platformRowTemplate) return;

    const row = els.platformRowTemplate.content.firstElementChild.cloneNode(true);
    const editing = options.editing !== false;
    const hasSaved = options.hasSaved === true;

    row.dataset.platform = platform;
    populateStoreSelect(row.querySelector('[data-key="store"]'), platform, entry.store || "");
    row.querySelector('[data-key="url"]').value = entry.url || "";
    row.querySelector('[data-key="state"]').value = getPlatformState(entry);
    row.querySelector('[data-key="note"]').value = entry.note || "";
    if (hasSaved) {
      writeSavedRowPayload(row, readPlatformRowInputs(row));
    }
    row.dataset.hasSaved = hasSaved ? "true" : "false";
    target.appendChild(row);
    setRowEditing(row, editing);
    updatePlatformRowIcon(row);
    return row;
  }

  function writeSavedRowPayload(row, payload) {
    row.dataset.savedPayload = payload ? JSON.stringify(payload) : "";
  }

  function readSavedRowPayload(row) {
    try {
      return row.dataset.savedPayload ? JSON.parse(row.dataset.savedPayload) : null;
    } catch (_error) {
      return null;
    }
  }

  function readDocRowInputs(row) {
    const name = String(row.querySelector('[data-key="name"]').value || "").trim();
    const url = String(row.querySelector('[data-key="url"]').value || "").trim();
    if (!url) return null;
    const doc = { url };
    if (name) doc.name = name;
    return doc;
  }

  function readPlatformRowInputs(row) {
    const store = String(row.querySelector('[data-key="store"]').value || "").trim();
    const url = String(row.querySelector('[data-key="url"]').value || "").trim();
    const stateValue = String(row.querySelector('[data-key="state"]').value || "").trim();
    const note = String(row.querySelector('[data-key="note"]').value || "").trim();

    if (!url) return null;

    const entry = {};
    if (store) entry.store = store;
    entry.url = url;
    if (note) entry.note = note;
    if (stateValue === "monthly_subscription") {
      entry.monthly_subscription = true;
    } else if (stateValue === "support_app") {
      entry.support_app = true;
    } else if (stateValue === "wishlisted") {
      entry.wishlisted = true;
    } else if (stateValue === "preordered") {
      entry.preordered = true;
    } else if (stateValue === "online") {
      entry.online = true;
    } else {
      entry.owned = true;
    }
    return entry;
  }

  function updateRowLinkPreview(row) {
    const link = row.querySelector('[data-key="view-link"]');
    const urlInput = row.querySelector('[data-key="url"]');
    if (!link || !urlInput) return;

    const value = normalizeUrl(urlInput.value || "");
    if (!value || row.dataset.editing === "true") {
      link.classList.add("hidden");
      link.removeAttribute("href");
      return;
    }

    link.href = value;
    link.title = value;
    link.setAttribute("aria-label", `Open link: ${value}`);
    link.classList.remove("hidden");
  }

  function getStateLabel(stateValue) {
    const value = String(stateValue || "").trim();
    if (!value) return "Owned";
    if (value === "monthly_subscription") return "Subscribe";
    if (value === "support_app") return "Support App";
    if (value === "wishlisted") return "Wishlisted";
    if (value === "preordered") return "Preordered";
    if (value === "online") return "Online";
    return value;
  }

  function updateDocRowView(row) {
    const nameTarget = row.querySelector('[data-key="view-name"]');
    const urlTarget = row.querySelector('[data-key="view-url"]');
    if (!nameTarget || !urlTarget) return;

    const name = String(row.querySelector('[data-key="name"]').value || "").trim();
    const url = normalizeUrl(row.querySelector('[data-key="url"]').value || "");

    nameTarget.textContent = name || "Document link";
    urlTarget.textContent = url || "No URL";
  }

  function updatePlatformRowView(row) {
    const storeTarget = row.querySelector('[data-key="view-store"]');
    const statusTarget = row.querySelector('[data-key="view-status"]');
    const noteTarget = row.querySelector('[data-key="view-note"]');
    const urlTarget = row.querySelector('[data-key="view-url"]');
    if (!storeTarget || !statusTarget || !noteTarget || !urlTarget) return;

    const store = String(row.querySelector('[data-key="store"]').value || "").trim();
    const stateValue = String(row.querySelector('[data-key="state"]').value || "").trim();
    const note = String(row.querySelector('[data-key="note"]').value || "").trim();
    const url = normalizeUrl(row.querySelector('[data-key="url"]').value || "");

    storeTarget.textContent = store || "Unspecified Store";
    statusTarget.textContent = getStateLabel(stateValue);
    noteTarget.textContent = note;
    noteTarget.classList.toggle("hidden", !note);
    urlTarget.textContent = url || "No URL";
  }

  function updateRowViewPreview(row) {
    if (row.classList.contains("doc-row")) {
      updateDocRowView(row);
      return;
    }
    if (row.classList.contains("platform-row")) {
      updatePlatformRowView(row);
    }
  }

  function setRowEditing(row, editing) {
    row.dataset.editing = editing ? "true" : "false";

    const rowView = row.querySelector('[data-key="row-view"]');
    if (rowView) {
      rowView.classList.toggle("hidden", editing);
    }

    row.querySelectorAll("input, select").forEach((input) => {
      input.disabled = !editing;
    });

    row.querySelectorAll('button[data-action="edit-row"]').forEach((button) => {
      button.classList.toggle("hidden", editing);
    });
    row.querySelectorAll('button[data-action="save-row"]').forEach((button) => {
      button.classList.toggle("hidden", !editing);
    });

    updateRowViewPreview(row);
    updateRowLinkPreview(row);
  }

  function onDocListClick(event) {
    const btn = event.target.closest("button[data-action]");
    if (!btn) return;

    const row = btn.closest(".doc-row");
    if (!row) return;
    const action = btn.dataset.action;

    if (action === "remove") {
      row.remove();
      commitEditorToState();
      return;
    }

    if (action === "edit-row") {
      setRowEditing(row, true);
      return;
    }

    if (action === "save-row") {
      const payload = readDocRowInputs(row);
      if (!payload) {
        alert("Document rows need a URL before saving.");
        return;
      }
      writeSavedRowPayload(row, payload);
      row.dataset.hasSaved = "true";
      setRowEditing(row, false);
      commitEditorToState();
    }
  }

  function onPlatformListClick(event) {
    const btn = event.target.closest("button[data-action]");
    if (!btn) return;

    const row = btn.closest(".platform-row");
    if (!row) return;
    const action = btn.dataset.action;

    if (action === "remove") {
      row.remove();
      commitEditorToState();
      return;
    }

    if (action === "edit-row") {
      setRowEditing(row, true);
      return;
    }

    if (action === "save-row") {
      const payload = readPlatformRowInputs(row);
      if (!payload) {
        alert("Platform rows need a URL before saving.");
        return;
      }
      writeSavedRowPayload(row, payload);
      row.dataset.hasSaved = "true";
      setRowEditing(row, false);
      updatePlatformRowIcon(row);
      commitEditorToState();
    }
  }

  function onFormInput(event) {
    if (!state.selectedId) return;

    const target = event.target;
    if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement)) {
      return;
    }

    if (target === els.fieldShortDescription) {
      return;
    }

    const docRow = target.closest(".doc-row");
    const platformRow = target.closest(".platform-row");

    if (docRow || platformRow) {
      if (platformRow && (target.matches('[data-key="store"]') || target.matches('[data-key="url"]'))) {
        updatePlatformRowIcon(platformRow);
      }
      updateRowViewPreview(docRow || platformRow);
      updateRowLinkPreview(docRow || platformRow);
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
      addDocRow(els.rulebooksList, {}, { editing: true, hasSaved: false });
    });

    if (els.shortDescriptionEdit) {
      els.shortDescriptionEdit.addEventListener("click", () => {
        setShortDescriptionEditing(true);
        if (els.fieldShortDescription) {
          els.fieldShortDescription.focus();
          els.fieldShortDescription.select();
        }
      });
    }

    if (els.shortDescriptionSave) {
      els.shortDescriptionSave.addEventListener("click", saveShortDescription);
    }

    els.addSupplemental.addEventListener("click", () => {
      addDocRow(els.supplementalList, {}, { editing: true, hasSaved: false });
    });

    document.querySelectorAll('button[data-action="add-platform-entry"]').forEach((button) => {
      button.addEventListener('click', () => {
        addPlatformRow(button.dataset.platform, {}, { editing: true, hasSaved: false });
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
    await Promise.all([
      loadFromDefaultFile({ silent: true }),
      loadCoverArtIndexFromSqlite(),
    ]);
  }

  init();
})();
