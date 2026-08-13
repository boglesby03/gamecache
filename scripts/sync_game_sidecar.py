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
import socket
import sqlite3
import re
import time
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
TABLETOP_SIMULATOR_DLC_CATALOG_API = "https://store.steampowered.com/api/dlcforapp/"
TABLETOP_SIMULATOR_APP_ID = "286160"
BRETTSPIELWELT_SPIELE_URL = "https://www.brettspielwelt.de/Spiele/"
BOARDSPACE_INDEX_URL = "https://boardspace.net/english/index.shtml"
FORTELLER_NARRATIVES_COLLECTION_URL = "https://fortellergames.com/collections/all-narrative-companions"

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

# Deterministic Tabletop Simulator overrides for titles where Steam workshop
# search ranking is inconsistent but a vetted module URL is known.
TABLETOP_SIMULATOR_GAME_ID_OVERRIDES: Dict[str, str] = {
    "206480": "https://steamcommunity.com/sharedfiles/filedetails/?id=2129754084",  # Imperial Struggle by GMT Games [Scripted]
    "296151": "https://steamcommunity.com/sharedfiles/filedetails/?id=2977151576",  # Viscounts of the West Kingdom [All Content]
}

# Known bad workshop URLs that have been manually confirmed as incorrect
# mappings for sidecar auto-linking.
TABLETOP_SIMULATOR_BAD_URLS: Set[str] = {
    "https://steamcommunity.com/sharedfiles/filedetails/?id=2795034467",  # Earth -> Erdein (wrong)
}

TABLETOP_SIMULATOR_DLC_URL_TEMPLATE = "https://store.steampowered.com/app/{appid}/"

# Explicit official DLC target overrides by DLC app id.
# Values are BGG game ids to attach the official DLC link to.
TABLETOP_SIMULATOR_OFFICIAL_DLC_APP_ID_TARGETS: Dict[str, List[str]] = {
    "610700": ["170216"],  # Tabletop Simulator - Blood Rage
    "610708": ["147949"],  # Tabletop Simulator - One Night Ultimate Werewolf
    "437590": ["163967", "285826"],  # Tabletop Simulator - Tiny Epic Galaxies
}

# Keep discovery enabled; matching rules determine confidence acceptance.
TABLETOP_SIMULATOR_ENABLE_AUTO_DISCOVERY = True


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


def load_all_name_candidates(conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
        """Load canonical game names + alternates for all games."""
        cur = conn.cursor()
        cur.execute(
                """
                WITH ranked AS (
                    SELECT
                        id,
                        name,
                        tags,
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
                SELECT id, name, alternate_names
                FROM ranked
                WHERE rn = 1
                """
        )

        candidates: Dict[str, Dict[str, Any]] = {}
        for game_id, name, alt_raw in cur.fetchall():
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


def augment_tts_candidates_with_sidecar(
    data: Dict[str, Any],
    candidates: Dict[str, Dict[str, Any]],
) -> int:
    """Promote existing sidecar TTS entries into discovery candidates.

    This allows manually curated sidecar TTS links/notes to guide matching for
    additional modules even when SQLite family metadata lacks the TTS tag.
    """
    games = data.get("games", {})
    if not isinstance(games, dict):
        return 0

    added = 0
    for game_id, entry in games.items():
        if not isinstance(entry, dict):
            continue
        gid = str(game_id).strip()
        if not gid.isdigit():
            continue

        existing_urls = _find_existing_tabletop_simulator_links(entry)
        if not existing_urls:
            continue

        name = str(entry.get("name", "")).strip()
        if not name:
            continue

        note_aliases = _tts_note_aliases_from_entry(entry)
        if gid not in candidates:
            candidates[gid] = {
                "id": gid,
                "name": name,
                "alternate_names": note_aliases,
            }
            added += 1
            continue

        existing_alt = candidates[gid].get("alternate_names", [])
        if not isinstance(existing_alt, list):
            existing_alt = []
        merged_alt = [str(v).strip() for v in existing_alt if str(v).strip()]
        seen = {v.lower() for v in merged_alt}
        for alias in note_aliases:
            k = alias.lower()
            if alias and k not in seen:
                seen.add(k)
                merged_alt.append(alias)
        candidates[gid]["alternate_names"] = merged_alt

    return added


def load_boardspace_candidates(conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    """Load games that appear to have Boardspace implementations from SQLite metadata."""
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
        has_boardspace_family = False
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
                        "digital implementations: boardspace" in normalized_family
                        or "digital implementations: boardspace.net" in normalized_family
                    ):
                        has_boardspace_family = True
                        break
        except Exception:
            pass

        if not has_boardspace_family:
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
        body = _read_http_body_with_timeout(resp, timeout=timeout)

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


def fetch_boardspace_catalog(timeout: float = 20.0) -> Dict[str, str]:
    """Fetch Boardspace index and map normalized keys to game info URLs."""
    req = urllib.request.Request(
        BOARDSPACE_INDEX_URL,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 (GameCache sidecar sync)",
        },
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")

    mapping: Dict[str, str] = {}
    seen_slugs: Set[str] = set()
    for match in re.finditer(r'(?:^|["\'/])about_([a-z0-9]+)\.html', body, flags=re.IGNORECASE):
        slug = str(match.group(1) or "").strip().lower()
        if not slug or slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        game_url = f"https://boardspace.net/english/about_{slug}.html"
        for key in _boardspace_name_keys(slug):
            mapping.setdefault(key, game_url)

    return mapping


def fetch_forteller_narratives_catalog(timeout: float = 20.0) -> Dict[str, str]:
    """Fetch Forteller narrative companions and map normalized titles to URLs."""
    api_url = f"{FORTELLER_NARRATIVES_COLLECTION_URL}/products.json?limit=250"
    req = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/json,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 (GameCache sidecar sync)",
        },
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")

    payload = json.loads(body)
    products = payload.get("products", []) if isinstance(payload, dict) else []

    mapping: Dict[str, str] = {}
    seen_urls: Set[str] = set()
    for product in products:
        if not isinstance(product, dict):
            continue
        title = html.unescape(str(product.get("title", "") or "").strip())
        handle = str(product.get("handle", "") or "").strip()
        if not title or not handle:
            continue

        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            continue

        abs_url = urllib.parse.urljoin("https://fortellergames.com", f"/products/{handle}")
        if abs_url in seen_urls:
            continue
        seen_urls.add(abs_url)

        key = _normalize_name(title)
        if key:
            mapping.setdefault(key, abs_url)

    return mapping


