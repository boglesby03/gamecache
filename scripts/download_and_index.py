import sys
import gzip
import os
import json
import sqlite3
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Add the scripts directory to the path for imports
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

# Now import after path is set
from gamecache.downloader import Downloader  # noqa: E402
from gamecache.sqlite_indexer import SqliteIndexer  # noqa: E402
from gamecache.github_integration import setup_github_integration  # noqa: E402
from gamecache.config import parse_config_file, create_nested_config  # noqa: E402
from gamecache.http_client import open_url  # noqa: E402
from gamecache.models import BoardGame  # noqa: E402
from setup_logging import setup_logging  # noqa: E402


UPGRADE_INSTRUCTIONS_URL = "https://github.com/EmilStenstrom/gamecache#keeping-your-copy-updated"


def _load_sidecar_game_ids(sidecar_path):
    """Load numeric BGG game ids from sidecar metadata file."""
    path = Path(sidecar_path)
    if not path.exists():
        return set()

    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        print(f"Warning: Could not parse sidecar file {sidecar_path}: {exc}")
        return set()

    games_obj = payload.get("games") if isinstance(payload, dict) else None
    if not isinstance(games_obj, dict):
        return set()

    ids = set()
    for key in games_obj.keys():
        key_str = str(key).strip()
        if key_str.isdigit():
            ids.add(int(key_str))
    return ids


def _create_unowned_collection_stub(game_id, game_name):
    """Create minimal collection metadata for a sidecar-only game."""
    return {
        "id": game_id,
        "name": game_name or f"BGG #{game_id}",
        "numplays": 0,
        "image": None,
        "image_version": None,
        "thumbnail": None,
        "thumbnail_version": None,
        "tags": ["unowned"],
        "comment": "",
        "wishlist_comment": "",
        "players": [],
        "version_name": "",
        "version_year": 0,
        "last_modified": "1970-01-01 00:00:00",
        "first_played": None,
        "last_played": None,
        "collection_id": game_id,
        "publisher_ids": [],
        "version_publisher": 0,
        "custom_version_year": 0,
        "wishlist_priority": "0",
    }


def _append_sidecar_missing_games(collection, downloader, sidecar_path):
    """Fetch and append games found in sidecar but missing from BGG collection."""
    sidecar_ids = _load_sidecar_game_ids(sidecar_path)
    if not sidecar_ids:
        print("No numeric sidecar game IDs found; skipping sidecar-only enrichment.")
        return 0

    existing_ids = {int(getattr(game, "id", 0)) for game in collection}
    missing_ids = sorted(game_id for game_id in sidecar_ids if game_id not in existing_ids)
    print(f"Sidecar IDs: {len(sidecar_ids)} • Collection IDs: {len(existing_ids)} • Missing from collection: {len(missing_ids)}")
    if not missing_ids:
        return 0

    print(f"Fetching {len(missing_ids)} sidecar games missing from collection...")
    try:
        detail_rows = downloader.client.game_list(missing_ids)
    except Exception as exc:
        print(f"Warning: Could not fetch sidecar-only game details from BGG: {exc}")
        return 0
    detail_by_id = {int(row.get("id")): row for row in detail_rows if row and row.get("id")}

    appended = 0
    for game_id in missing_ids:
        game_data = detail_by_id.get(game_id)
        if not game_data:
            print(f"Warning: BGG details not found for sidecar game id {game_id}")
            continue

        try:
            stub = _create_unowned_collection_stub(game_id, game_data.get("name", ""))
            collection.append(BoardGame(game_data, stub, expansions=[], accessories=[]))
            appended += 1
        except Exception as exc:
            print(f"Warning: Could not add sidecar game id {game_id}: {exc}")

    if appended:
        print(f"Added {appended} sidecar-only game(s) to index input.")
    return appended


