from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from deep_translator import GoogleTranslator

PLACEHOLDER_RE = re.compile(
    r"(\{[^{}]+\}|<[^>]+>|\[\[[^\]]+\]\]|%[sdif])"
)
INLINE_TAG_RE = re.compile(r"(\{[A-Z0-9_]+\})([^{}]+?)(\{##\})")
GENDER_RE = re.compile(r"\{Gender\}\|gender\(([^,]+),([^)]+)\)", re.IGNORECASE)
PLURAL_RE = re.compile(
    r"\|plural\((one=([^,]+),other=([^)]+))\)",
    re.IGNORECASE,
)
LOCME_PREFIXES = ("(LocMe)", "(LocMe?)")

ARTIFACT_PATTERNS = [
    re.compile(r"^Краткое название пользовательского интерфейса игры(?: \(множественное число\))?:\s*", re.I),
    re.compile(r"^Название (?:настройки|сеттинга):\s*", re.I),
    re.compile(r"^(?:Short game UI title(?: \(plural\))?|Setting name|Button label|Singular noun|Plural noun):\s*", re.I),
    re.compile(r"^Переведите на русский.+?\n\n", re.I | re.S),
]


@dataclass(frozen=True)
class LocEntry:
    namespace: str
    key: str
    english: str

    @property
    def cache_id(self) -> str:
        return f"{self.namespace}\x1f{self.key}\x1f{self.english}"

    @property
    def stable_cache_id(self) -> str:
        return f"{self.namespace}\x1f{self.key}"


GLOSSARY_BY_KEY: dict[str, str] = {
    "Character/ActionPoints_Acronym": "ОД",
    "Character/Gender_Female_DisplayName": "Женщина",
    "Character/Gender_Male_DisplayName": "Мужчина",
    "Settings/Audio_HeadphoneMode_Name": "3D-звук",
    "General/General_Button_OK": "OK",
    "General/General_Button_Cancel": "Отмена",
    "General/General_Button_Yes": "Да",
    "General/General_Button_No": "Нет",
    "General/General_MainMenu_NewGame": "Новая игра",
    "General/General_MainMenu_LoadGame": "Загрузить игру",
    "General/General_MainMenu_Settings": "Настройки",
    "General/General_MainMenu_Credits": "Авторы",
    "General/General_Button_QuitToMainMenu": "Выйти в главное меню",
    "General/GameModeSingleplayer_Label": "Одиночная игра",
    "General/GameModeMultiplayer_Label": "Сетевая игра",
    "General/General_Button_HostGame": "Хост-игра",
    "General/General_Button_StartGame": "Начать игру",
    "General/General_Button_JoinGame": "Присоединиться к игре",
    "General/General_Button_Previous": "Назад",
    "General/General_Button_Next": "Далее",
    "Settings/Option_On": "Вкл.",
    "Settings/Option_Off": "Выкл.",
    "Settings/Option_Normal": "Обычный",
    "Settings/Option_Small": "Малый",
    "Settings/Option_Large": "Крупный",
    "Settings/GuildInputDefaults": "Стандартные схемы",
    "Settings/Control_InputInfo_Collection_Name": "Подсказки управления",
    "General/General_Button_Back": "Назад",
    "General/SpeedControl_Normal_DisplayName": "Обычная скорость",
    "General/SpeedControl_Pause_DisplayName": "Пауза",
    "Actions/Begging_DisplayName": "Попрошайничество",
    "Actions/Espionage_DisplayName": "Шпионаж",
    "Actions/ApplyForOffice_DisplayName": "Подать заявку на должность",
    "Building Rooms/Guild_Office_DisplayName": "Кабинет",
    "Buildings/Category_MilitaryInstallation_DisplayName_plural": "Военные объекты",
    "Buildings/Category_StrongDefensiveInstallation_DisplayName_plural": "Мощные оборонительные сооружения",
    "Buildings/Category_WeakDefensiveInstallation_DisplayName_plural": "Слабые оборонительные сооружения",
    "Buildings/OccupationBuilding_Smithy_DisplayName": "Кузница",
    "Buildings/Guild_SmithsGuild_DisplayName": "Гильдия кузнецов",
    "Buildings/Guild_AlchemistsGuild_DisplayName": "Гильдия алхимиков",
    "Buildings/OccupationBuilding_Alchemist_DisplayName": "Лаборатория алхимика",
    "Professions/Profession_Smith_DisplayName": "Кузнец",
    "Professions/Profession_Smith_DisplayName_plural": "Кузнецы",
    "Professions/Profession_Alchemist_DisplayName": "Алхимик",
    "Professions/Profession_Alchemist_DisplayName_plural": "Алхимики",
    "Politics/CivicOffice_DisplayName": "Гражданская должность",
    "Politics/HonoraryOffice_DisplayName": "Почётные должности",
    "Politics/OfficeTree_Screen_DisplayName": "Должностные палаты",
    "Politics/OfficeTiers_DisplayName": "Ранги должностей",
    "Politics/Office_NoOffice_Label": "Без должности",
    "Politics/Office_TermsInOffice_DisplayName": "Сроки на должности",
    "General/MaintenancePhase_OfficeIncome_DisplayName": "Доход с должности",
    "Notifications/VictoryCondition_Success_Header": "Победа достигнута",
    "Events/OpponentElected_Name": "Должность занята",
    "Events/TutorialBusiness_Name": "Ведение вашего бизнеса",
    "Politics/Lawbook_Screen_Description": "Здесь собраны {L_LAW_C}законы{##}, на которых держится город, и {O_TYP_CIVOFF_C}должности{##}, которые следят за их исполнением.",
    "Politics/Lawbook_Screen_Description_extra1": "Выиграйте {O_ELE_C}выборы{##} на одну из этих ролей — и получите власть менять законы.",
    "Politics/Law_Option_Forbidden": "Незаконно",
    "Politics/Crime_Severity_Severe_DisplayName": "Серьёзное",
    "Politics/Law_SeverityOfTheLaw_DisplayName": "Строгость наказаний",
    "General/MaintenancePhase_PersonnelCosts_DisplayName": "Персонал",
    "Buildings/ConstructionScreen_Private_DisplayName": "Личное",
    "Carts/Tier_2_BetterCart_DisplayName": "Ослиная телега",
    "Professions/Profession_DisplayName": "Профессия",
    "Professions/XP_DisplayName_alt": "Опыт профессии",
    "/8DD821824C489058406FBCA6B68C5CC": "ЛКМ — захватить",
}

