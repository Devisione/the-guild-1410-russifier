#!/usr/bin/env python3
"""Rebuild Russian localization mod with context-aware translation rules."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from pylocres import LocresFile
from pylocres.locres import Entry, Namespace

from locmod_lib import (
    LocEntry,
    find_cached_translation,
    remember_cache_translation,
    resolve,
    should_keep_english,
    translate_entries,
)
from paths import ProjectPaths, resolve_paths

PATHS: ProjectPaths = resolve_paths()
GAME_PAKS = PATHS.game_paks
WORK = PATHS.work
SOURCE = PATHS.source
DIST_PAKS = PATHS.dist_paks
REPAK = PATHS.repak
CACHE = PATHS.cache
MOD_ROOT = PATHS.mod_root

MOD_FILES = (
    "RussianLocalization_P.pak",
    "RussianLocalization_P.ucas",
    "RussianLocalization_P.utoc",
)

LOC_FILES = [
    "Game/en/Game.locres",
    "Game_VO/en/Game_VO.locres",
    "Uncategorized Texts/en/Uncategorized Texts.locres",
]

MANUAL = PATHS.manual

# Host/Start live in ST_General -> Game.locres; Join Game was only gathered into
# Uncategorized Texts and never added to ST_General during development.
EXTRA_GAME_LOC_ENTRIES = (
    LocEntry("General", "General_Button_JoinGame", "Join Game"),
)

MOD_SUPPORT_FILES = (
    "Europa1410/Content/Localization/Game/Game.locmeta",
    "Europa1410/Content/Localization/Uncategorized Texts/Uncategorized Texts.locmeta",
    "Europa1410/Config/DefaultGame.ini",
)

STRING_TABLES_DIR = PATHS.string_tables_dir
UCAS_PATH = PATHS.ucas_path
UCAS_HASH_CACHE = PATHS.ucas_hash_cache
HASH_TEXT_RE = re.compile(rb"([0-9A-F]{32})([\x20-\x7e]{4,160})")

ST_NAMESPACE_BY_FILE: dict[str, str] = {
    "ST_Actions.csv": "Actions",
    "ST_BuildingImprovements.csv": "Building Improvements",
    "ST_BuildingNamePools.csv": "Building Name Pools",
    "ST_BuildingRooms.csv": "Building Rooms",
    "ST_Buildings.csv": "Buildings",
    "ST_Carts.csv": "Carts",
    "ST_Character.csv": "Character",
    "ST_CharacterNamePools.csv": "Character Name Pools",
    "ST_Cities.csv": "Cities",
    "ST_Combat.csv": "Combat",
    "ST_Controls.csv": "Controls",
    "ST_Effects.csv": "Effects",
    "ST_Events.csv": "Events",
    "ST_General.csv": "General",
    "ST_HistoricalEvents.csv": "Historical Events",
    "ST_Items.csv": "Items",
    "ST_Notifications.csv": "Notifications",
    "ST_Politics.csv": "Politics",
    "ST_Professions.csv": "Professions",
    "ST_Seasons.csv": "Seasons",
    "ST_Settings.csv": "Settings",
    "ST_StatusEffects.csv": "Status Effects",
    "ST_Test.csv": "Test",
    "ST_Titles.csv": "Titles",
    "ST_Traits.csv": "Traits",
    "ST_Workers.csv": "Workers",
}

MISSING_ST_ENTRIES: list[LocEntry] = []
ALL_ST_ENTRIES: list[LocEntry] = []
UCAS_HASH_ENTRIES: list[LocEntry] = []

KNOWN_HASH_ENTRIES: tuple[LocEntry, ...] = (
    LocEntry("", "8DD821824C489058406FBCA6B68C5CC", "Left-click to grab"),
)


def collect_entries(rel: str) -> tuple[LocresFile, list[LocEntry], list]:
    src = SOURCE / rel
    if not src.exists():
        raise FileNotFoundError(f"Missing source file: {src}")

    loc = LocresFile()
    loc.read(str(src))

    entries: list[LocEntry] = []
    handles = []
    for namespace in loc:
        ns_name = namespace.name or ""
        for entry in namespace:
            entries.append(LocEntry(ns_name, entry.key, entry.translation))
            handles.append(entry)
    return loc, entries, handles


def load_cache() -> dict[str, str]:
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict[str, str]) -> None:
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def load_manual_map() -> dict[str, str]:
    if not MANUAL.exists():
        return {}
    rows = json.loads(MANUAL.read_text(encoding="utf-8"))
    return {
        row["english"]: row["russian"]
        for row in rows
        if row.get("english") and row.get("russian", "").strip()
    }


def apply_manual_overrides(cache: dict[str, str], entries: list[LocEntry]) -> int:
    manual = load_manual_map()
    applied = 0
    for entry in entries:
        russian = manual.get(entry.english)
        if not russian or not russian.strip():
            continue
        if russian.strip() == entry.english.strip():
            continue
        remember_cache_translation(cache, entry, russian)
        applied += 1
    return applied


def export_manual(entries: list[LocEntry], cache: dict[str, str]) -> None:
    existing = load_manual_map()
    if MANUAL.exists():
        for row in json.loads(MANUAL.read_text(encoding="utf-8")):
            english = row.get("english", "")
            if english and english not in existing:
                existing[english] = row.get("russian", "")

    rows = []
    seen: set[str] = set()
    for entry in entries:
        if entry.english in seen:
            continue
        seen.add(entry.english)
        english = entry.english
        russian = existing.get(english)
        if (
            not russian
            or not russian.strip()
            or russian.strip() == english.strip()
        ):
            russian = resolve(entry, cache)
        rows.append({"english": english, "russian": russian})

    rows.sort(key=lambda row: row["english"].lower())
    MANUAL.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_string_tables() -> None:
    if (STRING_TABLES_DIR / "ST_General.csv").exists():
        return

    tmp = WORK / "tmp_string_tables"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    subprocess.run(
        [
            str(REPAK),
            "unpack",
            str(GAME_PAKS / "Europa1410-Windows.pak"),
            "--include",
            "Europa1410/Content/StringTables/",
            "--output",
            str(tmp),
        ],
        check=True,
    )
    src = tmp / "Europa1410/Content/StringTables"
    STRING_TABLES_DIR.parent.mkdir(parents=True, exist_ok=True)
    if STRING_TABLES_DIR.exists():
        shutil.rmtree(STRING_TABLES_DIR)
    shutil.copytree(src, STRING_TABLES_DIR)
    shutil.rmtree(tmp)
    print(f"Extracted string tables to {STRING_TABLES_DIR}", flush=True)


def parse_st_csv(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        if not line.startswith('"'):
            continue
        parts = line.split('","')
        if len(parts) < 2:
            continue
        key = parts[0].strip('"')
        english = parts[1].replace("\\n", "\n").replace('\\"', '"')
        rows.append((key, english))
    return rows


def collect_all_st_entries() -> list[LocEntry]:
    entries: list[LocEntry] = []
    for csv_name, namespace in ST_NAMESPACE_BY_FILE.items():
        csv_path = STRING_TABLES_DIR / csv_name
        if not csv_path.exists():
            continue
        for key, english in parse_st_csv(csv_path):
            entries.append(LocEntry(namespace, key, english))
    return entries


def collect_missing_st_entries(loc: LocresFile) -> list[LocEntry]:
    existing = {(namespace.name, entry.key) for namespace in loc for entry in namespace}
    return [entry for entry in ALL_ST_ENTRIES if (entry.namespace, entry.key) not in existing]


def merge_authoritative_entries(*groups: list[LocEntry]) -> list[LocEntry]:
    merged: dict[tuple[str, str], LocEntry] = {}
    for group in groups:
        for entry in group:
            merged[(entry.namespace, entry.key)] = entry
    return list(merged.values())


def sync_string_table_entries(
    loc: LocresFile,
    cache: dict[str, str],
    entries: list[LocEntry],
) -> int:
    added = 0
    updated = 0
    for entry in entries:
        namespace = loc[entry.namespace]
        if namespace is None:
            namespace = Namespace(entry.namespace)
            loc.add(namespace)

        russian = resolve(entry, cache)
        remember_cache_translation(cache, entry, russian)

        if entry.key in namespace:
            existing = namespace[entry.key]
            if existing.translation == russian:
                continue
            namespace.remove(entry.key)
            updated += 1
        else:
            added += 1
        namespace.add(Entry(entry.key, russian, entry.english, is_hash=False))

    if added or updated:
        print(
            f"Synced string-table keys in Game.locres: {added} added, {updated} updated",
            flush=True,
        )
    return added + updated


UI_GARBAGE_PARTS = (
    "Float",
    "Int32",
    "Bool",
    "Enum:",
    "AudioB",
    "Blueprint",
    "Delegate",
    "Component",
    "K2Node",
    "Widget",
    "Canvas",
    "Default__",
    "/Game/",
    "UE.",
)


def is_probable_ui_string(text: str) -> bool:
    if not text or len(text) < 3 or len(text) > 120:
        return False
    if not text[0].isalpha() or not text[0].isupper():
        return False
    letters = [char for char in text if char.isalpha()]
    if len(letters) < 2:
        return False
    if any(ord(char) < 32 for char in text):
        return False
    if text.count("{") > 8 or text.count("<") > 8:
        return False
    if sum(1 for char in text if char.isdigit()) > max(4, len(text) // 3):
        return False
    if "_" in text and " " not in text:
        return False
    if text.count("/") > 2 or "\\" in text:
        return False
    if any(part in text for part in UI_GARBAGE_PARTS):
        return False
    if len(text) <= 16 and text.isupper() and " " not in text:
        return False
    words = re.findall(r"[A-Za-z']+", text)
    if len(words) == 1 and len(words[0]) > 20:
        return False
    return True


def harvest_ucas_hash_entries(existing_keys: set[str], refresh: bool = False) -> list[LocEntry]:
    if UCAS_HASH_CACHE.exists() and not refresh:
        rows = json.loads(UCAS_HASH_CACHE.read_text(encoding="utf-8"))
        return [
            LocEntry("", row["key"], row["english"])
            for row in rows
            if row["key"] not in existing_keys
        ]

    if not UCAS_PATH.exists():
        print(f"UCAS not found, skipping hash harvest: {UCAS_PATH}", flush=True)
        return []

    print(f"Scanning {UCAS_PATH.name} for uncategorized hash strings...", flush=True)
    found: dict[str, str] = {
        entry.key.upper(): entry.english for entry in KNOWN_HASH_ENTRIES
    }
    chunk_size = 64 * 1024 * 1024
    overlap = 256
    with UCAS_PATH.open("rb") as handle:
        carry = b""
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            data = carry + chunk
            for match in HASH_TEXT_RE.finditer(data):
                key = match.group(1).decode("ascii")
                text = match.group(2).decode("ascii", errors="ignore").strip()
                if not is_probable_ui_string(text):
                    continue
                previous = found.get(key)
                if previous is None or len(text) < len(previous):
                    found[key] = text
            carry = data[-overlap:]

    rows = [{"key": key, "english": english} for key, english in sorted(found.items())]
    UCAS_HASH_CACHE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Harvested {len(rows)} hash strings from UCAS", flush=True)
    return [
        LocEntry("", row["key"], row["english"])
        for row in rows
        if row["key"] not in existing_keys
    ]


def inject_ucas_hash_entries(
    loc: LocresFile,
    cache: dict[str, str],
    entries: list[LocEntry],
) -> int:
    namespace = loc[""]
    if namespace is None:
        namespace = Namespace("")
        loc.add(namespace)

    added = 0
    updated = 0
    for entry in entries:
        russian = resolve(entry, cache)
        remember_cache_translation(cache, entry, russian)
        if entry.key in namespace:
            existing = namespace[entry.key]
            if existing.translation == russian:
                continue
            namespace.remove(entry.key)
            updated += 1
        else:
            added += 1
        namespace.add(Entry(entry.key, russian, entry.english, is_hash=False))
    if added or updated:
        print(
            f"Synced uncategorized hash keys: {added} added, {updated} updated",
            flush=True,
        )
    return added + updated


def inject_extra_game_entries(loc: LocresFile, cache: dict[str, str]) -> int:
    added = 0
    general = loc["General"]
    if general is None:
        general = Namespace("General")
        loc.add(general)

    for entry in EXTRA_GAME_LOC_ENTRIES:
        if entry.key in general:
            continue
        russian = resolve(entry, cache)
        general.add(Entry(entry.key, russian, entry.english, is_hash=False))
        added += 1
        print(f"Added missing Game key {entry.key} -> {russian!r}", flush=True)
    return added


def drop_english_cache_entries(cache: dict[str, str], entries: list[LocEntry]) -> int:
    dropped = 0
    for entry in entries:
        if should_keep_english(entry):
            continue
        cached = find_cached_translation(entry, cache)
        if cached is None:
            continue
        if cached.strip() != entry.english.strip():
            continue
        keys_to_drop = [entry.cache_id]
        prefix = f"{entry.stable_cache_id}\x1f"
        keys_to_drop.extend(key for key in cache if key.startswith(prefix))
        for key in keys_to_drop:
            if key in cache:
                del cache[key]
                dropped += 1
    return dropped


def build_locres(rel: str, cache: dict[str, str]) -> int:
    loc, entries, handles = collect_entries(rel)
    print(f"Building {rel} ({len(entries)} entries)...", flush=True)

    for handle, entry in zip(handles, entries, strict=True):
        handle.translation = resolve(entry, cache)

    added = 0
    if rel == "Game/en/Game.locres":
        added += inject_extra_game_entries(loc, cache)
        added += sync_string_table_entries(loc, cache, ALL_ST_ENTRIES)
    elif rel == "Uncategorized Texts/en/Uncategorized Texts.locres":
        added += inject_ucas_hash_entries(loc, cache, UCAS_HASH_ENTRIES)

    out = MOD_ROOT / "Europa1410/Content/Localization" / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    loc.write(str(out))
    return len(entries) + added


def stage_mod_support_files() -> None:
    tmp = WORK / "tmp_mod_support"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    includes = list(MOD_SUPPORT_FILES)
    subprocess.run(
        [
            str(REPAK),
            "unpack",
            str(GAME_PAKS / "Europa1410-Windows.pak"),
            *[arg for path in includes for arg in ("--include", path)],
            "--output",
            str(tmp),
        ],
        check=True,
    )

    for rel_path in (
        "Europa1410/Content/Localization/Game/Game.locmeta",
        "Europa1410/Content/Localization/Uncategorized Texts/Uncategorized Texts.locmeta",
    ):
        src = tmp / rel_path
        if not src.exists():
            raise FileNotFoundError(f"Missing {src}")
        dst = MOD_ROOT / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    ini_src = tmp / "Europa1410/Config/DefaultGame.ini"
    if not ini_src.exists():
        raise FileNotFoundError(f"Missing {ini_src}")
    ini_text = ini_src.read_text(encoding="utf-8-sig")
    extra_paths = (
        "+LocalizationPaths=%GAMEDIR%Content/Localization/Game",
        "+LocalizationPaths=%GAMEDIR%Content/Localization/Uncategorized Texts",
    )
    if not all(line in ini_text for line in extra_paths):
        marker = "[Internationalization]"
        if marker not in ini_text:
            raise RuntimeError("DefaultGame.ini is missing [Internationalization] section")
        insertion = marker + "\n" + "\n".join(extra_paths)
        ini_text = ini_text.replace(marker, insertion, 1)

    ini_dst = MOD_ROOT / "Europa1410/Config/DefaultGame.ini"
    ini_dst.parent.mkdir(parents=True, exist_ok=True)
    ini_dst.write_text(ini_text, encoding="utf-8")
    shutil.rmtree(tmp)
    print("Staged locmeta and DefaultGame.ini overrides", flush=True)


def pack_mod() -> Path:
    DIST_PAKS.mkdir(parents=True, exist_ok=True)
    mod_pak = DIST_PAKS / MOD_FILES[0]
    if mod_pak.exists():
        mod_pak.unlink()

    cmd = [
        str(REPAK),
        "pack",
        str(MOD_ROOT),
        str(mod_pak),
        "--mount-point",
        "../../../",
        "-v",
    ]
    print("Packing mod...", flush=True)
    subprocess.run(cmd, check=True)
    return mod_pak


def setup_iostore() -> None:
    """UE5 IoStore mods need companion ucas/utoc copied from global.*."""
    DIST_PAKS.mkdir(parents=True, exist_ok=True)
    for suffix in ("ucas", "utoc"):
        src = GAME_PAKS / f"global.{suffix}"
        dst = DIST_PAKS / f"RussianLocalization_P.{suffix}"
        if not src.exists():
            raise FileNotFoundError(f"Missing {src}")
        shutil.copy2(src, dst)
        print(f"Added {dst.name}", flush=True)


def publish_dist(install: bool) -> None:
    print(f"Dist ready: {DIST_PAKS}", flush=True)
    for name in MOD_FILES:
        path = DIST_PAKS / name
        if not path.exists():
            raise FileNotFoundError(f"Missing dist file: {path}")
        print(f"  {name} ({path.stat().st_size} bytes)", flush=True)

    if not install:
        return

    GAME_PAKS.mkdir(parents=True, exist_ok=True)
    for name in MOD_FILES:
        shutil.copy2(DIST_PAKS / name, GAME_PAKS / name)
        print(f"Installed {name}", flush=True)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Retranslate all entries via API")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--pause", type=float, default=0.4)
    parser.add_argument("--skip-translate", action="store_true", help="Only rebuild pak from cache")
    parser.add_argument(
        "--refresh-ucas-hash",
        action="store_true",
        help="Rescan Europa1410-Windows.ucas for uncategorized hash strings",
    )
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="Build dist only, do not copy files into the game folder",
    )
    args = parser.parse_args()

    WORK.mkdir(parents=True, exist_ok=True)
    if MOD_ROOT.exists():
        shutil.rmtree(MOD_ROOT)

    ensure_string_tables()

    global ALL_ST_ENTRIES, MISSING_ST_ENTRIES, UCAS_HASH_ENTRIES
    ALL_ST_ENTRIES = collect_all_st_entries()

    all_entries: list[LocEntry] = []
    for rel in LOC_FILES:
        _loc, entries, _handles = collect_entries(rel)
        all_entries.extend(entries)

    game_loc, _, _ = collect_entries("Game/en/Game.locres")
    MISSING_ST_ENTRIES = collect_missing_st_entries(game_loc)
    print(f"Missing string-table keys in Game.locres: {len(MISSING_ST_ENTRIES)}", flush=True)

    uncat_loc, _, _ = collect_entries("Uncategorized Texts/en/Uncategorized Texts.locres")
    existing_hash_keys = {
        entry.key
        for namespace in uncat_loc
        for entry in namespace
        if len(entry.key) == 32 and all(ch in "0123456789ABCDEFabcdef" for ch in entry.key)
    }
    UCAS_HASH_ENTRIES = harvest_ucas_hash_entries(
        existing_hash_keys,
        refresh=args.refresh_ucas_hash,
    )
    UCAS_HASH_ENTRIES = merge_authoritative_entries(
        list(KNOWN_HASH_ENTRIES),
        UCAS_HASH_ENTRIES,
    )
    print(f"Uncategorized hash entries to sync: {len(UCAS_HASH_ENTRIES)}", flush=True)

    all_entries = merge_authoritative_entries(
        all_entries,
        ALL_ST_ENTRIES,
        list(EXTRA_GAME_LOC_ENTRIES),
        UCAS_HASH_ENTRIES,
    )

    cache = {} if args.force else load_cache()
    locme = sum(1 for entry in all_entries if entry.english.startswith("(LocMe)"))
    print(f"Collected {len(all_entries)} entries, LocMe kept in English: {locme}", flush=True)

    if not args.skip_translate:
        dropped = drop_english_cache_entries(cache, all_entries)
        if dropped:
            print(f"Dropped {dropped} untranslated cache entries for re-translation", flush=True)
        cache = translate_entries(
            all_entries,
            cache,
            batch_size=args.batch_size,
            pause=args.pause,
            force=args.force,
        )
        save_cache(cache)

    manual_applied = apply_manual_overrides(cache, all_entries)
    print(f"Manual overrides applied: {manual_applied}", flush=True)
    save_cache(cache)

    export_manual(all_entries, cache)

    total = 0
    for rel in LOC_FILES:
        total += build_locres(rel, cache)

    stage_mod_support_files()
    pack_mod()
    setup_iostore()
    install = PATHS.install_mod_after_build and not args.no_install
    publish_dist(install=install)
    print(f"Done. {total} entries.", flush=True)


if __name__ == "__main__":
    main()
