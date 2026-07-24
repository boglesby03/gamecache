import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

# Add the scripts directory to the path for imports.
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from gamecache.backblaze_b2 import BackblazeB2Client  # noqa: E402
from gamecache.http_client import make_http_request  # noqa: E402


DOWNLOADABLE_EXTENSIONS = (".pdf", ".zip", ".doc", ".docx", ".rtf")


def is_bgg_filepage_url(url):
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    return "boardgamegeek.com" in host and "/filepage/" in parsed.path


def resolve_db_path(db_arg):
    candidate = Path(db_arg)
    if candidate.is_absolute() and candidate.exists():
        return candidate

    repo_root = script_dir.parent
    root_candidate = (repo_root / candidate).resolve()
    if root_candidate.exists():
        return root_candidate

    cwd_candidate = (Path.cwd() / candidate).resolve()
    if cwd_candidate.exists():
        return cwd_candidate

    return root_candidate


def ensure_columns(cursor):
    cursor.execute("PRAGMA table_info(games)")
    columns = {row[1] for row in cursor.fetchall()}

    if "rulebook_urls" not in columns:
        cursor.execute("ALTER TABLE games ADD COLUMN rulebook_urls TEXT DEFAULT '[]'")
    if "rulebook_assets" not in columns:
        cursor.execute("ALTER TABLE games ADD COLUMN rulebook_assets TEXT DEFAULT '[]'")


