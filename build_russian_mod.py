#!/usr/bin/env python3
"""Rebuild Russian localization mod with context-aware translation rules."""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import struct
import subprocess
import time
import zipfile
from pathlib import Path

from pylocres import LocmetaFile, LocresFile
from pylocres.locres import Entry, Namespace

from locmod_lib import (
    LocEntry,
    expand_russian_plurals,
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

# Bump this before publishing a GitHub release.
MOD_VERSION = "1.0.5"
VERSION_FILE = "RussianLocalization.version"
LAUNCHER_FILES = (
    "CheckTranslationUpdate.cmd",
    "check_translation_update.ps1",
)

LOC_FILES = [
    "Game/en/Game.locres",
    "Game_VO/en/Game_VO.locres",
    "Uncategorized Texts/en/Uncategorized Texts.locres",
]

# Official game locmeta already lists ru, but the in-game picker only shows
# cultures that were cooked (en, de, ash, pt-BR). The visible Русский item is
# the German slot with Russian locres, a Deutsch→Русский ICU label, and German
# plural rules retargeted to the Russian ICU set so 1/2/5 endings work.
OUTPUT_CULTURES = ("ru", "de")
# Game_VO follows voiced DialogueWave assets. Overlaying it also rewrites the
# audio-language locres; leave the vanilla files so English/Deutsch VO stay put.
SKIP_LOCRES_OVERLAY_PREFIXES = ("Game_VO/",)

ICU_LANG_DE = "Engine/Content/Internationalization/icudt64l/lang/de.res"
ICU_PLURALS = "Engine/Content/Internationalization/icudt64l/plurals.res"
ICU_DEUTSCH_LABEL = "Deutsch".encode("utf-16le")
ICU_RUSSIAN_LABEL = "Русский".encode("utf-16le")

MANUAL = PATHS.manual

# Host/Start live in ST_General -> Game.locres; Join Game was only gathered into
# Uncategorized Texts and never added to ST_General during development.
# "Current Level" is not in cooked locres/ST (likely a property display name);
# inject common lookup keys so the German slot can still replace it.
EXTRA_GAME_LOC_ENTRIES = (
    LocEntry("General", "General_Button_JoinGame", "Join Game"),
    LocEntry("General", "CurrentLevel_DisplayName", "Current Level"),
    LocEntry("General", "CurrentLevel_Label", "Current Level"),
    LocEntry("General", "Current_Level", "Current Level"),
    LocEntry("Buildings", "CurrentLevel_DisplayName", "Current Level"),
    LocEntry("Buildings", "CurrentLevel_Label", "Current Level"),
    LocEntry("Actions", "CurrentLevel_DisplayName", "Current Level"),
    LocEntry("Settings", "CurrentLevel_DisplayName", "Current Level"),
    LocEntry("UObjectDisplayNames", "CurrentLevel", "Current Level"),
)
EXTRA_UNCATEGORIZED_LOC_ENTRIES = (
    LocEntry("", "Current Level", "Current Level"),
    LocEntry("", "CurrentLevel", "Current Level"),
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
    "ST_AmbientBackstories.csv": "AmbientBackstories",
    "ST_Backstories.csv": "Backstories",
    "ST_BuildingImprovements.csv": "Building Improvements",
    "ST_BuildingNamePools.csv": "Building Name Pools",
    "ST_BuildingRooms.csv": "Building Rooms",
    "ST_Buildings.csv": "Buildings",
    "ST_Carts.csv": "Carts",
    "ST_Challenges.csv": "Challenges",
    "ST_Character.csv": "Character",
    "ST_CharacterNamePools.csv": "Character Name Pools",
    "ST_Cities.csv": "Cities",
    "ST_Combat.csv": "Combat",
    "ST_Controls.csv": "Controls",
    "ST_Effects.csv": "Effects",
    "ST_Events.csv": "Events",
    "ST_General.csv": "General",
    "ST_HistoricalEvents.csv": "HistoricalEvents",
    "ST_Items.csv": "Items",
    "ST_LoadingTips.csv": "LoadingTips",
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
        else:
            russian = expand_russian_plurals(russian)
        rows.append({"english": english, "russian": russian})

    rows.sort(key=lambda row: row["english"].lower())
    MANUAL.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_source_assets() -> None:
    locres_ok = (SOURCE / "Game/en/Game.locres").exists()
    tables_ok = (STRING_TABLES_DIR / "ST_General.csv").exists()
    if locres_ok and tables_ok:
        return

    tmp = WORK / "tmp_source_extract"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    subprocess.run(
        [
            str(REPAK),
            "unpack",
            str(GAME_PAKS / "Europa1410-Windows.pak"),
            "--include",
            "Europa1410/Content/Localization/",
            "--include",
            "Europa1410/Content/StringTables/",
            "--output",
            str(tmp),
        ],
        check=True,
    )

    extracted_root = tmp / "Europa1410/Content"
    if not locres_ok:
        src = extracted_root / "Localization"
        SOURCE.parent.mkdir(parents=True, exist_ok=True)
        if SOURCE.exists():
            shutil.rmtree(SOURCE)
        shutil.copytree(src, SOURCE)
        print(f"Extracted localization to {SOURCE}", flush=True)
    if not tables_ok:
        src = extracted_root / "StringTables"
        STRING_TABLES_DIR.parent.mkdir(parents=True, exist_ok=True)
        if STRING_TABLES_DIR.exists():
            shutil.rmtree(STRING_TABLES_DIR)
        shutil.copytree(src, STRING_TABLES_DIR)
        print(f"Extracted string tables to {STRING_TABLES_DIR}", flush=True)
    shutil.rmtree(tmp)


def ensure_string_tables() -> None:
    ensure_source_assets()


def parse_st_csv(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        for row in reader:
            if len(row) < 2 or not row[0]:
                continue
            english = (
                row[1]
                .replace("\\r\\n", "\n")
                .replace("\\n", "\n")
                .replace('\\"', '"')
            )
            rows.append((row[0], english))
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
    *,
    localized: bool = True,
) -> int:
    added = 0
    updated = 0
    for entry in entries:
        namespace = loc[entry.namespace]
        if namespace is None:
            namespace = Namespace(entry.namespace)
            loc.add(namespace)

        text = resolve(entry, cache) if localized else entry.english
        if localized:
            remember_cache_translation(cache, entry, text)

        if entry.key in namespace:
            existing = namespace[entry.key]
            if existing.translation == text:
                continue
            namespace.remove(entry.key)
            updated += 1
        else:
            added += 1
        namespace.add(Entry(entry.key, text, entry.english, is_hash=False))

    if added or updated:
        kind = "ru" if localized else "en"
        print(
            f"Synced string-table keys in Game.locres ({kind}): {added} added, {updated} updated",
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
    *,
    localized: bool = True,
) -> int:
    namespace = loc[""]
    if namespace is None:
        namespace = Namespace("")
        loc.add(namespace)

    added = 0
    updated = 0
    for entry in entries:
        text = resolve(entry, cache) if localized else entry.english
        if localized:
            remember_cache_translation(cache, entry, text)
        if entry.key in namespace:
            existing = namespace[entry.key]
            if existing.translation == text:
                continue
            namespace.remove(entry.key)
            updated += 1
        else:
            added += 1
        namespace.add(Entry(entry.key, text, entry.english, is_hash=False))
    if added or updated:
        kind = "ru" if localized else "en"
        print(
            f"Synced uncategorized hash keys ({kind}): {added} added, {updated} updated",
            flush=True,
        )
    return added + updated


def inject_extra_loc_entries(
    loc: LocresFile,
    cache: dict[str, str],
    entries: tuple[LocEntry, ...] | list[LocEntry],
    *,
    localized: bool = True,
) -> int:
    added = 0
    for entry in entries:
        namespace = loc[entry.namespace]
        if namespace is None:
            namespace = Namespace(entry.namespace)
            loc.add(namespace)
        if entry.key in namespace:
            continue
        text = resolve(entry, cache) if localized else entry.english
        if localized:
            remember_cache_translation(cache, entry, text)
        namespace.add(Entry(entry.key, text, entry.english, is_hash=False))
        added += 1
        print(f"Added missing {entry.namespace}/{entry.key} -> {text!r}", flush=True)
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


def culture_output_rel(rel: str, culture: str) -> str:
    parts = Path(rel).parts
    if len(parts) < 3:
        raise ValueError(f"Unexpected locres path: {rel}")
    return str(Path(parts[0]) / culture / parts[-1])


def write_locres(loc: LocresFile, rel: str) -> None:
    out = MOD_ROOT / "Europa1410/Content/Localization" / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    loc.write(str(out))


def build_locres(rel: str, cache: dict[str, str]) -> int:
    if rel.startswith(SKIP_LOCRES_OVERLAY_PREFIXES):
        _, entries, _ = collect_entries(rel)
        print(f"Skipping {rel} overlay (keep vanilla audio-language locres)", flush=True)
        return len(entries)

    def populate(*, localized: bool) -> tuple[LocresFile, int]:
        loc, entries, handles = collect_entries(rel)
        if localized:
            print(f"Building {rel} ({len(entries)} entries)...", flush=True)
            for handle, entry in zip(handles, entries, strict=True):
                handle.translation = resolve(entry, cache)

        added = 0
        if rel == "Game/en/Game.locres":
            added += inject_extra_loc_entries(
                loc, cache, EXTRA_GAME_LOC_ENTRIES, localized=localized
            )
            added += sync_string_table_entries(
                loc, cache, ALL_ST_ENTRIES, localized=localized
            )
        elif rel == "Uncategorized Texts/en/Uncategorized Texts.locres":
            added += inject_extra_loc_entries(
                loc, cache, EXTRA_UNCATEGORIZED_LOC_ENTRIES, localized=localized
            )
            added += inject_ucas_hash_entries(
                loc, cache, UCAS_HASH_ENTRIES, localized=localized
            )
        return loc, len(entries) + added

    ru_loc, total = populate(localized=True)
    for culture in OUTPUT_CULTURES:
        write_locres(ru_loc, culture_output_rel(rel, culture))
        print(f"  wrote {culture_output_rel(rel, culture)}", flush=True)
    # Do not overlay en locres: Text Language=English must stay the base game.
    return total


def patch_locmeta(path: Path, extra_cultures: tuple[str, ...]) -> None:
    meta = LocmetaFile()
    meta.read(str(path))
    cultures = list(meta.compiled_cultures or [])
    reader = getattr(meta, "reader", None)
    if reader is not None:
        reader.close()
        meta.reader = None

    changed = False
    for culture in extra_cultures:
        if culture not in cultures:
            cultures.append(culture)
            changed = True
    if not changed:
        return

    meta.compiled_cultures = cultures
    meta.write(str(path))
    writer = getattr(meta, "writer", None)
    if writer is not None:
        writer.close()
        meta.writer = None
    print(f"Added cultures {list(extra_cultures)} to {path.name}", flush=True)


def stage_mod_support_files() -> None:
    tmp = WORK / "tmp_mod_support"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    includes = [*MOD_SUPPORT_FILES, ICU_LANG_DE, ICU_PLURALS]
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

    locmeta_files = (
        "Europa1410/Content/Localization/Game/Game.locmeta",
        "Europa1410/Content/Localization/Uncategorized Texts/Uncategorized Texts.locmeta",
    )
    for rel_path in locmeta_files:
        src = tmp / rel_path
        if not src.exists():
            raise FileNotFoundError(f"Missing {src}")
        dst = MOD_ROOT / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        patch_locmeta(dst, OUTPUT_CULTURES)

    ini_src = tmp / "Europa1410/Config/DefaultGame.ini"
    if not ini_src.exists():
        raise FileNotFoundError(f"Missing {ini_src}")
    ini_text = ini_src.read_text(encoding="utf-8-sig")
    extra_paths = (
        "+LocalizationPaths=%GAMEDIR%Content/Localization/Game",
        "+LocalizationPaths=%GAMEDIR%Content/Localization/Uncategorized Texts",
        "+CulturesToStage=ru",
    )
    marker = "[Internationalization]"
    if marker not in ini_text:
        raise RuntimeError("DefaultGame.ini is missing [Internationalization] section")
    missing = [line for line in extra_paths if line not in ini_text]
    if missing:
        insertion = marker + "\n" + "\n".join(missing)
        ini_text = ini_text.replace(marker, insertion, 1)
    ini_text = pin_audio_culture_english(ini_text)

    ini_dst = MOD_ROOT / "Europa1410/Config/DefaultGame.ini"
    ini_dst.parent.mkdir(parents=True, exist_ok=True)
    ini_dst.write_text(ini_text, encoding="utf-8")
    patch_icu_german_menu_label(tmp / ICU_LANG_DE, MOD_ROOT / ICU_LANG_DE)
    patch_icu_german_plurals_to_russian(tmp / ICU_PLURALS, MOD_ROOT / ICU_PLURALS)
    shutil.rmtree(tmp)
    print("Staged locmeta and DefaultGame.ini overrides", flush=True)


def pin_audio_culture_english(ini_text: str) -> str:
    """Keep voiced assets on English even when the text slot is Deutsch/Русский."""
    section = "[Internationalization.AssetGroupCultures]"
    audio_line = "Audio=en"
    marker = "[Internationalization]"
    if section in ini_text:
        start = ini_text.index(section) + len(section)
        rest = ini_text[start:]
        next_section = rest.find("\n[")
        body = rest if next_section < 0 else rest[:next_section]
        tail = "" if next_section < 0 else rest[next_section:]
        lines = body.splitlines()
        rewritten: list[str] = []
        found = False
        for line in lines:
            if line.startswith("Audio="):
                rewritten.append(audio_line)
                found = True
            else:
                rewritten.append(line)
        if not found:
            if not rewritten:
                rewritten = ["", audio_line]
            elif rewritten[0] == "":
                rewritten.insert(1, audio_line)
            else:
                rewritten.insert(0, "")
                rewritten.insert(1, audio_line)
        return ini_text[:start] + "\n".join(rewritten) + tail

    insertion = f"{section}\n{audio_line}\n\n{marker}"
    if marker not in ini_text:
        return ini_text + "\n" + insertion + "\n"
    return ini_text.replace(marker, insertion, 1)


def patch_icu_german_menu_label(src: Path, dst: Path) -> None:
    if not src.exists():
        raise FileNotFoundError(f"Missing {src}")
    if len(ICU_DEUTSCH_LABEL) != len(ICU_RUSSIAN_LABEL):
        raise RuntimeError("ICU Deutsch/Русский labels must be the same UTF-16 length")
    data = src.read_bytes()
    hits = data.count(ICU_DEUTSCH_LABEL)
    if hits != 1:
        raise RuntimeError(f"Expected 1 Deutsch label in {src.name}, found {hits}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data.replace(ICU_DEUTSCH_LABEL, ICU_RUSSIAN_LABEL, 1))
    print("Relabeled ICU menu Deutsch -> Русский", flush=True)


def _icu_cstring(data: memoryview, offset: int) -> str:
    end = bytes(data[offset:]).find(b"\x00")
    if end < 0:
        raise RuntimeError(f"Unterminated ICU key at {offset}")
    return bytes(data[offset : offset + end]).decode("ascii")


def _icu_table32_items(data: memoryview, res: int) -> dict[str, int]:
    base = (res & 0x0FFFFFFF) * 4
    length = struct.unpack_from("<H", data, base)[0]
    key_off = base + 2
    keys = [struct.unpack_from("<H", data, key_off + 2 * i)[0] for i in range(length)]
    items_off = key_off + 2 * length + 2 * ((~length) & 1)
    return {
        _icu_cstring(data, key): struct.unpack_from("<I", data, items_off + 4 * i)[0]
        for i, key in enumerate(keys)
    }


def patch_icu_german_plurals_to_russian(src: Path, dst: Path) -> None:
    """Point the German locale at the Russian cardinal plural rule set."""
    if not src.exists():
        raise FileNotFoundError(f"Missing {src}")
    raw = bytearray(src.read_bytes())
    header_size = struct.unpack_from("<H", raw, 0)[0]
    data = memoryview(raw)[header_size:]
    root = struct.unpack_from("<I", data, 0)[0]
    keys_top = struct.unpack_from("<I", data, 8)[0]
    locales = _icu_table32_items(data, root).get("locales")
    if locales is None or locales >> 28 != 5:
        raise RuntimeError("ICU plurals.res is missing a TABLE16 locales table")

    table_off = locales & 0x0FFFFFFF
    arr16_off = keys_top * 4

    def unit(index: int) -> int:
        return struct.unpack_from("<H", data, arr16_off + index * 2)[0]

    count = unit(table_off)
    key_start = table_off + 1
    val_start = key_start + count
    de_index = ru_index = None
    for i in range(count):
        name = _icu_cstring(data, unit(key_start + i))
        if name == "de":
            de_index = i
        elif name == "ru":
            ru_index = i
    if de_index is None or ru_index is None:
        raise RuntimeError("ICU plurals.res is missing de/ru locale entries")

    de_rule = unit(val_start + de_index)
    ru_rule = unit(val_start + ru_index)
    file_off = header_size + arr16_off + (val_start + de_index) * 2
    struct.pack_into("<H", raw, file_off, ru_rule)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(raw)
    print(
        f"Retargeted ICU de plurals to Russian set ({de_rule} -> {ru_rule})",
        flush=True,
    )


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


def normalize_mod_version(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ValueError("Empty mod version")
    if not value.lower().startswith("v"):
        value = "v" + value
    return value


def resolve_mod_version(explicit: str | None) -> str:
    for raw in (explicit, os.environ.get("EUROPA1410_MOD_VERSION"), MOD_VERSION):
        if raw and raw.strip():
            return normalize_mod_version(raw)
    return normalize_mod_version("0.0.0")


def write_version_file(version: str, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / VERSION_FILE
    path.write_text(version + "\n", encoding="utf-8")
    return path


def copy_launcher_files(dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name in LAUNCHER_FILES:
        src = WORK / name
        if not src.exists():
            raise FileNotFoundError(f"Missing launcher file: {src}")
        dest = dest_dir / name
        if src.suffix.lower() == ".ps1":
            dest.write_text(src.read_text(encoding="utf-8-sig"), encoding="utf-8-sig")
        else:
            shutil.copy2(src, dest)


def game_install_root() -> Path:
    return GAME_PAKS.parents[2]


def publish_dist(install: bool, version: str) -> None:
    write_version_file(version, DIST_PAKS)
    print(f"Dist ready: {DIST_PAKS}", flush=True)
    for name in MOD_FILES:
        path = DIST_PAKS / name
        if not path.exists():
            raise FileNotFoundError(f"Missing dist file: {path}")
        print(f"  {name} ({path.stat().st_size} bytes)", flush=True)
    print(f"  {VERSION_FILE} ({version})", flush=True)

    if not install:
        return

    GAME_PAKS.mkdir(parents=True, exist_ok=True)
    locked: list[str] = []
    for name in MOD_FILES:
        try:
            last_error: OSError | None = None
            for _attempt in range(8):
                try:
                    shutil.copy2(DIST_PAKS / name, GAME_PAKS / name)
                    last_error = None
                    break
                except OSError as error:
                    last_error = error
                    if getattr(error, "winerror", None) != 32:
                        raise
                    time.sleep(0.4)
            if last_error is not None:
                raise last_error
            print(f"Installed {name}", flush=True)
        except OSError as error:
            locked.append(name)
            print(f"Could not replace {name} while the game is running: {error}", flush=True)
    write_version_file(version, GAME_PAKS)
    print(f"Installed {VERSION_FILE}", flush=True)
    copy_launcher_files(game_install_root())
    print(f"Installed launcher into {game_install_root()}", flush=True)
    if locked:
        essential = [name for name in locked if name.endswith(".pak")]
        print(
            "Skipped locked files (close the game to refresh them): "
            + ", ".join(locked),
            flush=True,
        )
        if essential:
            raise RuntimeError(
                "Close Europa 1410 and rebuild to replace: " + ", ".join(essential)
            )


RELEASE_README = """THE GUILD - EUROPA 1410 — русский перевод (неофициальный)

Версия: {version}
Поддержать автора: https://www.donationalerts.com/r/link_it

Установка:
1. Распакуйте этот архив в папку игры, например:
   C:\\Program Files (x86)\\Steam\\steamapps\\common\\The Guild - Europa 1410
2. В итоге должны появиться файлы:
   Europa1410\\Content\\Paks\\RussianLocalization_P.pak
   Europa1410\\Content\\Paks\\RussianLocalization_P.ucas
   Europa1410\\Content\\Paks\\RussianLocalization_P.utoc
   Europa1410\\Content\\Paks\\RussianLocalization.version
   CheckTranslationUpdate.cmd
   check_translation_update.ps1
3. Чтобы перевод включился, откройте Settings → Language → Text Language
   и выберите Русский. Игра не показывает отдельный слот ru — пункт Русский
   стоит на месте Deutsch, с русским текстом и русскими окончаниями 1/2/5.
   Audio Language оставьте English: русской озвучки нет, мод её не подменяет.
   Text Language = English больше не переписывается — меняется только слот Deutsch.
4. В Steam: игра → Свойства → Параметры запуска. Вставьте как есть:

CheckTranslationUpdate.cmd %command%

   Кнопка Играть в Steam сначала откроет лаунчер перевода: можно
   скачать обновление, запустить игру или отключить перевод на сутки,
   если он мешает.
5. Полностью перезапустите игру, чтобы язык применился.

Нужна легально купленная копия игры. Это не взлом и не пиратство —
только текстовая локализация поверх вашей установки Steam.
"""


def make_release_zip(version: str) -> Path:
    release_dir = WORK / "release"
    release_dir.mkdir(parents=True, exist_ok=True)
    zip_path = release_dir / "Europa1410-RussianLocalization.zip"
    if zip_path.exists():
        zip_path.unlink()

    readme_path = release_dir / "README.txt"
    readme_path.write_text(RELEASE_README.format(version=version), encoding="utf-8")
    disclaimer_src = release_dir / "DISCLAIMER.txt"
    if not disclaimer_src.exists():
        raise FileNotFoundError(f"Missing {disclaimer_src}")

    launcher_dir = WORK / "tmp_launcher"
    if launcher_dir.exists():
        shutil.rmtree(launcher_dir)
    copy_launcher_files(launcher_dir)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(readme_path, "README.txt")
        archive.write(disclaimer_src, "DISCLAIMER.txt")
        for name in MOD_FILES:
            archive.write(DIST_PAKS / name, f"Europa1410/Content/Paks/{name}")
        archive.write(DIST_PAKS / VERSION_FILE, f"Europa1410/Content/Paks/{VERSION_FILE}")
        for name in LAUNCHER_FILES:
            archive.write(launcher_dir / name, name)
    shutil.rmtree(launcher_dir, ignore_errors=True)
    print(f"Release zip: {zip_path} ({zip_path.stat().st_size} bytes)", flush=True)
    return zip_path


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Retranslate all entries via API")
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--pause", type=float, default=0.4)
    parser.add_argument("--workers", type=int, default=1)
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
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="Do not create the user-facing zip archive",
    )
    parser.add_argument(
        "--mod-version",
        default="",
        help="Version stamp written into the release (e.g. 1.0.4)",
    )
    args = parser.parse_args()

    version = resolve_mod_version(args.mod_version or None)
    print(f"Mod version: {version}", flush=True)

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
        list(EXTRA_UNCATEGORIZED_LOC_ENTRIES),
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
            workers=args.workers,
            on_progress=save_cache,
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
    publish_dist(install=install, version=version)
    if not args.no_zip:
        make_release_zip(version)
    print(f"Done. {total} entries.", flush=True)


if __name__ == "__main__":
    main()
