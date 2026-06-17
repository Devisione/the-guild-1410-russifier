"""Resolve project paths from config.json or environment variables."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_GAME_PAKS = (
    Path(r"C:/Program Files (x86)/Steam/steamapps/common")
    / "The Guild - Europa 1410 Demo/Europa1410/Content/Paks"
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
    install_mod_after_build: bool = True


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
        install_mod_after_build=config.get("install_mod_after_build", True),
    )
