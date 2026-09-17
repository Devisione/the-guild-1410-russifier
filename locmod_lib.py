from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

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

CONTEXT_MARK = "⟦E1410⟧"

ARTIFACT_PATTERNS = [
    re.compile(r"^Краткое название пользовательского интерфейса игры(?: \(множественное число\))?:\s*", re.I),
    re.compile(r"^Название (?:настройки|сеттинга):\s*", re.I),
    re.compile(r"^(?:Short game UI title(?: \(plural\))?|Setting name|Button label|Singular noun|Plural noun):\s*", re.I),
    re.compile(r"^Переведите на русский.+?\n\n", re.I | re.S),
    re.compile(r"^.*?⟦E1410⟧[^\n]*\n", re.I | re.S),
    re.compile(r"^Игра Europa 1410[^\n]*\n", re.I),
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
    "Settings/Language_TextLanguage_Name": "Язык текста",
    "Settings/Language_AudioLanguage_Name": "Язык озвучки",
    "Settings/Language_Collection_Name": "Язык",
    "Settings/Language_Changed_Warning_Message": "Чтобы все изменения языка вступили в силу, игру нужно полностью перезапустить.",
    "Settings/Language_SystemDefaultLanguage": "Системный ({0})",
    "General/General_MainMenu_JoinGame": "Присоединиться к игре",
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
    "Carts/Cart_DisplayName": "Телега",
    "Carts/Cart_DisplayName_plural": "Телеги",
    "Carts/Button_ManageCarts": "Телеги",
    "Carts/CartManagement_DisplayName": "Управление телегами",
    "Carts/Sell_Cart_DisplayName": "Продать телегу",
    "Carts/Tier_1_Cart_DisplayName": "Козья телега",
    "Carts/Tier_2_BetterCart_DisplayName": "Ослиная телега",
    "Carts/Tier_3_BestCart_DisplayName": "Конная телега",
    "Carts/Escort_DisplayName": "Сопровождение",
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
    "Titles": "Титулы",
    "Turn": "Ход",
    "Energy": "Энергия",
    "Standing": "Репутация",
    "offices": "должности",
    "Office": "Должность",
    "Offices": "Должности",
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
    "Cart": "Телега",
    "Carts": "Телеги",
    "cart": "телега",
    "carts": "телеги",
    "cargo compartment": "грузовой отсек",
    "cargo compartments": "грузовые отсеки",
    "Guild": "Гильдия",
    "Guilds": "Гильдии",
    "Marketplace": "Рынок",
    "Joiner": "Столяр",
    "Joiners": "Столяры",
    "Quarter": "Квартал",
    "Quarters": "Кварталы",
    "Escort": "Сопровождение",
    "Ambush": "Засада",
    "Waylaying": "Нападение на дороге",
}

