#!/usr/bin/env python3
"""Interactive editor for game sidecar metadata.

This tool helps update game metadata in game_metadata_overrides.json before
running the indexer. It supports:
- base metadata (name, short_description)
- multiple rulebooks
- multiple supplemental files
- digital platform links/status for android/ios/pc

Status flags (owned, wishlisted, preordered) are stored only when true.
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_SIDECAR = "game_metadata_overrides.json"


def load_sidecar(path: Path) -> Dict[str, Any]:
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


def save_sidecar(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=True)
        f.write("\n")


def normalize_docs(raw: Any) -> List[Dict[str, str]]:
    if not isinstance(raw, list):
        return []

    docs: List[Dict[str, str]] = []
    for item in raw:
        url = ""
        name = ""

        if isinstance(item, str):
            url = item.strip()
        elif isinstance(item, dict):
            url = str(item.get("url", "") or "").strip()
            name = str(item.get("name", "") or "").strip()

        if not url:
            continue

        doc = {"url": url}
        if name:
            doc["name"] = name
        docs.append(doc)

    return docs


def normalize_platform(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    url = str(raw.get("url", "") or "").strip()
    owned = bool(raw.get("owned", False))
    wishlisted = bool(raw.get("wishlisted", False))
    preordered = bool(raw.get("preordered", False))

    out: Dict[str, Any] = {}
    if url:
        out["url"] = url
    if owned:
        out["owned"] = True
    if wishlisted:
        out["wishlisted"] = True
    if preordered:
        out["preordered"] = True

    return out


def normalize_entry(raw: Any, fallback_name: str = "") -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}

    name = str(raw.get("name", fallback_name) or fallback_name).strip()
    short_description = str(raw.get("short_description", "") or "").strip()

    rulebooks = normalize_docs(raw.get("rulebooks", []))
    supplemental_files = normalize_docs(raw.get("supplemental_files", []))

    out: Dict[str, Any] = {
        "name": name,
        "short_description": short_description,
        "rulebooks": rulebooks,
        "supplemental_files": supplemental_files,
    }

    for platform in ("android", "ios", "pc"):
        platform_data = normalize_platform(raw.get(platform, {}))
        if platform_data:
            out[platform] = platform_data

    return out


def prompt_text(label: str, current: str, allow_clear: bool = True) -> str:
    clear_hint = " (type - to clear)" if allow_clear else ""
    value = input(f"{label} [{current}]{clear_hint}: ").strip()
    if value == "":
        return current
    if allow_clear and value == "-":
        return ""
    return value


def prompt_bool_optional(label: str, current: bool) -> Optional[bool]:
    current_str = "y" if current else "n"
    value = input(f"{label} (y/n, Enter keep={current_str}): ").strip().lower()
    if value == "":
        return None
    if value in ("y", "yes", "true", "1"):
        return True
    if value in ("n", "no", "false", "0"):
        return False
    print("  Invalid input, keeping current value.")
    return None


def edit_documents(label: str, docs: List[Dict[str, str]]) -> List[Dict[str, str]]:
    while True:
        print(f"\n{label}:")
        if not docs:
            print("  (none)")
        else:
            for idx, doc in enumerate(docs, start=1):
                name = doc.get("name", "")
                name_part = f" ({name})" if name else ""
                print(f"  {idx}. {doc.get('url', '')}{name_part}")

        cmd = input("  Commands: a=add, e <n>=edit, d <n>=delete, q=done > ").strip()
        if cmd.lower() == "q":
            return docs

        if cmd.lower() == "a":
            url = input("    URL: ").strip()
            if not url:
                print("    URL is required.")
                continue
            name = input("    Name (optional): ").strip()
            doc = {"url": url}
            if name:
                doc["name"] = name
            docs.append(doc)
            continue

        parts = cmd.split()
        if len(parts) != 2 or parts[0].lower() not in ("e", "d"):
            print("    Invalid command.")
            continue

        try:
            index = int(parts[1]) - 1
        except ValueError:
            print("    Invalid number.")
            continue

        if index < 0 or index >= len(docs):
            print("    Out of range.")
            continue

        if parts[0].lower() == "d":
            docs.pop(index)
            continue

        current = docs[index]
        url = prompt_text("    URL", current.get("url", ""), allow_clear=False).strip()
        if not url:
            print("    URL is required.")
            continue
        name = prompt_text("    Name", current.get("name", ""), allow_clear=True).strip()
        updated = {"url": url}
        if name:
            updated["name"] = name
        docs[index] = updated


def edit_platform(entry: Dict[str, Any], platform: str) -> None:
    current = normalize_platform(entry.get(platform, {}))

    print(f"\n{platform.upper()}:")
    url = prompt_text("  URL", current.get("url", ""), allow_clear=True).strip()

    owned_change = prompt_bool_optional("  owned", bool(current.get("owned", False)))
    wishlisted_change = prompt_bool_optional("  wishlisted", bool(current.get("wishlisted", False)))
    preordered_change = prompt_bool_optional("  preordered", bool(current.get("preordered", False)))

    owned = bool(current.get("owned", False)) if owned_change is None else owned_change
    wishlisted = bool(current.get("wishlisted", False)) if wishlisted_change is None else wishlisted_change
    preordered = bool(current.get("preordered", False)) if preordered_change is None else preordered_change

    out: Dict[str, Any] = {}
    if url:
        out["url"] = url
    if owned:
        out["owned"] = True
    if wishlisted:
        out["wishlisted"] = True
    if preordered:
        out["preordered"] = True

    if out:
        entry[platform] = out
    else:
        entry.pop(platform, None)


def summarize_entry(entry: Dict[str, Any]) -> str:
    docs = len(entry.get("rulebooks", []))
    supplements = len(entry.get("supplemental_files", []))
    platform_count = sum(1 for p in ("android", "ios", "pc") if isinstance(entry.get(p), dict) and entry.get(p))
    return f"docs={docs}, supplemental={supplements}, platforms={platform_count}"


def choose_game_id(data: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    games = data.get("games", {})
    game_id = input("\nEnter BGG game id (or q to cancel): ").strip()
    if game_id.lower() == "q":
        return None
    if not game_id.isdigit():
        print("Game id must be numeric.")
        return None

    key = str(int(game_id))
    existing = games.get(key)
    existing_name = ""
    if isinstance(existing, dict):
        existing_name = str(existing.get("name", "") or "").strip()

    if existing_name:
        print(f"Editing {key}: {existing_name}")
    else:
        print(f"Editing {key}: (new or unnamed entry)")
    return key, existing_name


def list_games(data: Dict[str, Any], limit: int = 25) -> None:
    games = data.get("games", {})
    rows = []
    for key, value in games.items():
        if not isinstance(value, dict):
            continue
        name = str(value.get("name", "") or "").strip()
        rows.append((key, name, summarize_entry(normalize_entry(value, fallback_name=name))))

    rows.sort(key=lambda row: (row[1].lower(), int(row[0]) if row[0].isdigit() else row[0]))
    if not rows:
        print("No entries in sidecar.")
        return

    print(f"\nShowing first {min(limit, len(rows))} of {len(rows)} entries:")
    for key, name, summary in rows[:limit]:
        print(f"  {key:>6}  {name}  ({summary})")


def edit_game_entry(data: Dict[str, Any], key: str, fallback_name: str) -> bool:
    games = data.setdefault("games", {})
    entry = normalize_entry(games.get(key), fallback_name=fallback_name)

    print("\nPress Enter to keep current value. Type '-' to clear a text field.")
    entry["name"] = prompt_text("name", entry.get("name", ""), allow_clear=False).strip()
    entry["short_description"] = prompt_text("short_description", entry.get("short_description", ""), allow_clear=True).strip()

    entry["rulebooks"] = edit_documents("Rulebooks", list(entry.get("rulebooks", [])))
    entry["supplemental_files"] = edit_documents("Supplemental files", list(entry.get("supplemental_files", [])))

    for platform in ("android", "ios", "pc"):
        edit_platform(entry, platform)

    games[key] = normalize_entry(entry, fallback_name=entry.get("name", ""))
    print(f"Saved in memory: id={key} ({summarize_entry(games[key])})")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactive sidecar editor for game metadata overrides.")
    parser.add_argument(
        "--sidecar",
        default=DEFAULT_SIDECAR,
        help=f"Path to sidecar JSON file (default: {DEFAULT_SIDECAR})",
    )
    args = parser.parse_args()

    sidecar_path = Path(args.sidecar)
    data = load_sidecar(sidecar_path)
    dirty = False

    print(f"Loaded sidecar: {sidecar_path}")
    print("Commands: edit, list, save, quit")

    while True:
        cmd = input("\nCommand [edit/list/save/quit]: ").strip().lower()
        if cmd in ("quit", "q", "exit"):
            if dirty:
                confirm = input("Unsaved changes. Save before quitting? [y/N]: ").strip().lower()
                if confirm in ("y", "yes"):
                    save_sidecar(sidecar_path, data)
                    print(f"Wrote {sidecar_path}")
            return 0

        if cmd in ("list", "ls"):
            list_games(data)
            continue

        if cmd in ("save", "s"):
            save_sidecar(sidecar_path, data)
            print(f"Wrote {sidecar_path}")
            dirty = False
            continue

        if cmd in ("edit", "e"):
            selected = choose_game_id(data)
            if not selected:
                continue
            key, fallback_name = selected
            if edit_game_entry(data, key, fallback_name):
                dirty = True
            continue

        print("Unknown command. Use: edit, list, save, quit")


if __name__ == "__main__":
    raise SystemExit(main())