KEEP_ENGLISH_EXACT: set[str] = {
    "MediEvilCare",
    "FSR",
    "XeSS",
    "lvl",
    "OK",
    "TBD",
}

INLINE_GLOSSARY: dict[str, str] = {
    "Wealth": "Богатство",
    "Title": "Титул",
    "Turn": "Ход",
    "Energy": "Энергия",
    "Standing": "Репутация",
    "offices": "должности",
    "Office": "Должность",
    "Civic Offices": "Гражданские должности",
    "Townsfolk": "Горожане",
    "Dynasty": "Династия",
    "heir": "наследник",
    "dynasty": "династия",
    "evidence": "улики",
    "Integrity": "Прочность",
    "Town Hall": "Ратуша",
    "Town Servant": "Городской служитель",
    "lampoons": "пасквили",
    "laws": "законы",
    "hr": "ч",
    "hrs": "ч",
    "Worker": "Рабочий",
    "Workers": "Рабочие",
}


def protect_syntax(text: str) -> str:
    return (
        text.replace("|gender(", "|GENDERFUNC(")
        .replace("|plural(", "|PLURALFUNC(")
    )


def restore_syntax(text: str) -> str:
    return (
        text.replace("|GENDERFUNC(", "|gender(")
        .replace("|PLURALFUNC(", "|plural(")
    )


SYSTEM_KEY_PARTS = (
    "FSR",
    "XeSS",
    "DLSS",
    "RayTracing",
)


def protect(text: str) -> tuple[str, list[str]]:
    tokens: list[str] = []

    def repl(match: re.Match[str]) -> str:
        tokens.append(match.group(0))
        return f"__PH{len(tokens) - 1}__"

    return PLACEHOLDER_RE.sub(repl, text), tokens


def restore(text: str, tokens: list[str]) -> str:
    for index, token in enumerate(tokens):
        text = text.replace(f"__PH{index}__", token)
    return text


def cyrillic_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for char in text if "\u0400" <= char <= "\u04ff") / len(text)


def clean_artifacts(text: str) -> str:
    cleaned = text.strip()
    changed = True
    while changed:
        changed = False
        for pattern in ARTIFACT_PATTERNS:
            new_value = pattern.sub("", cleaned).strip(" :«»\"'")
            if new_value != cleaned:
                cleaned = new_value
                changed = True
    return cleaned


