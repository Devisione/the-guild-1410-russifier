"""Resolve project paths from config.json or environment variables."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

STEAM_APP_ID = "2977260"
DEFAULT_GAME_PAKS = (
    Path(r"C:/Program Files (x86)/Steam/steamapps/common")
    / "The Guild - Europa 1410/Europa1410/Content/Paks"
)


@dataclass(frozen=True)
class ProjectPaths:
    work: Path
    game_paks: Path
    source: Path
    dist_paks: Path
    repak: Path
    cache: Path
    manual: Path
    mod_root: Path
    string_tables_dir: Path
    ucas_path: Path
    ucas_hash_cache: Path
    steam_appmanifest: Path
    install_mod_after_build: bool = True
    auto_release: bool = False


def _load_config(work: Path) -> dict:
    config_path = work / "config.json"
    if not config_path.exists():
        return {}
    return json.loads(config_path.read_text(encoding="utf-8"))


def resolve_paths(work: Path | None = None) -> ProjectPaths:
    work = Path(work or os.environ.get("EUROPA1410_WORK_DIR") or Path(__file__).resolve().parent)
    config = _load_config(work)

    game_paks = Path(
        os.environ.get("EUROPA1410_GAME_PAKS")
        or config.get("game_paks_dir")
        or DEFAULT_GAME_PAKS
    )
    steamapps = next((parent for parent in game_paks.parents if parent.name.lower() == "steamapps"), None)
    default_manifest = (
        steamapps / f"appmanifest_{STEAM_APP_ID}.acf"
        if steamapps is not None
        else Path(r"C:/Program Files (x86)/Steam/steamapps") / f"appmanifest_{STEAM_APP_ID}.acf"
    )

    return ProjectPaths(
        work=work,
        game_paks=game_paks,
        source=work / "source/Europa1410/Content/Localization",
        dist_paks=work / "dist/Europa1410/Content/Paks",
        repak=work / "tools/repak.exe",
        cache=work / "entry_cache.json",
        manual=work / "translation_manual.json",
        mod_root=work / "build/RussianLocalization_P",
        string_tables_dir=work / "source/Europa1410/Content/StringTables",
        ucas_path=game_paks / "Europa1410-Windows.ucas",
        ucas_hash_cache=work / "ucas_hash_entries.json",
        steam_appmanifest=Path(
            os.environ.get("EUROPA1410_STEAM_MANIFEST")
            or config.get("steam_appmanifest")
            or default_manifest
        ),
        install_mod_after_build=config.get("install_mod_after_build", True),
        auto_release=bool(config.get("auto_release", False)),
    )
