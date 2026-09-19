#!/usr/bin/env python3
"""Rebuild Russian localization mod with context-aware translation rules."""
from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from pylocres import LocmetaFile, LocresFile
from pylocres.locres import Entry, Namespace

from locmod_lib import (
    GLOSSARY_BY_KEY,
    INLINE_GLOSSARY,
    LocEntry,
    NS_HINTS,
    PLACEHOLDER_RE,
    cyrillic_ratio,
    find_cached_translation,
    is_opaque_encoded_text,
    remember_cache_translation,
    repair_translation_placeholders,
    review_translation_drafts,
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
REVIEW_REPORT = WORK / "translation_review.csv"
GLOSSARY_REPORT = WORK / "translation_glossary.json"
REVIEW_STATE = WORK / "translation_review_state.json"

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

# Official game locmeta already lists ru. Writing de as well so the existing
# German menu entry also becomes Russian if the extra culture is hidden.
OUTPUT_CULTURES = ("ru", "de")

MANUAL = PATHS.manual

# Host/Start live in ST_General -> Game.locres; Join Game was only gathered into
# Uncategorized Texts and never added to ST_General during development.
EXTRA_GAME_LOC_ENTRIES = (
    LocEntry("General", "General_Button_JoinGame", "Join Game"),
)

MOD_SUPPORT_FILES = (
    "Europa1410/Content/Localization/Game/Game.locmeta",
    "Europa1410/Content/Localization/Game_VO/Game_VO.locmeta",
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


def load_review_state() -> set[str]:
    if not REVIEW_STATE.exists():
        return set()
    try:
        values = json.loads(REVIEW_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {str(value) for value in values} if isinstance(values, list) else set()


def save_review_state(values: set[str]) -> None:
    REVIEW_STATE.write_text(
        json.dumps(sorted(values), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_translation_glossary() -> None:
    payload = {
        "game": "The Guild - Europa 1410",
        "style": "literary medieval economic strategy UI",
        "terms_by_key": GLOSSARY_BY_KEY,
        "inline_terms": INLINE_GLOSSARY,
        "namespace_hints": NS_HINTS,
    }
    GLOSSARY_REPORT.parent.mkdir(parents=True, exist_ok=True)
    GLOSSARY_REPORT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_translation_review(entries: list[LocEntry], cache: dict[str, str]) -> None:
    rows: list[dict[str, str]] = []
    for entry in entries:
        cached = find_cached_translation(entry, cache)
        cache_hit = entry.cache_id in cache
        if cached is None and not cache_hit:
            prefix = f"{entry.stable_cache_id}\x1f"
            cache_hit = any(
                key.startswith(prefix)
                and key.split("\x1f", 2)[-1].strip() == entry.english.strip()
                for key in cache
            )
        russian = resolve(entry, cache)
        flags: list[str] = []
        if not cache_hit:
            status = "pending"
            flags = []
        else:
            status = "ok"
        if sorted(PLACEHOLDER_RE.findall(entry.english)) != sorted(
            PLACEHOLDER_RE.findall(russian)
        ):
            flags.append("placeholder_mismatch")
        if (
            not should_keep_english(entry)
            and russian.strip() == entry.english.strip()
        ):
            flags.append("untranslated")
        if not cache_hit:
            status = "pending"
        elif should_keep_english(entry):
            status = "protected"
        elif flags:
            status = "review"
        else:
            status = "ok"
        rows.append(
            {
                "namespace": entry.namespace,
                "key": entry.key,
                "english": entry.english,
                "russian": russian,
                "status": status,
                "flags": ",".join(flags),
                "cyrillic_ratio": f"{cyrillic_ratio(russian):.3f}",
            }
        )
    rows.sort(key=lambda row: (row["status"], row["namespace"], row["key"]))
    REVIEW_REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REVIEW_REPORT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    write_translation_glossary()


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
        current = cache.get(entry.cache_id) or find_cached_translation(entry, cache)
        if current and current.strip() != entry.english.strip():
            continue
        if is_opaque_encoded_text(entry.english):
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
        russian = resolve(entry, cache)
        if not russian or not russian.strip() or russian.strip() == english.strip():
            russian = existing.get(english)
        if is_opaque_encoded_text(english):
            russian = english
        if (
            not russian
            or not russian.strip()
            or russian.strip() == english.strip()
        ):
            russian = resolve(entry, cache)
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

    for culture in OUTPUT_CULTURES:
        write_locres(loc, culture_output_rel(rel, culture))
        print(f"  wrote {culture_output_rel(rel, culture)}", flush=True)
    return len(entries) + added


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

    locmeta_files = (
        "Europa1410/Content/Localization/Game/Game.locmeta",
        "Europa1410/Content/Localization/Game_VO/Game_VO.locmeta",
        "Europa1410/Content/Localization/Uncategorized Texts/Uncategorized Texts.locmeta",
    )
    for rel_path in locmeta_files:
        src = tmp / rel_path
        if not src.exists():
            raise FileNotFoundError(f"Missing {src}")
        dst = MOD_ROOT / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    patch_locmeta(
        MOD_ROOT / "Europa1410/Content/Localization/Game/Game.locmeta",
        OUTPUT_CULTURES,
    )
    patch_locmeta(
        MOD_ROOT / "Europa1410/Content/Localization/Game_VO/Game_VO.locmeta",
        OUTPUT_CULTURES,
    )
    patch_locmeta(
        MOD_ROOT
        / "Europa1410/Content/Localization/Uncategorized Texts/Uncategorized Texts.locmeta",
        OUTPUT_CULTURES,
    )

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

    ini_dst = MOD_ROOT / "Europa1410/Config/DefaultGame.ini"
    ini_dst.parent.mkdir(parents=True, exist_ok=True)
    ini_dst.write_text(ini_text, encoding="utf-8")
    shutil.rmtree(tmp)
    print("Staged locmeta and DefaultGame.ini overrides", flush=True)


def write_translated_string_tables(cache: dict[str, str]) -> int:
    dest_dir = MOD_ROOT / "Europa1410/Content/StringTables"
    dest_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    replaced = 0
    for csv_name, namespace in ST_NAMESPACE_BY_FILE.items():
        src = STRING_TABLES_DIR / csv_name
        if not src.exists():
            continue
        with src.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        if not rows:
            continue
        out_rows = [rows[0]]
        for row in rows[1:]:
            if len(row) < 2 or not row[0]:
                out_rows.append(row)
                continue
            source_english = row[1]
            lookup_english = (
                source_english.replace("\\r\\n", "\n")
                .replace("\\n", "\n")
                .replace('\\"', '""')
            )
            entry = LocEntry(namespace, row[0], lookup_english)
            russian = resolve(entry, cache)
            if lookup_english != source_english and russian == lookup_english:
                russian = source_english
            new_row = list(row)
            if russian != source_english:
                replaced += 1
            new_row[1] = russian
            out_rows.append(new_row)
        dest = dest_dir / csv_name
        with dest.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, quoting=csv.QUOTE_ALL, lineterminator="\n")
            writer.writerows(out_rows)
        written += 1
    print(
        f"Staged {written} string tables with {replaced} translated SourceString cells",
        flush=True,
    )
    return written


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


RELEASE_README = """THE GUILD - EUROPA 1410 — русский перевод (неофициальный)

Поддержать автора: https://www.donationalerts.com/r/link_it

Установка:
1. Распакуйте этот архив в папку игры, например:
   C:\\Program Files (x86)\\Steam\\steamapps\\common\\The Guild - Europa 1410
2. В итоге должны появиться файлы:
   Europa1410\\Content\\Paks\\RussianLocalization_P.pak
   Europa1410\\Content\\Paks\\RussianLocalization_P.ucas
   Europa1410\\Content\\Paks\\RussianLocalization_P.utoc
3. Чтобы перевод включился, откройте Settings → Language → Text Language
   и выберите Deutsch (немецкий). Мод подменяет немецкий текст русским —
   без этого шага интерфейс останется на английском.
4. Полностью перезапустите игру, чтобы язык применился.

Нужна легально купленная копия игры. Это не взлом и не пиратство —
только текстовая локализация поверх вашей установки Steam.
"""


def make_release_zip() -> Path:
    release_dir = WORK / "release"
    release_dir.mkdir(parents=True, exist_ok=True)
    zip_path = release_dir / "Europa1410-RussianLocalization.zip"
    if zip_path.exists():
        zip_path.unlink()

    readme_path = release_dir / "README.txt"
    readme_path.write_text(RELEASE_README, encoding="utf-8")
    disclaimer_src = release_dir / "DISCLAIMER.txt"
    if not disclaimer_src.exists():
        raise FileNotFoundError(f"Missing {disclaimer_src}")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(readme_path, "README.txt")
        archive.write(disclaimer_src, "DISCLAIMER.txt")
        for name in MOD_FILES:
            archive.write(DIST_PAKS / name, f"Europa1410/Content/Paks/{name}")
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
        "--review-existing",
        action="store_true",
        help="Actively review risky cached translations with the local editor",
    )
    parser.add_argument(
        "--review-all",
        action="store_true",
        help="Review every non-protected cached translation with the local editor",
    )
    parser.add_argument(
        "--review-batch-size",
        type=int,
        default=32,
        help="Number of translations per local-editor request",
    )
    parser.add_argument(
        "--reset-review-state",
        action="store_true",
        help="Forget completed review fingerprints and audit every entry again",
    )
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
    write_translation_glossary()

    def save_progress(
        current_cache: dict[str, str],
        changed_entries: list[LocEntry] | None = None,
    ) -> None:
        if changed_entries:
            review_translation_drafts(changed_entries, current_cache)
        save_cache(current_cache)
        try:
            write_translation_review(all_entries, current_cache)
        except OSError as exc:
            print(f"Review report deferred: {exc}", flush=True)

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
            on_progress=save_progress,
        )
        save_progress(cache)

    manual_applied = apply_manual_overrides(cache, all_entries)
    print(f"Manual overrides applied: {manual_applied}", flush=True)
    if args.review_existing or args.review_all:
        reviewed_fingerprints = (
            set()
            if args.reset_review_state
            else load_review_state() if args.review_all else set()
        )

        def save_review_progress(current_cache: dict[str, str]) -> None:
            save_cache(current_cache)
            if args.review_all:
                save_review_state(reviewed_fingerprints)

        reviewed, edited = review_translation_drafts(
            all_entries,
            cache,
            batch_size=args.review_batch_size,
            review_all=args.review_all,
            reviewed_fingerprints=reviewed_fingerprints if args.review_all else None,
            on_progress=save_review_progress,
        )
        print(f"Existing translation review: {reviewed} checked, {edited} edited", flush=True)
        save_cache(cache)
    checked_placeholders, fixed_placeholders = repair_translation_placeholders(
        all_entries, cache
    )
    if checked_placeholders:
        print(
            f"Placeholder repair: {checked_placeholders} checked, "
            f"{fixed_placeholders} fixed",
            flush=True,
        )
    save_cache(cache)
    write_translation_review(all_entries, cache)

    export_manual(all_entries, cache)

    total = 0
    for rel in LOC_FILES:
        total += build_locres(rel, cache)

    stage_mod_support_files()
    write_translated_string_tables(cache)
    pack_mod()
    setup_iostore()
    install = PATHS.install_mod_after_build and not args.no_install
    publish_dist(install=install)
    if not args.no_zip:
        make_release_zip()
    print(f"Done. {total} entries.", flush=True)


if __name__ == "__main__":
    main()