def is_system_entry(entry: LocEntry) -> bool:
    text = entry.english
    if not text or not text.strip():
        return True
    if text.startswith(LOCME_PREFIXES):
        return True
    if text in KEEP_ENGLISH_EXACT:
        return True
    if any(part in entry.key for part in SYSTEM_KEY_PARTS):
        return True
    if entry.namespace in {"Test"}:
        return True
    return False


def is_personal_name(entry: LocEntry) -> bool:
    text = entry.english.strip()
    if "Character Name" not in entry.namespace and "FirstName" not in entry.key:
        return False
    return bool(re.fullmatch(r"[A-Z][a-z'`-]{1,19}", text))


def is_pure_template(text: str) -> bool:
    protected, _tokens = protect(text)
    stripped = INLINE_TAG_RE.sub("__TAG__", protected)
    stripped = GENDER_RE.sub("__GENDER__", stripped)
    stripped = PLURAL_RE.sub("__PLURAL__", stripped)
    return bool(re.fullmatch(r"(__PH\d+__|__TAG__|__GENDER__|__PLURAL__|\s|[\d\W_])+", stripped))


def should_keep_english(entry: LocEntry) -> bool:
    if is_system_entry(entry):
        return True
    if is_personal_name(entry):
        return True
    if is_pure_template(entry.english):
        return True
    if cyrillic_ratio(entry.english) > 0.3:
        return True
    return False


def glossary_lookup(entry: LocEntry) -> str | None:
    by_path = GLOSSARY_BY_KEY.get(f"{entry.namespace}/{entry.key}")
    if by_path is not None:
        return by_path
    if len(entry.key) == 32 and all(ch in "0123456789ABCDEFabcdef" for ch in entry.key):
        return GLOSSARY_BY_KEY.get(f"/{entry.key.upper()}")
    return None


def find_cached_translation(entry: LocEntry, cache: dict[str, str]) -> str | None:
    exact = cache.get(entry.cache_id)
    if exact is not None:
        return None if exact.strip() == entry.english.strip() else exact

    prefix = f"{entry.stable_cache_id}\x1f"
    for key, value in cache.items():
        if not key.startswith(prefix):
            continue
        old_english = key.split("\x1f", 2)[2]
        if old_english.strip() != entry.english.strip():
            continue
        return None if value.strip() == entry.english.strip() else value
    return None


def remember_cache_translation(cache: dict[str, str], entry: LocEntry, russian: str) -> None:
    cache[entry.cache_id] = russian


def decompose_text(text: str) -> tuple[str, list[str], list[str]]:
    segments: list[str] = []

    def add(value: str) -> str:
        if not value or not re.search(r"[A-Za-z]", value):
            return value
        mapped = INLINE_GLOSSARY.get(value.strip(), INLINE_GLOSSARY.get(value.strip().title()))
        if mapped:
            return mapped
        token = f"__S{len(segments)}__"
        segments.append(value.strip())
        return token

    working = protect_syntax(text)

    gender_match = GENDER_RE.search(working)
    if gender_match:
        male = add(gender_match.group(1))
        female = add(gender_match.group(2))
        replacement = f"{{Gender}}|gender({male},{female})"
        working = working[: gender_match.start()] + replacement + working[gender_match.end() :]

    plural_match = PLURAL_RE.search(working)
    if plural_match:
        one = add(plural_match.group(2))
        other = add(plural_match.group(3))
        replacement = f"|plural(one={one},other={other})"
        working = working[: plural_match.start()] + replacement + working[plural_match.end() :]

    def inline_repl(match: re.Match[str]) -> str:
        inner = match.group(2).strip()
        if not inner or not re.search(r"[A-Za-z]", inner):
            return match.group(0)
        return f"{match.group(1)}{add(inner)}{match.group(3)}"

    working = INLINE_TAG_RE.sub(inline_repl, working)
    protected, tokens = protect(working)

    if _has_translatable_english(protected):
        segments.insert(0, protected)

    return protected, segments, tokens