# Longer phrases first. Injected into English before MT so "cart" cannot
# become "карта" or "корзина".
SOURCE_GLOSSARY: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bGoat Carts\b"), "козьи телеги"),
    (re.compile(r"\bGoat Cart\b"), "козья телега"),
    (re.compile(r"\bDonkey Carts\b"), "ослиные телеги"),
    (re.compile(r"\bDonkey Cart\b"), "ослиная телега"),
    (re.compile(r"\bHorse Carts\b"), "конные телеги"),
    (re.compile(r"\bHorse Cart\b"), "конная телега"),
    (re.compile(r"\bManage Carts\b"), "управление телегами"),
    (re.compile(r"\bSell Cart\b"), "продать телегу"),
    (re.compile(r"\bCart Management\b"), "управление телегами"),
    (re.compile(r"\bCart Escort\b"), "сопровождение телеги"),
    (re.compile(r"\bCart Cover\b"), "крышка телеги"),
    (re.compile(r"\bdangerous quarters\b"), "опасные кварталы"),
    (re.compile(r"\bDangerous quarters\b"), "Опасные кварталы"),
    (re.compile(r"\bQuarter inhabitants\b"), "жители квартала"),
    (re.compile(r"\bQuarter Accident Risk\b"), "риск несчастных случаев в квартале"),
    (re.compile(r"\bCivic Offices\b"), "гражданские должности"),
    (re.compile(r"\bCivic Office\b"), "гражданская должность"),
    (re.compile(r"\bTown Hall\b"), "ратуша"),
    (re.compile(r"\bTown Servant\b"), "городской служитель"),
    (re.compile(r"\bTownsfolk\b"), "горожане"),
    (re.compile(r"\bMarketplace\b"), "рынок"),
    (re.compile(r"\bJoiners\b"), "столяры"),
    (re.compile(r"\bJoiner\b"), "столяр"),
    (re.compile(r"\bLampoons\b"), "пасквили"),
    (re.compile(r"\blampoons\b"), "пасквили"),
    (re.compile(r"\bLampoon\b"), "пасквиль"),
    (re.compile(r"\blampoon\b"), "пасквиль"),
    (re.compile(r"\bCarts\b"), "телеги"),
    (re.compile(r"\bcarts\b"), "телеги"),
    (re.compile(r"\bCart\b"), "телега"),
    (re.compile(r"\bcart\b"), "телега"),
    (re.compile(r"\bGuilds\b"), "гильдии"),
    (re.compile(r"\bguilds\b"), "гильдии"),
    (re.compile(r"\bGuild\b"), "гильдия"),
    (re.compile(r"\bguild\b"), "гильдия"),
    (re.compile(r"\bQuarters\b"), "кварталы"),
    (re.compile(r"\bquarters\b"), "кварталы"),
    (re.compile(r"\bQuarter\b"), "квартал"),
    (re.compile(r"\bquarter\b"), "квартал"),
    (re.compile(r"\bStanding\b"), "репутация"),
    (re.compile(r"\bstanding\b"), "репутация"),
    (re.compile(r"\bIntegrity\b"), "прочность"),
    (re.compile(r"\bintegrity\b"), "прочность"),
    (re.compile(r"\bEscorts\b"), "сопровождение"),
    (re.compile(r"\bescort\b"), "сопровождение"),
    (re.compile(r"\bEscort\b"), "сопровождение"),
    (re.compile(r"\bWealth\b"), "богатство"),
    (re.compile(r"\bwealth\b"), "богатство"),
    (re.compile(r"\bDynasty\b"), "династия"),
    (re.compile(r"\bdynasty\b"), "династия"),
    (re.compile(r"\bWorkers\b"), "рабочие"),
    (re.compile(r"\bworkers\b"), "рабочие"),
    (re.compile(r"\bWorker\b"), "рабочий"),
    (re.compile(r"\bTitles\b"), "титулы"),
    (re.compile(r"\bTitle\b"), "титул"),
]

