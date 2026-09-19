#!/usr/bin/env python3
"""Validate Russian localization data before release."""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from locmod_lib import (
    ARTIFACT_PATTERNS,
    KEEP_ENGLISH_EXACT,
    LOCME_PREFIXES,
    LocEntry,
    cyrillic_ratio,
    is_system_entry,
    protect,
    review_fingerprint,
    should_keep_english,
)

from paths import resolve_paths
from build_russian_mod import ST_NAMESPACE_BY_FILE

PATHS = resolve_paths()
WORK = PATHS.work
MANUAL = PATHS.manual
CACHE = PATHS.cache
CHAR_NAMES = PATHS.string_tables_dir / "ST_CharacterNamePools.csv"
BUILD_LOC = PATHS.mod_root / "Europa1410/Content/Localization"
DIST = PATHS.dist_paks
STAGED_STRING_TABLES = PATHS.mod_root / "Europa1410/Content/StringTables"
REVIEW_REPORT = WORK / "translation_review.csv"
REVIEW_STATE = WORK / "translation_review_state.json"

PLACEHOLDER_RE = re.compile(r"\{[^{}]+\}|<[^>]+>|\[\[[^\]]+\]\]|%[sdif]")
LATIN_RE = re.compile(r"[A-Za-z]")


def latin_name_content(text: str) -> str:
    """Ignore localization markup when checking transliterated names."""
    cleaned = re.sub(r"\{Gender\}\|gender\([^)]*\)", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\|plural\([^)]*\)", "", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\{[^{}]+\}", "", cleaned)


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, int | str] = field(default_factory=dict)

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def load_manual() -> list[dict[str, str]]:
    rows = json.loads(MANUAL.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("translation_manual.json must be a list")
    return rows


def load_character_names() -> set[str]:
    names: set[str] = set()
    with CHAR_NAMES.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            names.add(row["SourceString"].strip())
    return names


def placeholder_keys(text: str) -> list[str]:
    return PLACEHOLDER_RE.findall(text)


def validate_manual(report: Report) -> dict[str, str]:
    rows = load_manual()
    manual: dict[str, str] = {}
    report.stats["manual_rows"] = len(rows)

    empty_en = 0
    empty_ru = 0
    same_unexpected = 0
    artifact_hits = 0
    cjk_hits = 0
    placeholder_mismatch = 0
    char_names = load_character_names()
    char_latin = 0

    for index, row in enumerate(rows, start=1):
        english = row.get("english", "")
        russian = row.get("russian", "")
        if not isinstance(english, str) or not isinstance(russian, str):
            report.error(f"Row {index}: english/russian must be strings")
            continue
        if not english.strip():
            empty_en += 1
            continue
        if english in manual and manual[english] != russian:
            report.warn(f"Duplicate english key with different russian: {english!r}")
        manual[english] = russian

        if not russian.strip():
            empty_ru += 1
            report.warn(f"Empty russian: {english[:80]!r}")
            continue

        if english.strip() == russian.strip():
            if english in char_names and english not in KEEP_ENGLISH_EXACT:
                report.error(f"Character name not transliterated: {english!r}")
            elif not english.startswith(LOCME_PREFIXES) and english not in KEEP_ENGLISH_EXACT:
                same_unexpected += 1

        if english in char_names and english not in KEEP_ENGLISH_EXACT and LATIN_RE.search(latin_name_content(russian)):
            char_latin += 1
            report.error(f"Character name still has latin letters: {english!r} -> {russian!r}")

        for pattern in ARTIFACT_PATTERNS:
            if pattern.search(russian):
                artifact_hits += 1
                report.warn(f"Artifact in russian for {english[:60]!r}")
                break

        if re.search(r"[\u3400-\u9fff]", russian):
            cjk_hits += 1
            report.error(f"Unexpected CJK characters: {english[:60]!r} -> {russian[:100]!r}")

        en_ph = placeholder_keys(english)
        ru_ph = placeholder_keys(russian)
        if Counter(en_ph) != Counter(ru_ph):
            placeholder_mismatch += 1
            report.error(
                f"Placeholder mismatch: {english[:50]!r} | EN={en_ph} RU={ru_ph}"
            )

    report.stats["manual_empty_english"] = empty_en
    report.stats["manual_empty_russian"] = empty_ru
    report.stats["manual_same_unexpected"] = same_unexpected
    report.stats["manual_artifact_hits"] = artifact_hits
    report.stats["manual_cjk_hits"] = cjk_hits
    report.stats["manual_placeholder_mismatch"] = placeholder_mismatch
    report.stats["manual_unique_english"] = len(manual)
    report.stats["character_names_latin"] = char_latin
    report.stats["character_names_total"] = len(char_names)
    report.stats["character_names_cyrillic"] = len(char_names) - char_latin

    expected_names = {name for name in char_names if name not in KEEP_ENGLISH_EXACT}
    translated_names = sum(
        1
        for name in expected_names
        if (
            (manual.get(name) or manual.get(f"{name} ") or "").strip()
            and (manual.get(name) or manual.get(f"{name} ") or "").strip() != name
        )
    )
    report.stats["character_names_translated"] = translated_names
    if translated_names != len(expected_names):
        report.error(
            f"Character names translated {translated_names}/{len(expected_names)}"
        )

    return manual


def validate_cache(report: Report, manual: dict[str, str]) -> None:
    if not CACHE.exists():
        report.error("entry_cache.json is missing")
        return

    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    report.stats["cache_entries"] = len(cache)

    protected_paths = load_protected_paths()
    english_identical = 0
    english_identical_protected = 0
    for key, value in cache.items():
        parts = key.split("\x1f")
        if len(parts) < 3:
            continue
        english = parts[2]
        if value.strip() == english.strip() and english.strip():
            if (parts[0], parts[1]) in protected_paths:
                english_identical_protected += 1
            else:
                english_identical += 1

    report.stats["cache_identical_protected"] = english_identical_protected
    report.stats["cache_identical_unexpected"] = english_identical
    if english_identical:
        report.error(f"Cache has {english_identical} unexpected entries identical to english")


def load_protected_paths() -> set[tuple[str, str]]:
    if not REVIEW_REPORT.exists():
        return set()
    with REVIEW_REPORT.open(encoding="utf-8-sig", newline="") as handle:
        return {
            (row.get("namespace", ""), row.get("key", ""))
            for row in csv.DictReader(handle)
            if row.get("status") == "protected"
        }


def validate_review_coverage(report: Report) -> None:
    if not REVIEW_REPORT.exists() or not REVIEW_STATE.exists():
        report.stats["semantic_review_status"] = "not checked (local audit artifacts absent)"
        return

    try:
        reviewed = set(json.loads(REVIEW_STATE.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        report.error(f"Cannot read full-review state: {exc}")
        return

    total = 0
    missing: list[str] = []
    with REVIEW_REPORT.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("status") == "protected":
                continue
            total += 1
            entry = LocEntry(row.get("namespace", ""), row.get("key", ""), row.get("english", ""))
            fingerprint = review_fingerprint(entry, row.get("russian", ""))
            if fingerprint not in reviewed:
                missing.append(f"{entry.namespace}/{entry.key}")

    report.stats["semantic_review_total"] = total
    report.stats["semantic_review_covered"] = total - len(missing)
    report.stats["semantic_review_missing"] = len(missing)
    report.stats["semantic_review_status"] = "checked"
    for key in missing[:25]:
        report.error(f"Current translation was not semantically reviewed: {key}")
    if len(missing) > 25:
        report.error(f"... and {len(missing) - 25} more unreviewed translations")


def validate_staged_string_tables(report: Report) -> None:
    expected: dict[tuple[str, str], str] = {}
    with REVIEW_REPORT.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("status") != "protected":
                expected[(row.get("namespace", ""), row.get("key", ""))] = row.get("russian", "")

    checked = 0
    mismatches: list[str] = []
    for csv_name, namespace in ST_NAMESPACE_BY_FILE.items():
        path = STAGED_STRING_TABLES / csv_name
        if not path.exists():
            report.error(f"Staged string table is missing: {csv_name}")
            continue
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in list(csv.reader(handle))[1:]:
                if len(row) < 2:
                    continue
                wanted = expected.get((namespace, row[0]))
                if wanted is None:
                    continue
                checked += 1
                if row[1] != wanted:
                    mismatches.append(f"{csv_name}:{row[0]}")

    report.stats["staged_string_tables_checked"] = checked
    report.stats["staged_string_table_mismatches"] = len(mismatches)
    for item in mismatches[:50]:
        report.error(f"Staged string table does not match reviewed translation: {item}")
    if len(mismatches) > 50:
        report.error(f"... and {len(mismatches) - 50} more staged string-table mismatches")


def validate_built_locres(report: Report) -> None:
    try:
        from pylocres import LocresFile
    except ImportError:
        report.warn("pylocres not installed, skipping locres validation")
        return

    rel_files = [
        "Game/ru/Game.locres",
        "Game_VO/ru/Game_VO.locres",
        "Uncategorized Texts/ru/Uncategorized Texts.locres",
    ]

    total = 0
    untranslated = 0
    untranslated_items: list[str] = []
    protected_latin = 0
    locme = 0
    cyrillic_entries = 0
    char_names = load_character_names()
    protected_paths = load_protected_paths()

    for rel in rel_files:
        path = BUILD_LOC / rel
        if not path.exists():
            report.warn(f"Built locres missing: {rel}")
            continue

        loc = LocresFile()
        loc.read(str(path))
        for namespace in loc:
            ns_name = namespace.name or ""
            for entry in namespace:
                total += 1
                text = entry.translation or ""
                if text.startswith(LOCME_PREFIXES):
                    locme += 1
                    continue
                if (ns_name, entry.key) in protected_paths:
                    protected_latin += 1
                    continue
                if re.search(r"[А-Яа-яЁё]", text):
                    cyrillic_entries += 1
                elif text and any(c.isascii() and c.isalpha() for c in text):
                    if text.strip() in KEEP_ENGLISH_EXACT:
                        continue
                    if not ns_name and re.fullmatch(r"[0-9A-Fa-f]{24,}", entry.key):
                        continue
                    if text in char_names and LATIN_RE.search(text):
                        report.error(
                            f"Built locres latin name: {ns_name}/{entry.key} -> {text!r}"
                        )
                    else:
                        untranslated += 1
                        untranslated_items.append(f"{ns_name}/{entry.key} -> {text!r}")

    report.stats["built_total"] = total
    report.stats["built_locme"] = locme
    report.stats["built_cyrillic"] = cyrillic_entries
    report.stats["built_untranslated"] = untranslated
    report.stats["built_protected"] = protected_latin

    if total == 0:
        report.warn("No built locres entries found")
    elif untranslated:
        report.error(f"Built locres has {untranslated} likely-untranslated entries")
        for item in untranslated_items:
            report.error(f"Likely untranslated: {item}")


def validate_dist(report: Report) -> None:
    required = (
        "RussianLocalization_P.pak",
        "RussianLocalization_P.ucas",
        "RussianLocalization_P.utoc",
    )
    missing = [name for name in required if not (DIST / name).exists()]
    report.stats["dist_missing"] = ", ".join(missing) if missing else "none"
    if missing:
        report.warn(f"Dist files missing: {', '.join(missing)}")
    else:
        for name in required:
            report.stats[f"dist_{name}_bytes"] = (DIST / name).stat().st_size


def render_report(report: Report) -> str:
    lines = ["Russian localization validation report", "=" * 40, "", "Stats:"]
    for key, value in sorted(report.stats.items()):
        lines.append(f"  {key}: {value}")
    lines.append("")
    lines.append(f"Errors: {len(report.errors)}")
    for item in report.errors[:100]:
        lines.append(f"  [ERROR] {item}")
    if len(report.errors) > 100:
        lines.append(f"  ... and {len(report.errors) - 100} more")
    lines.append("")
    lines.append(f"Warnings: {len(report.warnings)}")
    for item in report.warnings[:100]:
        lines.append(f"  [WARN] {item}")
    if len(report.warnings) > 100:
        lines.append(f"  ... and {len(report.warnings) - 100} more")
    lines.append("")
    lines.append(
        "RESULT: PASS"
        if not report.errors
        else "RESULT: FAIL"
    )
    return "\n".join(lines)


def main() -> int:
    report = Report()
    manual = validate_manual(report)
    validate_cache(report, manual)
    validate_review_coverage(report)
    validate_staged_string_tables(report)
    validate_built_locres(report)
    validate_dist(report)

    text = render_report(report)
    out = WORK / "validation_report.txt"
    out.write_text(text, encoding="utf-8")

    sys.stdout.reconfigure(encoding="utf-8")
    print(text)
    print(f"\nSaved: {out}")
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
