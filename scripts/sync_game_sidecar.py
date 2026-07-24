#!/usr/bin/env python3
"""Sync game metadata stubs into game_metadata_overrides.json from SQLite.

This script ensures every game id present in the SQLite `games` table has an
entry in the sidecar file under `games` with at least:
- name
- short_description
- rulebook_url

Existing entries are preserved and only missing keys are added.
"""

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Dict, Tuple, List


DEFAULT_SIDECAR = "game_metadata_overrides.json"
DEFAULT_DB = "gamecache.sqlite"


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
                "rulebook_url": "",
            }
            added += 1
            continue

        entry = games[key]
        if not isinstance(entry, dict):
            games[key] = {
                "name": name,
                "short_description": "",
                "rulebook_url": "",
            }
            updated_existing += 1
            continue

        before = dict(entry)
        entry.setdefault("name", name)
        entry.setdefault("short_description", "")
        entry.setdefault("rulebook_url", "")
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
    args = parser.parse_args()

    db_path = Path(args.db)
    sidecar_path = Path(args.sidecar)

    if not db_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {db_path}")

    data = load_sidecar(sidecar_path)

    conn = sqlite3.connect(str(db_path))
    try:
        rows = choose_canonical_names(conn)
    finally:
        conn.close()

    data, added, updated_existing = sync_sidecar(data, rows)

    if args.dry_run:
        print(f"rows_seen={len(rows)}")
        print(f"would_add={added}")
        print(f"would_update_existing={updated_existing}")
        print(f"total_after={len(data.get('games', {}))}")
        return 0

    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    with sidecar_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=True)
        f.write("\n")

    print(f"rows_seen={len(rows)}")
    print(f"added={added}")
    print(f"updated_existing={updated_existing}")
    print(f"total_sidecar_games={len(data.get('games', {}))}")
    print(f"written={sidecar_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