NS_HINTS: dict[str, str] = {
    "Carts": "Речь о грузовых телегах, а не о картах.",
    "Cities": "Quarter — городской квартал, не четверть.",
    "Politics": "Office — городская должность, не офис.",
    "Buildings": "Речь о средневековых домах, мастерских и гильдиях.",
    "Building Rooms": "Речь о комнатах в доме или мастерской.",
    "Professions": "Речь о ремёслах и цехах XV века.",
    "Workers": "Worker — работник в мастерской.",
    "Titles": "Title — дворянский титул.",
    "Character": "Standing — репутация, Integrity — прочность здания.",
    "Actions": "Речь о действиях персонажа в средневековом городе.",
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


def apply_source_glossary(text: str) -> str:
    parts = re.split(r"(__PH\d+__|__S\d+__)", text)
    for index, part in enumerate(parts):
        if re.fullmatch(r"__(?:PH|S)\d+__", part):
            continue
        for pattern, replacement in SOURCE_GLOSSARY:
            part = pattern.sub(replacement, part)
        parts[index] = part
    return "".join(parts)


def wrap_for_mt(text: str, entry: LocEntry) -> str:
    extra = NS_HINTS.get(entry.namespace, "")
    hint = (
        "Игра Europa 1410, XV век, гильдии и ремёсла. "
        "cart=телега, quarter=квартал, office=должность, guild=гильдия, "
        "standing=репутация, integrity=прочность, escort=сопровождение, "
        "wealth=богатство, title=титул, worker=рабочий, marketplace=рынок, "
        "joiner=столяр. На «вы». "
        f"{extra}"
    )
    return f"{CONTEXT_MARK} {hint}\n{apply_source_glossary(text)}"


def unwrap_mt(text: str) -> str:
    cleaned = (text or "").strip()
    if CONTEXT_MARK in cleaned:
        cleaned = cleaned.split(CONTEXT_MARK, 1)[-1]
        cleaned = re.sub(r"^[^\n]*\n", "", cleaned, count=1).strip()
    elif re.match(r"^(?:Игра Europa 1410|Переведите на русский)", cleaned, re.I):
        parts = re.split(r"\n\s*\n", cleaned, maxsplit=1)
        if len(parts) == 2:
            cleaned = parts[1].strip()
        else:
            cleaned = re.sub(r"^[^\n]*\n", "", cleaned, count=1).strip()
    return clean_artifacts(cleaned)


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
        result = restore(unwrap_mt(translated[0]), tokens)
        segment_offset = 1
        token_base = 0
    else:
        result = restore(protected, tokens)
        segment_offset = 0
        token_base = 0

    for index in range(segment_offset, len(segments)):
        token = f"__S{index - segment_offset + token_base}__"
        value = unwrap_mt(
            translated[index] if index < len(translated) else segments[index]
        )
        result = result.replace(token, value)
    return restore_syntax(clean_artifacts(result))


_RATE_LOCK = threading.Lock()
_RATE_NEXT = 0.0
_RATE_INTERVAL = 3.5
_MODEL_COOLDOWN: dict[str, float] = {}
GEMINI_MODELS = (
    "gemini-3.6-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
    "gemini-3-flash-preview",
)
LLM_SYSTEM = """Ты переводчик полной версии игры The Guild - Europa 1410.
Это средневековая экономическая стратегия про гильдии, ремёсла и город Священной Римской империи около 1410 года.

Жёсткий глоссарий (не нарушай):
- cart / carts = телега / телеги (грузовая повозка). НИКОГДА не карта и не корзина.
- quarter / quarters = квартал / кварталы города. НЕ четверть.
- office = городская должность (выборы, власть). Комната в здании — «кабинет».
- guild = ремесленная гильдия / цех
- standing = репутация
- integrity = прочность здания или телеги
- escort = вооружённое сопровождение телеги
- wealth = богатство
- title / titles = дворянский титул / титулы
- worker / workers = рабочий / рабочие в мастерской
- marketplace = рынок
- joiner / joiners = столяр / столяры
- dynasty = династия
- turn = игровой ход, если речь про время в игре

Правила:
- Обращение к игроку на «вы».
- Живой игровой русский, без канцелярита Google Translate.
- Сохрани без изменений плейсхолдеры: {ТЕГИ}, {##}, __PH0__, __S0__, |gender(...), |plural(...).
- Верни ТОЛЬКО JSON вида {"items":["..."]} — ровно столько строк, сколько во входе, в том же порядке.
"""


def _load_gemini_key() -> str:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("EUROPA1410_GEMINI_API_KEY")
    if key:
        return key.strip()
    config_path = Path(__file__).resolve().parent / "config.json"
    if config_path.exists():
        data = json.loads(config_path.read_text(encoding="utf-8"))
        value = data.get("gemini_api_key") or ""
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


class RateLimitError(RuntimeError):
    def __init__(self, model: str, detail: str, delay: float, daily: bool = False) -> None:
        super().__init__(f"429 {model} ({detail})")
        self.model = model
        self.detail = detail
        self.delay = delay
        self.daily = daily


def _wait_for_rate_slot() -> None:
    global _RATE_NEXT
    with _RATE_LOCK:
        now = time.monotonic()
        wait = _RATE_NEXT - now
        _RATE_NEXT = max(now, _RATE_NEXT) + _RATE_INTERVAL
    if wait > 0:
        time.sleep(wait)


def _model_ready(model: str) -> bool:
    return time.monotonic() >= _MODEL_COOLDOWN.get(model, 0.0)


def _cool_model(model: str, delay: float) -> None:
    _MODEL_COOLDOWN[model] = time.monotonic() + max(5.0, delay)


def _is_rate_limit_error(exc: Exception) -> bool:
    if isinstance(exc, RateLimitError):
        return True
    text = str(exc).lower()
    return "too many requests" in text or "429" in text or "resource exhausted" in text


def _rate_limit_details(response: requests.Response) -> tuple[str, float, bool]:
    text = response.text or ""
    delay = 60.0
    daily = "PerDay" in text
    header = response.headers.get("Retry-After") or response.headers.get("retry-after")
    if header:
        try:
            delay = max(delay, float(header))
        except ValueError:
            pass
    retry_match = re.search(r"retry in ([\d.]+)\s*s", text, re.I)
    if retry_match:
        delay = max(float(retry_match.group(1)), 8.0)
    metric = "quota"
    try:
        payload = response.json()
        error = payload.get("error") or {}
        for item in error.get("details") or []:
            for violation in item.get("violations") or []:
                quota_id = str(violation.get("quotaId") or "")
                if "PerDay" in quota_id:
                    daily = True
                metric_name = str(violation.get("quotaMetric") or "").rsplit("/", 1)[-1]
                if metric_name:
                    metric = metric_name
    except Exception:  # noqa: BLE001
        pass
    limit_match = re.search(r"limit:\s*(\d+)", text, re.I)
    if limit_match:
        metric = f"{metric} limit {limit_match.group(1)}"
    if daily:
        delay = max(delay, 12 * 3600)
        metric = f"day {metric}"
    return metric, delay, daily


def _extract_json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    if start < 0:
        raise RuntimeError("LLM did not return JSON")
    parsed, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    if not isinstance(parsed, dict):
        raise RuntimeError("LLM JSON is not an object")
    return parsed


def _gemini_translate_batch(texts: list[str], api_key: str, model: str) -> list[str]:
    _wait_for_rate_slot()
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = {
        "systemInstruction": {"parts": [{"text": LLM_SYSTEM}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": json.dumps({"items": texts}, ensure_ascii=False),
                    }
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 0},
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "items": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    }
                },
                "required": ["items"],
            },
        },
    }
    response = requests.post(url, json=payload, timeout=90)
    if response.status_code == 429:
        detail, delay, daily = _rate_limit_details(response)
        raise RateLimitError(model, detail, delay, daily=daily)
    if response.status_code >= 400:
        raise RuntimeError(f"Gemini {response.status_code}: {response.text[:300]}")
    body = response.json()
    text = body["candidates"][0]["content"]["parts"][0]["text"]
    parsed = _extract_json_object(text)
    items = parsed.get("items")
    if not isinstance(items, list) or len(items) != len(texts):
        raise RuntimeError(
            f"LLM item count {0 if not isinstance(items, list) else len(items)}/{len(texts)}"
        )
    return [unwrap_mt(str(item)) for item in items]


