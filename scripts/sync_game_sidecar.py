#!/usr/bin/env python3
"""Sync game metadata stubs into game_metadata_overrides.json from SQLite.

This script ensures every game id present in the SQLite `games` table has an
entry in the sidecar file under `games` with at least:
- name
- short_description
- rulebooks
- supplemental_files

Existing entries are preserved and only missing keys are added.
"""

import argparse
import html
import json
import sqlite3
import re
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, Tuple, List, Any, Optional, Set


DEFAULT_SIDECAR = "game_metadata_overrides.json"
DEFAULT_DB = "gamecache.sqlite"
YUCATA_CATALOG_API = "https://www.yucata.de/Services/YucataService.svc/GetGamesWithTags"
TABLETOPIA_CATALOG_API = "https://api.tabletopia.com/games"
VASSAL_PROJECTS_API = "https://vassalengine.org/api/gls/v1/projects"
TABLETOP_SIMULATOR_WORKSHOP_SEARCH_URL = "https://steamcommunity.com/workshop/browse/"
BRETTSPIELWELT_SPIELE_URL = "https://www.brettspielwelt.de/Spiele/"

# Some Yucata titles don't expose an IdName that matches BGG naming conventions.
# Use explicit game-id overrides so sync can still add/maintain correct links.
YUCATA_GAME_ID_OVERRIDES: Dict[str, str] = {
    "305221": "https://www.yucata.de/en/GameInfo/DTB",  # Dinosaur Table Battles
    "191977": "https://www.yucata.de/en/GameInfo/CoBCard",  # Castles of Burgundy: The Card Game
    "9217": "https://www.yucata.de/en/GameInfo/SaintPetersburg2",  # Saint Petersburg
    "79828": "https://www.yucata.de/en/GameInfo/FewAcresOfSnow",  # A Few Acres of Snow
    "84876": "https://www.yucata.de/en/GameInfo/CastlesOfBurgundy",  # Castles of Burgundy
    "156009": "https://www.yucata.de/en/GameInfo/PortRoyal2",  # Port Royal
    "157809": "https://www.yucata.de/en/GameInfo/NationsDiceGame",  # Nations: The Dice Game
    "227224": "https://www.yucata.de/en/GameInfo/RedCathedral",  # The Red Cathedral
    "371942": "https://www.yucata.de/en/GameInfo/WhiteCastle",  # The White Castle
    "37380": "https://www.yucata.de/en/GameInfo/RollAges",  # Roll Through the Ages: The Bronze Age
    "182874": "https://www.yucata.de/en/GameInfo/GrandAustria",  # Grand Austria Hotel
    "193558": "https://www.yucata.de/en/GameInfo/Delphi",  # The Oracle of Delphi
    "203993": "https://www.yucata.de/en/GameInfo/Lorenzo",  # Lorenzo il Magnifico
    "220877": "https://www.yucata.de/en/GameInfo/Rajas",  # Rajas of the Ganges
    "312484": "https://www.yucata.de/en/GameInfo/Arnak",  # Lost Ruins of Arnak
    "318553": "https://www.yucata.de/en/GameInfo/RajasDice",  # Rajas of the Ganges: The Dice Charmers
    "144733": "https://www.yucata.de/en/GameInfo/RRR2",  # Russian Railroads
}

# Some BrettspielWelt slugs are localized or product-line based and don't
# normalize to BGG naming conventions. Keep deterministic per-game overrides.
BRETTSPIELWELT_GAME_ID_OVERRIDES: Dict[str, str] = {
    "13": "https://www.brettspielwelt.de/Spiele/Siedler/",  # Catan
    "41": "https://www.brettspielwelt.de/Spiele/CantStop/",  # Can't Stop!
    "8203": "https://www.brettspielwelt.de/Spiele/Fischen/",  # Hey, That's My Fish!
    "24480": "https://www.brettspielwelt.de/Spiele/DieSaeulenDerErde/",  # The Pillars of the Earth
    "54138": "https://www.brettspielwelt.de/Spiele/Imperial/",  # Imperial 2030
    "136888": "https://www.brettspielwelt.de/Spiele/Bruegge/",  # Bruges
    "156943": "https://www.brettspielwelt.de/Spiele/SanktPetersburg/",  # Saint Petersburg (Second Edition)
    "171623": "https://www.brettspielwelt.de/Spiele/",  # The Voyages of Marco Polo (no dedicated slug found)
    "244522": "https://www.brettspielwelt.de/Spiele/GanzSchoenClever/",  # Ganz Schön Clever
    "463783": "https://www.brettspielwelt.de/Spiele/LasVegas/",  # Las Vegas
    "199966": "https://www.brettspielwelt.de/Spiele/Kingsburg/",  # Kingsburg (Second Edition)
    "425064": "https://www.brettspielwelt.de/Spiele/Kingsburg/",  # Kingsburg (Third Edition)
}

# Optional note overrides for explicit edition fallback mappings.
BRETTSPIELWELT_GAME_ID_NOTE_OVERRIDES: Dict[str, str] = {
    "13": "Siedler",
    "41": "Can't Stop",
    "8203": "Fischen",
    "24480": "Die Saeulen der Erde",
    "54138": "Imperial",
    "136888": "Bruegge",
    "156943": "Sankt Petersburg",
    "171623": "BrettspielWelt Spiele",
    "199966": "Kingsburg",
    "425064": "Kingsburg",
}


def choose_canonical_names(conn: sqlite3.Connection) -> List[Tuple[int, str]]:
    """Return one canonical name per BGG id from SQLite.

    Ranking preference:
    1) Rows tagged own
    2) Rows tagged preordered
    3) Shorter names
    4) Alphabetical
    """
    cur = conn.cursor()
    cur.execute(
        """
        WITH ranked AS (
          SELECT
            id,
            name,
            tags,
            ROW_NUMBER() OVER (
              PARTITION BY id
              ORDER BY
                CASE WHEN tags LIKE '%"own"%' THEN 0 ELSE 1 END,
                CASE WHEN tags LIKE '%"preordered"%' THEN 1 ELSE 2 END,
                LENGTH(name),
                name
            ) AS rn
          FROM games
          WHERE id IS NOT NULL AND name IS NOT NULL AND TRIM(name) <> ''
        )
        SELECT id, name
        FROM ranked
        WHERE rn = 1
        ORDER BY LOWER(name)
        """
    )
    return cur.fetchall()


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _split_camel(value: str) -> str:
    return re.sub(r"([a-z])([A-Z])", r"\1 \2", value or "")


def _tokenize_words(value: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (value or "").lower()) if t]


def _acronym(tokens: List[str]) -> str:
    if not tokens:
        return ""
    return "".join(t[0] for t in tokens if t)


def _candidate_name_keys(value: str) -> List[str]:
    """Return ordered candidate keys for cross-catalog matching.

    Includes exact normalized names plus lightweight abbreviation heuristics
    (e.g. "Dinosaur Table Battles" -> "dtb", and
    "Castles of Burgundy: The Card Game" -> "cobcard").
    """
    keys: List[str] = []
    seen: Set[str] = set()

    def add(key: str) -> None:
        key = _normalize_name(key)
        if key and key not in seen:
            seen.add(key)
            keys.append(key)

    raw = str(value or "").strip()
    if not raw:
        return keys

    add(raw)
    add(_split_camel(raw))

    tokens = _tokenize_words(raw)
    if tokens:
        add("".join(tokens))
        add(_acronym(tokens))
        no_articles = [t for t in tokens if t not in {"the", "a", "an"}]
        add("".join(no_articles))
        add(_acronym(no_articles))
        core = [t for t in no_articles if t not in {"of", "and", "or", "to", "for", "with"}]
        add("".join(core))
        add(_acronym(core))

    # Handle subtitle forms like "X: The Card Game" that often map to compact IdNames.
    if ":" in raw:
        base, suffix = raw.split(":", 1)
        base_tokens = _tokenize_words(base)
        suffix_tokens = _tokenize_words(suffix)
        base_no_articles = [t for t in base_tokens if t not in {"the", "a", "an"}]
        base_core = [t for t in base_no_articles if t not in {"of", "and", "or", "to", "for", "with"}]
        base_acronym = _acronym(base_core) or _acronym(base_no_articles) or _acronym(base_tokens)
        if base_acronym:
            add(base_acronym)
            for marker in ("card", "dice", "duel", "solo"):
                if marker in suffix_tokens:
                    add(base_acronym + marker)

    return keys