def _has_translatable_english(text: str) -> bool:
    stripped = re.sub(r"__S\d+__", "", text)
    stripped = re.sub(r"\{[^{}]+\}", "", stripped)
    stripped = stripped.replace("|GENDERFUNC(", "").replace("|PLURALFUNC(", "")
    stripped = re.sub(r"[^A-Za-z]+", " ", stripped)
    return bool(re.search(r"[A-Za-z]{2,}", stripped))


def compose_text(protected: str, tokens: list[str], segments: list[str], translated: list[str]) -> str:
    if not segments:
        return restore_syntax(restore(protected, tokens))

    if _has_translatable_english(protected):
        result = restore(clean_artifacts(translated[0]), tokens)
        segment_offset = 1
        token_base = 0
    else:
        result = restore(protected, tokens)
        segment_offset = 0
        token_base = 0

    for index in range(segment_offset, len(segments)):
        token = f"__S{index - segment_offset + token_base}__"
        value = clean_artifacts(
            translated[index] if index < len(translated) else segments[index]
        )
        result = result.replace(token, value)
    return restore_syntax(clean_artifacts(result))


def translate_google_batch(
    texts: list[str],
    source: str,
    target: str,
    retries: int,
) -> list[str]:
    if not texts:
        return []
    translator = GoogleTranslator(source=source, target=target)
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            return [clean_artifacts(item) for item in translator.translate_batch(texts)]
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(min(20, 2**attempt))
    raise RuntimeError(f"Google batch failed: {last_error}")


def translate_mymemory(text: str, source: str, target: str) -> str:
    params = urllib.parse.urlencode(
        {
            "q": text,
            "langpair": f"{source}|{target}",
            "de": "europa1410-locmod@local",
        }
    )
    url = f"https://api.mymemory.translated.net/get?{params}"
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("responseStatus") != 200:
        raise RuntimeError(payload.get("responseDetails", "MyMemory error"))
    return clean_artifacts(payload["responseData"]["translatedText"])


def translate_entries(
    entries: list[LocEntry],
    cache: dict[str, str],
    *,
    source: str = "en",
    target: str = "ru",
    batch_size: int = 50,
    pause: float = 0.4,
    retries: int = 5,
    force: bool = False,
) -> dict[str, str]:
    pending: list[tuple[LocEntry, str, list[str], list[str]]] = []

    for entry in entries:
        if not force:
            cached = find_cached_translation(entry, cache)
            if cached is not None and cached.strip() != entry.english.strip():
                remember_cache_translation(cache, entry, cached)
                continue
        if should_keep_english(entry):
            remember_cache_translation(cache, entry, entry.english)
            continue
        glossary = glossary_lookup(entry)
        if glossary is not None:
            remember_cache_translation(cache, entry, glossary)
            continue
        protected, segments, tokens = decompose_text(entry.english)
        pending.append((entry, protected, segments, tokens))

    total_batches = (len(pending) + batch_size - 1) // batch_size if pending else 0
    for batch_index, start in enumerate(range(0, len(pending), batch_size), start=1):
        chunk = pending[start : start + batch_size]
        all_segments: list[str] = []
        layout: list[tuple[LocEntry, str, list[str], list[str], int, int]] = []

        for entry, protected, segments, tokens in chunk:
            start_idx = len(all_segments)
            all_segments.extend(segments)
            layout.append((entry, protected, segments, tokens, start_idx, len(all_segments)))

        print(
            f"  API batch {batch_index}/{total_batches}: "
            f"{len(chunk)} entries, {len(all_segments)} segments",
            flush=True,
        )

        try:
            translated_segments = translate_google_batch(all_segments, source, target, retries)
        except Exception as exc:  # noqa: BLE001
            print(f"  fallback one-by-one: {exc}", flush=True)
            translated_segments = []
            for segment in all_segments:
                try:
                    translated_segments.append(translate_mymemory(segment, source, target))
                except Exception:  # noqa: BLE001
                    translated_segments.append(segment)
                time.sleep(0.1)

        for entry, protected, segments, tokens, start_idx, end_idx in layout:
            translated = translated_segments[start_idx:end_idx]
            remember_cache_translation(
                cache,
                entry,
                compose_text(protected, tokens, segments, translated),
            )

        if batch_index < total_batches:
            time.sleep(pause)

    return cache


def resolve(entry: LocEntry, cache: dict[str, str]) -> str:
    glossary = glossary_lookup(entry)
    if glossary is not None:
        return glossary
    cached = find_cached_translation(entry, cache)
    if cached is not None:
        return cached
    return entry.english