def translate_google_batch(
    texts: list[str],
    source: str,
    target: str,
    retries: int,
) -> list[str]:
    if not texts:
        return []
    api_key = _load_gemini_key()
    if not api_key:
        raise RuntimeError("Нет GEMINI_API_KEY / config.json gemini_api_key")

    last_error: Exception | None = None
    for _attempt in range(max(retries, 6)):
        minute_waited = False
        for model in GEMINI_MODELS:
            if not _model_ready(model):
                continue
            try:
                return _gemini_translate_batch(texts, api_key, model)
            except RateLimitError as exc:
                last_error = exc
                _cool_model(exc.model, exc.delay)
                kind = "day" if exc.daily else "min"
                print(f"  429 {exc.model} ({exc.detail}, {kind})", flush=True)
                if not exc.daily:
                    print(f"  wait {int(exc.delay)}s then retry", flush=True)
                    time.sleep(exc.delay)
                    minute_waited = True
                    break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                print(f"  Gemini {model} failed: {exc}", flush=True)
                if "503" in str(exc) or "UNAVAILABLE" in str(exc):
                    _cool_model(model, 25)
        if minute_waited:
            continue
        waits = [
            _MODEL_COOLDOWN.get(model, 0.0) - time.monotonic()
            for model in GEMINI_MODELS
        ]
        soonest = min(waits) if waits else 0.0
        if soonest > 3600:
            raise RuntimeError(
                "Дневная квота Gemini исчерпана. "
                "Подождите до сброса (полночь PT) или включите биллинг в AI Studio."
            )
        wait = max(5.0, min(max(soonest, 5.0), 90.0))
        print(f"  cooldown, sleep {int(wait)}s", flush=True)
        time.sleep(wait)
    raise RuntimeError(f"Gemini batch failed: {last_error}")


def _chunk_by_chars(texts: list[str], max_chars: int, max_items: int) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    current_chars = 0
    for text in texts:
        extra = len(text) + 1
        if current and (
            len(current) >= max_items or current_chars + extra > max_chars
        ):
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(text)
        current_chars += extra
    if current:
        chunks.append(current)
    return chunks