def get_last_run_date_from_sqlite(sqlite_path):
    """Read last successful run timestamp from existing SQLite metadata."""
    if not os.path.exists(sqlite_path):
        return None

    try:
        conn = sqlite3.connect(sqlite_path)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM metadata WHERE key = ?", ("last_run_date",))
        row = cursor.fetchone()
        conn.close()
        if not row or not row[0]:
            return None
        return datetime.fromisoformat(row[0])
    except Exception:
        return None


def _print_info_box(title, lines):
    content = [title] + list(lines)
    width = max(len(s) for s in content) if content else len(title)
    border = "+" + ("-" * (width + 2)) + "+"
    print(border)
    print(f"| {title.ljust(width)} |")
    print("|" + (" " * (width + 2)) + "|")
    for line in lines:
        print(f"| {line.ljust(width)} |")
    print(border)


def _http_get_json(url, timeout=10, headers=None):
    req = urllib.request.Request(url)
    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('User-Agent', 'GameCache/1.0')
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    with open_url(req, timeout=timeout) as resp:
        data = resp.read()
        return json.loads(data.decode('utf-8', errors='replace'))


def _get_default_branch(owner, repo):
    info = _http_get_json(f"https://api.github.com/repos/{owner}/{repo}")
    return info.get('default_branch')


def check_for_upstream_updates_via_github(github_repo):
    """Check whether the user's repo is behind the upstream template.

    Uses GitHub's compare API (HTTP GET) so it works without git installed.
    """
    if os.environ.get("GAMECACHE_SKIP_UPDATE_CHECK"):
        return

    if not github_repo or '/' not in github_repo:
        return

    owner, repo = github_repo.split('/', 1)

    try:
        upstream_owner = 'EmilStenstrom'
        upstream_repo = 'gamecache'

        upstream_branch = _get_default_branch(upstream_owner, upstream_repo) or 'master'
        head_branch = _get_default_branch(owner, repo) or 'master'

        compare_url = (
            f"https://api.github.com/repos/{upstream_owner}/{upstream_repo}"
            f"/compare/{upstream_branch}...{owner}:{head_branch}"
        )
        comparison = _http_get_json(compare_url, timeout=10)

        behind_by = int(comparison.get('behind_by', 0) or 0)
        if behind_by > 0:
            _print_info_box(
                "New GameCache version available",
                [
                    f"Your repo ({github_repo}) is {behind_by} commits behind upstream.",
                    f"How to update: {UPGRADE_INSTRUCTIONS_URL}",
                    "(Set GAMECACHE_SKIP_UPDATE_CHECK=1 to hide this message)",
                ],
            )
    except urllib.error.HTTPError as e:
        # Don't block the main script if GitHub is rate-limiting or unavailable.
        if e.code == 403:
            # Often rate limit.
            return
        return
    except Exception:
        return

