import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Add the scripts directory to the path for imports.
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from gamecache.rulebook_discovery import RulebookDiscovery, parse_publishers_json  # noqa: E402


def resolve_db_path(db_arg, prefer_repo_root=False):
    """Resolve sqlite path robustly for runs from either repo root or scripts/."""
    candidate = Path(db_arg)
    if candidate.is_absolute() and candidate.exists():
        return candidate

    repo_root = script_dir.parent
    root_candidate = (repo_root / candidate).resolve()
    if prefer_repo_root and root_candidate.exists():
        return root_candidate

    cwd_candidate = (Path.cwd() / candidate).resolve()
    if cwd_candidate.exists():
        return cwd_candidate

    if root_candidate.exists():
        return root_candidate

    # Fall back to the root candidate so the error message is deterministic.
    return root_candidate


def ensure_games_table_exists(cursor, db_path):
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='games'"
    )
    if cursor.fetchone() is None:
        raise RuntimeError(
            f"Database file does not contain a games table: {db_path}. "
            "Run download_and_index.py first or pass --db with the correct path."
        )


def ensure_rulebook_column_exists(cursor):
    cursor.execute("PRAGMA table_info(games)")
    columns = {row[1] for row in cursor.fetchall()}
    if "rulebook_urls" not in columns:
        cursor.execute("ALTER TABLE games ADD COLUMN rulebook_urls TEXT DEFAULT '[]'")


def iter_games(cursor, only_missing):
    where_clause = ""
    if only_missing:
        where_clause = "WHERE rulebook_urls IS NULL OR rulebook_urls = '' OR rulebook_urls = '[]'"

    cursor.execute(
        f'''
            SELECT collection_id, id, name, publishers, rulebook_urls
            FROM games
            {where_clause}
            ORDER BY name
        '''
    )

    for row in cursor.fetchall():
        yield {
            "collection_id": row[0],
            "id": row[1],
            "name": row[2],
            "publishers": row[3],
            "rulebook_urls": row[4],
        }


def main(args):
    db_path = resolve_db_path(args.db, prefer_repo_root=(args.db == "gamecache.sqlite"))
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    ensure_games_table_exists(cursor, db_path)
    ensure_rulebook_column_exists(cursor)
    conn.commit()

    discovery = RulebookDiscovery(timeout=args.timeout, delay_seconds=args.delay)

    processed = 0
    updated = 0

    for game in iter_games(cursor, only_missing=args.only_missing):
        if args.limit and processed >= args.limit:
            break

        processed += 1
        publishers = parse_publishers_json(game["publishers"])
        findings = discovery.discover_urls(game["id"], game["name"], publishers)

        # Preserve existing results unless --force is used.
        if not args.force and game["rulebook_urls"]:
            try:
                existing = json.loads(game["rulebook_urls"])
                if isinstance(existing, list) and existing:
                    continue
            except Exception:
                pass

        cursor.execute(
            "UPDATE games SET rulebook_urls = ? WHERE collection_id = ?",
            (json.dumps(findings), game["collection_id"]),
        )
        updated += 1

        if args.verbose:
            print(f"[{processed}] {game['name']}: {len(findings)} URLs")

        if processed % 25 == 0:
            conn.commit()

    conn.commit()
    conn.close()

    print(f"Processed {processed} games. Updated {updated} records.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Discover and store rulebook URLs for games in SQLite")
    parser.add_argument("--db", default="gamecache.sqlite", help="Path to gamecache sqlite file")
    parser.add_argument("--limit", type=int, default=0, help="Max number of games to process (0 = all)")
    parser.add_argument("--only-missing", action="store_true", help="Only process rows with empty rulebook_urls")
    parser.add_argument("--force", action="store_true", help="Overwrite existing rulebook_urls")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between HTTP requests (seconds)")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout in seconds")
    parser.add_argument("--verbose", action="store_true", help="Print per-game progress")

    main(parser.parse_args())