def _translate_chunk_resilient(
    chunk: list[str],
    source: str,
    target: str,
    retries: int,
    rate_retries: int = 6,
) -> list[str]:
    try:
        return translate_google_batch(chunk, source, target, retries)
    except Exception as exc:  # noqa: BLE001
        if len(chunk) == 1:
            try:
                return [translate_mymemory(chunk[0], source, target)]
            except Exception:  # noqa: BLE001
                print(f"  keeping English for one segment: {exc}", flush=True)
                return chunk
        if _is_rate_limit_error(exc) and rate_retries > 0:
            if getattr(exc, "daily", False) or "day " in str(exc):
                raise
            delay = getattr(exc, "delay", 45.0)
            print(
                f"  rate limited, waiting {int(delay)}s "
                f"({rate_retries} left)",
                flush=True,
            )
            time.sleep(max(20.0, min(delay, 90.0)))
            return _translate_chunk_resilient(
                chunk, source, target, retries, rate_retries - 1
            )
        print(f"  batch of {len(chunk)} failed ({exc}), splitting", flush=True)
        mid = max(1, len(chunk) // 2)
        return _translate_chunk_resilient(
            chunk[:mid], source, target, retries, rate_retries
        ) + _translate_chunk_resilient(
            chunk[mid:], source, target, retries, rate_retries
        )


def _translate_segments_parallel(
    segments: list[str],
    *,
    source: str,
    target: str,
    retries: int,
    workers: int,
    batch_size: int,
    pause: float,
    max_chars: int = 14000,
) -> list[str]:
    if not segments:
        return []

    chunks = _chunk_by_chars(segments, max_chars=max_chars, max_items=batch_size)
    translated = [""] * len(segments)
    print(
        f"  API: {len(segments)} segments in {len(chunks)} joined requests "
        f"(sequential, 1 HTTP call per batch)",
        flush=True,
    )

    cursor = 0
    for index, chunk in enumerate(chunks, start=1):
        values = _translate_chunk_resilient(chunk, source, target, retries)
        translated[cursor : cursor + len(values)] = values
        cursor += len(values)
        if index == len(chunks) or index % max(1, len(chunks) // 10) == 0:
            print(f"  translated batches {index}/{len(chunks)}", flush=True)
        if pause and index < len(chunks):
            time.sleep(pause)

    return translated


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
    return unwrap_mt(payload["responseData"]["translatedText"])


def translate_entries(
    entries: list[LocEntry],
    cache: dict[str, str],
    *,
    source: str = "en",
    target: str = "ru",
    batch_size: int = 80,
    pause: float = 0.05,
    retries: int = 4,
    force: bool = False,
    workers: int = 8,
    on_progress: Callable[[dict[str, str]], None] | None = None,
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

    if not pending:
        return cache

    unique_pending: list[tuple[LocEntry, str, list[str], list[str]]] = []
    seen_english: set[str] = set()
    for item in pending:
        english = item[0].english
        if english in seen_english:
            continue
        seen_english.add(english)
        unique_pending.append(item)

    print(
        f"  translating {len(unique_pending)} unique texts "
        f"({len(pending)} entries)",
        flush=True,
    )

    unique_result: dict[str, str] = {}
    wave_size = max(batch_size * max(1, workers), batch_size)
    total_waves = (len(unique_pending) + wave_size - 1) // wave_size
    for wave_index, wave_start in enumerate(range(0, len(unique_pending), wave_size), start=1):
        wave = unique_pending[wave_start : wave_start + wave_size]
        all_segments: list[str] = []
        layout: list[tuple[LocEntry, str, list[str], list[str], int, int]] = []
        for entry, protected, segments, tokens in wave:
            start_idx = len(all_segments)
            all_segments.extend(segments)
            layout.append(
                (entry, protected, segments, tokens, start_idx, len(all_segments))
            )

        print(
            f"  wave {wave_index}/{total_waves}: {len(wave)} texts, "
            f"{len(all_segments)} segments",
            flush=True,
        )
        translated_segments = _translate_segments_parallel(
            all_segments,
            source=source,
            target=target,
            retries=retries,
            workers=workers,
            batch_size=batch_size,
            pause=pause,
        )
        for entry, protected, segments, tokens, start_idx, end_idx in layout:
            unique_result[entry.english] = compose_text(
                protected,
                tokens,
                segments,
                translated_segments[start_idx:end_idx],
            )
        for pending_entry, *_rest in pending:
            russian = unique_result.get(pending_entry.english)
            if russian is not None:
                remember_cache_translation(cache, pending_entry, russian)
        if on_progress:
            on_progress(cache)

    return cache


def resolve(entry: LocEntry, cache: dict[str, str]) -> str:
    glossary = glossary_lookup(entry)
    if glossary is not None:
        return glossary
    cached = find_cached_translation(entry, cache)
    if cached is not None:
        return cached
    return entry.english