def load_yucata_candidates(conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    """Load games that appear to have Yucata implementations from SQLite metadata.

    We consider a game Yucata-capable when its `families` JSON contains a
    "Digital Implementations: Yucata" entry.
    """
    cur = conn.cursor()
    cur.execute(
        """
        WITH ranked AS (
          SELECT
            id,
            name,
            tags,
            families,
            alternate_names,
            ROW_NUMBER() OVER (
              PARTITION BY id
              ORDER BY
                CASE WHEN tags LIKE '%"own"%' THEN 0 ELSE 1 END,
                CASE WHEN tags LIKE '%"preordered"%' THEN 1 ELSE 2 END,
                LENGTH(name),
                name
            ) AS rn
          FROM games
          WHERE id IS NOT NULL AND name IS NOT NULL AND TRIM(name) <> ''
        )
        SELECT id, name, families, alternate_names
        FROM ranked
        WHERE rn = 1
        """
    )

    candidates: Dict[str, Dict[str, Any]] = {}
    for game_id, name, families_raw, alt_raw in cur.fetchall():
        has_yucata_family = False
        try:
            families = json.loads(families_raw or "[]")
            if isinstance(families, list):
                for family in families:
                    family_name = ""
                    if isinstance(family, dict):
                        family_name = str(family.get("name", ""))
                    elif isinstance(family, str):
                        family_name = family
                    if "digital implementations: yucata" in family_name.lower():
                        has_yucata_family = True
                        break
        except Exception:
            pass

        if not has_yucata_family:
            continue

        alt_names: List[str] = []
        try:
            parsed_alt = json.loads(alt_raw or "[]")
            if isinstance(parsed_alt, list):
                alt_names = [str(item).strip() for item in parsed_alt if str(item).strip()]
        except Exception:
            pass

        key = str(int(game_id))
        candidates[key] = {
            "id": key,
            "name": str(name or "").strip(),
            "alternate_names": alt_names,
        }

    return candidates


def load_tabletopia_candidates(conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    """Load games that appear to have Tabletopia implementations from SQLite metadata."""
    cur = conn.cursor()
    cur.execute(
        """
        WITH ranked AS (
          SELECT
            id,
            name,
            tags,
            families,
            alternate_names,
            ROW_NUMBER() OVER (
              PARTITION BY id
              ORDER BY
                CASE WHEN tags LIKE '%"own"%' THEN 0 ELSE 1 END,
                CASE WHEN tags LIKE '%"preordered"%' THEN 1 ELSE 2 END,
                LENGTH(name),
                name
            ) AS rn
          FROM games
          WHERE id IS NOT NULL AND name IS NOT NULL AND TRIM(name) <> ''
        )
        SELECT id, name, families, alternate_names
        FROM ranked
        WHERE rn = 1
        """
    )

    candidates: Dict[str, Dict[str, Any]] = {}
    for game_id, name, families_raw, alt_raw in cur.fetchall():
        has_tabletopia_family = False
        try:
            families = json.loads(families_raw or "[]")
            if isinstance(families, list):
                for family in families:
                    family_name = ""
                    if isinstance(family, dict):
                        family_name = str(family.get("name", ""))
                    elif isinstance(family, str):
                        family_name = family
                    if "digital implementations: tabletopia" in family_name.lower():
                        has_tabletopia_family = True
                        break
        except Exception:
            pass

        if not has_tabletopia_family:
            continue

        alt_names: List[str] = []
        try:
            parsed_alt = json.loads(alt_raw or "[]")
            if isinstance(parsed_alt, list):
                alt_names = [str(item).strip() for item in parsed_alt if str(item).strip()]
        except Exception:
            pass

        key = str(int(game_id))
        candidates[key] = {
            "id": key,
            "name": str(name or "").strip(),
            "alternate_names": alt_names,
        }

    return candidates


def load_vassal_candidates(conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    """Load games that appear to have VASSAL implementations from SQLite metadata."""
    cur = conn.cursor()
    cur.execute(
        """
        WITH ranked AS (
          SELECT
            id,
            name,
            tags,
            families,
            alternate_names,
            ROW_NUMBER() OVER (
              PARTITION BY id
              ORDER BY
                CASE WHEN tags LIKE '%"own"%' THEN 0 ELSE 1 END,
                CASE WHEN tags LIKE '%"preordered"%' THEN 1 ELSE 2 END,
                LENGTH(name),
                name
            ) AS rn
          FROM games
          WHERE id IS NOT NULL AND name IS NOT NULL AND TRIM(name) <> ''
        )
        SELECT id, name, families, alternate_names
        FROM ranked
        WHERE rn = 1
        """
    )

    candidates: Dict[str, Dict[str, Any]] = {}
    for game_id, name, families_raw, alt_raw in cur.fetchall():
        has_vassal_family = False
        try:
            families = json.loads(families_raw or "[]")
            if isinstance(families, list):
                for family in families:
                    family_name = ""
                    if isinstance(family, dict):
                        family_name = str(family.get("name", ""))
                    elif isinstance(family, str):
                        family_name = family
                    normalized_family = family_name.lower()
                    if (
                        "digital implementations: vassal" in normalized_family
                        or "digital implementations: vassel" in normalized_family
                    ):
                        has_vassal_family = True
                        break
        except Exception:
            pass

        if not has_vassal_family:
            continue

        alt_names: List[str] = []
        try:
            parsed_alt = json.loads(alt_raw or "[]")
            if isinstance(parsed_alt, list):
                alt_names = [str(item).strip() for item in parsed_alt if str(item).strip()]
        except Exception:
            pass

        key = str(int(game_id))
        candidates[key] = {
            "id": key,
            "name": str(name or "").strip(),
            "alternate_names": alt_names,
        }

    return candidates


def load_tabletop_simulator_candidates(conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    """Load games that appear to have Tabletop Simulator implementations from SQLite metadata."""
    cur = conn.cursor()
    cur.execute(
        """
        WITH ranked AS (
          SELECT
            id,
            name,
            tags,
            families,
            alternate_names,
            ROW_NUMBER() OVER (
              PARTITION BY id
              ORDER BY
                CASE WHEN tags LIKE '%"own"%' THEN 0 ELSE 1 END,
                CASE WHEN tags LIKE '%"preordered"%' THEN 1 ELSE 2 END,
                LENGTH(name),
                name
            ) AS rn
          FROM games
          WHERE id IS NOT NULL AND name IS NOT NULL AND TRIM(name) <> ''
        )
        SELECT id, name, families, alternate_names
        FROM ranked
        WHERE rn = 1
        """
    )

    candidates: Dict[str, Dict[str, Any]] = {}
    for game_id, name, families_raw, alt_raw in cur.fetchall():
        has_tts_family = False
        try:
            families = json.loads(families_raw or "[]")
            if isinstance(families, list):
                for family in families:
                    family_name = ""
                    if isinstance(family, dict):
                        family_name = str(family.get("name", ""))
                    elif isinstance(family, str):
                        family_name = family
                    normalized_family = family_name.lower()
                    if "digital implementations: tabletop simulator" in normalized_family:
                        has_tts_family = True
                        break
        except Exception:
            pass

        if not has_tts_family:
            continue

        alt_names: List[str] = []
        try:
            parsed_alt = json.loads(alt_raw or "[]")
            if isinstance(parsed_alt, list):
                alt_names = [str(item).strip() for item in parsed_alt if str(item).strip()]
        except Exception:
            pass

        key = str(int(game_id))
        candidates[key] = {
            "id": key,
            "name": str(name or "").strip(),
            "alternate_names": alt_names,
        }

    return candidates


def load_brettspielwelt_candidates(conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    """Load games that appear to have BrettspielWelt implementations from SQLite metadata."""
    cur = conn.cursor()
    cur.execute(
        """
        WITH ranked AS (
          SELECT
            id,
            name,
            tags,
            families,
            alternate_names,
            ROW_NUMBER() OVER (
              PARTITION BY id
              ORDER BY
                CASE WHEN tags LIKE '%"own"%' THEN 0 ELSE 1 END,
                CASE WHEN tags LIKE '%"preordered"%' THEN 1 ELSE 2 END,
                LENGTH(name),
                name
            ) AS rn
          FROM games
          WHERE id IS NOT NULL AND name IS NOT NULL AND TRIM(name) <> ''
        )
        SELECT id, name, families, alternate_names
        FROM ranked
        WHERE rn = 1
        """
    )

    candidates: Dict[str, Dict[str, Any]] = {}
    for game_id, name, families_raw, alt_raw in cur.fetchall():
        has_bsw_family = False
        try:
            families = json.loads(families_raw or "[]")
            if isinstance(families, list):
                for family in families:
                    family_name = ""
                    if isinstance(family, dict):
                        family_name = str(family.get("name", ""))
                    elif isinstance(family, str):
                        family_name = family
                    normalized_family = family_name.lower()
                    if "digital implementations: brettspielwelt" in normalized_family:
                        has_bsw_family = True
                        break
        except Exception:
            pass

        key = str(int(game_id))
        has_override = key in BRETTSPIELWELT_GAME_ID_OVERRIDES

        if not has_bsw_family and not has_override:
            continue

        alt_names: List[str] = []
        try:
            parsed_alt = json.loads(alt_raw or "[]")
            if isinstance(parsed_alt, list):
                alt_names = [str(item).strip() for item in parsed_alt if str(item).strip()]
        except Exception:
            pass

        candidates[key] = {
            "id": key,
            "name": str(name or "").strip(),
            "alternate_names": alt_names,
        }

    return candidates


def fetch_yucata_game_urls(timeout: float = 20.0) -> Dict[str, str]:
    """Fetch Yucata catalog and map normalized names to GameInfo URLs."""
    payload = b"{}"
    req = urllib.request.Request(
        YUCATA_CATALOG_API,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (GameCache sidecar sync)",
        },
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")

    data = json.loads(body)
    games = data.get("d", {}).get("Games", [])

    mapping: Dict[str, str] = {}
    for game in games:
        if not isinstance(game, dict):
            continue
        id_name = str(game.get("IdName", "")).strip()
        if not id_name:
            continue
        url = f"https://www.yucata.de/en/GameInfo/{id_name}"

        raw_keys = {
            _normalize_name(id_name),
            _normalize_name(_split_camel(id_name)),
        }

        for key in raw_keys:
            if not key:
                continue
            # Keep first hit to avoid noisy overwrites for ambiguous keys.
            mapping.setdefault(key, url)

    return mapping


def collect_unmatched_yucata_candidates(
    data: Dict[str, Any],
    candidates: Dict[str, Dict[str, Any]],
    yucata_map: Dict[str, str],
) -> List[Tuple[str, str, List[str]]]:
    """Return Yucata-family candidates still unresolved after matching + overrides."""
    games = data.get("games", {})
    if not isinstance(games, dict):
        return []

    misses: List[Tuple[str, str, List[str]]] = []
    for game_id, candidate in candidates.items():
        entry = games.get(game_id)
        if not isinstance(entry, dict):
            continue

        existing = _find_existing_yucata_link(entry)
        if existing:
            continue

        candidate_names = [candidate.get("name", "")] + list(candidate.get("alternate_names", []))
        found = False
        for candidate_name in candidate_names:
            for key in _candidate_name_keys(str(candidate_name)):
                if key in yucata_map:
                    found = True
                    break
            if found:
                break

        if not found and str(YUCATA_GAME_ID_OVERRIDES.get(str(game_id), "")).strip():
            found = True

        if not found:
            misses.append(
                (
                    str(game_id),
                    str(candidate.get("name", "")).strip(),
                    [str(v).strip() for v in candidate_names[1:] if str(v).strip()][:3],
                )
            )

    return misses


def fetch_tabletopia_game_catalog(timeout: float = 20.0) -> Tuple[Dict[str, str], Dict[str, bool], Dict[str, str]]:
    """Fetch Tabletopia catalog and map normalized names to game URLs.

    Also returns:
    - mapping of shortUrl -> premium flag (hasOnlyPremiumSetups)
    - mapping of shortUrl -> catalog display name
    """
    req = urllib.request.Request(
        f"{TABLETOPIA_CATALOG_API}?take=5000&skip=0",
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (GameCache sidecar sync)",
        },
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")

    data = json.loads(body)
    records = data.get("records", []) if isinstance(data, dict) else []

    mapping: Dict[str, str] = {}
    premium_by_short_url: Dict[str, bool] = {}
    name_by_short_url: Dict[str, str] = {}

    for record in records:
        if not isinstance(record, dict):
            continue

        short_url = str(record.get("shortUrl", "")).strip()
        name = str(record.get("name", "")).strip()
        if not short_url:
            continue

        url = f"https://tabletopia.com/games/{short_url}"

        keys = {
            _normalize_name(short_url),
            _normalize_name(_split_camel(short_url)),
            _normalize_name(name),
        }
        for key in keys:
            if key:
                mapping.setdefault(key, url)

        premium_by_short_url[short_url.lower()] = bool(record.get("hasOnlyPremiumSetups", False))
        if name:
            name_by_short_url[short_url.lower()] = name

    return mapping, premium_by_short_url, name_by_short_url


def fetch_vassal_project_catalog(timeout: float = 20.0, limit: int = 100) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Fetch VASSAL project catalog and map normalized names to project URLs.

    Returns:
    - mapping normalized key -> project URL
    - mapping slug -> catalog display title
    """
    mapping: Dict[str, str] = {}
    title_by_slug: Dict[str, str] = {}

    safe_limit = max(1, min(int(limit), 100))
    next_query = f"?limit={safe_limit}"
    visited_queries: Set[str] = set()

    while next_query:
        if next_query in visited_queries:
            break
        visited_queries.add(next_query)

        req = urllib.request.Request(
            f"{VASSAL_PROJECTS_API}{next_query}",
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (GameCache sidecar sync)",
            },
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        payload = json.loads(body)

        projects = payload.get("projects", []) if isinstance(payload, dict) else []
        meta = payload.get("meta", {}) if isinstance(payload, dict) else {}

        for project in projects:
            if not isinstance(project, dict):
                continue

            slug = str(project.get("slug", "")).strip()
            name = str(project.get("name", "")).strip()
            game = project.get("game") if isinstance(project.get("game"), dict) else {}
            game_title = str(game.get("title", "")).strip() if isinstance(game, dict) else ""

            if not slug:
                continue

            project_url = f"https://vassalengine.org/library/projects/{slug}"

            key_sources = [slug, name, game_title]
            for source_name in key_sources:
                for key in _candidate_name_keys(source_name):
                    mapping.setdefault(key, project_url)

            title_candidate = game_title or name or slug.replace("_", " ")
            if title_candidate:
                title_by_slug[slug.lower()] = title_candidate

        next_page = str(meta.get("next_page", "") or "").strip()
        next_query = next_page if next_page.startswith("?") else ""

    return mapping, title_by_slug


def fetch_brettspielwelt_catalog(timeout: float = 20.0) -> Dict[str, str]:
    """Fetch BrettspielWelt Spiele catalog and map normalized keys to game URLs."""
    req = urllib.request.Request(
        BRETTSPIELWELT_SPIELE_URL,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 (GameCache sidecar sync)",
        },
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")

    mapping: Dict[str, str] = {}
    seen_slugs: Set[str] = set()
    for match in re.finditer(r'href="/Spiele/([^"#?]+)/"', body, flags=re.IGNORECASE):
        slug = str(match.group(1) or "").strip()
        if not slug or slug.lower() == "spiele":
            continue
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        game_url = f"https://www.brettspielwelt.de/Spiele/{slug}/"
        for key in _brettspielwelt_name_keys(slug):
            mapping.setdefault(key, game_url)

    return mapping


def _brettspielwelt_name_keys(value: str) -> List[str]:
    """Return conservative keys for BrettspielWelt matching.

    Unlike `_candidate_name_keys`, this intentionally avoids acronym keys because
    they can cause high-collision false positives against short BSW slugs.
    """
    raw = str(value or "").strip()
    if not raw:
        return []

    keys: List[str] = []
    seen: Set[str] = set()

    def add(v: str) -> None:
        k = _normalize_name(v)
        if k and k not in seen:
            seen.add(k)
            keys.append(k)

    add(raw)
    add(_split_camel(raw))

    tokens = _tokenize_words(raw)
    if tokens:
        add("".join(tokens))
        no_articles = [t for t in tokens if t not in {"the", "a", "an"}]
        if no_articles:
            add("".join(no_articles))

    if ":" in raw:
        base, _suffix = raw.split(":", 1)
        add(base)

    return keys


def _fetch_tts_workshop_matches_for_query(query: str, timeout: float = 20.0) -> List[Tuple[str, str]]:
    """Return parsed TTS workshop (url, title) pairs from Steam browse search HTML."""
    query = str(query or "").strip()
    if not query:
        return []

    params = urllib.parse.urlencode(
        {
            "appid": "286160",  # Tabletop Simulator
            "searchtext": query,
            "childpublishedfileid": "0",
            "browsesort": "textsearch",
            "section": "home",
        }
    )
    url = f"{TABLETOP_SIMULATOR_WORKSHOP_SEARCH_URL}?{params}"

    req = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 (GameCache sidecar sync)",
        },
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")

    matches: List[Tuple[str, str]] = []
    seen_urls: Set[str] = set()

    pattern = re.compile(
        r'href="(https://steamcommunity\.com/sharedfiles/filedetails/\?id=\d+)"[^>]*>\s*<img[^>]*alt="([^"]+)"',
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(body):
        item_url = str(match.group(1) or "").strip()
        item_title = html.unescape(str(match.group(2) or "").strip())
        if not item_url or not item_title:
            continue
        if item_url in seen_urls:
            continue
        seen_urls.add(item_url)
        matches.append((item_url, item_title))

    return matches


def _score_tts_title_match(candidate_name: str, result_title: str) -> int:
    """Return a conservative confidence score for candidate<->workshop title matching."""
    candidate_name = str(candidate_name or "").strip()
    result_title = str(result_title or "").strip()
    if not candidate_name or not result_title:
        return 0

    candidate_norm = _normalize_name(candidate_name)
    title_norm = _normalize_name(result_title)
    if not candidate_norm or not title_norm:
        return 0

    if candidate_norm == title_norm:
        return 100

    candidate_keys = set(_candidate_name_keys(candidate_name))
    title_keys = set(_candidate_name_keys(result_title))
    if candidate_keys & title_keys:
        return 90

    if candidate_norm in title_norm and len(candidate_norm) >= 8:
        extra = max(0, len(title_norm) - len(candidate_norm))
        return max(70, 85 - min(extra, 15))

    if title_norm in candidate_norm and len(title_norm) >= 8:
        return 72

    return 0


def _find_existing_yucata_link(entry: Dict[str, Any]) -> Optional[str]:
    for platform in ("android", "ios", "pc"):
        platform_items = entry.get(platform)
        if isinstance(platform_items, dict):
            platform_items = [platform_items]
        if not isinstance(platform_items, list):
            continue
        for item in platform_items:
            if not isinstance(item, dict):
                continue
            store = str(item.get("store", "")).strip().lower()
            url = str(item.get("url", "")).strip()
            if "yucata" in store or "yucata.de" in url.lower():
                return url
    return None


def _find_existing_tabletopia_link(entry: Dict[str, Any]) -> Optional[str]:
    for platform in ("android", "ios", "pc"):
        platform_items = entry.get(platform)
        if isinstance(platform_items, dict):
            platform_items = [platform_items]
        if not isinstance(platform_items, list):
            continue
        for item in platform_items:
            if not isinstance(item, dict):
                continue
            store = str(item.get("store", "")).strip().lower()
            url = str(item.get("url", "")).strip().lower()
            if "tabletopia" in store or "tabletopia.com" in url:
                return str(item.get("url", "")).strip()
    return None


def _find_existing_vassal_link(entry: Dict[str, Any]) -> Optional[str]:
    for platform in ("android", "ios", "pc"):
        platform_items = entry.get(platform)
        if isinstance(platform_items, dict):
            platform_items = [platform_items]
        if not isinstance(platform_items, list):
            continue
        for item in platform_items:
            if not isinstance(item, dict):
                continue
            store = str(item.get("store", "")).strip().lower()
            url = str(item.get("url", "")).strip().lower()
            if "vassal" in store or "vassalengine.org" in url:
                return str(item.get("url", "")).strip()
    return None


def _find_existing_tabletop_simulator_link(entry: Dict[str, Any]) -> Optional[str]:
    for platform in ("android", "ios", "pc"):
        platform_items = entry.get(platform)
        if isinstance(platform_items, dict):
            platform_items = [platform_items]
        if not isinstance(platform_items, list):
            continue
        for item in platform_items:
            if not isinstance(item, dict):
                continue
            store = str(item.get("store", "")).strip().lower()
            url = str(item.get("url", "")).strip().lower()
            if "tabletop simulator" in store or "steamcommunity.com/sharedfiles/filedetails/" in url:
                return str(item.get("url", "")).strip()
    return None


def _find_existing_brettspielwelt_link(entry: Dict[str, Any]) -> Optional[str]:
    for platform in ("android", "ios", "pc"):
        platform_items = entry.get(platform)
        if isinstance(platform_items, dict):
            platform_items = [platform_items]
        if not isinstance(platform_items, list):
            continue
        for item in platform_items:
            if not isinstance(item, dict):
                continue
            store = str(item.get("store", "")).strip().lower()
            url = str(item.get("url", "")).strip().lower()
            if "brettspielwelt" in store or "brettspielwelt.de" in url:
                return str(item.get("url", "")).strip()
    return None


def _extract_vassal_slug(url: str) -> str:
    match = re.search(r"vassalengine\.org/library/projects/([^/?#]+)", str(url or ""), flags=re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip().lower()


def _extract_tabletopia_short_url(url: str) -> str:
    match = re.search(r"tabletopia\.com/games/([^/?#]+)", str(url or ""), flags=re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip().lower()


def _ensure_platform_list(entry: Dict[str, Any], platform: str) -> List[Dict[str, Any]]:
    platform_items = entry.get(platform)
    if isinstance(platform_items, list):
        return platform_items
    if isinstance(platform_items, dict):
        entry[platform] = [platform_items]
        return entry[platform]
    entry[platform] = []
    return entry[platform]


def annotate_yucata_notes(data: Dict[str, Any]) -> int:
    """Ensure each Yucata digital implementation has note=<game name>.

    Returns the number of entries changed.
    """
    games = data.get("games", {})
    if not isinstance(games, dict):
        return 0

    updated = 0
    for _game_id, entry in games.items():
        if not isinstance(entry, dict):
            continue

        game_name = str(entry.get("name", "")).strip()
        if not game_name:
            continue

        for platform in ("android", "ios", "pc"):
            platform_items = entry.get(platform)
            if isinstance(platform_items, dict):
                platform_items = [platform_items]
                entry[platform] = platform_items
            if not isinstance(platform_items, list):
                continue

            for item in platform_items:
                if not isinstance(item, dict):
                    continue
                store = str(item.get("store", "")).strip().lower()
                url = str(item.get("url", "")).strip().lower()
                if "yucata" not in store and "yucata.de" not in url:
                    continue

                current_note = str(item.get("note", "")).strip()
                if current_note == game_name:
                    if item.get("online") is not True:
                        item["online"] = True
                        updated += 1
                    continue

                item["note"] = game_name
                if item.get("online") is not True:
                    item["online"] = True
                updated += 1

    return updated


def _normalize_store_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _should_mark_online(item: Dict[str, Any], tabletopia_premium_by_short_url: Optional[Dict[str, bool]] = None) -> bool:
    store = str(item.get("store", "")).strip()
    url = str(item.get("url", "")).strip()
    note = str(item.get("note", "")).strip()

    if item.get("monthly_subscription") is True:
        return False

    if item.get("online") is True:
        return True

    store_key = _normalize_store_key(store)
    url_lower = url.lower()
    note_lower = note.lower()
    store_lower = store.lower()

    if store_key in ("yucata", "yucatade") or "yucata.de" in url_lower:
        return True
    if store_key == "vassal" or "vassalengine.org" in url_lower:
        return True
    if store_key == "brettspielwelt" or "brettspielwelt.de" in url_lower:
        return True
    if (
        "tabletopsimulator" in store_key
        or store_key == "tts"
        or "steamcommunity.com/sharedfiles/filedetails/" in url_lower
    ):
        return True
    if store_key in ("bga", "boardgamearena") or "boardgamearena.com" in url_lower:
        return True

    mentions_tabletopia = store_key == "tabletopia" or "tabletopia.com" in url_lower or "tabletopia" in store_lower
    if mentions_tabletopia and tabletopia_premium_by_short_url:
        short_url = _extract_tabletopia_short_url(url)
        is_premium = bool(tabletopia_premium_by_short_url.get(short_url, False)) if short_url else False
        if short_url and not is_premium:
            return True

    return False


def annotate_online_statuses(data: Dict[str, Any], tabletopia_premium_by_short_url: Optional[Dict[str, bool]] = None) -> int:
    """Backfill online=true for Yucata, VASSAL, TTS, BGA, and free Tabletopia entries."""
    games = data.get("games", {})
    if not isinstance(games, dict):
        return 0

    updated = 0
    for _game_id, entry in games.items():
        if not isinstance(entry, dict):
            continue

        for platform in ("android", "ios", "pc"):
            platform_items = entry.get(platform)
            if isinstance(platform_items, dict):
                platform_items = [platform_items]
                entry[platform] = platform_items
            if not isinstance(platform_items, list):
                continue

            for item in platform_items:
                if not isinstance(item, dict):
                    continue
                if not _should_mark_online(item, tabletopia_premium_by_short_url=tabletopia_premium_by_short_url):
                    continue
                changed = False
                if item.get("online") is not True:
                    item["online"] = True
                    changed = True
                if item.get("owned") is True:
                    item.pop("owned", None)
                    changed = True
                if changed:
                    updated += 1

    return updated


def strip_owned_from_online_entries(data: Dict[str, Any]) -> int:
    """Enforce rule: entries marked online must not also be owned."""
    games = data.get("games", {})
    if not isinstance(games, dict):
        return 0

    removed = 0
    for _game_id, entry in games.items():
        if not isinstance(entry, dict):
            continue

        for platform in ("android", "ios", "pc"):
            platform_items = entry.get(platform)
            if isinstance(platform_items, dict):
                platform_items = [platform_items]
                entry[platform] = platform_items
            if not isinstance(platform_items, list):
                continue

            for item in platform_items:
                if not isinstance(item, dict):
                    continue
                if item.get("online") is True and item.get("owned") is True:
                    item.pop("owned", None)
                    removed += 1

    return removed


def annotate_tabletopia_statuses_and_notes(
    data: Dict[str, Any],
    tabletopia_premium_by_short_url: Optional[Dict[str, bool]] = None,
    tabletopia_name_by_short_url: Optional[Dict[str, str]] = None,
) -> int:
    """Apply Tabletopia rules:

    - Note must be game name (or Tabletopia catalog name when game name is blank).
    - Non-premium => online=true and monthly_subscription removed.
    - Premium => monthly_subscription=true and online removed.
    - online/monthly_subscription entries must not keep owned=true.
    """
    games = data.get("games", {})
    if not isinstance(games, dict):
        return 0

    updated = 0
    for _game_id, entry in games.items():
        if not isinstance(entry, dict):
            continue

        game_name = str(entry.get("name", "")).strip()

        for platform in ("android", "ios", "pc"):
            platform_items = entry.get(platform)
            if isinstance(platform_items, dict):
                platform_items = [platform_items]
                entry[platform] = platform_items
            if not isinstance(platform_items, list):
                continue

            for item in platform_items:
                if not isinstance(item, dict):
                    continue

                store = str(item.get("store", "")).strip().lower()
                url = str(item.get("url", "")).strip().lower()
                if "tabletopia" not in store and "tabletopia.com" not in url:
                    continue

                changed = False

                note_target = game_name
                short_url = _extract_tabletopia_short_url(url)
                if not note_target and short_url and tabletopia_name_by_short_url:
                    note_target = str(tabletopia_name_by_short_url.get(short_url, "")).strip()

                if note_target and str(item.get("note", "")).strip() != note_target:
                    item["note"] = note_target
                    changed = True

                is_premium = False
                if short_url and tabletopia_premium_by_short_url:
                    is_premium = bool(tabletopia_premium_by_short_url.get(short_url, False))

                if short_url and is_premium:
                    if item.get("monthly_subscription") is not True:
                        item["monthly_subscription"] = True
                        changed = True
                    if item.get("online") is True:
                        item.pop("online", None)
                        changed = True
                elif short_url:
                    if item.get("online") is not True:
                        item["online"] = True
                        changed = True
                    if item.get("monthly_subscription") is True:
                        item.pop("monthly_subscription", None)
                        changed = True

                if (item.get("online") is True or item.get("monthly_subscription") is True) and item.get("owned") is True:
                    item.pop("owned", None)
                    changed = True

                if changed:
                    updated += 1

    return updated


def annotate_vassal_statuses_and_notes(
    data: Dict[str, Any],
    vassal_title_by_slug: Optional[Dict[str, str]] = None,
) -> int:
    """Apply VASSAL rules:

    - Note must be game name (or VASSAL catalog title when game name is blank).
    - VASSAL entries are online=true.
    - online entries must not keep owned=true.
    """
    games = data.get("games", {})
    if not isinstance(games, dict):
        return 0

    updated = 0
    for _game_id, entry in games.items():
        if not isinstance(entry, dict):
            continue

        game_name = str(entry.get("name", "")).strip()

        for platform in ("android", "ios", "pc"):
            platform_items = entry.get(platform)
            if isinstance(platform_items, dict):
                platform_items = [platform_items]
                entry[platform] = platform_items
            if not isinstance(platform_items, list):
                continue

            for item in platform_items:
                if not isinstance(item, dict):
                    continue

                store = str(item.get("store", "")).strip().lower()
                url = str(item.get("url", "")).strip().lower()
                if "vassal" not in store and "vassalengine.org" not in url:
                    continue

                changed = False

                note_target = game_name
                slug = _extract_vassal_slug(url)
                if not note_target and slug and vassal_title_by_slug:
                    note_target = str(vassal_title_by_slug.get(slug, "")).strip()

                if note_target and str(item.get("note", "")).strip() != note_target:
                    item["note"] = note_target
                    changed = True

                if item.get("online") is not True:
                    item["online"] = True
                    changed = True

                if item.get("owned") is True:
                    item.pop("owned", None)
                    changed = True

                if changed:
                    updated += 1

    return updated


def enrich_yucata_links(data: Dict[str, Any], candidates: Dict[str, Dict[str, Any]], yucata_map: Dict[str, str]) -> Tuple[int, int]:
    """Add missing Yucata links to sidecar entries.

    Returns:
        (added_links, candidate_count)
    """
    games = data.setdefault("games", {})
    added_links = 0

    for game_id, candidate in candidates.items():
        entry = games.get(game_id)
        if not isinstance(entry, dict):
            # sync_sidecar creates/normalizes the basic object first.
            continue

        existing = _find_existing_yucata_link(entry)
        if existing:
            continue

        candidate_names = [candidate.get("name", "")] + list(candidate.get("alternate_names", []))
        match_url = ""
        for candidate_name in candidate_names:
            for key in _candidate_name_keys(str(candidate_name)):
                if key in yucata_map:
                    match_url = yucata_map[key]
                    break
            if match_url:
                break

        if not match_url:
            match_url = str(YUCATA_GAME_ID_OVERRIDES.get(str(game_id), "")).strip()

        if not match_url:
            continue

        pc_entries = _ensure_platform_list(entry, "pc")
        pc_entries.append({
            "store": "Yucata",
            "url": match_url,
            "online": True,
        })
        added_links += 1

    return added_links, len(candidates)


def enrich_tabletopia_links(
    data: Dict[str, Any],
    candidates: Dict[str, Dict[str, Any]],
    tabletopia_map: Dict[str, str],
    tabletopia_premium_by_short_url: Optional[Dict[str, bool]] = None,
    tabletopia_name_by_short_url: Optional[Dict[str, str]] = None,
) -> Tuple[int, int]:
    """Add missing Tabletopia links to sidecar entries."""
    games = data.setdefault("games", {})
    added_links = 0

    for game_id, candidate in candidates.items():
        entry = games.get(game_id)
        if not isinstance(entry, dict):
            continue

        existing = _find_existing_tabletopia_link(entry)
        if existing:
            continue

        candidate_names = [candidate.get("name", "")] + list(candidate.get("alternate_names", []))
        match_url = ""
        for candidate_name in candidate_names:
            normalized = _normalize_name(str(candidate_name))
            if not normalized:
                continue
            if normalized in tabletopia_map:
                match_url = tabletopia_map[normalized]
                break

        if not match_url:
            continue

        short_url = _extract_tabletopia_short_url(match_url)
        fallback_catalog_name = ""
        if short_url and tabletopia_name_by_short_url:
            fallback_catalog_name = str(tabletopia_name_by_short_url.get(short_url, "")).strip()

        payload: Dict[str, Any] = {
            "store": "Tabletopia",
            "url": match_url,
            "note": str(candidate.get("name", "")).strip() or fallback_catalog_name or "",
        }
        if payload.get("note") == "":
            payload.pop("note", None)

        is_premium = False
        if short_url and tabletopia_premium_by_short_url:
            is_premium = bool(tabletopia_premium_by_short_url.get(short_url, False))

        if short_url and is_premium:
            payload["monthly_subscription"] = True
        elif short_url:
            payload["online"] = True

        pc_entries = _ensure_platform_list(entry, "pc")
        pc_entries.append(payload)
        added_links += 1

    return added_links, len(candidates)


def enrich_vassal_links(
    data: Dict[str, Any],
    candidates: Dict[str, Dict[str, Any]],
    vassal_map: Dict[str, str],
    vassal_title_by_slug: Optional[Dict[str, str]] = None,
) -> Tuple[int, int]:
    """Add missing VASSAL links to sidecar entries."""
    games = data.setdefault("games", {})
    added_links = 0

    for game_id, candidate in candidates.items():
        entry = games.get(game_id)
        if not isinstance(entry, dict):
            continue

        existing = _find_existing_vassal_link(entry)
        if existing:
            continue

        candidate_names = [candidate.get("name", "")] + list(candidate.get("alternate_names", []))
        match_url = ""
        for candidate_name in candidate_names:
            for key in _candidate_name_keys(str(candidate_name)):
                if key in vassal_map:
                    match_url = vassal_map[key]
                    break
            if match_url:
                break

        if not match_url:
            continue

        slug = _extract_vassal_slug(match_url)
        fallback_catalog_name = ""
        if slug and vassal_title_by_slug:
            fallback_catalog_name = str(vassal_title_by_slug.get(slug, "")).strip()

        payload: Dict[str, Any] = {
            "store": "VASSAL",
            "url": match_url,
            "online": True,
            "note": str(candidate.get("name", "")).strip() or fallback_catalog_name or "",
        }
        if payload.get("note") == "":
            payload.pop("note", None)

        pc_entries = _ensure_platform_list(entry, "pc")
        pc_entries.append(payload)
        added_links += 1

    return added_links, len(candidates)


def enrich_tabletop_simulator_links(
    data: Dict[str, Any],
    candidates: Dict[str, Dict[str, Any]],
    timeout: float = 20.0,
) -> Tuple[int, int]:
    """Add missing Tabletop Simulator workshop links to sidecar entries.

    Uses a conservative, name-based search over Steam Workshop and only accepts
    high-confidence matches.
    """
    games = data.setdefault("games", {})
    added_links = 0

    for game_id, candidate in candidates.items():
        entry = games.get(game_id)
        if not isinstance(entry, dict):
            continue

        existing = _find_existing_tabletop_simulator_link(entry)
        if existing:
            continue

        candidate_names = [candidate.get("name", "")] + list(candidate.get("alternate_names", []))
        best_url = ""
        best_title = ""
        best_score = 0

        # Query only first few distinct names to control request volume.
        queried_names: List[str] = []
        for candidate_name in candidate_names:
            query_name = str(candidate_name or "").strip()
            if not query_name or query_name in queried_names:
                continue
            queried_names.append(query_name)
            if len(queried_names) > 3:
                break

            try:
                results = _fetch_tts_workshop_matches_for_query(query_name, timeout=timeout)
            except Exception:
                continue

            for item_url, item_title in results[:25]:
                score = 0
                for name_for_scoring in queried_names:
                    score = max(score, _score_tts_title_match(name_for_scoring, item_title))
                if score > best_score:
                    best_score = score
                    best_url = item_url
                    best_title = item_title

            if best_score >= 90:
                break

        # Keep matching strict to avoid accidental bad links.
        if best_score < 90 or not best_url:
            continue

        payload: Dict[str, Any] = {
            "store": "Tabletop Simulator",
            "url": best_url,
            "online": True,
            "note": str(candidate.get("name", "")).strip() or best_title,
        }
        if not payload.get("note"):
            payload.pop("note", None)

        pc_entries = _ensure_platform_list(entry, "pc")
        pc_entries.append(payload)
        added_links += 1

    return added_links, len(candidates)


def enrich_brettspielwelt_links(
    data: Dict[str, Any],
    candidates: Dict[str, Dict[str, Any]],
    brettspielwelt_map: Dict[str, str],
) -> Tuple[int, int, int]:
    """Upsert BrettspielWelt links to sidecar entries.

    Returns:
        (added_links, candidate_count, corrected_or_removed_links)
    """
    games = data.setdefault("games", {})
    added_links = 0
    corrected_links = 0

    for game_id, candidate in candidates.items():
        entry = games.get(game_id)
        if not isinstance(entry, dict):
            continue

        candidate_names = [candidate.get("name", "")] + list(candidate.get("alternate_names", []))
        match_url = ""
        for candidate_name in candidate_names:
            for key in _brettspielwelt_name_keys(str(candidate_name)):
                if key in brettspielwelt_map:
                    match_url = brettspielwelt_map[key]
                    break
            if match_url:
                break

        if not match_url:
            match_url = str(BRETTSPIELWELT_GAME_ID_OVERRIDES.get(str(game_id), "")).strip()

        pc_entries = _ensure_platform_list(entry, "pc")
        existing_indexes: List[int] = []
        for idx, item in enumerate(pc_entries):
            if not isinstance(item, dict):
                continue
            store = str(item.get("store", "")).strip().lower()
            url = str(item.get("url", "")).strip().lower()
            if "brettspielwelt" in store or "brettspielwelt.de" in url:
                existing_indexes.append(idx)

        if not match_url:
            # Remove stale/incorrect BSW links for this candidate when no
            # confident mapping exists.
            for idx in reversed(existing_indexes):
                pc_entries.pop(idx)
                corrected_links += 1
            continue

        payload: Dict[str, Any] = {
            "store": "BrettspielWelt",
            "url": match_url,
            "online": True,
            "note": str(
                BRETTSPIELWELT_GAME_ID_NOTE_OVERRIDES.get(
                    str(game_id),
                    str(candidate.get("name", "")).strip(),
                )
            ).strip(),
        }
        if payload.get("note") == "":
            payload.pop("note", None)

        if not existing_indexes:
            pc_entries.append(payload)
            added_links += 1
            continue

        first_idx = existing_indexes[0]
        first_item = pc_entries[first_idx]
        if not isinstance(first_item, dict):
            pc_entries[first_idx] = payload
            corrected_links += 1
        else:
            changed = False
            if str(first_item.get("store", "")).strip() != payload["store"]:
                first_item["store"] = payload["store"]
                changed = True
            if str(first_item.get("url", "")).strip() != payload["url"]:
                first_item["url"] = payload["url"]
                changed = True
            if first_item.get("online") is not True:
                first_item["online"] = True
                changed = True
            desired_note = payload.get("note", "")
            if desired_note and str(first_item.get("note", "")).strip() != desired_note:
                first_item["note"] = desired_note
                changed = True
            if changed:
                corrected_links += 1

        # Remove duplicates after keeping/updating the first entry.
        for idx in reversed(existing_indexes[1:]):
            pc_entries.pop(idx)
            corrected_links += 1

    return added_links, len(candidates), corrected_links


def load_sidecar(path: Path) -> Dict:
    if not path.exists():
        return {"games": {}}

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        data = {}
    games = data.get("games")
    if not isinstance(games, dict):
        data["games"] = {}

    return data


def sync_sidecar(data: Dict, rows: List[Tuple[int, str]]) -> Tuple[Dict, int, int]:
    games = data.setdefault("games", {})

    added = 0
    updated_existing = 0

    for game_id, name in rows:
        key = str(game_id)

        if key not in games:
            games[key] = {
                "name": name,
                "short_description": "",
                "rulebooks": [],
                "supplemental_files": [],
            }
            added += 1
            continue

        entry = games[key]
        if not isinstance(entry, dict):
            games[key] = {
                "name": name,
                "short_description": "",
                "rulebooks": [],
                "supplemental_files": [],
            }
            updated_existing += 1
            continue

        before = dict(entry)
        entry.setdefault("name", name)
        entry.setdefault("short_description", "")
        entry.setdefault("rulebooks", [])
        entry.setdefault("supplemental_files", [])
        if entry != before:
            updated_existing += 1

    ordered_games = {
        key: games[key]
        for key in sorted(games.keys(), key=lambda x: int(x) if x.isdigit() else x)
    }
    data["games"] = ordered_games

    return data, added, updated_existing


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Populate missing game metadata stubs in game_metadata_overrides.json from SQLite."
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help=f"Path to SQLite database (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--sidecar",
        default=DEFAULT_SIDECAR,
        help=f"Path to sidecar JSON file (default: {DEFAULT_SIDECAR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing the sidecar file.",
    )
    parser.add_argument(
        "--skip-yucata-auto-links",
        action="store_true",
        help="Skip automatic Yucata link enrichment from SQLite + Yucata catalog.",
    )
    parser.add_argument(
        "--yucata-timeout",
        type=float,
        default=20.0,
        help="Timeout in seconds for Yucata catalog API request (default: 20).",
    )
    parser.add_argument(
        "--report-yucata-misses",
        action="store_true",
        help="Print unresolved Yucata candidates after matching so overrides can be added.",
    )
    parser.add_argument(
        "--skip-tabletopia-auto-links",
        action="store_true",
        help="Skip automatic Tabletopia link enrichment from SQLite + Tabletopia catalog.",
    )
    parser.add_argument(
        "--tabletopia-timeout",
        type=float,
        default=20.0,
        help="Timeout in seconds for Tabletopia catalog API request (default: 20).",
    )
    parser.add_argument(
        "--skip-vassal-auto-links",
        action="store_true",
        help="Skip automatic VASSAL link enrichment from SQLite + VASSAL catalog.",
    )
    parser.add_argument(
        "--vassal-timeout",
        type=float,
        default=20.0,
        help="Timeout in seconds for VASSAL catalog API request (default: 20).",
    )
    parser.add_argument(
        "--skip-tabletop-simulator-auto-links",
        action="store_true",
        help="Skip automatic Tabletop Simulator workshop link enrichment.",
    )
    parser.add_argument(
        "--tabletop-simulator-timeout",
        type=float,
        default=20.0,
        help="Timeout in seconds for Tabletop Simulator workshop search requests (default: 20).",
    )
    parser.add_argument(
        "--skip-brettspielwelt-auto-links",
        action="store_true",
        help="Skip automatic BrettspielWelt link enrichment from SQLite + Spiele catalog.",
    )
    parser.add_argument(
        "--brettspielwelt-timeout",
        type=float,
        default=20.0,
        help="Timeout in seconds for BrettspielWelt Spiele catalog request (default: 20).",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    sidecar_path = Path(args.sidecar)

    if not db_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {db_path}")

    data = load_sidecar(sidecar_path)

    conn = sqlite3.connect(str(db_path))
    try:
        rows = choose_canonical_names(conn)
        yucata_candidates = load_yucata_candidates(conn)
        tabletopia_candidates = load_tabletopia_candidates(conn)
        vassal_candidates = load_vassal_candidates(conn)
        tabletop_simulator_candidates = load_tabletop_simulator_candidates(conn)
        brettspielwelt_candidates = load_brettspielwelt_candidates(conn)
    finally:
        conn.close()

    data, added, updated_existing = sync_sidecar(data, rows)

    yucata_added = 0
    yucata_notes_updated = 0
    tabletopia_added = 0
    vassal_added = 0
    tabletop_simulator_added = 0
    brettspielwelt_added = 0
    brettspielwelt_corrected = 0
    tabletopia_premium_catalog_count = 0
    tabletopia_status_notes_updated = 0
    vassal_status_notes_updated = 0
    online_statuses_updated = 0
    owned_removed_from_online = 0
    yucata_candidates_count = len(yucata_candidates)
    tabletopia_candidates_count = len(tabletopia_candidates)
    vassal_candidates_count = len(vassal_candidates)
    tabletop_simulator_candidates_count = len(tabletop_simulator_candidates)
    brettspielwelt_candidates_count = len(brettspielwelt_candidates)
    yucata_error = ""
    tabletopia_error = ""
    vassal_error = ""
    tabletop_simulator_error = ""
    brettspielwelt_error = ""
    tabletopia_premium_by_short_url: Dict[str, bool] = {}
    tabletopia_name_by_short_url: Dict[str, str] = {}
    vassal_title_by_slug: Dict[str, str] = {}
    yucata_misses: List[Tuple[str, str, List[str]]] = []
    if not args.skip_yucata_auto_links and yucata_candidates_count > 0:
        try:
            yucata_map = fetch_yucata_game_urls(timeout=args.yucata_timeout)
            yucata_added, _ = enrich_yucata_links(data, yucata_candidates, yucata_map)
            yucata_notes_updated = annotate_yucata_notes(data)
            if args.report_yucata_misses:
                yucata_misses = collect_unmatched_yucata_candidates(data, yucata_candidates, yucata_map)
        except Exception as exc:
            yucata_error = str(exc)

    if not args.skip_tabletopia_auto_links and tabletopia_candidates_count > 0:
        try:
            tabletopia_map, tabletopia_premium_by_short_url, tabletopia_name_by_short_url = fetch_tabletopia_game_catalog(timeout=args.tabletopia_timeout)
            tabletopia_premium_catalog_count = sum(1 for _k, v in tabletopia_premium_by_short_url.items() if bool(v))
            tabletopia_added, _ = enrich_tabletopia_links(
                data,
                tabletopia_candidates,
                tabletopia_map,
                tabletopia_premium_by_short_url=tabletopia_premium_by_short_url,
                tabletopia_name_by_short_url=tabletopia_name_by_short_url,
            )
            tabletopia_status_notes_updated = annotate_tabletopia_statuses_and_notes(
                data,
                tabletopia_premium_by_short_url=tabletopia_premium_by_short_url,
                tabletopia_name_by_short_url=tabletopia_name_by_short_url,
            )
        except Exception as exc:
            tabletopia_error = str(exc)

    if not args.skip_vassal_auto_links and vassal_candidates_count > 0:
        try:
            vassal_map, vassal_title_by_slug = fetch_vassal_project_catalog(timeout=args.vassal_timeout)
            vassal_added, _ = enrich_vassal_links(
                data,
                vassal_candidates,
                vassal_map,
                vassal_title_by_slug=vassal_title_by_slug,
            )
            vassal_status_notes_updated = annotate_vassal_statuses_and_notes(
                data,
                vassal_title_by_slug=vassal_title_by_slug,
            )
        except Exception as exc:
            vassal_error = str(exc)

    if not args.skip_tabletop_simulator_auto_links and tabletop_simulator_candidates_count > 0:
        try:
            tabletop_simulator_added, _ = enrich_tabletop_simulator_links(
                data,
                tabletop_simulator_candidates,
                timeout=args.tabletop_simulator_timeout,
            )
        except Exception as exc:
            tabletop_simulator_error = str(exc)

    if not args.skip_brettspielwelt_auto_links and brettspielwelt_candidates_count > 0:
        try:
            brettspielwelt_map = fetch_brettspielwelt_catalog(timeout=args.brettspielwelt_timeout)
            brettspielwelt_added, _, brettspielwelt_corrected = enrich_brettspielwelt_links(
                data,
                brettspielwelt_candidates,
                brettspielwelt_map,
            )
        except Exception as exc:
            brettspielwelt_error = str(exc)

    online_statuses_updated = annotate_online_statuses(data, tabletopia_premium_by_short_url=tabletopia_premium_by_short_url)
    owned_removed_from_online = strip_owned_from_online_entries(data)

    if args.dry_run:
        print(f"rows_seen={len(rows)}")
        print(f"would_add={added}")
        print(f"would_update_existing={updated_existing}")
        print(f"yucata_candidates={yucata_candidates_count}")
        print(f"tabletopia_candidates={tabletopia_candidates_count}")
        print(f"vassal_candidates={vassal_candidates_count}")
        print(f"tabletop_simulator_candidates={tabletop_simulator_candidates_count}")
        print(f"brettspielwelt_candidates={brettspielwelt_candidates_count}")
        print(f"would_add_yucata_links={yucata_added}")
        print(f"would_add_tabletopia_links={tabletopia_added}")
        print(f"would_add_vassal_links={vassal_added}")
        print(f"would_add_tabletop_simulator_links={tabletop_simulator_added}")
        print(f"would_add_brettspielwelt_links={brettspielwelt_added}")
        print(f"would_correct_brettspielwelt_links={brettspielwelt_corrected}")
        print(f"tabletopia_premium_catalog_count={tabletopia_premium_catalog_count}")
        print(f"would_update_tabletopia_statuses_notes={tabletopia_status_notes_updated}")
        print(f"would_update_vassal_statuses_notes={vassal_status_notes_updated}")
        print(f"would_update_yucata_notes={yucata_notes_updated}")
        if args.report_yucata_misses:
            print(f"would_report_yucata_misses={len(yucata_misses)}")
            for game_id, name, alt_names in yucata_misses:
                alt_text = " | ".join(alt_names)
                print(f"yucata_miss={game_id}|{name}|alts={alt_text}")
        print(f"would_update_online_statuses={online_statuses_updated}")
        print(f"would_remove_owned_from_online={owned_removed_from_online}")
        if yucata_error:
            print(f"yucata_error={yucata_error}")
        if tabletopia_error:
            print(f"tabletopia_error={tabletopia_error}")
        if vassal_error:
            print(f"vassal_error={vassal_error}")
        if tabletop_simulator_error:
            print(f"tabletop_simulator_error={tabletop_simulator_error}")
        if brettspielwelt_error:
            print(f"brettspielwelt_error={brettspielwelt_error}")
        print(f"total_after={len(data.get('games', {}))}")
        return 0

    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    with sidecar_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=True)
        f.write("\n")

    print(f"rows_seen={len(rows)}")
    print(f"added={added}")
    print(f"updated_existing={updated_existing}")
    print(f"yucata_candidates={yucata_candidates_count}")
    print(f"tabletopia_candidates={tabletopia_candidates_count}")
    print(f"vassal_candidates={vassal_candidates_count}")
    print(f"tabletop_simulator_candidates={tabletop_simulator_candidates_count}")
    print(f"brettspielwelt_candidates={brettspielwelt_candidates_count}")
    print(f"added_yucata_links={yucata_added}")
    print(f"added_tabletopia_links={tabletopia_added}")
    print(f"added_vassal_links={vassal_added}")
    print(f"added_tabletop_simulator_links={tabletop_simulator_added}")
    print(f"added_brettspielwelt_links={brettspielwelt_added}")
    print(f"corrected_brettspielwelt_links={brettspielwelt_corrected}")
    print(f"tabletopia_premium_catalog_count={tabletopia_premium_catalog_count}")
    print(f"updated_tabletopia_statuses_notes={tabletopia_status_notes_updated}")
    print(f"updated_vassal_statuses_notes={vassal_status_notes_updated}")
    print(f"updated_yucata_notes={yucata_notes_updated}")
    if args.report_yucata_misses:
        print(f"reported_yucata_misses={len(yucata_misses)}")
        for game_id, name, alt_names in yucata_misses:
            alt_text = " | ".join(alt_names)
            print(f"yucata_miss={game_id}|{name}|alts={alt_text}")
    print(f"updated_online_statuses={online_statuses_updated}")
    print(f"removed_owned_from_online={owned_removed_from_online}")
    if yucata_error:
        print(f"yucata_error={yucata_error}")
    if tabletopia_error:
        print(f"tabletopia_error={tabletopia_error}")
    if vassal_error:
        print(f"vassal_error={vassal_error}")
    if tabletop_simulator_error:
        print(f"tabletop_simulator_error={tabletop_simulator_error}")
    if brettspielwelt_error:
        print(f"brettspielwelt_error={brettspielwelt_error}")
    print(f"total_sidecar_games={len(data.get('games', {}))}")
    print(f"written={sidecar_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
