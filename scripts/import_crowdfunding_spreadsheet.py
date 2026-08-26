#!/usr/bin/env python3
"""Import public crowdfunding hyperlinks from a Google Sheets XLSX export."""

import argparse
import io
import json
import re
import sqlite3
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from sync_game_sidecar import (
    DEFAULT_DB,
    DEFAULT_SIDECAR,
    _crowdfunding_platform,
    _find_existing_crowdfunding_links,
    _normalize_crowdfunding_url,
    load_all_name_candidates,
    load_sidecar,
)

SHEET_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def _cell_value(cell: ET.Element, shared_strings: List[str]) -> str:
    value = cell.find("m:v", SHEET_NS)
    if value is None or value.text is None:
        return ""
    if cell.attrib.get("t") == "s":
        return shared_strings[int(value.text)]
    return value.text


def _load_xlsx_rows_and_links(
    payload: bytes, worksheet: str = "sheet1.xml", name_column: str = "E"
) -> List[Tuple[int, str, str, str]]:
    """Return (row number, game display name, BGG id, campaign URL)."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        shared_strings = [
            "".join(text.text or "" for text in item.iter(f"{{{SHEET_NS['m']}}}t"))
            for item in shared_root.findall("m:si", SHEET_NS)
        ]
        rels_root = ET.fromstring(archive.read(f"xl/worksheets/_rels/{worksheet}.rels"))
        relationships = {
            item.attrib["Id"]: item.attrib["Target"]
            for item in rels_root.findall("pr:Relationship", SHEET_NS)
        }
        sheet_root = ET.fromstring(archive.read(f"xl/worksheets/{worksheet}"))

        values: Dict[str, str] = {}
        for cell in sheet_root.findall(".//m:c", SHEET_NS):
            values[cell.attrib["r"]] = _cell_value(cell, shared_strings)

        row_links: Dict[int, List[str]] = {}
        for cell in sheet_root.findall(".//m:c", SHEET_NS):
            formula = cell.find("m:f", SHEET_NS)
            if formula is None or not formula.text:
                continue
            match = re.match(r'HYPERLINK\("([^"]+)"', formula.text, re.IGNORECASE)
            if match:
                row_number = int("".join(char for char in cell.attrib["r"] if char.isdigit()))
                row_links.setdefault(row_number, []).append(urllib.parse.unquote(match.group(1)))
        for hyperlink in sheet_root.findall(".//m:hyperlink", SHEET_NS):
            ref = hyperlink.attrib.get("ref", "")
            relation_id = hyperlink.attrib.get(f"{{{SHEET_NS['r']}}}id")
            url = relationships.get(relation_id, "").strip()
            if not url or not ref:
                continue
            row_number = int("".join(char for char in ref if char.isdigit()))
            row_links.setdefault(row_number, []).append(urllib.parse.unquote(url))

        rows: List[Tuple[int, str, str, str]] = []
        for row_number, urls in row_links.items():
            game_name = values.get(f"{name_column}{row_number}", "").strip()
            bgg_urls = [url for url in urls if "boardgamegeek.com/boardgame/" in url.lower()]
            bgg_match = (
                re.search(r"boardgamegeek\.com/boardgame/(\d+)", bgg_urls[0], re.IGNORECASE)
                if bgg_urls else None
            )
            if not game_name or not bgg_match:
                continue
            for url in urls:
                if _is_public_crowdfunding_url(url):
                    rows.append((row_number, game_name, bgg_match.group(1), url))
        return rows


def _is_public_crowdfunding_url(url: str) -> bool:
    platform = _crowdfunding_platform(url)
    if not platform:
        return False
    path = urllib.parse.urlparse(url).path.lower()
    if platform in {"Kickstarter", "Gamefound"}:
        return "/projects/" in path
    if platform == "BackerKit":
        return "/invites/" not in path and "/users/" not in path and "/confirm" not in path
    return True


def _load_base_game_ids(conn: sqlite3.Connection) -> Dict[str, Set[str]]:
    """Build an expansion BGG ID -> base-game BGG IDs reverse index."""
    base_game_ids: Dict[str, Set[str]] = {}
    for base_id, expansions_json in conn.execute("SELECT id, expansions FROM games"):
        try:
            expansions = json.loads(expansions_json or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(expansions, list):
            continue
        for expansion in expansions:
            if not isinstance(expansion, dict) or expansion.get("id") is None:
                continue
            expansion_id = str(int(expansion["id"]))
            base_game_ids.setdefault(expansion_id, set()).add(str(int(base_id)))
    return base_game_ids


def import_spreadsheet(
    sidecar: Dict[str, Any],
    candidates: Dict[str, Dict[str, Any]],
    rows: List[Tuple[int, str, str, str]],
    base_game_ids: Dict[str, Set[str]],
) -> Tuple[int, int, List[str], List[str]]:
    games = sidecar.setdefault("games", {})
    added = 0
    propagated = 0
    unmatched: List[str] = []
    ambiguous: List[str] = []
    seen_rows: Set[Tuple[str, str]] = set()

    for row_number, game_name, bgg_id, url in rows:
        url = _normalize_crowdfunding_url(url)
        game_id = str(int(bgg_id))
        target_ids = base_game_ids.get(game_id, {game_id})
        target_ids = {target_id for target_id in target_ids if target_id in candidates}
        if not target_ids:
            unmatched.append(f"row {row_number}: {game_name} (BGG {game_id}) -> {url}")
            continue
        for target_id in sorted(target_ids):
            entry = games.get(target_id)
            if not isinstance(entry, dict):
                continue
            key = (target_id, url.lower().rstrip("/"))
            if key in seen_rows:
                continue
            seen_rows.add(key)
            existing = _find_existing_crowdfunding_links(entry)
            if key[1] in existing:
                continue
            entry.setdefault("crowdfunding_links", []).append({
                "name": game_name,
                "site": _crowdfunding_platform(url),
                "url": url,
            })
            added += 1
            if target_id != game_id:
                propagated += 1

    return added, propagated, unmatched, ambiguous


def normalize_sidecar_crowdfunding_links(sidecar: Dict[str, Any]) -> int:
    """Normalize and deduplicate crowdfunding links within each game."""
    changed = 0
    platforms = {
        "Gamefound", "Kickstarter", "BackerKit", "GMT P500", "Indiegogo",
        "Game On Tabletop", "Verkami", "Spieleschmiede", "zagramw.to", "Wspieram",
        "Ulule", "Giochistarter", "Zeczec", "Modian", "Catarse", "Tumblbug",
    }
    for entry in sidecar.get("games", {}).values():
        if not isinstance(entry, dict) or not isinstance(entry.get("crowdfunding_links"), list):
            continue
        normalized_links = []
        seen: Set[str] = set()
        for item in entry["crowdfunding_links"]:
            if isinstance(item, str):
                item = {"name": _crowdfunding_platform(item), "url": item}
            if not isinstance(item, dict):
                continue
            url = _normalize_crowdfunding_url(str(item.get("url", "")))
            if not url:
                continue
            key = url.lower()
            if key in seen:
                changed += 1
                continue
            seen.add(key)
            old_name = str(item.get("name", "")).strip()
            site = str(item.get("site", "")).strip() or (
                old_name if old_name in platforms else _crowdfunding_platform(url)
            )
            title = old_name if item.get("site") and old_name else str(item.get("display_name", "")).strip() or (
                "Tuscany: Expand the World of Viticulture"
                if url.endswith("/tuscany-expand-the-world-of-viticulture")
                else "Viticulture"
                if url.endswith("/viticulture-the-strategic-game-of-winemaking")
                else str(entry.get("name", "")).strip()
            )
            canonical = {"name": title, "site": site, "url": url}
            if item.get("url") != url:
                changed += 1
            if item != canonical:
                changed += 1
            normalized_links.append(canonical)
        entry["crowdfunding_links"] = normalized_links
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spreadsheet_url")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--sidecar", default=DEFAULT_SIDECAR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    request = urllib.request.Request(
        args.spreadsheet_url,
        headers={"User-Agent": "Mozilla/5.0 (GameCache crowdfunding importer)"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read()
    rows = (
        _load_xlsx_rows_and_links(payload, "sheet1.xml", "E")
        + _load_xlsx_rows_and_links(payload, "sheet2.xml", "D")
    )

    conn = sqlite3.connect(args.db)
    try:
        candidates = load_all_name_candidates(conn)
        base_game_ids = _load_base_game_ids(conn)
    finally:
        conn.close()

    sidecar_path = Path(args.sidecar)
    data = load_sidecar(sidecar_path)
    normalized = normalize_sidecar_crowdfunding_links(data)
    added, propagated, unmatched, ambiguous = import_spreadsheet(data, candidates, rows, base_game_ids)
    print(f"spreadsheet_links={len(rows)}")
    print(f"would_add_crowdfunding_links={added}")
    print(f"would_propagate_to_base_games={propagated}")
    print(f"normalized_existing_links={normalized}")
    print(f"unmatched_rows={len(unmatched)}")
    print(f"ambiguous_rows={len(ambiguous)}")
    for item in unmatched:
        print(f"unmatched={item}")
    for item in ambiguous:
        print(f"ambiguous={item}")

    if not args.dry_run:
        sidecar_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
