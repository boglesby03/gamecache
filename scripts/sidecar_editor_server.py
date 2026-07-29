#!/usr/bin/env python3
"""Local server for sidecar editor with force digital-search API."""

from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.sync_game_sidecar import (
    add_tabletop_simulator_official_dlc_links,
    annotate_online_statuses,
    annotate_tabletopia_statuses_and_notes,
    annotate_vassal_statuses_and_notes,
    annotate_yucata_notes,
    enrich_boardspace_links,
    enrich_brettspielwelt_links,
    enrich_tabletopia_links,
    enrich_tabletop_simulator_links,
    enrich_vassal_links,
    enrich_yucata_links,
    fetch_boardspace_catalog,
    fetch_brettspielwelt_catalog,
    fetch_tabletopia_game_catalog,
    fetch_vassal_project_catalog,
    fetch_yucata_game_urls,
    load_all_name_candidates,
    load_sidecar,
    normalize_tabletop_simulator_dlc_store_labels,
    normalize_tabletop_simulator_official_dlc_entries,
    normalize_tabletop_simulator_official_dlc_urls,
)
DEFAULT_SIDECAR = ROOT_DIR / "game_metadata_overrides.json"
DEFAULT_DB = ROOT_DIR / "gamecache.sqlite"


def _collect_platform_urls(entry: Dict[str, Any]) -> Set[str]:
    urls: Set[str] = set()
    for platform in ("android", "ios", "pc"):
        items = entry.get(platform)
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            if url:
                urls.add(url)
    return urls


