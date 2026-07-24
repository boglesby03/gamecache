import sqlite3
import json
import logging
from pathlib import Path
from typing import List, Dict, Any
from .models import BoardGame
import io
import time  # Added for fetch_image retry
from .vendor import colorgram
from PIL import Image, ImageFile
from .http_client import make_http_request
from tqdm import tqdm

# Allow colorgram to read truncated files
ImageFile.LOAD_TRUNCATED_IMAGES = True

logger = logging.getLogger(__name__)

class SqliteIndexer:
    """SQLite-based indexer to replace Algolia indexer."""

    def __init__(self, db_path: str = "gamecache.sqlite", extract_colors: bool = True, digital_versions_path: str = "game_metadata_overrides.json"):
        self.db_path = db_path
        self.db_path_gz = f"{db_path}.gz"
        self.extract_colors = extract_colors
        self.digital_versions = self._load_digital_versions(digital_versions_path)
        self._init_database()

    def _load_digital_versions(self, digital_versions_path: str) -> Dict[str, Any]:
        """Load optional per-game digital ownership metadata from JSON file."""
        path = Path(digital_versions_path)
        if not path.exists():
            logger.info(f"Digital versions file not found ({digital_versions_path}); continuing without digital metadata")
            return {}

        try:
            with open(path, 'r', encoding='utf-8') as f:
                payload = json.load(f)

            if isinstance(payload, dict) and isinstance(payload.get('games'), dict):
                payload = payload['games']

            if not isinstance(payload, dict):
                logger.warning(f"Digital versions file {digital_versions_path} must be a JSON object keyed by BGG id")
                return {}

            return payload
        except Exception as e:
            logger.warning(f"Failed to parse digital versions file {digital_versions_path}: {e}")
            return {}

    def _init_database(self):
        """Initialize the SQLite database with required tables."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Create games table with all necessary fields.
        # Keep existing data so enrichment data (for example rulebook URLs)
        # survives future ingestion runs.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS games (
                collection_id INTEGER PRIMARY KEY,
                id INTEGER,
                name TEXT NOT NULL,
                description TEXT,
                categories TEXT,  -- JSON array
                mechanics TEXT,   -- JSON array
                players TEXT,     -- JSON array of [number, type] pairs
                weight REAL,
                playing_time TEXT,
                min_age INTEGER,
                rank INTEGER,
                usersrated INTEGER,
                numowned INTEGER,
                rating REAL,
                numplays INTEGER,
                image TEXT,
                thumbnail TEXT,
                tags TEXT,        -- JSON array
                previous_players TEXT,  -- JSON array
                expansions TEXT,        -- JSON array
                color TEXT,             -- Changed from colors to color (singular)
                alternate_names TEXT,   -- JSON array
                comment TEXT,
                wishlist_comment TEXT,
                wishlist_priority TEXT,
                artists TEXT,           -- JSON array
                designers TEXT,         -- JSON array
                publishers TEXT,        -- JSON array
                year INTEGER,
                accessories TEXT,       -- JSON array
                families TEXT,          -- JSON array
                reimplements TEXT,      -- JSON array
                reimplementedby TEXT,   -- JSON array
                integrates TEXT,        -- JSON array
                wl_exp TEXT,          -- JSON array
                wl_acc TEXT,          -- JSON array
                po_exp TEXT,          -- JSON array
                po_acc TEXT,          -- JSON array
                contained TEXT,       -- JSON array
                weightRating REAL,    -- JSON array
                other_ranks TEXT,     -- JSON array
                average REAL,
                suggested_age REAL,
                last_modified TEXT,
                version_name TEXT,
                version_year INTEGER,
                first_played TEXT,
                last_played TEXT,
                rulebook_urls TEXT DEFAULT '[]',
                digital_versions TEXT DEFAULT '{}'
            )
        ''')

        self._ensure_games_schema_migrations(cursor)

        cursor.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS games_fts USING fts5 (
                collection_id,
                id,
                name,
                description,
                categories,
                mechanics,
                tags,
                expansions,
                alternate_names,
                comment,
                wishlist_comment,
                artists,
                designers,
                publishers,
                year,
                accessories,
                families,
                reimplements,
                reimplementedby,
                integrates,
                wl_exp,
                wl_acc,
                po_exp,
                po_acc,
                contained,
                other_ranks,
                version_name
            )
        ''')

        # Create metadata table for storing database metadata
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS color_cache (
                thumbnail TEXT PRIMARY KEY,
                color TEXT NOT NULL
            )
        ''')

        conn.commit()
        conn.close()
        logger.info(f"Initialized SQLite database: {self.db_path}")

    def _ensure_games_schema_migrations(self, cursor):
        """Apply additive schema migrations for existing gamecache databases."""
        cursor.execute("PRAGMA table_info(games)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        if 'rulebook_urls' not in existing_columns:
            cursor.execute("ALTER TABLE games ADD COLUMN rulebook_urls TEXT DEFAULT '[]'")
        if 'digital_versions' not in existing_columns:
            cursor.execute("ALTER TABLE games ADD COLUMN digital_versions TEXT DEFAULT '{}'")

    def _create_indexes(self, cursor):
        """Create secondary indexes after bulk loading rows."""
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_name ON games(name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_categories ON games(categories)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_mechanics ON games(mechanics)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_weight ON games(weight)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_playing_time ON games(playing_time)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_min_age ON games(min_age)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_rank ON games(rank)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_rating ON games(rating)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_numplays ON games(numplays)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_publisher on games(publishers)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_designer on games(designers)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_artists on games(artists)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_year on games(year)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_priority on games(wishlist_priority)')

    def _normalize_document_list(self, raw_value: Any) -> List[Dict[str, str]]:
        """Normalize document lists to [{"url": ..., "name": ...?}] shape."""
        if not isinstance(raw_value, list):
            return []

        normalized: List[Dict[str, str]] = []
        for item in raw_value:
            url = ""
            name = ""

            if isinstance(item, str):
                url = item.strip()
            elif isinstance(item, dict):
                url = str(item.get('url', '') or '').strip()
                name = str(item.get('name', '') or '').strip()
            else:
                continue

            if not url:
                continue

            doc: Dict[str, str] = {'url': url}
            if name:
                doc['name'] = name
            normalized.append(doc)

        return normalized

    def _normalize_digital_platform_entry(self, entry: Any) -> Dict[str, Any]:
        """Normalize a single digital platform/service entry."""
        if not isinstance(entry, dict):
            return {}

        normalized: Dict[str, Any] = {}

        store = str(
            entry.get('store', '')
            or entry.get('service', '')
            or entry.get('name', '')
            or entry.get('label', '')
            or ''
        ).strip()
        url = str(entry.get('url', '') or '').strip()

        if store:
            normalized['store'] = store
        if url:
            normalized['url'] = url

        for flag in ('owned', 'wishlisted', 'preordered'):
            if bool(entry.get(flag, False)):
                normalized[flag] = True

        return normalized

    def _normalize_digital_platform_list(self, raw_value: Any) -> List[Dict[str, Any]]:
        """Normalize a platform into a list of service entries."""
        if isinstance(raw_value, list):
            items = raw_value
        elif isinstance(raw_value, dict):
            items = [raw_value]
        else:
            return []

        normalized: List[Dict[str, Any]] = []
        for item in items:
            platform_entry = self._normalize_digital_platform_entry(item)
            if platform_entry:
                normalized.append(platform_entry)

        return normalized

    def _normalize_digital_entry(self, entry: Any) -> Dict[str, Any]:
        """Normalize digital metadata and platform status fields for storage in SQLite."""
        if not isinstance(entry, dict):
            return {}

        normalized: Dict[str, Any] = {}

        name = str(entry.get('name', '') or '').strip()
        short_description = str(entry.get('short_description', '') or '').strip()
        rulebooks = self._normalize_document_list(entry.get('rulebooks', []))
        supplemental_files = self._normalize_document_list(entry.get('supplemental_files', []))

        if name:
            normalized['name'] = name
        if short_description:
            normalized['short_description'] = short_description

        if rulebooks:
            normalized['rulebooks'] = rulebooks
        if supplemental_files:
            normalized['supplemental_files'] = supplemental_files

        for platform in ('android', 'ios', 'pc'):
            platform_data = self._normalize_digital_platform_list(entry.get(platform))
            if platform_data:
                normalized[platform] = platform_data

        return normalized

    def _get_cached_color(self, cursor, thumbnail):
        if not thumbnail:
            return None

        cursor.execute('SELECT color FROM color_cache WHERE thumbnail = ?', (thumbnail,))
        row = cursor.fetchone()
        return row[0] if row else None

    def _set_cached_color(self, cursor, thumbnail, color):
        if not thumbnail or not color:
            return

        cursor.execute(
            'INSERT OR REPLACE INTO color_cache (thumbnail, color) VALUES (?, ?)',
            (thumbnail, color),
        )

    def _extract_dominant_color(self, game, cursor):
        default_color = "211, 211, 211"
        thumbnail = game.get("thumbnail")

        if not thumbnail or not self.extract_colors:
            return default_color

        cached_color = self._get_cached_color(cursor, thumbnail)
        if cached_color:
            return cached_color

        color_str = None
        image_data = self.fetch_image(thumbnail)
        if image_data:
            try:
                pil_image = Image.open(io.BytesIO(image_data)).convert('RGBA')
                num_colors_to_try = 10
                extracted_colors = colorgram.extract(pil_image, num_colors_to_try)

                if extracted_colors:
                    selected_color_rgb = None
                    for i in range(min(num_colors_to_try, len(extracted_colors))):
                        c = extracted_colors[i].rgb
                        luma = (
                            0.2126 * c.r / 255.0 +
                            0.7152 * c.g / 255.0 +
                            0.0722 * c.b / 255.0
                        )
                        if 0.2 < luma < 0.8:
                            selected_color_rgb = c
                            break

                    if not selected_color_rgb:
                        selected_color_rgb = extracted_colors[0].rgb

                    color_str = f"{selected_color_rgb.r}, {selected_color_rgb.g}, {selected_color_rgb.b}"
                else:
                    logger.warning(f"Colorgram could not extract colors for image: {game['image']}")
            except Exception as e:
                logger.error(f"Error processing image for color extraction {game['image']}: {e}")

        if not color_str:
            color_str = default_color

        self._set_cached_color(cursor, thumbnail, color_str)
        return color_str

    def fetch_image(self, url, tries=0):  # Copied from indexer.py
        try:
            response, status = make_http_request(url)
        except Exception as e:
            logger.warning(f"Failed to fetch image {url} (try {tries + 1}): {e}")
            if tries < 2:  # Max 3 tries (0, 1, 2)
                time.sleep(2)
                return self.fetch_image(url, tries=tries + 1)
            return None  # Return None after max retries

        return response

    def add_objects(self, collection: List[BoardGame]):
        """Add BoardGame objects to the SQLite database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("PRAGMA synchronous = OFF")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA cache_size = -20000")

        game_rows = []
        fts_rows = []

        for game_obj in tqdm(collection, desc="Processing games", total=len(collection)):
            game = game_obj.todict()  # Convert BoardGame object to dictionary

            # Convert complex fields to JSON strings
            categories_list = game.get('categories', [])
            categories_json = json.dumps(categories_list)

            mechanics_list = game.get('mechanics', [])
            mechanics_json = json.dumps(mechanics_list)

            players_json = json.dumps(game.get('players', []))

            tags_list = game.get('tags', [])
            tags_json = json.dumps(tags_list)

            previous_players_json = json.dumps(game.get('previous_players', []))

            expansions_list = game.get('expansions', [])
            expansions_names_list = ' '.join(item.name for item in expansions_list)
            expansions_json = json.dumps([self._expansion_to_dict(exp) for exp in expansions_list if exp])

            accessories_list = game.get('accessories', [])
            accessories_name_list = ' '.join(item.name for item in accessories_list)
            accessories_json = json.dumps([self._expansion_to_dict(acc) for acc in accessories_list if acc])

            wl_exp_list = game.get('wl_exp', [])
            wl_exp_name_list = ' '.join(item.name for item in wl_exp_list)
            wl_exp_json = json.dumps([self._expansion_to_dict(exp) for exp in wl_exp_list if exp])

            wl_acc_list = game.get('wl_acc', [])
            wl_acc_name_list = ' '.join(item.name for item in wl_acc_list)
            wl_acc_json = json.dumps([self._expansion_to_dict(acc) for acc in wl_acc_list if acc])

            po_exp_list = game.get('po_exp', [])
            po_exp_name_list = ' '.join(item.name for item in po_exp_list)
            po_exp_json = json.dumps([self._expansion_to_dict(exp) for exp in po_exp_list if exp])

            po_acc_list = game.get('po_acc', [])
            po_acc_name_list = ' '.join(item.name for item in po_acc_list)
            po_acc_json = json.dumps([self._expansion_to_dict(acc) for acc in po_acc_list if acc])

            alternate_names_list = game.get('alternate_names', [])
            alternate_names_json = json.dumps(alternate_names_list)

            artists_name_list = ' '.join(item.get("name", "") for item in game.get('artists', []))
            artists_json = json.dumps(game.get('artists', []))

            designers_name_list = ' '.join(item.get("name", "") for item in game.get('designers', []))
            designers_json = json.dumps(game.get('designers', []))

            publishers_name_list = ' '.join(item.get("name", "") for item in game.get('publishers', []))
            publishers_json = json.dumps(game.get('publishers', []))

            families_name_list = ' '.join(item.get("name", "") for item in game.get('families', []))
            families_json = json.dumps(game.get('families', []))

            reimplements_name_list = ' '.join(item.get("name", "") for item in game.get('reimplements', []))
            reimplements_json = json.dumps(game.get('reimplements', []))

            reimplementedby_name_list = ' '.join(item.get("name", "") for item in game.get('reimplementedby', []))
            reimplementedby_json = json.dumps(game.get('reimplementedby', []))

            integrated_name_list = ' '.join(item.get("name", "") for item in game.get('integrates', []))
            integrated_json = json.dumps(game.get('integrates', []))

            contained_name_list = ' '.join(item.get("name", "") for item in game.get('contained', []))
            contained_json = json.dumps(game.get('contained', []))

            other_ranks_name_list = ' '.join(item.get("friendlyname", "") for item in game.get('other_ranks', []))
            other_ranks_json = json.dumps(game.get('other_ranks', []))

            color_str = self._extract_dominant_color(game, cursor)
            digital_versions_json = json.dumps(
                self._normalize_digital_entry(self.digital_versions.get(str(game.get('id')), {}))
            )

            game_rows.append((
                game.get('id'), game.get('name'), game.get('description'), categories_json, mechanics_json,
                players_json,
                float(game.get('weight')) if game.get('weight') is not None else None,
                game.get('playing_time'),
                game.get('min_age'),
                int(game.get('rank')) if game.get('rank') is not None else None,
                int(game.get('usersrated')) if game.get('usersrated') is not None else None,
                int(game.get('numowned')) if game.get('numowned') is not None else None,
                float(game.get('rating')) if game.get('rating') is not None else None,
                game.get('numplays'), game.get('image'), game.get('thumbnail'), tags_json, previous_players_json,
                expansions_json, color_str,
                alternate_names_json,
                game.get('comment'),
                game.get('wishlist_comment'),
                game.get('wishlist_priority'),
                artists_json, designers_json, publishers_json,
                int(game.get('year')) if game.get('year') is not None else None,
                accessories_json, families_json, reimplements_json, reimplementedby_json,
                integrated_json, wl_exp_json, wl_acc_json, po_exp_json, po_acc_json, contained_json,
                float(game.get('weightRating')) if game.get('weightRating') is not None else None,
                other_ranks_json,
                float(game.get('average')) if game.get('average') is not None else None,
                float(game.get('suggested_age')) if game.get('suggested_age') is not None else None,
                game.get('last_modified'),
                game.get('version_name'),
                int(game.get('version_year')) if game.get('version_year') is not None else None,
                int(game.get('collection_id')) if game.get('collection_id') is not None else None,
                game.get('first_played'), game.get('last_played'),
                digital_versions_json,
            ))

            fts_rows.append((
                game.get('collection_id'),
                game.get('id'),
                game.get('name'),
                game.get('description'),
                ' '.join(categories_list),
                ' '.join(mechanics_list),
                ' '.join(tags_list),
                expansions_names_list,
                ' '.join(alternate_names_list),
                game.get('comment'),
                game.get('wishlist_comment'),
                artists_name_list, designers_name_list, publishers_name_list,
                game.get('year'),
                accessories_name_list, families_name_list, reimplements_name_list, reimplementedby_name_list,
                integrated_name_list, wl_exp_name_list, wl_acc_name_list, po_exp_name_list, po_acc_name_list, contained_name_list,
                other_ranks_name_list,
                game.get('version_name')
            ))

        cursor.executemany('''
                INSERT INTO games (
                    id, name, description, categories, mechanics, players,
                    weight, playing_time, min_age, rank, usersrated, numowned,
                    rating, numplays, image, thumbnail, tags, previous_players, expansions, color,
                    alternate_names, comment, wishlist_comment, wishlist_priority,
                    artists, designers, publishers, year, accessories, families, reimplements, reimplementedby,
                    integrates, wl_exp, wl_acc, po_exp, po_acc, contained, weightRating, other_ranks,
                    average, suggested_age, last_modified, version_name, version_year, collection_id, first_played, last_played,
                    digital_versions
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(collection_id) DO UPDATE SET
                    id = excluded.id,
                    name = excluded.name,
                    description = excluded.description,
                    categories = excluded.categories,
                    mechanics = excluded.mechanics,
                    players = excluded.players,
                    weight = excluded.weight,
                    playing_time = excluded.playing_time,
                    min_age = excluded.min_age,
                    rank = excluded.rank,
                    usersrated = excluded.usersrated,
                    numowned = excluded.numowned,
                    rating = excluded.rating,
                    numplays = excluded.numplays,
                    image = excluded.image,
                    thumbnail = excluded.thumbnail,
                    tags = excluded.tags,
                    previous_players = excluded.previous_players,
                    expansions = excluded.expansions,
                    color = excluded.color,
                    alternate_names = excluded.alternate_names,
                    comment = excluded.comment,
                    wishlist_comment = excluded.wishlist_comment,
                    wishlist_priority = excluded.wishlist_priority,
                    artists = excluded.artists,
                    designers = excluded.designers,
                    publishers = excluded.publishers,
                    year = excluded.year,
                    accessories = excluded.accessories,
                    families = excluded.families,
                    reimplements = excluded.reimplements,
                    reimplementedby = excluded.reimplementedby,
                    integrates = excluded.integrates,
                    wl_exp = excluded.wl_exp,
                    wl_acc = excluded.wl_acc,
                    po_exp = excluded.po_exp,
                    po_acc = excluded.po_acc,
                    contained = excluded.contained,
                    weightRating = excluded.weightRating,
                    other_ranks = excluded.other_ranks,
                    average = excluded.average,
                    suggested_age = excluded.suggested_age,
                    last_modified = excluded.last_modified,
                    version_name = excluded.version_name,
                    version_year = excluded.version_year,
                    first_played = excluded.first_played,
                    last_played = excluded.last_played,
                    digital_versions = excluded.digital_versions
            ''', game_rows)

        active_collection_ids = [
            row[45]
            for row in game_rows
            if row[45] is not None
        ]
        if active_collection_ids:
            cursor.execute('DROP TABLE IF EXISTS active_collection_ids')
            cursor.execute('CREATE TEMP TABLE active_collection_ids (collection_id INTEGER PRIMARY KEY)')
            cursor.executemany(
                'INSERT OR IGNORE INTO active_collection_ids (collection_id) VALUES (?)',
                [(collection_id,) for collection_id in active_collection_ids],
            )
            cursor.execute('''
                DELETE FROM games
                WHERE collection_id NOT IN (
                    SELECT collection_id FROM active_collection_ids
                )
            ''')
            cursor.execute('DROP TABLE active_collection_ids')
        else:
            cursor.execute('DELETE FROM games')

        # Rebuild FTS contents from the current run to keep the search index in sync.
        cursor.execute('DELETE FROM games_fts')

        cursor.executemany('''
                INSERT INTO games_fts (
                    collection_id, id, name, description, categories, mechanics, tags, expansions,
                    alternate_names, comment, wishlist_comment, artists, designers, publishers, year,
                    accessories, families, reimplements, reimplementedby, integrates, wl_exp, wl_acc,
                    po_exp, po_acc, contained, other_ranks, version_name
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?
                )
            ''', fts_rows)

        self._create_indexes(cursor)

        conn.commit()
        conn.close()
        logger.info(f"Added {len(collection)} games to SQLite database")

    def set_metadata(self, key: str, value: str):
        """Set a metadata value in the database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO metadata (key, value)
            VALUES (?, ?)
        ''', (key, value))
        conn.commit()
        conn.close()
        logger.info(f"Set metadata: {key} = {value}")

    def _expansion_to_dict(self, expansion) -> Dict[str, Any]:
        """Convert expansion object to dictionary for JSON serialization."""
        # Ensure expansion is a dict or can be converted
        if isinstance(expansion, dict):
            return {
                'id': expansion.get('id'),
                'name': expansion.get('name', ''),
                'players': expansion.get('players', []),
                'image': expansion.get('image', ''),
                'thumbnail': expansion.get('thumbnail', ''),
                'rating': str(expansion.get('rating', '')),
                'year': expansion.get('year', ''),
                'wishlist': expansion.get('wishlist_priority', ''),
                'promo': expansion.get('promo', ''),
            }
        if hasattr(expansion, 'todict'):  # If it's an object with todict method
            exp_dict = expansion.todict()
            return {
                'id': exp_dict.get('id'),
                'name': exp_dict.get('name', ''),
                'players': exp_dict.get('players', []),
                'image': exp_dict.get('image', ''),
                'thumbnail': exp_dict.get('thumbnail', ''),
                'rating': str(exp_dict.get('rating', '')),
                'year': exp_dict.get('year', ''),
                'wishlist': exp_dict.get('wishlist_priority', ''),
                'promo': exp_dict.get('promo', ''),
            }
        if hasattr(expansion, '__dict__'):  # Fallback for simple objects
            exp_vars = vars(expansion)
            return {
                'id': exp_vars.get('id'),
                'name': exp_vars.get('name', ''),
                'players': exp_vars.get('players', []),
                'image': exp_vars.get('image', ''),
                'thumbnail': exp_vars.get('thumbnail', ''),
                'rating': str(exp_vars.get('rating', '')),
                'year': exp_vars.get('year', ''),
                'wishlist': exp_vars.get('wishlist_priority', ''),
                'promo': exp_vars.get('promo', ''),
            }
        logger.warning(f"Cannot convert expansion to dict: {expansion}")
        return {}