def iter_games(cursor, only_missing_assets):
    where_clause = ""
    if only_missing_assets:
        where_clause = (
            "WHERE (rulebook_assets IS NULL OR rulebook_assets = '' OR rulebook_assets = '[]') "
            "AND rulebook_urls IS NOT NULL AND rulebook_urls <> '' AND rulebook_urls <> '[]'"
        )

    cursor.execute(
        f'''
            SELECT collection_id, id, name, rulebook_urls, rulebook_assets
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
            "rulebook_urls": row[3],
            "rulebook_assets": row[4],
        }


def safe_slug(value):
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower())
    cleaned = cleaned.strip("-")
    return cleaned or "unknown"


def infer_extension(url, fallback_name=""):
    path = urlparse(url).path.lower()
    for ext in DOWNLOADABLE_EXTENSIONS:
        if path.endswith(ext):
            return ext

    fallback_name = fallback_name.lower()
    for ext in DOWNLOADABLE_EXTENSIONS:
        if fallback_name.endswith(ext):
            return ext

    return ""


def is_probably_html(payload):
    sample = payload[:512].strip().lower()
    return sample.startswith(b"<!doctype html") or sample.startswith(b"<html")


def fetch_bgg_filepage_bytes(url, timeout):
    match = re.search(r"/filepage/(\d+)", url)
    if not match:
        return None

    filepage_id = match.group(1)
    api_url = f"https://api.geekdo.com/api/filepage/{filepage_id}/file"

    try:
        payload = json.loads(make_http_request(api_url, timeout=timeout)[0].decode("utf-8", errors="ignore"))
    except Exception:
        return None

    files = payload.get("files", [])
    for file_obj in files:
        file_id = file_obj.get("id")
        if not file_id:
            continue

        download_url = f"https://boardgamegeek.com/file/download/{file_id}"
        try:
            data, _ = make_http_request(download_url, timeout=timeout)
        except Exception:
            continue

        if not data or is_probably_html(data):
            continue

        file_name = file_obj.get("filename") or f"file-{file_id}"
        return {
            "data": data,
            "content_type": "b2/x-auto",
            "file_name": file_name,
            "download_url": download_url,
        }

    return None


def fetch_rulebook_bytes(url, timeout, allow_bgg_fallback):
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if "boardgamegeek.com" in host and "/filepage/" in parsed.path:
        if not allow_bgg_fallback:
            return None
        return fetch_bgg_filepage_bytes(url, timeout)

    try:
        response = requests.get(url, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
        payload = response.content
        if not payload or is_probably_html(payload):
            return None

        content_type = response.headers.get("Content-Type", "b2/x-auto")
        if not content_type:
            content_type = "b2/x-auto"

        return {
            "data": payload,
            "content_type": content_type,
            "file_name": Path(urlparse(response.url).path).name,
            "download_url": response.url,
        }
    except Exception:
        return None


def load_json_array(raw_value):
    if not raw_value:
        return []
    try:
        value = json.loads(raw_value)
        return value if isinstance(value, list) else []
    except Exception:
        return []


def sort_rulebook_candidates(url_items):
    # Non-BGG sources first, BGG filepage links last.
    return sorted(
        url_items,
        key=lambda item: 1 if is_bgg_filepage_url(item.get("url", "")) else 0,
    )


def main(args):
    key_id = os.environ.get("B2_KEY_ID")
    app_key = os.environ.get("B2_APPLICATION_KEY")
    bucket_id = os.environ.get("B2_BUCKET_ID")

    if not key_id or not app_key or not bucket_id:
        raise RuntimeError(
            "Missing Backblaze credentials. Set B2_KEY_ID, B2_APPLICATION_KEY, and B2_BUCKET_ID."
        )

    db_path = resolve_db_path(args.db)
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    ensure_columns(cursor)
    conn.commit()

    b2 = BackblazeB2Client(key_id=key_id, application_key=app_key, bucket_id=bucket_id)
    b2.authorize()

    processed = 0
    updated = 0

    for game in iter_games(cursor, only_missing_assets=args.only_missing_assets):
        if args.limit and processed >= args.limit:
            break

        urls = load_json_array(game["rulebook_urls"])
        if not urls:
            continue

        processed += 1

        if not args.force and load_json_array(game["rulebook_assets"]):
            continue

        assets = []
        for item in sort_rulebook_candidates(urls):
            source_url = item.get("url") if isinstance(item, dict) else None
            source = item.get("source") if isinstance(item, dict) else "unknown"
            if not source_url:
                continue

            if is_bgg_filepage_url(source_url) and not args.allow_bgg_fallback:
                assets.append({
                    "source": source,
                    "source_url": source_url,
                    "status": "skipped",
                    "error": "bgg_source_skipped",
                })
                continue

            fetched = fetch_rulebook_bytes(
                source_url,
                timeout=args.timeout,
                allow_bgg_fallback=args.allow_bgg_fallback,
            )
            if not fetched:
                assets.append({
                    "source": source,
                    "source_url": source_url,
                    "status": "failed",
                    "error": "download_unavailable",
                })
                continue

            file_name = fetched.get("file_name") or Path(urlparse(source_url).path).name or "rulebook"
            ext = infer_extension(source_url, file_name)
            if ext and not file_name.lower().endswith(ext):
                file_name = f"{file_name}{ext}"

            destination = (
                f"rulebooks/{game['id']}/{safe_slug(game['name'])}/"
                f"{safe_slug(Path(file_name).stem)}{ext or ''}"
            )

            try:
                result = b2.upload_bytes(
                    data=fetched["data"],
                    destination_path=destination,
                    content_type=fetched.get("content_type", "b2/x-auto"),
                )
                assets.append({
                    "source": source,
                    "source_url": source_url,
                    "resolved_download_url": fetched.get("download_url"),
                    "status": "uploaded",
                    "b2_file_id": result.get("fileId"),
                    "b2_file_name": result.get("fileName"),
                    "sha1": result.get("sha1"),
                    "size": len(fetched["data"]),
                })
            except requests.HTTPError as exc:
                assets.append({
                    "source": source,
                    "source_url": source_url,
                    "resolved_download_url": fetched.get("download_url"),
                    "status": "failed",
                    "error": BackblazeB2Client.describe_http_error(exc),
                })
            except Exception as exc:
                assets.append({
                    "source": source,
                    "source_url": source_url,
                    "resolved_download_url": fetched.get("download_url"),
                    "status": "failed",
                    "error": str(exc),
                })

        cursor.execute(
            "UPDATE games SET rulebook_assets = ? WHERE collection_id = ?",
            (json.dumps(assets), game["collection_id"]),
        )
        updated += 1

        if args.verbose:
            uploaded_count = sum(1 for a in assets if a.get("status") == "uploaded")
            print(f"[{processed}] {game['name']}: uploaded {uploaded_count}/{len(assets)}")

        if processed % 10 == 0:
            conn.commit()

    conn.commit()
    conn.close()
    print(f"Processed {processed} games. Updated {updated} records.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download discovered rulebooks and upload to Backblaze B2")
    parser.add_argument("--db", default="gamecache.sqlite", help="Path to gamecache sqlite file")
    parser.add_argument("--limit", type=int, default=0, help="Max games to process (0 = all)")
    parser.add_argument("--only-missing-assets", action="store_true", help="Only process games without rulebook_assets")
    parser.add_argument("--force", action="store_true", help="Overwrite existing rulebook_assets")
    parser.add_argument("--timeout", type=int, default=45, help="HTTP timeout in seconds")
    parser.add_argument(
        "--allow-bgg-fallback",
        action="store_true",
        help="Allow BGG filepage URLs as download fallback after trying non-BGG links",
    )
    parser.add_argument("--verbose", action="store_true", help="Print per-game progress")
    args = parser.parse_args()

    main(args)