def _boardspace_name_keys(value: str) -> List[str]:
    """Return conservative keys for Boardspace matching.

    Avoid acronym keys to reduce collisions between unrelated short slugs.
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

    return keys


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


def _read_http_body_with_timeout(resp: Any, timeout: float, max_bytes: int = 2_000_000) -> str:
    """Read an HTTP response body with a hard deadline to avoid stalled reads."""
    timeout = max(float(timeout or 0.0), 0.5)
    deadline = time.monotonic() + timeout
    chunks: List[bytes] = []
    total = 0

    # Try to access the underlying socket so each chunk read can respect the
    # remaining deadline, even for long chunked-transfer responses.
    sock: Optional[socket.socket] = None
    try:
        sock = resp.fp.raw._sock  # type: ignore[attr-defined]
    except Exception:
        sock = None

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("HTTP response read timed out")
        if sock is not None:
            try:
                sock.settimeout(max(0.1, min(timeout, remaining)))
            except Exception:
                pass
        try:
            chunk = resp.read(64 * 1024)
        except socket.timeout as exc:
            raise TimeoutError("HTTP response read timed out") from exc
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"HTTP response exceeded max_bytes={max_bytes}")

    return b"".join(chunks).decode("utf-8", errors="replace")


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


def _fetch_tts_workshop_title(url: str, timeout: float = 20.0) -> str:
    """Fetch a Steam Workshop item page title for confidence validation."""
    item_url = str(url or "").strip()
    if not item_url:
        return ""

    id_match = re.search(r"[?&]id=(\d+)", item_url)
    if id_match:
        workshop_id = id_match.group(1)
        try:
            api_url = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
            payload = urllib.parse.urlencode(
                {
                    "itemcount": "1",
                    "publishedfileids[0]": workshop_id,
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                api_url,
                data=payload,
                headers={
                    "User-Agent": "Mozilla/5.0 (GameCache sidecar sync)",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = _read_http_body_with_timeout(resp, timeout=timeout)
            parsed = json.loads(body)
            details = parsed.get("response", {}).get("publishedfiledetails", [])
            if isinstance(details, list) and details:
                title = str(details[0].get("title", "") or "").strip()
                if title:
                    return title
        except Exception:
            pass

    req = urllib.request.Request(
        item_url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 (GameCache sidecar sync)",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = _read_http_body_with_timeout(resp, timeout=timeout)
    except Exception:
        return ""

    meta_match = re.search(
        r'<meta\s+property="og:title"\s+content="([^"]+)"',
        body,
        flags=re.IGNORECASE,
    )
    if meta_match:
        return html.unescape(str(meta_match.group(1) or "").strip())

    h1_match = re.search(
        r'<div[^>]*class="workshopItemTitle"[^>]*>(.*?)</div>',
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if h1_match:
        text = re.sub(r"<[^>]+>", "", str(h1_match.group(1) or ""))
        return html.unescape(text.strip())

    return ""


def _tts_generic_suffix_tokens() -> Set[str]:
    return {
        "edition",
        "essential",
        "deluxe",
        "complete",
        "collector",
        "collectors",
        "game",
        "family",
        "anniversary",
        "basic",
        "full",
        "vanilla",
        "second",
        "third",
        "fourth",
        "fifth",
        "sixth",
        "seventh",
        "eighth",
        "ninth",
        "tenth",
    }


def _tts_allowed_tail_tokens() -> Set[str]:
    return {
        "scripted",
        "prototype",
        "mod",
        "module",
        "setup",
        "save",
        "table",
        "tts",
        "expansion",
        "expansions",
        "all",
        "solo",
        "redux",
        "updated",
        "update",
        "beta",
        "alpha",
        "english",
        "en",
        "full",
        "basic",
        "vanilla",
        "family",
        "plus",
    }


def _tts_candidate_prefixes(value: str) -> List[str]:
    raw = str(value or "").strip()
    if not raw:
        return []

    variants: List[str] = []
    seen: Set[str] = set()

    def add(text: str) -> None:
        normalized = re.sub(r"\s+", " ", str(text or "").strip())
        key = normalized.lower()
        if normalized and key not in seen:
            seen.add(key)
            variants.append(normalized)

    add(raw)

    raw_tokens = _tokenize_words(raw)
    generic_suffix = _tts_generic_suffix_tokens()
    trimmed_tokens = list(raw_tokens)
    while trimmed_tokens and trimmed_tokens[-1] in generic_suffix:
        trimmed_tokens.pop()
    if trimmed_tokens and trimmed_tokens != raw_tokens:
        add(" ".join(trimmed_tokens))

    if ":" in raw:
        base, suffix = raw.split(":", 1)
        if _tts_is_base_equivalent_suffix_text(suffix):
            add(base)

    return variants


def _tts_is_generic_suffix_text(value: str) -> bool:
    tokens = _tokenize_words(value)
    if not tokens:
        return False

    generic = _tts_generic_suffix_tokens() | {
        "second",
        "third",
        "fourth",
        "fifth",
        "sixth",
        "seventh",
        "eighth",
        "ninth",
        "tenth",
        "anniversary",
        "mini",
        "master",
        "set",
    }
    return all(token in generic for token in tokens)


def _tts_is_base_equivalent_suffix_text(value: str) -> bool:
    tokens = _tokenize_words(value)
    if not tokens:
        return False

    allowed = _tts_generic_suffix_tokens() | {"mini", "master", "set"}
    if any(token.isdigit() for token in tokens):
        return False
    return all(token in allowed for token in tokens)


def _tts_query_names(candidate: Dict[str, Any]) -> List[str]:
    primary = str(candidate.get("name", "") or "").strip()
    alternates = list(candidate.get("alternate_names", []) or [])

    names: List[str] = []
    seen: Set[str] = set()

    def add(value: str) -> None:
        text = re.sub(r"[:\-\s]+$", "", str(value or "").strip())
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            names.append(text)

    add(primary)

    if ":" in primary:
        base, suffix = primary.split(":", 1)
        if _tts_is_base_equivalent_suffix_text(suffix):
            add(base)

    match = re.match(r"^(.*?)(?:\s*[-,(]\s*|\s+)([A-Za-z0-9' ]+edition|[A-Za-z0-9' ]+anniversary edition|collector'?s edition|deluxe edition|master set)\)?$", primary, flags=re.IGNORECASE)
    if match and _tts_is_base_equivalent_suffix_text(match.group(2)):
        add(match.group(1))

    for alt in alternates:
        alt_text = str(alt or "").strip()
        if not alt_text:
            continue
        if re.search(r"[^\x00-\x7F]", alt_text):
            continue
        alt_tokens = _tokenize_words(alt_text)
        if not alt_tokens:
            continue

        # Keep alternates only when they are substantial enough to reduce
        # single-word/franchise collision risk.
        substantial = (len(alt_tokens) >= 2 and len("".join(alt_tokens)) >= 8) or (
            len(alt_tokens) == 1 and len(alt_tokens[0]) >= 10
        )
        if not substantial:
            continue

        if alt_text.lower() == primary.lower():
            add(alt_text)
            continue
        if ":" in primary:
            base, suffix = primary.split(":", 1)
            if alt_text.lower() == base.strip().lower() and _tts_is_base_equivalent_suffix_text(suffix):
                add(alt_text)
                continue

            # For non-base-equivalent subtitles, avoid broad base/franchise aliases.
            if alt_text.lower() == base.strip().lower():
                continue

        add(alt_text)

    return names


def _tts_tail_is_allowed(tail: str) -> bool:
    text = str(tail or "").strip()
    if not text:
        return True

    if re.search(r"[^\x00-\x7F]", text):
        return False

    # Drop bracket wrappers and punctuation separators, then ensure only a
    # narrow set of modifier tokens remain.
    cleaned = re.sub(r"[\[\]\(\){}|:+,./\\_-]+", " ", text)
    tokens = _tokenize_words(cleaned)
    if not tokens:
        return True
    if all(token.isdigit() for token in tokens):
        return False

    allowed_tokens = _tts_allowed_tail_tokens()
    for token in tokens:
        if token.isdigit():
            continue
        if token not in allowed_tokens:
            return False
    return True


def _tts_title_language_allowed(result_title: str) -> bool:
    """Allow titles that are English, language-independent, or untagged.

    Policy details:
    - Reject explicit non-English language markers.
    - Allow explicit English markers.
    - Allow explicit language-independent markers.
    - If no language markers are present, treat as untagged and allow.
    """
    text = str(result_title or "").strip()
    if not text:
        return False

    lowered = text.lower()
    if re.search(r"[^\x00-\x7F]", lowered):
        return False

    normalized = re.sub(r"[\[\]\(\){}|:+,./\\_-]+", " ", lowered)
    tokens = set(_tokenize_words(normalized))

    english_tokens = {
        "en",
        "eng",
        "english",
    }
    language_independent_phrases = {
        "language independent",
        "language-independent",
        "lang independent",
        "lang-independent",
        "no language",
        "textless",
    }

    non_english_tokens = {
        "ru",
        "rus",
        "russian",
        "fr",
        "fre",
        "french",
        "de",
        "ger",
        "german",
        "deutsch",
        "es",
        "spa",
        "spanish",
        "espanol",
        "it",
        "ita",
        "italian",
        "pt",
        "por",
        "portuguese",
        "pl",
        "pol",
        "polish",
        "nl",
        "dut",
        "dutch",
        "cz",
        "cze",
        "czech",
        "tr",
        "tur",
        "turkish",
        "ua",
        "ukr",
        "ukrainian",
        "jp",
        "jpn",
        "japanese",
        "kr",
        "kor",
        "korean",
        "cn",
        "chi",
        "chs",
        "cht",
        "chinese",
    }

    has_english = bool(tokens.intersection(english_tokens))
    has_language_independent = any(phrase in lowered for phrase in language_independent_phrases)
    has_non_english = bool(tokens.intersection(non_english_tokens))

    if has_non_english:
        return False
    if has_english or has_language_independent:
        return True

    # Untagged titles are kept; strict language rejection only applies when a
    # specific non-English language tag is present in the title.
    return True


def _score_tts_title_match(candidate_name: str, result_title: str) -> int:
    """Return a conservative confidence score for candidate<->workshop title matching."""
    candidate_name = str(candidate_name or "").strip()
    result_title = str(result_title or "").strip()
    if not candidate_name or not result_title:
        return 0
    if re.search(r"[^\x00-\x7F]", result_title):
        return 0

    candidate_norm = _normalize_name(candidate_name)
    title_norm = _normalize_name(result_title)
    if not candidate_norm or not title_norm:
        return 0

    candidate_years = set(re.findall(r"\b\d{4}\b", candidate_name))
    title_years = set(re.findall(r"\b\d{4}\b", result_title))
    if title_years - candidate_years:
        return 0

    if candidate_norm == title_norm and not re.search(r"[^\x00-\x7F]", result_title):
        return 100

    def significant_tokens(value: str) -> List[str]:
        tokens = _tokenize_words(value)
        stop = {
            "the",
            "a",
            "an",
            "of",
            "and",
            "to",
            "for",
            "with",
            "vs",
            "edition",
            "game",
            "base",
            "set",
            "deluxe",
            "collector",
            "collectors",
            "chest",
            "battle",
            "second",
            "third",
            "fourth",
            "fifth",
            "sixth",
            "seventh",
            "eighth",
            "ninth",
            "tenth",
            "anniversary",
            "family",
            "basic",
            "full",
            "vanilla",
        }
        out: List[str] = []
        for token in tokens:
            if token in stop:
                continue
            if token.isdigit():
                continue
            if len(token) < 3:
                continue
            out.append(token)
        return out

    cand_sig = significant_tokens(candidate_name)
    title_sig = set(significant_tokens(result_title))
    if cand_sig:
        if len(cand_sig) >= 3 and all(token in title_sig for token in cand_sig):
            return 95
        if len(cand_sig) == 2 and (min(len(cand_sig[0]), len(cand_sig[1])) >= 4 or len("".join(cand_sig)) >= 8):
            if all(token in title_sig for token in cand_sig):
                return 95

    # Canonical comparison after removing edition/variant qualifiers.
    def canonical_text(value: str) -> str:
        drop = {
            "the", "a", "an", "of", "and", "to", "for", "with", "vs",
            "edition", "game", "base", "set", "deluxe", "collector", "collectors",
            "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
            "anniversary", "family", "basic", "full", "vanilla",
        }
        tokens = [t for t in _tokenize_words(value) if t and t not in drop and not t.isdigit()]
        return " ".join(tokens)

    candidate_core = canonical_text(candidate_name)
    title_core = canonical_text(result_title)
    if candidate_core and title_core:
        if title_core == candidate_core or title_core.startswith(candidate_core + " "):
            return 95

    lowered_title = result_title.lower()
    for prefix in _tts_candidate_prefixes(candidate_name):
        lowered_prefix = prefix.lower()
        if lowered_title == lowered_prefix:
            return 100
        if lowered_title.startswith(lowered_prefix):
            tail = result_title[len(prefix):]
            if _tts_tail_is_allowed(tail):
                return 96

    stripped_title = re.sub(r"\s+", " ", re.sub(r"[\[\(].*$", "", result_title)).strip()
    if stripped_title:
        stripped_norm = _normalize_name(stripped_title)
        if stripped_norm == candidate_norm:
            tail = result_title[len(stripped_title):]
            if _tts_tail_is_allowed(tail):
                return 95

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


def _find_existing_tabletop_simulator_links(entry: Dict[str, Any]) -> Set[str]:
    urls: Set[str] = set()
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
            if "tabletop simulator" in store or "steamcommunity.com/sharedfiles/filedetails/" in url.lower():
                if url:
                    urls.add(url)
    return urls


def _is_tts_entry(item: Dict[str, Any]) -> bool:
    store = str(item.get("store", "")).strip().lower()
    url = str(item.get("url", "")).strip().lower()
    return (
        "tabletop simulator" in store
        or store == "tts"
        or "steamcommunity.com/sharedfiles/filedetails/" in url
        or "store.steampowered.com/app/" in url
    )


def _is_tts_official_dlc_entry(item: Dict[str, Any]) -> bool:
    if not _is_tts_entry(item):
        return False
    note = str(item.get("note", "")).strip().lower()
    return "official dlc" in note


def _tts_note_aliases_from_entry(entry: Dict[str, Any]) -> List[str]:
    """Collect candidate alias names from existing TTS notes in sidecar."""
    aliases: List[str] = []
    seen: Set[str] = set()

    for platform in ("android", "ios", "pc"):
        items = entry.get(platform)
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if not _is_tts_entry(item):
                continue
            note = re.sub(r"\s+", " ", str(item.get("note", "") or "").strip())
            if not note:
                continue
            if re.search(r"[^\x00-\x7F]", note):
                continue
            tokens = _tokenize_words(note)
            if len(tokens) < 2 and len(note) < 8:
                continue
            key = note.lower()
            if key in seen:
                continue
            seen.add(key)
            aliases.append(note)

    return aliases


def _fetch_tts_official_dlc_catalog(timeout: float = 20.0) -> List[Dict[str, str]]:
    """Fetch official DLC list for Tabletop Simulator from Steam store API."""
    params = urllib.parse.urlencode({"appid": TABLETOP_SIMULATOR_APP_ID, "l": "english", "cc": "us"})
    url = f"{TABLETOP_SIMULATOR_DLC_CATALOG_API}?{params}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 (GameCache sidecar sync)",
        },
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = _read_http_body_with_timeout(resp, timeout=timeout)
    parsed = json.loads(body)

    out: List[Dict[str, str]] = []
    dlc_items = parsed.get("dlc", []) if isinstance(parsed, dict) else []
    if not isinstance(dlc_items, list):
        return out

    for item in dlc_items:
        if not isinstance(item, dict):
            continue
        appid = str(item.get("id", "") or "").strip()
        name = str(item.get("name", "") or "").strip()
        if not appid.isdigit() or not name:
            continue
        out.append(
            {
                "appid": appid,
                "name": name,
                "url": TABLETOP_SIMULATOR_DLC_URL_TEMPLATE.format(appid=appid),
            }
        )

    return out


def _normalize_tts_dlc_game_name(dlc_name: str) -> str:
    text = str(dlc_name or "").strip()
    text = re.sub(r"^Tabletop\s+Simulator\s*[-:\u2013\u2014]\s*", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _build_exact_name_index(candidates: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    index: Dict[str, List[str]] = {}
    for game_id, candidate in candidates.items():
        names = [str(candidate.get("name", "")).strip()] + [str(n).strip() for n in candidate.get("alternate_names", [])]
        for name in names:
            key = _normalize_name(name)
            if not key:
                continue
            index.setdefault(key, [])
            if game_id not in index[key]:
                index[key].append(game_id)
    return index


def add_tabletop_simulator_official_dlc_links(
    data: Dict[str, Any],
    all_name_candidates: Dict[str, Dict[str, Any]],
    timeout: float = 20.0,
) -> Tuple[int, int]:
    """Add official TTS DLC links from Steam, matched to collection games.

    Returns:
        (added_links, unmatched_dlc_items)
    """
    games = data.get("games", {})
    if not isinstance(games, dict):
        return 0, 0

    exact_index = _build_exact_name_index(all_name_candidates)
    dlc_items = _fetch_tts_official_dlc_catalog(timeout=timeout)
    added = 0
    unmatched = 0

    for dlc in dlc_items:
        dlc_name = str(dlc.get("name", "")).strip()
        normalized_dlc_game_name = _normalize_tts_dlc_game_name(dlc_name)
        dlc_appid = str(dlc.get("appid", "")).strip()
        dlc_url = str(dlc.get("url", "")).strip()
        if not normalized_dlc_game_name or not dlc_url:
            continue

        override_target_ids = TABLETOP_SIMULATOR_OFFICIAL_DLC_APP_ID_TARGETS.get(dlc_appid, [])
        if override_target_ids:
            added_any = False
            for target_game_id in override_target_ids:
                entry = games.get(str(target_game_id))
                if not isinstance(entry, dict):
                    continue

                pc_entries = _ensure_platform_list(entry, "pc")
                dlc_url_lower = dlc_url.lower()
                already_present = any(
                    isinstance(item, dict)
                    and str(item.get("url", "")).strip().lower() == dlc_url_lower
                    for item in pc_entries
                )
                if already_present:
                    added_any = True
                    continue

                pc_entries.append(
                    {
                        "store": "Tabletop Simulator",
                        "url": dlc_url,
                        "monthly_subscription": True,
                        "note": f"Official DLC: {normalized_dlc_game_name}",
                    }
                )
                added += 1
                added_any = True

            if not added_any:
                unmatched += 1
            continue

        target_game_id: Optional[str] = None

        exact_key = _normalize_name(normalized_dlc_game_name)
        exact_matches = exact_index.get(exact_key, [])
        if len(exact_matches) == 1:
            target_game_id = exact_matches[0]
        elif len(exact_matches) > 1:
            with_workshop: List[str] = []
            for match_game_id in exact_matches:
                match_entry = games.get(str(match_game_id))
                if not isinstance(match_entry, dict):
                    continue
                existing_urls = _find_existing_tabletop_simulator_links(match_entry)
                if any("steamcommunity.com/sharedfiles/filedetails/" in url.lower() for url in existing_urls):
                    with_workshop.append(match_game_id)
            if len(with_workshop) == 1:
                target_game_id = with_workshop[0]
            else:
                unmatched += 1
                continue
        else:
            best_id = ""
            best_score = 0
            second_score = 0
            for game_id, candidate in all_name_candidates.items():
                score = 0
                for name_for_scoring in _tts_query_names(candidate):
                    score = max(score, _score_tts_title_match(name_for_scoring, normalized_dlc_game_name))
                if score > best_score:
                    second_score = best_score
                    best_score = score
                    best_id = game_id
                elif score > second_score:
                    second_score = score

            if best_score >= 95 and best_score > second_score:
                target_game_id = best_id

        if not target_game_id:
            unmatched += 1
            continue

        entry = games.get(str(target_game_id))
        if not isinstance(entry, dict):
            unmatched += 1
            continue

        pc_entries = _ensure_platform_list(entry, "pc")
        dlc_url_lower = dlc_url.lower()
        already_present = any(
            isinstance(item, dict)
            and str(item.get("url", "")).strip().lower() == dlc_url_lower
            for item in pc_entries
        )
        if already_present:
            continue

        # Use the same store label as modules so UI/icon mapping remains consistent.
        pc_entries.append(
            {
                "store": "Tabletop Simulator",
                "url": dlc_url,
                "monthly_subscription": True,
                "note": f"Official DLC: {normalized_dlc_game_name}",
            }
        )
        added += 1

    return added, unmatched


def normalize_tabletop_simulator_dlc_store_labels(data: Dict[str, Any]) -> int:
    """Migrate legacy DLC store labels to Tabletop Simulator.

    This ensures official DLC entries render with the same icon/style mapping
    as workshop modules in the UI.
    """
    games = data.get("games", {})
    if not isinstance(games, dict):
        return 0

    updated = 0
    for _game_id, entry in games.items():
        if not isinstance(entry, dict):
            continue
        for platform in ("android", "ios", "pc"):
            items = entry.get(platform)
            if isinstance(items, dict):
                items = [items]
                entry[platform] = items
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                store = str(item.get("store", "")).strip().lower()
                if store != "tabletop simulator dlc":
                    continue
                item["store"] = "Tabletop Simulator"
                note = str(item.get("note", "")).strip()
                if note.lower() == "official dlc":
                    # Keep existing note text; no-op, but count store migration.
                    pass
                updated += 1

    return updated


def normalize_tabletop_simulator_official_dlc_urls(data: Dict[str, Any]) -> int:
    """Canonicalize and dedupe official TTS DLC app links.

    For entries marked as official DLC under Tabletop Simulator, convert any
    slugged Steam app URL to canonical `.../app/<id>/` and remove duplicates.
    """
    games = data.get("games", {})
    if not isinstance(games, dict):
        return 0

    updated = 0
    for _game_id, entry in games.items():
        if not isinstance(entry, dict):
            continue
        pc_entries = entry.get("pc")
        if isinstance(pc_entries, dict):
            pc_entries = [pc_entries]
            entry["pc"] = pc_entries
        if not isinstance(pc_entries, list):
            continue

        seen_official_appids: Set[str] = set()
        for idx in reversed(range(len(pc_entries))):
            item = pc_entries[idx]
            if not isinstance(item, dict):
                continue
            store = str(item.get("store", "")).strip().lower()
            note = str(item.get("note", "")).strip().lower()
            url = str(item.get("url", "")).strip()
            if store != "tabletop simulator" or "official dlc" not in note:
                continue

            match = re.search(r"store\.steampowered\.com/app/(\d+)", url, flags=re.IGNORECASE)
            if not match:
                continue
            appid = match.group(1)
            canonical_url = TABLETOP_SIMULATOR_DLC_URL_TEMPLATE.format(appid=appid)

            if appid in seen_official_appids:
                pc_entries.pop(idx)
                updated += 1
                continue

            seen_official_appids.add(appid)
            if url != canonical_url:
                item["url"] = canonical_url
                updated += 1

    return updated


def normalize_tabletop_simulator_official_dlc_entries(data: Dict[str, Any]) -> int:
    """Normalize official TTS DLC entry state and ordering.

    - Official DLC entries become Subscribe-only (`monthly_subscription=true`)
      and are not marked `online`.
    - Official DLC entries are ordered before other TTS module entries.
    """
    games = data.get("games", {})
    if not isinstance(games, dict):
        return 0

    updated = 0
    for _game_id, entry in games.items():
        if not isinstance(entry, dict):
            continue

        pc_entries = entry.get("pc")
        if isinstance(pc_entries, dict):
            pc_entries = [pc_entries]
            entry["pc"] = pc_entries
        if not isinstance(pc_entries, list):
            continue

        # Normalize state for official DLC entries.
        for item in pc_entries:
            if not isinstance(item, dict):
                continue
            if not _is_tts_official_dlc_entry(item):
                continue

            changed = False
            if item.get("monthly_subscription") is not True:
                item["monthly_subscription"] = True
                changed = True
            if "online" in item:
                item.pop("online", None)
                changed = True
            if item.get("owned") is True:
                item.pop("owned", None)
                changed = True
            if changed:
                updated += 1

        # Reorder only TTS entries so official DLC appears first among them.
        tts_indexes = [idx for idx, item in enumerate(pc_entries) if isinstance(item, dict) and _is_tts_entry(item)]
        if tts_indexes:
            tts_items = [pc_entries[idx] for idx in tts_indexes]
            tts_official = [it for it in tts_items if isinstance(it, dict) and _is_tts_official_dlc_entry(it)]
            tts_other = [it for it in tts_items if not (isinstance(it, dict) and _is_tts_official_dlc_entry(it))]
            reordered_tts = tts_official + tts_other
            if reordered_tts != tts_items:
                for idx, new_item in zip(tts_indexes, reordered_tts):
                    pc_entries[idx] = new_item
                updated += 1

    return updated


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


def _find_existing_boardspace_link(entry: Dict[str, Any]) -> Optional[str]:
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
            if "boardspace" in store or "boardspace.net" in url:
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

    if _is_tts_official_dlc_entry(item):
        return False

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
    if store_key == "boardspace" or "boardspace.net" in url_lower:
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
) -> Tuple[int, int, int]:
    """Add missing Tabletop Simulator workshop links to sidecar entries.

    Uses a conservative, name-based search over Steam Workshop and only accepts
    high-confidence matches.
    """
    games = data.setdefault("games", {})
    added_links = 0
    corrected_links = 0

    for game_id, candidate in candidates.items():
        entry = games.get(game_id)
        if not isinstance(entry, dict):
            continue

        pc_entries = _ensure_platform_list(entry, "pc")

        # Remove deterministic known-bad mappings quickly without remote calls.
        bad_urls_lower = {u.lower() for u in TABLETOP_SIMULATOR_BAD_URLS}
        for idx in reversed(range(len(pc_entries))):
            item = pc_entries[idx]
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            if url and url.lower() in bad_urls_lower:
                pc_entries.pop(idx)
                corrected_links += 1

        existing_urls = _find_existing_tabletop_simulator_links(entry)

        override_url = str(TABLETOP_SIMULATOR_GAME_ID_OVERRIDES.get(str(game_id), "")).strip()
        if override_url and override_url not in existing_urls:
            payload: Dict[str, Any] = {
                "store": "Tabletop Simulator",
                "url": override_url,
                "online": True,
                "note": str(candidate.get("name", "")).strip() or "Tabletop Simulator",
            }
            pc_entries.append(payload)
            added_links += 1
            existing_urls.add(override_url)

        if not TABLETOP_SIMULATOR_ENABLE_AUTO_DISCOVERY:
            continue

        candidate_names = _tts_query_names(candidate)
        # Manual sidecar TTS notes often capture practical aliases/editions;
        # use them as high-signal hints for finding additional modules.
        note_aliases = _tts_note_aliases_from_entry(entry)
        if note_aliases:
            merged: List[str] = []
            seen_names: Set[str] = set()

            def add_name(value: str) -> None:
                text = str(value or "").strip()
                key = text.lower()
                if text and key not in seen_names:
                    seen_names.add(key)
                    merged.append(text)

            # Keep canonical name first, then note aliases, then other alternates.
            if candidate_names:
                add_name(candidate_names[0])
                for alias in note_aliases:
                    add_name(alias)
                for name in candidate_names[1:]:
                    add_name(name)
                candidate_names = merged
        matched_results: Dict[str, Tuple[int, str]] = {}

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
                if item_url in existing_urls:
                    continue
                if not _tts_title_language_allowed(item_title):
                    continue
                score = 0
                for name_for_scoring in queried_names:
                    score = max(score, _score_tts_title_match(name_for_scoring, item_title))
                if score < 95:
                    continue
                existing_match = matched_results.get(item_url)
                if not existing_match or score > existing_match[0]:
                    matched_results[item_url] = (score, item_title)

        if not matched_results:
            matched_results = {}

        for item_url, (_score, item_title) in sorted(
            matched_results.items(),
            key=lambda pair: (-pair[1][0], pair[1][1].lower(), pair[0]),
        )[:3]:
            payload: Dict[str, Any] = {
                "store": "Tabletop Simulator",
                "url": item_url,
                "online": True,
                "note": str(item_title or "").strip() or str(candidate.get("name", "")).strip(),
            }
            if not payload.get("note"):
                payload.pop("note", None)
            pc_entries.append(payload)
            added_links += 1

    return added_links, len(candidates), corrected_links


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


def enrich_boardspace_links(
    data: Dict[str, Any],
    candidates: Dict[str, Dict[str, Any]],
    boardspace_map: Dict[str, str],
) -> Tuple[int, int, int]:
    """Upsert Boardspace links to sidecar entries.

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
            for key in _boardspace_name_keys(str(candidate_name)):
                if key in boardspace_map:
                    match_url = boardspace_map[key]
                    break
            if match_url:
                break

        pc_entries = _ensure_platform_list(entry, "pc")
        existing_indexes: List[int] = []
        for idx, item in enumerate(pc_entries):
            if not isinstance(item, dict):
                continue
            store = str(item.get("store", "")).strip().lower()
            url = str(item.get("url", "")).strip().lower()
            if "boardspace" in store or "boardspace.net" in url:
                existing_indexes.append(idx)

        if not match_url:
            for idx in reversed(existing_indexes):
                pc_entries.pop(idx)
                corrected_links += 1
            continue

        payload: Dict[str, Any] = {
            "store": "Boardspace",
            "url": match_url,
            "online": True,
            "note": str(candidate.get("name", "")).strip(),
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

        for idx in reversed(existing_indexes[1:]):
            pc_entries.pop(idx)
            corrected_links += 1

    return added_links, len(candidates), corrected_links


def _find_existing_forteller_link(entry: Dict[str, Any]) -> Optional[str]:
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
            if "forteller" in store or "fortellergames.com" in url:
                return str(item.get("url", "")).strip()
    return None


def enrich_forteller_narratives_links(
    data: Dict[str, Any],
    all_name_candidates: Dict[str, Dict[str, Any]],
    forteller_map: Dict[str, str],
) -> Tuple[int, int]:
    """Add Forteller narrative companion links as Subscribe entries.

    Returns:
        (added_links, unmatched_catalog_items)
    """
    games = data.setdefault("games", {})
    added_links = 0
    unmatched = 0

    exact_index = _build_exact_name_index(all_name_candidates)

    for normalized_title, url in forteller_map.items():
        matched_ids = list(exact_index.get(normalized_title, []))
        if len(matched_ids) > 1:
            primary_exact = [
                gid
                for gid in matched_ids
                if _normalize_name(str(all_name_candidates.get(gid, {}).get("name", ""))) == normalized_title
            ]
            if len(primary_exact) == 1:
                matched_ids = primary_exact

        if len(matched_ids) != 1:
            unmatched += 1
            continue

        game_id = matched_ids[0]
        entry = games.get(game_id)
        if not isinstance(entry, dict):
            continue

        if _find_existing_forteller_link(entry):
            continue

        canonical_note = str(all_name_candidates.get(game_id, {}).get("name", "")).strip()
        payload: Dict[str, Any] = {
            "store": "Forteller Narratives",
            "url": str(url).strip(),
            "monthly_subscription": True,
            "note": canonical_note,
        }
        if not payload.get("note"):
            payload.pop("note", None)

        pc_entries = _ensure_platform_list(entry, "pc")
        pc_entries.append(payload)
        added_links += 1

    return added_links, unmatched


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
    parser.add_argument(
        "--skip-boardspace-auto-links",
        action="store_true",
        help="Skip automatic Boardspace link enrichment from SQLite + index catalog.",
    )
    parser.add_argument(
        "--boardspace-timeout",
        type=float,
        default=20.0,
        help="Timeout in seconds for Boardspace index request (default: 20).",
    )
    parser.add_argument(
        "--skip-forteller-auto-links",
        action="store_true",
        help="Skip automatic Forteller Narratives link enrichment from catalog.",
    )
    parser.add_argument(
        "--forteller-timeout",
        type=float,
        default=20.0,
        help="Timeout in seconds for Forteller catalog request (default: 20).",
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
        all_name_candidates = load_all_name_candidates(conn)
        yucata_candidates = load_yucata_candidates(conn)
        tabletopia_candidates = load_tabletopia_candidates(conn)
        vassal_candidates = load_vassal_candidates(conn)
        tabletop_simulator_candidates = load_tabletop_simulator_candidates(conn)
        brettspielwelt_candidates = load_brettspielwelt_candidates(conn)
        boardspace_candidates = load_boardspace_candidates(conn)
    finally:
        conn.close()

    data, added, updated_existing = sync_sidecar(data, rows)

    yucata_added = 0
    yucata_notes_updated = 0
    tabletopia_added = 0
    vassal_added = 0
    tabletop_simulator_added = 0
    tabletop_simulator_corrected = 0
    tabletop_simulator_dlc_added = 0
    tabletop_simulator_dlc_unmatched = 0
    tabletop_simulator_dlc_store_label_updates = 0
    tabletop_simulator_dlc_url_updates = 0
    tabletop_simulator_official_dlc_entry_updates = 0
    tabletop_simulator_sidecar_candidate_promotions = 0
    brettspielwelt_added = 0
    boardspace_added = 0
    forteller_added = 0
    brettspielwelt_corrected = 0
    boardspace_corrected = 0
    forteller_unmatched = 0
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
    boardspace_candidates_count = len(boardspace_candidates)
    yucata_error = ""
    tabletopia_error = ""
    vassal_error = ""
    tabletop_simulator_error = ""
    brettspielwelt_error = ""
    boardspace_error = ""
    forteller_error = ""
    tabletopia_premium_by_short_url: Dict[str, bool] = {}
    tabletopia_name_by_short_url: Dict[str, str] = {}
    vassal_title_by_slug: Dict[str, str] = {}
    yucata_misses: List[Tuple[str, str, List[str]]] = []

    tabletop_simulator_sidecar_candidate_promotions = augment_tts_candidates_with_sidecar(
        data,
        tabletop_simulator_candidates,
    )
    tabletop_simulator_candidates_count = len(tabletop_simulator_candidates)
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
            tabletop_simulator_added, _, tabletop_simulator_corrected = enrich_tabletop_simulator_links(
                data,
                tabletop_simulator_candidates,
                timeout=args.tabletop_simulator_timeout,
            )
        except Exception as exc:
            tabletop_simulator_error = str(exc)

    if not args.skip_tabletop_simulator_auto_links:
        tabletop_simulator_dlc_store_label_updates = normalize_tabletop_simulator_dlc_store_labels(data)
        tabletop_simulator_dlc_url_updates = normalize_tabletop_simulator_official_dlc_urls(data)
        tabletop_simulator_official_dlc_entry_updates = normalize_tabletop_simulator_official_dlc_entries(data)

    if not args.skip_tabletop_simulator_auto_links:
        try:
            tabletop_simulator_dlc_added, tabletop_simulator_dlc_unmatched = add_tabletop_simulator_official_dlc_links(
                data,
                all_name_candidates,
                timeout=args.tabletop_simulator_timeout,
            )
        except Exception as exc:
            if tabletop_simulator_error:
                tabletop_simulator_error = f"{tabletop_simulator_error}; dlc_error={exc}"
            else:
                tabletop_simulator_error = f"dlc_error={exc}"

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

    if not args.skip_boardspace_auto_links and boardspace_candidates_count > 0:
        try:
            boardspace_map = fetch_boardspace_catalog(timeout=args.boardspace_timeout)
            boardspace_added, _, boardspace_corrected = enrich_boardspace_links(
                data,
                boardspace_candidates,
                boardspace_map,
            )
        except Exception as exc:
            boardspace_error = str(exc)

    if not args.skip_forteller_auto_links:
        try:
            forteller_map = fetch_forteller_narratives_catalog(timeout=args.forteller_timeout)
            forteller_added, forteller_unmatched = enrich_forteller_narratives_links(
                data,
                all_name_candidates,
                forteller_map,
            )
        except Exception as exc:
            forteller_error = str(exc)

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
        print(f"tabletop_simulator_sidecar_candidate_promotions={tabletop_simulator_sidecar_candidate_promotions}")
        print(f"brettspielwelt_candidates={brettspielwelt_candidates_count}")
        print(f"boardspace_candidates={boardspace_candidates_count}")
        print(f"would_add_yucata_links={yucata_added}")
        print(f"would_add_tabletopia_links={tabletopia_added}")
        print(f"would_add_vassal_links={vassal_added}")
        print(f"would_add_tabletop_simulator_links={tabletop_simulator_added}")
        print(f"would_add_tabletop_simulator_dlc_links={tabletop_simulator_dlc_added}")
        print(f"would_unmatched_tabletop_simulator_dlc_links={tabletop_simulator_dlc_unmatched}")
        print(f"would_update_tabletop_simulator_dlc_store_labels={tabletop_simulator_dlc_store_label_updates}")
        print(f"would_update_tabletop_simulator_dlc_urls={tabletop_simulator_dlc_url_updates}")
        print(f"would_update_tabletop_simulator_official_dlc_entries={tabletop_simulator_official_dlc_entry_updates}")
        print(f"would_correct_tabletop_simulator_links={tabletop_simulator_corrected}")
        print(f"would_add_brettspielwelt_links={brettspielwelt_added}")
        print(f"would_add_boardspace_links={boardspace_added}")
        print(f"would_add_forteller_links={forteller_added}")
        print(f"would_correct_brettspielwelt_links={brettspielwelt_corrected}")
        print(f"would_correct_boardspace_links={boardspace_corrected}")
        print(f"would_unmatched_forteller_catalog_items={forteller_unmatched}")
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
        if boardspace_error:
            print(f"boardspace_error={boardspace_error}")
        if forteller_error:
            print(f"forteller_error={forteller_error}")
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
    print(f"tabletop_simulator_sidecar_candidate_promotions={tabletop_simulator_sidecar_candidate_promotions}")
    print(f"brettspielwelt_candidates={brettspielwelt_candidates_count}")
    print(f"boardspace_candidates={boardspace_candidates_count}")
    print(f"added_yucata_links={yucata_added}")
    print(f"added_tabletopia_links={tabletopia_added}")
    print(f"added_vassal_links={vassal_added}")
    print(f"added_tabletop_simulator_links={tabletop_simulator_added}")
    print(f"added_tabletop_simulator_dlc_links={tabletop_simulator_dlc_added}")
    print(f"unmatched_tabletop_simulator_dlc_links={tabletop_simulator_dlc_unmatched}")
    print(f"updated_tabletop_simulator_dlc_store_labels={tabletop_simulator_dlc_store_label_updates}")
    print(f"updated_tabletop_simulator_dlc_urls={tabletop_simulator_dlc_url_updates}")
    print(f"updated_tabletop_simulator_official_dlc_entries={tabletop_simulator_official_dlc_entry_updates}")
    print(f"corrected_tabletop_simulator_links={tabletop_simulator_corrected}")
    print(f"added_brettspielwelt_links={brettspielwelt_added}")
    print(f"added_boardspace_links={boardspace_added}")
    print(f"added_forteller_links={forteller_added}")
    print(f"corrected_brettspielwelt_links={brettspielwelt_corrected}")
    print(f"corrected_boardspace_links={boardspace_corrected}")
    print(f"unmatched_forteller_catalog_items={forteller_unmatched}")
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
    if boardspace_error:
        print(f"boardspace_error={boardspace_error}")
    if forteller_error:
        print(f"forteller_error={forteller_error}")
    print(f"total_sidecar_games={len(data.get('games', {}))}")
    print(f"written={sidecar_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