def main(args):
    config = parse_config_file(args.config)
    # Convert flat config to nested structure for backward compatibility
    SETTINGS = create_nested_config(config)

    # Best-effort update check (does not affect script success)
    check_for_upstream_updates_via_github(SETTINGS.get("github", {}).get("repo"))

    # Get BGG token from config, with environment fallback used by update scripts.
    bgg_token = SETTINGS["boardgamegeek"].get("token") or os.environ.get("GAMECACHE_BGG_TOKEN")
    if not bgg_token:
        print("Warning: No BGG token found in config.ini or GAMECACHE_BGG_TOKEN; BGG requests may fail.")

    downloader = Downloader(
        cache_bgg=args.cache_bgg,
        debug=args.debug,
        token=bgg_token,
    )
    sqlite_path = "gamecache.sqlite"
    last_run_date = get_last_run_date_from_sqlite(sqlite_path)
    plays_mindate = None
    if last_run_date:
        # Include a one-day overlap to avoid missing plays around timezone boundaries.
        plays_mindate = (last_run_date - timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"Using incremental plays sync from {plays_mindate}")

    extra_params = {} # SETTINGS["boardgamegeek"].get("extra_params", {"own": 1})
    collection = downloader.collection(
        user_name=SETTINGS["boardgamegeek"]["user_name"],
        extra_params=extra_params,
        plays_mindate=plays_mindate,
        ignore_collection_cache=args.ignore_collection_cache,
    )

    _append_sidecar_missing_games(collection, downloader, args.digital_versions_file)

    #TODO Fix allowing duplicates
    # Deduplicate collection based on game ID
    # seen_ids = set()
    # unique_collection = []
    # for game in collection:
    #     if game.id not in seen_ids:
    #         unique_collection.append(game)
    #         seen_ids.add(game.id)
    # collection = unique_collection

    num_games = len(collection)
    num_expansions = sum([len(game.expansions) for game in collection])
    num_accessories = sum([len(game.accessories) for game in collection ])
    print(f"Imported {num_games} games, {num_expansions} expansions, and {num_accessories} accessories from boardgamegeek.")

    if not len(collection):
        assert False, "No games imported, is the boardgamegeek part of config.ini correctly set?"

    # Create SQLite database
    indexer = SqliteIndexer(
        sqlite_path,
        extract_colors=not args.skip_colors,
        digital_versions_path=args.digital_versions_file,
    )
    indexer.add_objects(collection)
    print(f"Created SQLite database with {num_games} games and {num_expansions} expansions.")

    # Store the last run date in metadata (UTC with timezone info so browsers can convert to local time)
    now = datetime.now(timezone.utc).isoformat()
    indexer.set_metadata("last_run_date", now)
    print(f"Stored last run date: {now}")

    # Gzip the database and remove the original
    gzip_path = f"{sqlite_path}.gz"
    with open(sqlite_path, 'rb') as f_in, gzip.open(gzip_path, 'wb') as f_out:
        f_out.write(f_in.read())
    if not args.save_db:
        os.remove(sqlite_path)
    print(f"Created gzipped database: {gzip_path}")

    # Upload to GitHub if not disabled
    if not args.no_upload:
        try:
            github_manager = setup_github_integration(SETTINGS)

            # Upload the gzipped SQLite file
            snapshot_tag = SETTINGS["github"].get("snapshot_tag", "database")
            asset_name = SETTINGS["github"].get("snapshot_asset", "gamecache.sqlite.gz")

            download_url = github_manager.upload_snapshot(gzip_path, snapshot_tag, asset_name)
            print(f"Successfully uploaded to GitHub: {download_url}")

        except Exception as e:
            print(f"Error uploading to GitHub: {e}")
            sys.exit(1)
    else:
        print("Skipped GitHub upload.")


if __name__ == '__main__':
    import argparse

    setup_logging()

    parser = argparse.ArgumentParser(description='Download and create SQLite database of boardgames')
    parser.add_argument(
        '--no_upload',
        action='store_true',
        help=(
            "Skip uploading to GitHub. This is useful during development"
            ", when you want to test the SQLite creation without uploading."
        )
    )
    parser.add_argument(
        '--cache_bgg',
        action='store_true',
        help=(
            "Enable a cache for all BGG calls. This makes script run very "
            "fast the second time it's run."
        )
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help="Print debug information, such as requests made and responses received."
    )
    parser.add_argument(
        '--config',
        type=str,
        required=False,
        default="config.ini",
        help="Path to the config file (default: config.ini from the working directory)."
    )
    parser.add_argument(
        '--save_db',
        action='store_true',
        help=(
            "Keep the unzipped copy of the sqlite database."
        )
    )
    parser.add_argument(
        '--ignore_collection_cache',
        action='store_true',
        help=(
            "Bypass cache for BGG collection endpoint calls only (main and accessory collection)."
        )
    )
    parser.add_argument(
        '--skip_colors',
        action='store_true',
        help=(
            "Skip thumbnail color extraction to speed up SQLite generation."
        )
    )
    parser.add_argument(
        '--digital_versions_file',
        type=str,
        required=False,
        default='game_metadata_overrides.json',
        help='Path to JSON file containing per-game digital ownership metadata keyed by BGG id.'
    )

    args = parser.parse_args()

    main(args)