def _build_single_candidate(
    game_id: str,
    entry: Dict[str, Any],
    all_name_candidates: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    candidate = all_name_candidates.get(game_id)
    if isinstance(candidate, dict):
        name = str(candidate.get("name", "")).strip() or str(entry.get("name", "")).strip()
        alternates = candidate.get("alternate_names", [])
        alt_names = [str(v).strip() for v in alternates if str(v).strip()] if isinstance(alternates, list) else []
        return {
            game_id: {
                "id": game_id,
                "name": name,
                "alternate_names": alt_names,
            }
        }

    return {
        game_id: {
            "id": game_id,
            "name": str(entry.get("name", "")).strip(),
            "alternate_names": [],
        }
    }


def force_search_game(
    game_id: str,
    entry_override: Optional[Dict[str, Any]] = None,
    sidecar_path: Path = DEFAULT_SIDECAR,
    db_path: Path = DEFAULT_DB,
    timeout: float = 6.0,
) -> Dict[str, Any]:
    game_id = str(game_id or "").strip()
    if not game_id.isdigit():
        raise ValueError("game_id must be numeric")

    data = load_sidecar(sidecar_path)
    games = data.setdefault("games", {})
    if not isinstance(games, dict):
        raise ValueError("sidecar games container is not an object")

    existing_entry = games.get(game_id)
    if not isinstance(existing_entry, dict):
        existing_entry = {}

    if isinstance(entry_override, dict):
        existing_entry = entry_override

    if not str(existing_entry.get("name", "")).strip():
        raise ValueError("selected game has no name; set a name before force-search")

    games[game_id] = existing_entry
    before_urls = _collect_platform_urls(existing_entry)

    all_name_candidates: Dict[str, Dict[str, Any]] = {}
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        try:
            all_name_candidates = load_all_name_candidates(conn)
        finally:
            conn.close()

    single_candidate = _build_single_candidate(game_id, existing_entry, all_name_candidates)
    source_errors: Dict[str, str] = {}

    # Source runs are isolated so one timeout/error does not block others.
    try:
        yucata_map = fetch_yucata_game_urls(timeout=timeout)
        enrich_yucata_links(data, single_candidate, yucata_map)
        annotate_yucata_notes(data)
    except Exception as exc:
        source_errors["yucata"] = str(exc)

    tabletopia_premium_by_short_url: Dict[str, bool] = {}
    tabletopia_name_by_short_url: Dict[str, str] = {}
    try:
        tabletopia_map, tabletopia_premium_by_short_url, tabletopia_name_by_short_url = fetch_tabletopia_game_catalog(timeout=timeout)
        enrich_tabletopia_links(
            data,
            single_candidate,
            tabletopia_map,
            tabletopia_premium_by_short_url=tabletopia_premium_by_short_url,
            tabletopia_name_by_short_url=tabletopia_name_by_short_url,
        )
        annotate_tabletopia_statuses_and_notes(
            data,
            tabletopia_premium_by_short_url=tabletopia_premium_by_short_url,
            tabletopia_name_by_short_url=tabletopia_name_by_short_url,
        )
    except Exception as exc:
        source_errors["tabletopia"] = str(exc)

    try:
        vassal_map, vassal_title_by_slug = fetch_vassal_project_catalog(timeout=timeout)
        enrich_vassal_links(data, single_candidate, vassal_map, vassal_title_by_slug=vassal_title_by_slug)
        annotate_vassal_statuses_and_notes(data, vassal_title_by_slug=vassal_title_by_slug)
    except Exception as exc:
        source_errors["vassal"] = str(exc)

    try:
        enrich_tabletop_simulator_links(data, single_candidate, timeout=timeout)
        normalize_tabletop_simulator_dlc_store_labels(data)
        normalize_tabletop_simulator_official_dlc_urls(data)
        normalize_tabletop_simulator_official_dlc_entries(data)
        add_tabletop_simulator_official_dlc_links(
            data,
            single_candidate,
            timeout=timeout,
        )
    except Exception as exc:
        source_errors["tabletop_simulator"] = str(exc)

    try:
        brettspielwelt_map = fetch_brettspielwelt_catalog(timeout=timeout)
        enrich_brettspielwelt_links(data, single_candidate, brettspielwelt_map)
    except Exception as exc:
        source_errors["brettspielwelt"] = str(exc)

    try:
        boardspace_map = fetch_boardspace_catalog(timeout=timeout)
        enrich_boardspace_links(data, single_candidate, boardspace_map)
    except Exception as exc:
        source_errors["boardspace"] = str(exc)

    try:
        annotate_online_statuses(data, tabletopia_premium_by_short_url=tabletopia_premium_by_short_url)
    except Exception as exc:
        source_errors["status_normalization"] = str(exc)

    entry_after = games.get(game_id)
    if not isinstance(entry_after, dict):
        entry_after = {}

    after_urls = _collect_platform_urls(entry_after)
    added_urls = sorted(after_urls - before_urls)

    return {
        "ok": True,
        "game_id": game_id,
        "entry": copy.deepcopy(entry_after),
        "summary": {
            "added_urls_count": len(added_urls),
            "added_urls": added_urls,
            "source_errors": source_errors,
        },
    }


class SidecarEditorHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT_DIR), **kwargs)

    def _send_json(self, payload: Dict[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/api/force-digital-search":
            self._send_json({"ok": False, "error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0

        raw = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._send_json({"ok": False, "error": "Invalid JSON body"}, status=HTTPStatus.BAD_REQUEST)
            return

        game_id = str(payload.get("game_id", "")).strip()
        entry = payload.get("entry")
        timeout = payload.get("timeout", 6.0)
        try:
            timeout_value = float(timeout)
        except Exception:
            timeout_value = 6.0

        try:
            result = force_search_game(
                game_id=game_id,
                entry_override=entry if isinstance(entry, dict) else None,
                timeout=max(0.5, timeout_value),
            )
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        self._send_json(result, status=HTTPStatus.OK)


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve sidecar editor with force digital-search API")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), SidecarEditorHandler)
    print(f"Serving {ROOT_DIR} at http://{args.host}:{args.port}")
    print("Open http://localhost:8000/sidecar-editor.html")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
