from __future__ import annotations

import hashlib
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

# These entries were found to contain cross-batch substitutions or broken
# placeholder markup in the existing cache. Keep the repairs keyed by the
# full locres path so a stale English-key override cannot reintroduce them.
OBVIOUS_REPAIRS_BY_KEY: dict[str, str] = {
    "Actions/Action_Description": "Действия — это занятия, которыми вы занимаетесь лично.",
    "Actions/BanditContract02_Description": "Явитесь к Барону-разбойнику и попросите надёжную шайку из его людей. За приличный куш и добрую сталь вы получите {I_ROBCON_02_C}договор{##}, который свяжет закалённых в боях головорезов с вашим делом.",
    "Actions/Curse_Description": "Чтобы наложить на кого-то проклятие, принесите предмет в жертву.",
    "Actions/ExterminateBees_Description": "Навсегда прогоните пчёл из улья. Каковы бы ни были ваши причины, колония не вернётся.",
    "Actions/GenerateHatred_Description": "Внесите немного раздора между двумя людьми, понизив их {CHAR_STA_C}мнение{##} друг о друге.",
    "Actions/HireManager_Description": "Поручите управление предприятием опытному мастеру. Можно выбрать даже члена семьи, если у него есть необходимая {P_C}лицензия{##}. Он возьмёт на себя повседневные заботы, а предприятие и прибыль останутся вашими.",
    "Actions/HireManager_Family_Description_extra2": "Они освоят ремесло, будут вести повседневные дела и поддерживать работу предприятия, пока вы отдыхаете и собираете прибыль. Все в выигрыше.",
    "Actions/LivePerformance_Description": "Шутки, песни — всё, что у рабочего получается лучше всего, — выведите его на сцену. Сначала стоит {A_ACT_TRALIVPER_C}порепетировать{##}, ведь хорошая {S_RHE_C}риторика{##} наверняка {B_PARAM_ATT_C}соберёт больше зрителей{##}.",
    "Actions/PestControl_Description": "Дым от насекомых, порошок от крыс и кое-что «особенное» для самых упрямых. Лучше доверить это рабочим, сведущим в {S_HAN_C}ремесле{##}.",
    "Actions/PrayInShrine02_Description": "Помолитесь о безопасной работе в выбранном предприятии, уберегая рабочих от {ACC_C}несчастных случаев{##}.",
    "Actions/PromoteWorker_Description": "Отметьте тяжёлый труд повышением. Рабочий станет более {CHAR_PRO_C}производительным{##}, реже будет сталкиваться с {B_PARAM_ACCPRO_ACC}несчастными случаями{##} — и полностью оправдает более высокую зарплату.",
    "Actions/Repair_DisplayName": "Ремонт",
    "Actions/ResignFromOffice_PromptButton": "Подать в отставку",
    "Actions/Robbery_Description": "Найдите здание с высокой {B_PARAM_VAL_C}ценностью{##}, слабой {B_PARAM_BURPRO_C}охраной{##} и отправьте туда самых {S_STE_C}ловких{##} разбойников, чтобы они забрали всё, что смогут унести.",
    "Actions/Robbery_Description_Robber": "Найдите здание с высокой {B_PARAM_VAL_C}ценностью{##}, слабой {B_PARAM_BURPRO_C}охраной{##} и отправьте туда самых {S_STE_C}ловких{##} разбойников, чтобы они забрали всё, что смогут унести.",
    "Actions/Robbery_Description_Thief": "Найдите здание с высокой {B_PARAM_VAL_C}ценностью{##}, слабой {B_PARAM_BURPRO_C}охраной{##} и отправьте туда самых {S_STE_C}ловких{##} воров, чтобы они забрали всё, что смогут унести.",
    "Actions/StealHoney_Description": "Украдите мёд до того, как его хранитель успеет насладиться им, но остерегайтесь пчёл. Они сразу узнают чужака.",
    "Actions/TeachExpertise_Description": "Проведите практическое обучение рабочего. Полученный {CHAR_XP_C}опыт{##} приблизит его к {A_TEAEXP_C}повышению{##}.",
    "Actions/TrainCombat_Description_Guard": "Назначьте стражников на тренировку навыка {S_COM_C}боевого мастерства{##}. Больше дисциплины, мощнее удары и выше шансы в бою.",
    "Actions/TrainCombat_Description_Robber": "Назначьте разбойников на тренировку навыка {S_COM_C}боевого мастерства{##}. Мощнее удары, устойчивее стойка и явное преимущество в бою.",
    "Actions/TrainProfession_Description": "Получите {CHAR_XP_C}опыт{##} в своей {P_C}профессии{##} благодаря целенаправленным тренировкам.",
    "Actions/TrainSkill_Handicraft_Description": "Практикуйтесь и улучшайте своё {S_HAN_C}ремесло{##}.",
    "Actions/TrainSkill_Negotiation_Description": "Учитесь заключать более выгодные сделки и улучшайте свои навыки {S_NEG_C}переговоров{##}.",
    "Actions/TrainStealth_Description_Robber": "Назначьте разбойников на тренировку {S_STE_C}хитрости{##}. Лучше обман, быстрее реакция — меньше неожиданностей в схватке.",
    "Actions/TryCatchingBeeQueen_Description_extra1": "Пчелиные матки редки. По мере того как {B_PER_BEEHIV_C}ульи{##} распространяются по городу, найти бесхозную матку становится всё труднее.",
    "Actions/WorkAtInn_Description": "Назначьте на стойку работника с сильными навыками {S_NEG_C}переговоров{##}. Он будет продавать товары из вашей {B_STOFRO_C}лавки{##} по более высоким ценам. Возможно, стоит вложиться в {B_IMP_INN_BAR_C}бар большего размера{##}.",
    "Building Improvements/AdditionalStorefront_Description": "Добавляет одно место для {B_STOFRO_C}торговой точки{##}.",
    "Building Improvements/Camouflage_Description": "{#green}{Value}{##} шанс быть обнаруженным {A_ACT_GUAPAT_C}патрулём стражи{##}",
    "Building Improvements/EfficiencyGathering _Description": "При {A_ACT_GATING_C}поиске ингредиентов{##}:\n{CV_({Value})} к скорости сбора",
    "Building Improvements/ImprovedPreaching_Description": "При проведении {A_ACT_HOLSTRSER_C}уличной проповеди{##}:\n{CV_({Value})} {CHAR_PRO}",
    "Building Improvements/ImprovedStanding_Description": "Каждый ход:\n{CV_({Value})} к {CHAR_STA_C}мнению{##} рабочих о вас",
    "Building Improvements/Infirmary_Description": "{CV_({Value})} к скорости восстановления при {ST_INJ_C}ранении{##}",
    "Building Improvements/LargerBar_Description": "При {A_ACT_WORINN_C}работе за стойкой{##}:\n{CV_({Value})} к {CHAR_PRO}",
    "Building Improvements/PatrolDetection_Description": "Во время {A_ACT_GUAPAT_C}патрулирования{##}:\n{CV_({Value})} шанс обнаружить {P_THI_C}воров{##} и {P_ROB_C}разбойников{##}",
    "Building Improvements/PickpocketTactics_Description": "При {A_ACT_PICPOC_C}карманной краже{##}:\n{CV_({Value})} {CHAR_MON}",
    "Building Improvements/TrainingBonusI_Description": "Все рабочие в здании:\n{CV_({Value})} {S_COM}",
    "Building Improvements/TrainingBonusII_Description": "Все рабочие в здании:\n{CV_({Value})} {S_STE}",
    "Building Improvements/TrainingEquipment_Combat_Description_Guard": "Позволяет отправлять стражников на {A_ACT_TRACOMGUA_C}тренировку боевого мастерства{##}",
    "Building Improvements/VictoryEnergizer_Description": "После победы в бою:\n{CV_({Value})} {CHAR_ENE}",
    "Buildings/Build_Button": "Построить",
    "Buildings/Enter_instruction": "Дважды щёлкните по зданию, чтобы войти внутрь",
    "Buildings/OccupationBuilding_Church_II_DisplayName": "Аббатство",
    "Buildings/OccupationBuilding_Inn_Description": "Тёплое заведение, где путешественников и горожан ждут еда, напитки и игры.",
    "Buildings/PublicBuilding_Well_Description": "В каждом городе нужен колодец. Люди шепчутся, что это ещё и портал — лучше не кричать в него, иначе вам может не понравиться ответ.",
    "Challenges/General_Item_Buy_LongDescription": "Посетите рынок — для этого используйте красные кнопки {marketplaceicon} слева. Чтобы купить предмет для личного пользования, кликните по нему, затем по пустому слоту в вашем {inventoryicon}личном инвентаре.",
    "Challenges/General_Item_Sell_Value_Specific": "Продайте {item} на сумму {value}",
    "Challenges/General_Wealth": "Достигните богатства в размере {value}",
    "Challenges/Onboarding_BuildBusinessImprovement_LongDescription": "Строительство улучшений требует небольших первоначальных затрат, но приносит долгосрочную выгоду. Иногда придётся выбирать между двумя или более типами улучшений.\n\nВ вашей кузнице выберите {B_IMP_icon}улучшение на свой вкус и постройте его.",
    "Challenges/Onboarding_BuildImprovement_LongDescription": "Строительство улучшений требует небольших первоначальных затрат, но приносит долгосрочную выгоду. Иногда придётся выбирать между двумя или более типами улучшений.\n\nЗайдите в свой дом или предприятие и выберите {B_IMP_icon}улучшение для строительства.",
    "Challenges/Onboarding_PressCharges_LongDescription": "Вы можете получить {CR_EVI_C}улики{##}, если будете {A_SPY_C}подглядывать{##} за другими, для этого вам понадобится {B_IMP_SPY_C}подзорная труба{##}. Другой способ — {A_SNI_C}распространять слухи{##} через {O_CHA_INF_C}информаторов{##}. Наблюдая за людьми вокруг и зарабатывая на этом, вы можете обнаружить доказательства преступлений!\n\nКогда у вас появятся улики, откройте раздел {HB_CRIEVI}«Преступления и улики» слева и выберите {A_PRECHA_C}«Предъявить обвинение»{##}.",
    "Challenges/Onboarding_SendCartToMarketplace_LongDescription": "Используйте {CAR_C}телеги{##}, чтобы закупать материалы и доставлять готовые изделия в различные места. \n\nКогда будете готовы продавать свои товары, загрузите телегу на предприятии: {CC_LMB}кликните по предмету, затем {CC_LMB}кликните по слоту телеги; выберите {B_PUB_MKT_C}рынок{##} на карте или в списке зданий и отправьте телегу в путь.\n\nЕсли вы предпочитаете производить и запасать товары, ожидая выгодного момента на рынке, отправьте пустую телегу на рынок за материалами, чтобы рабочие могли продолжать работу.",
    "Character/Dynasty_ViewFamilyTree_Prompt": "Просмотреть родословное дерево {dynastyname}",
    "Character/Wealth_Amount": "{value} {CHAR_WEA_C}богатства{##}",
    "Cities/Kuttenberg_Smith_Description": "Ваша {B_OCC_SMI_01_C}кузница{##} находится в {C_QUA_C}квартале{##} от центра событий — достаточно {QUA_CRI_C}безопасном{##}, но в часовой поездке до {B_PUB_MKT_C}рынка{##} берегите свои {I_SILRIN_C}серебряные кольца{##}: преступность растёт, а улицы остаются опасными, пока вы не окажетесь почти у {ST_QTR_STRROA_C}городских ворот{##}. {B_EXTFAC_MIN_C}Шахты{##} предлагают материалы со скидкой, но дорога через {ST_QTR_CRI_1_C}неблагополучные{##} окраины города — рискованная {CAR_CHANATT_C}авантюра{##}. Лучше начать копить на {CAR_2_C}более быструю телегу{##}.",
    "Combat/Duel_Challenger_Victory_Minor_Message": "Вы победили — но едва-едва.\nВпрочем, победа есть победа, и город это заметил.",
    "Combat/Duel_Respondent_Victory_Minor_Message": "Вы победили — но едва-едва.\nВпрочем, победа есть победа, и город это заметил.",
    "Events/BallInvite_Option_Refuse_Outcome_Instigator": "Рабочий класс одобряет ваш отказ от подобных роскошных удовольствий. Кроме того, вы сохранили свои деньги.",
    "Events/CharacterAtPillory_Description_2": "Если в нашем городе построена {B_PUB_PIL_C}позорная башня{##} и эти новые правила действительно не просто слухи, наверняка уже собирается толпа, чтобы бросить в осуждённого пару гнилых кочанов капусты. Найдите её, чтобы присоединиться к забаве или проявить своё превосходство и способность прощать.",
    "Events/Onboarding_PrivateTimeAndHeirs_Description_01": "Это Ян, Виллем, Питер, Маргрит, Лисбет, Корнелис, Алейд, Гертрёйд, маленькая Хендрика — а под столом прячется Адриан. Хотя, возможно, это Питер. Нет, Питер ест сыр.",
    "Events/Onboarding_Purge_B1_Description_01": "Глашатай объявил о Чистке. По указу высших должностных лиц один день беспорядка должен сохранить порядок на все остальные.",
    "Events/Preacher_Description_2": "Если вы мечтаете когда-нибудь подняться по церковной иерархии, найдите этого проповедника на городских улицах и вступите с ним в богословский спор. Возможно, это склонит общественное мнение к вашим взглядам.",
    "Events/TutorialUX_Description_7": "То же самое происходит при управлении предприятием: щёлкните левой кнопкой мыши по рабочему на скамье отдыха, затем ещё раз — чтобы назначить его на задание; или выберите рабочего на задании и щёлкните по скамье отдыха, чтобы снять его с задания и дать ему отдохнуть.",
    "Events/WorkerAccident_Description_0": "Один из ваших рабочих потерял сознание. Поскольку он больше не на рабочем месте, производство останавливается.",
    "General/VictoryCondition_Renown_Description": "Заработать {value} {CHAR_REN_C}репутации{##}",
    "HistoricalEvents/Kuttenberg_1422_Name": "Слепой Жижка",
    "Items/CombatEquipment_Armor_HeavyArmor_DisplayName": "Полный латный доспех",
    "Items/CombatEquipment_Weapon_LongSword_Description": "Классика рыцарского оружия.",
    "Items/IntermediateProduct_Leather_Description": "Шкура, которая переживёт своего владельца. Вчерашний зверь, завтрашняя мода.",
    "Items/IntermediateProduct_Polish_Description": "Последний штрих блеска для любого шедевра.",
    "Items/InventoryItem_FaustsElixir_DisplayName": "Эликсир Фауста",
    "Items/InventoryItem_RoyalJelly_Description": "Годится королеве — и отлично подходит для слабого улья.",
    "Items/InventoryItem_SkullCandle_Description": "Свеча с жутким пламенем, сгорающая сама и выжигающая половину ваших проступков из официальных записей и памяти свидетелей.",
    "Items/InventoryItem_SlateBoard_Description": "Для тех случаев, когда вы хотите, чтобы дети спорили остроумно, пусть даже мелом.",
    "Items/Potion_FragranceOfManipulation_Description": "Хитрый аромат, защищающий от козней и оборачивающий замыслы против их создателей.",
    "Items/Potion_FragranceOfManipulation_DisplayName": "Аромат манипуляции",
    "Items/Potion_FumeOfDecay_Description": "Едкий пар, разъедающий кладовые и оставляющий гниль там, где прежде было богатство.",
    "Items/Potion_PerfumeOfRomance_Description": "Душистый, дурманящий аромат — сердца теплеют, разум холодеет. Немногие устоят, да и не должны.",
    "Items/Potion_TinctureOfOblivion_Description": "Глоток — и даже ваш злейший враг забудет, за что вас ненавидит.",
    "Items/RawMaterial_Flax_Description": "Волокнистое растение, перерабатываемое в лён для одежды.",
    "Items/RawMaterial_Flax_DisplayName": "Лён",
    "Items/RawMaterial_Gold_Description": "Цельный слиток чистого золота.",
    "Items/Type_FinishedProduct_DisplayName": "Готовая продукция",
    "Notifications/Action_AscensionA_01_Instigator_Fail_Message": "Во время охоты вы замечаете движение в кустах и кричите: «Кабан!»\nКопья взлетают, всадники разбегаются. Из кустов выскакивает кролик. Смех длится дольше охоты.",
    "Notifications/Action_AscensionB_01_Instigator_Fail_Message": "Вы повторяете слух, услышанный за столом. Зал затихает. К утру весь двор знает, что вы не умеете хранить секреты.",
    "Notifications/Action_AscensionB_02_Instigator_Fail_Message": "Графиня Батори поднимает тост в честь недавно скончавшегося мужа. Вы говорите не вовремя. Все взгляды обращаются к вам.",
    "Notifications/Action_Curse_Instigator_Message": "Вы действительно ненавидите этого человека. Проклятие наложено.",
    "Notifications/Action_LampoonAlpha_Instigator_Message": "Все об этом говорят.\nГорожане уже составили своё мнение — одни сочувствуют герою пасквиля, другие смеются над ним.",
    "Notifications/Action_SellEvidence_Message": "Монеты у вас в кошельке. Дальнейшее вас больше не касается.",
    "Notifications/Action_SmearReputation_Instigator_Message": "Умело сделано. В их отношениях появились первые трещины.",
    "Notifications/Activity_PickpocketCitizen_Target_Fail_Message": "Вы замечаете длинные пальцы, тянущиеся к вашему кошельку, но вовремя уклоняетесь. Всё на месте.",
    "Notifications/Building_OutOfMaterials_Header": "У вас закончились материалы для изготовления",
    "Notifications/Category_Cart_DisplayName_alt": "Телеги",
    "Notifications/Negotiation_PurchaseSavings_Header": "Вы сэкономили на сделке",
    "Notifications/Profession_Rank_Advance_Success_Message": "Поздравляем! Ваше мастерство доказано усердием и опытом.",
    "Notifications/Trial_Appointment_Accused_Message": "Вас обвинит {accusername} в {crime} на суде.\n\nЧтобы склонить {CR_TRI_JUR_C}присяжных{##} на свою сторону, отточите {S_RHE_C}риторику{##} и подумайте об улучшении их {CHAR_STA_C}мнения{##} о вас с помощью особых предметов, например {I_SILCHA_C}серебряной цепи{##}.",
    "Notifications/VictoryCondition_Success_Message": "Поздравляем! Условие победы «{victorycondition}» выполнено. Теперь вы можете продолжить формировать своё наследие или уйти на покой с честью. Выбор за вами.",
    "Notifications/Worker_Accident_Message": "{workername} попал в {B_PARAM_ACCPRO_ACC}несчастный случай{##}, работая в здании {buildingtype}.",
    "Notifications/Worker_CanBePromoted_Header": "Каждый мастер когда-то был подмастерьем",
    "Notifications/Worker_CanBePromoted_Message": "{workername} готов к {A_PROWOR_C}повышению{##}. Рост производительности и снижение числа несчастных случаев могут с лихвой оправдать увеличение зарплаты.",
    "Notifications/Trial_Cancelled_DeathOfAccusedDuringTrial_Header": "Божественный суд",
    "Politics/CivicOffice_ChurchAuthority_Description": "Там, где вера встречается с властью. Церковные служители формируют нравы, следят за десятиной и заботятся о том, чтобы законы не забывали о небесах.",
    "Politics/Evidence_OnCharacter": "У вас есть улики против них.",
    "Politics/Evidence_OnCharacter_LookingFor": "Вы следите за ними.",
    "Politics/Evidence_Strength_DisplayName": "Сила улик",
    "Politics/Law_FinancialLaws_DisplayName_alt": "Финансовый",
    "Politics/Law_TownFaith_Description": "Вера — добродетель, если она истинна. Тех, кто отклоняется от принятой доктрины, могут подвергнуть испытанию веры в не самых лучших условиях.",
    "Politics/Law_Witchcraft_DisplayName": "Колдовство",
    "Politics/Trial_Punishment_Description": "После признания обвиняемого виновным на {CR_TRI_C}суде{##} его наказывают соответствующим образом. Наказание зависит от {CR_SEV_C}тяжести преступления{##} и от того, насколько {L_LAWSEV_C}строг{##} действующий закон.",
    "Politics/Trial_Punishment_Fine_Wealth": "Штраф в размере {value}% от {CHAR_WEA_C}богатства{##}",
    "Politics/Trial_Punishment_PropertyConfiscation": "Конфисковано одно {B_TYP_OCCBUI_C}профессиональное здание{##}",
    "Politics/Trial_Punishment_StandingWithEveryCharacter": "Изменение {CHAR_STA_C}мнения{##} о вас у всех персонажей: {value}",
    "Politics/Office_Election_AppointerCandidate_CannotVote_DuringElection_Warning": "Нельзя одновременно быть назначателем и кандидатом на одних и тех же выборах. Вы больше не можете голосовать.",
    "Seasons/Season_Autumn_Description": "Дожди пропитывают город, замедляя телеги в грязи, зато сырость помогает быстрее справляться с пожарами.",
    "Seasons/Season_Autumn_DisplayName": "Осень",
    "Seasons/Season_Spring_Description": "Сезон роста. Ингредиенты легче собирать, а после зимнего затишья торговля оживает.",
    "Seasons/Season_Spring_DisplayName": "Весна",
    "Seasons/Season_Summer_Description": "Долгие дни благоприятствуют торговле и труду, но жара сушит дерево и солому — пожары распространяются быстрее.",
    "Seasons/Season_Summer_DisplayName": "Лето",
    "Seasons/Season_Winter_Description": "Световой день короче всего, работать труднее, а в холоде распространяются болезни.",
    "Seasons/Weather_HeatWave_DisplayName": "Жара",
    "Seasons/Weather_Rain_DisplayName": "Дождь",
    "Status Effects/Quarter_CrimeRate_Tier00_Worsens": "Преступность под контролем.\nЕсли {QUA_CRI_C}уровень преступности{##} превысит {value}, квартал станет {ST_QTR_CRI_1_C}неблагополучным{##}.",
    "Status Effects/Quarter_Devastation_Tier00_Worsens": "Город спокоен и свободен от чумы.\nЕсли {QUA_DEV_C}грязь и разруха{##} превысит {value}, квартал станет {ST_QTR_DEV_1_C}плохо содержаться{##}.",
    "Status Effects/Quarter_Unrest_Tier00_Worsens": "Люди довольны.\nЕсли {QUA_UNR_C}беспокойство{##} превысит {value}, квартал станет {ST_QTR_UNR_1_C}неспокойным{##}.",
    "Status Effects/Quarter_Zeal_Tier00_Improves": "Откровение 3:15–16\nЕсли {QUA_ZEA_C}рвение{##} превысит {value}, квартал станет {ST_QTR_ZEA_1_C}набожно настроенным{##}.",
    "Workers/Employment_Thief_Journeyman_DisplayName": "Подмастерье вора",
    "Workers/Employment_Trader_Journeyman_DisplayName": "Подмастерье торговца",
    "Workers/Employment_Smith_Journeyman_DisplayName": "Подмастерье кузнеца",
    "Workers/Hiring_HiringFee_DisplayName": "Плата за найм",
    "Workers/Salary_Description": "Высокая зарплата обходится дороже, зато покупает лояльность — достаточную, чтобы сгладить грубое обращение.",
    "Workers/TaskContextMenu_EnableRepeating_Description": "Рабочие, назначенные на это задание, будут автоматически повторять его, пока вы их не отзовёте.",
    "Workers/TaskContextMenu_EnableRepeating_NotPossible": "Это задание нельзя повторять автоматически. Назначьте рабочих вручную, чтобы выполнить его снова.",
    "Workers/EmployeeManagement_HireMaster_Prompt": "Нанять мастера для управления предприятием",
    "Workers/AccidentChance_Value": "{value} за {duration}|plural(one={duration} минуту, other={duration} минут)",
    "Actions/BeeAttack_Description": "Когда дипломатия бессильна, всегда остаётся план «Пчела». Натравите обученную пчелу-убийцу на того, кто заслужил ваш гнев. Тому, за кем вы её пошлёте, придётся пережить нечто болезненное и крайне неприятное.",
    "Actions/AddBees_Description": "Поселите пчёл в пустом улье и подарите новой колонии дом. Обжившись, они смогут заняться тем, что умеют лучше всего.",
    "Actions/ApplyForOffice_Description": "Подайте заявку и дождитесь {O_ELE_C}выборов{##}. Хорошая {S_RHE_C}риторика{##} и благосклонное {CHAR_STA_C}мнение{##} назначателей нередко решают исход — порой даже сильнее, чем заслуги.",
    "Actions/BanditContract01_Description": "Подойдите к Барону-разбойнику и спросите, не найдётся ли у него нескольких свободных клинков. Предложите скромный кошель и сносное оружие — и получите {I_ROBCON_01_C}договор{##}.",
    "Actions/BanditContract03_Description": "Явитесь к Барону-разбойнику с щедрой данью и искусно изготовленным оружием и попросите его лучших людей. Если он останется доволен, вы получите {I_ROBCON_03_C}договор{##}, который отдаст под ваше начало его самых дисциплинированных и умелых бойцов.",
    "Actions/PastoralVisit_Description": "Служитель навещает дом заблудшей души и предлагает отпустить один из совершённых ею {CR_CRI_C}грехов{##}. Чем искуснее его {S_RHE_C}риторика{##} и чем менее {CR_SEV_C}тяжко{##} преступление, тем выше шанс на милость. Если прощение будет даровано, небеса вознаградят служителя: {CHAR_AP}.",
    "Actions/SitAtRegularsTable_Description": "Место за столом завсегдатаев даёт не только компанию, но и сведения, которые можно выведать лишь за кружкой эля. Посидите подольше — и кто-нибудь прольёт не только свой напиток.",
    "Actions/SpouseHelp_Description": "Попросите супруга помочь в делах. Прибыль в счётных книгах будет расти, даже если в {ST_CHR_FERDEC_C}спальне{##} дела идут не столь успешно.",
    "Actions/GiveProfession_Alchemist_Description": "Выберите члена семьи, который получит лицензию {P_ALC_C}алхимика{##}. После этого он сможет стать {B_OCC_MGR_C}мастером{##} в вашей {B_OCC_ALC_C}алхимической лаборатории{##}, пока вы занимаетесь другими делами.",
    "Actions/GiveProfession_Guard_Description": "Выберите члена семьи, который принесёт присягу {P_GUA_C}стражника{##}. Он сможет взять на себя {B_OCC_MGR_C}руководство{##} {B_OCC_GUA_C}казармами{##}, пока вы сосредоточитесь на других делах.",
    "Actions/GiveProfession_Innkeeper_Description": "Выберите члена семьи, который получит лицензию {P_INN_C}трактирщика{##}. Он сможет стать {B_OCC_MGR_C}мастером{##} в вашей {B_OCC_INN_C}таверне{##} и следить, чтобы эль лился рекой, пока вы занимаетесь другими делами.",
    "Actions/GiveProfession_Joiner_Description": "Выберите члена семьи, который получит лицензию {P_JOI_C}столяра{##}. После этого он сможет стать {B_OCC_MGR_C}мастером{##} в вашей {B_OCC_JOI_C}столярной мастерской{##}, пока вы занимаетесь другими делами.",
    "Actions/GiveProfession_Perfumer_Description": "Выберите члена семьи, который получит лицензию {P_PER_C}парфюмера{##}. После этого он сможет стать {B_OCC_MGR_C}мастером{##} в вашей {B_OCC_PER_C}парфюмерной мастерской{##}, пока вы занимаетесь другими делами.",
    "Actions/GiveProfession_Preacher_Description": "Выберите члена семьи, который принесёт обет {P_PRE_C}проповедника{##}. Он сможет исполнять священные обязанности {B_OCC_MGR_C}настоятеля{##} вашей {B_OCC_PRE_C}церкви{##}, пока вы посвящаете себя другим делам.",
    "Actions/GiveProfession_Robber_Description": "Выберите члена семьи, принятого в ряды {P_ROB_C}разбойников{##}. Доказав свою состоятельность, он сможет {B_OCC_MGR_C}возглавить{##} ваше {B_OCC_ROB_C}преступное предприятие{##}, пока вы занимаетесь другими делами.",
    "Actions/GiveProfession_Smith_Description": "Выберите члена семьи, который получит лицензию {P_SMI_C}кузнеца{##}. После этого он сможет стать {B_OCC_MGR_C}мастером{##} в вашей {B_OCC_SMI_C}кузнице{##}, пока вы занимаетесь другими делами.",
    "Actions/GiveProfession_StoneMason_Description": "Выберите члена семьи, который получит лицензию {P_MAS_C}каменотёса{##}. После этого он сможет стать {B_OCC_MGR_C}мастером{##} в вашей {B_OCC_MAS_C}каменотёсной мастерской{##}, пока вы занимаетесь другими делами.",
    "Actions/GiveProfession_Tailor_Description": "Выберите члена семьи, который получит лицензию {P_TAI_C}портного{##}. После этого он сможет стать {B_OCC_MGR_C}мастером{##} в вашей {B_OCC_TAI_C}портняжной мастерской{##}, пока вы занимаетесь другими делами.",
    "Actions/GiveProfession_Thief_Description": "Выберите члена семьи, принятого в воровскую шайку. Он сможет {B_OCC_MGR_C}возглавить{##} ваше {B_OCC_THI_C}убежище{##}, оставив вам время для других дел.",
    "Actions/PreachingListenTo_Option_Object": "Возразить",
    "Actions/ShowSkills_Combat_Option_extra1": "Проверка {S_COM_C}боевого мастерства{##}",
    "Actions/ShowSkills_Handicraft_Option_extra1": "Проверка {S_HAN_C}ремесла{##}",
    "Actions/ShowSkills_Rhetoric_Option_extra1": "Проверка {S_RHE_C}риторики{##}",
    "Actions/SmearReputation_Description": "Любовь — хрупкая вещь. Было бы жаль, если бы с ней что-нибудь… случилось. Посейте сомнения между двумя влюблёнными и {S_RHE_C}убедите{##} их, что каждый достоин лучшего.",
    "Actions/Snitch_DisplayName": "Сообщить слухи",
    "AmbientBackstories/Moneylender_011": "Всегда носит с собой учётную книгу",
    "AmbientBackstories/Moneylender_015": "Хорошо разбирается в людях",
    "AmbientBackstories/Thief_003": "Взломщик",
    "AmbientBackstories/Thief_004": "Щипач",
    "Buildings/Activity_DisplayName_Plural": "Виды деятельности",
    "Buildings/OccupationBuilding_Guard_I_DisplayName": "Караульный дом",
    "Buildings/PublicBuilding_Blackmarket_I_DisplayName": "Подпольный рынок",
    "Buildings/PublicBuilding_Sewers_I_DisplayName": "Стоки",
    "Carts/Tier_1_Cart_Description": "Одна коза. Один грузовой отсек. И бесконечное недовольство козы выбранным маршрутом. Телега достаточно легка, чтобы двигаться с приличной {CAR_SPE_C}скоростью{##}, но забудьте об {B_HP_C}обслуживании{##} — и обратно придётся толкать её самим.",
    "Challenges/Onboarding_Courtship2_LongDescription": "Выбрав персонажа для {A_COU_C}ухаживаний{##}, {CC_RMB} щёлкните правой кнопкой мыши по его портрету и выберите {A_SWETAL_C}Умаслить избранника{##}. Так вы сможете произвести впечатление и скорее добиться согласия на брак.",
    "Challenges/Onboarding_GainMasteryGrade_LongDescription": "Говорят, мастерство подтверждается бумагой. Чтобы продвинуться в ремесле {P_C}кузнеца{##}, получите {A_MASGRA_C}ступень мастерства{##}. Она повысит вашу {CHAR_REN_C}репутацию{##} и позволит расширить кузницу, где можно будет производить больше товаров и оказывать больше услуг.\n\nВ левой панели откройте {HB_PERACT} Личные действия и выберите {A_MASGRA_C}Получить ступень мастерства{##}.",
    "Backstories/HouseOfScholars_Description": "Книги, наставники и неудобные вопросы всегда находили место в вашей династии. Членов семьи поощряют изучать то, что другие упускают из виду, и задавать вопросы, которые остальные предпочли бы оставить без ответа. Это принесло семье немалые знания — и репутацию людей, которые не знают, когда следует прекратить поиски.",
    "Challenges/Onboarding_CraftItem_LongDescription": "Назначив рабочего на производство, убедитесь, что в {B_STOROO_C}кладовой{##} есть необходимые материалы.\n\nРабочие выполняют назначенные задания только в течение {WORHOU_C}рабочего дня{##}, поэтому не ждите готового товара до начала смены.",
    "Combat/Duel_Challenger_Defeat_Major_Message": "Вы проиграли поединок. Какая досада.\nГород ещё долго будет вам об этом напоминать.",
    "Combat/Duel_Respondent_Defeat_Major_Message": "Вы проиграли поединок. Какая досада.\nГород ещё долго будет вам об этом напоминать.",
    "Events/DynastyHeadWithHeirDied_Description_2": "Говорят, время ничего не забывает, но помнит избирательно. Долго ли ваше имя будет жить в памяти города?",
    "Events/Onboarding_SocializeAtInn_Description_03": "Признаюсь, вторую половину я соблюдаю уже не так строго, как прежде. В Праге я слышал проповедника, который говорил, что о вере человека следует судить по его поступкам, а не по тому, как часто он переступает порог церкви. В этом есть здравый смысл.",
    "HistoricalEvents/Kuttenberg_1415_Description": "Гнев охватывает {H_BOH_C}Богемию{##}: {H_JANHUS_C}Ян Гус{##}, прибывший на {H_COUOFCON_C}Констанцский собор{##}, поскольку {H_SIG_C}Сигизмунд{##} обещал ему безопасный проезд, был предан суду и осуждён католической церковью. Ему предложили сохранить жизнь в обмен на отречение, но он не изменил своим убеждениям даже перед лицом неминуемой смерти.",
    "Items/CombatEquipment_Weapon_Handgun_Description": "Грохочущее новшество, которого боятся больше из-за шума, чем из-за меткости. В отличие от лука или меча, оно не требует особого мастерства.",
    "Items/CombatEquipment_Weapon_PotGrenade_Description": "Простой сосуд, но его содержимое несёт взрывную погибель.",
    "Items/CombatEquipment_Weapon_ShadowDagger_Description": "Кинжал, пропитанный запретным ароматом, заставляет врагов сражаться с тенями. Они увидят вас, лишь когда будет уже поздно.",
    "Items/InventoryItem_ProtectiveCross_Description": "Божественная защита от сверхъестественных угроз и людских сплетен.",
    "Items/InventoryItem_WillowRod_Description": "Когда рабочие мешкают, ивовый прут убедительно подталкивает их закончить дело.",
    "Notifications/Action_DestroyBees_Instigator_Stung_Message": "Улей уничтожен. Пчёлы сражались до конца — такой отвагой вам не похвастаться.",
    "Notifications/Marriage_Success_Message": "Поздравляем! Вы вступаете в брак с {charactername}.",
    "Notifications/Trial_Appointment_Accuser_Message": "Ваше обвинение против {accusedname} будет рассмотрено на суде.\n\nТщательно подготовьтесь: развитая {S_RHE_C}риторика{##} поможет убедить {CR_TRI_JUR_C}присяжных{##}.",
    "Professions/ActiveProfession_Description": "Ваше {P_C}ремесло{##} определяет доступные действия, {CHAR_STA_C}мнение{##} окружающих о вас и продвижение по {P_MASGRA_C}ступеням мастерства{##}.",
    "Professions/Profession_Preacher_Description": "Проводит богослужения и ведает делами {B_OCC_PRE_C}церкви{##}: читает {A_ACT_HOLSTRSER_C}проповеди{##}, {A_ACT_PASVIS_C}отпускает грехи{##} и {A_PRAOTH_C}молится{##} за верующих.",
    "Professions/Profession_Robber_Description": "Руководит набегами и управляет {B_OCC_ROB_C}крепостью Барона-разбойника{##}, организуя {A_ACT_ROB_C}ограбления{##} и {A_ACT_WAYTRA_C}засады на телеги{##}.",
    "Status Effects/Illness_Leprosy_DisplayName": "Проказа",
    "Workers/AccidentChance_Description": "Несчастные случаи неизбежны, но при хорошей {B_PARAM_ACCPRO_C}профилактике{##} происходят реже. Кроме того, можно {A_TRAWORSKI_C}обучать рабочих{##}: это не только повышает {CHAR_PRO_C}производительность{##}, но и снижает {ACC_CHAACC_C}риск несчастных случаев{##}.",
    "Workers/Accident_Description": "Несчастные случаи неизбежны, но при хорошей {B_PARAM_ACCPRO_C}профилактике{##} происходят реже. Кроме того, можно {A_TRAWORSKI_C}обучать рабочих{##}: это не только повышает {CHAR_PRO_C}производительность{##}, но и снижает {ACC_CHAACC_C}риск несчастных случаев{##}.",
    "Workers/Energy_Regeneration_Tip": "Незанятые рабочие быстрее восстанавливают {CHAR_ENE_C}энергию{##}. Иногда лучше дать им отдохнуть, чтобы они вернулись к работе более {CHAR_PRO_C}производительными{##}.",
    "Actions/PublicService_Description": "Небольшая служба на благо города — ничего особенного. Чем лучше ваша {S_RHE_C}риторика{##}, тем выше оплата. И кто знает — возможно, вы даже заведёте {CHAR_STA_C}друзей{##} — или врагов — среди {O_CHA_TOWSER_C}городских служащих{##}.",
    "Actions/Socialize_Description": "Купите выпивку, посмейтесь вместе, заведите друга — или заручитесь услугой на будущее.",
    "Building Improvements/CrossII_Description": "Прощает одно {CR_SEV_C}незначительное преступление{##} каждый {C_TRN_C}ход{##}",
    "Character/MaritalStatus_Divorced_Description": "Снова свободен для новых отношений.",
    "Dialogue/3B77EFEE4C516FFB53852BB4A80144E7_642B736C00000000": "Вы достигли мастерства в четырёх навыках. На этом фоне весь остальной город начинает казаться… не вполне достойным.",
    "Dialogue/3CDF90D14BCA63FDA1E1E9B171DF9D31_642B736C00000000": "Ваша собственная палата подписала прошение о вашем отстранении. Ничто не ранит так глубоко, как предательство, прикрытое формальностями.",
    "Dialogue/8D8354864C205E45E5082295B2D612AD_642B736C00000000": "Вы достигли мастерства в четырёх навыках. На этом фоне весь остальной город начинает казаться… не вполне достойным.",
    "Dialogue/ED6259DA4D470F4CE7213AB49DAA03C3_642B736C00000000": "Ваша палата встала на вашу защиту. Не все желают вам провала.",
    "Events/Onboarding_BeeHives_Description_05": "И всё же от этих разговоров мне захотелось медовухи. Пчелиных маток можно найти в лесах. Разыщите одну — тогда и посмотрим, не было ли в словах того торговца доли истины.",
    "Events/Riot_Description_0": "Толпы недовольных горожан бунтуют на улицах и даже не могут договориться о своих требованиях. Неужели наши почтенные чиновники и вправду не замечали {QUA_UNR_C}волнений{##}, охвативших наш славный город?",
    "Events/StreetBrawl_Description_1": "Некоторые {CHAR_CITVSWOR_C}рабочие{##} оказались втянуты в драку:",
    "Events/TutorialCrime_Description_4": "Впрочем, те, кто говорит, будто преступлением не прокормишься, вероятно, просто не слишком {S_STE_C}искусны{##} в этом деле. Иногда человеку приходится преуспеть в преступном ремесле, чтобы заработать на жизнь. А поскольку политика и преступность всегда шли рука об руку, почему бы не занять должность, позволяющую узаконить ваше любимое занятие?",
    "HistoricalEvents/Kuttenberg_1422_Description": "Через два года после битвы на Витковой горе имя {H_JANZIZ_C}Яна Жижки{##} произносят с благоговением или страхом. Он разбил {H_SIG_C}Сигизмунда{##} при {H_VYS_C}Вышеграде{##} и, даже лишившись второго глаза от стрелы при {H_RAB_C}Раби{##}, по-прежнему командует {H_HUS_C}гуситами{##} {H_BOH_C}Богемии{##}.",
    "Items/InventoryItem_NecromancersRobe_Description": "Нападение становится самообороной, кража — займом, а если вы всё же проиграете, палач постарается нанести удар поаккуратнее.",
    "Notifications/Action_ShootCannonBall_Instigator_Success_Message": "Выстрел попал в цель. Повреждены следующие места:",
    "Notifications/Worker_Death_Message": "К сожалению, {workername} больше нет. В {buildingtype} будет тяжело без этого работника.",
    "Workers/Employment_Trader_Description": "Покупает, продаёт и распределяет товары на {B_OCC_TRA_C}складе{##}.",
    "Actions/DestroyBees_Description": "Проберитесь в улей соперника и выкурите рой, пока никто не успел вам помешать. Но берегитесь: атакованный улей непременно станет защищаться.",
    "Actions/GatherIngredients_Description": "Ищите в окрестностях полезные ингредиенты. Чем лучше рабочий владеет {S_HAN_C}ремеслом{##}, тем ценнее будут находки.",
    "Actions/PickpocketCitizen_Description": "Что-то привлекло ваше внимание — и это не ваше. Отправьте за добычей воров: чем они {S_STE_C}красноречивее{##}, тем выше шансы, если только нынешний владелец ничего не заметит и окажется {S_STE_C}доверчивым{##}.",
    "Actions/PressCharges_Description": "Используйте {CR_EVI_C}улики{##}, чтобы предъявить обвинение. На следующем {C_TRN_C}ходу{##} начнётся {CR_TRI_C}суд{##} над обвиняемым.",
    "Actions/SendHelp_Description": "Отправьте монеты с письмом поддержки.\nЭто может быть благодарность, попытка примирения или просто бескорыстный добрый жест.",
    "Actions/Snitch_DisplayName": "Сообщить слухи",
    "AmbientBackstories/Joiner_019": "Учился у родителей",
    "AmbientBackstories/MineWorker_015": "Суеверен под землёй",
    "AmbientBackstories/RobberBaron_002": "Устраивает засады у дорог",
    "AmbientBackstories/RobberBaron_008": "Охотится на богатых купцов",
    "AmbientBackstories/Thief_006": "Взломщик замков",
    "AmbientBackstories/Thief_008": "Пробирается внутрь по ночам",
    "AmbientBackstories/Thief_009": "Обчищает богатые дома",
    "AmbientBackstories/Thief_013": "Работает со скупщиком краденого",
    "Backstories/SurvivorsBlood_Description": "Ваша династия пережила чуму, пожары, суровые зимы и не одни похороны, предназначавшиеся кому-то другому. Какие бы беды ни случались, её члены упрямо продолжают жить. Одни говорят, что дело в крепкой крови. Другие сомневаются, что столь невероятное везение можно назвать простой удачей.",
    "Building Improvements/Infirmary_DisplayName": "Лазарет",
    "Building Name Pools/ExtractionFacility_WoodlandArea_Pool009": "Руби-лес",
    "Building Name Pools/Guild_GraveyardWardensGuild_Pool003": "Общество «Естественное посмертное движение»",
    "Building Name Pools/Guild_JoinersGuild_Pool009": "Общество «Тонкий рисунок»",
    "Building Name Pools/Guild_SmithsGuild_Pool001": "Братство «Шлифовка всё исправит»",
    "Building Name Pools/Guild_ThievesGuild_Pool003": "Общество «Потерянные вещи»",
    "Building Name Pools/PersonalBuilding_BeeHives_Pool014": "Да здравствует Плодородная",
    "Building Name Pools/PublicBuilding_PhysiciansOffice_Pool008": "Отрада доктора Пиявки",
    "Building Name Pools/PublicBuilding_Prison_Pool002": "Железная ирония",
    "Buildings/Parameter_BurglaryProtection_Description": "Мешает преступникам {A_ACT_STEITESTOMKT_C}воровать товары{##} из вашего заведения и {A_ACT_ROB_C}взламывать{##} его.",
    "Challenges/Onboarding_Courtship2_Description": "Умаслите того, за кем ухаживаете",
    "Challenges/Onboarding_HaveChild_LongDescription": "После свадьбы у вас может родиться {CHAR_HEI_C}ребёнок{##}. {CHAR_CHABABY_C}Вероятность{##} зависит от {CHAR_HP_C}здоровья{##} обоих супругов, их возраста и {CHAR_STA_C}мнения{##} друг о друге. Действие {A_PRITIM_C}Провести время наедине{##} повышает вероятность, но не является ни обязательным условием, ни гарантией.",
    "Challenges/Onboarding_SellFromStorefront_LongDescription": "В вашем предприятии {CC_LMB} щёлкните левой кнопкой по предмету в {B_STOROO_C}кладовой{##}, затем {CC_LMB} щёлкните по свободному месту в {B_STOFRO_C}витрине{##}, чтобы выложить его туда.\n\n{B_PARAM_ATT_C}Привлекательность{##} вашей кузницы время от времени приводит покупателей к витрине, поэтому следите, чтобы им всегда было что купить.",
    "Character/Score_Description": "Показатель успеха отражает достижения персонажа, его поступки и выполненные цели.",
    "Combat/MarginOfVictory_Description": "Показывает, насколько убедительна победа, исходя из итогового положения {CO_SCOBAR_C}шкалы победы{##}. От этого зависят применяемые эффекты.",
    "Dialogue/3128832A43C160D02C16DF9FF23E7E6D_642B736C00000000": "Похоже, вы производите впечатление человека, у которого есть цена. Но решать, что именно вы должны, всё равно вам.",
    "Dialogue/32F6B4F14E8C3C66A606BCB451AA8430_642B736C00000000": "Виновен. Если рассказ достаточно убедителен, осудят даже невиновного.",
    "Dialogue/66581370432C2EC60AE96B9A9240D6B5_642B736C00000000": "Виновен. Если рассказ достаточно убедителен, осудят даже невиновного.",
    "Dialogue/6F1C54404F1293C5A79D0F92ADA66350_642B736C00000000": "На свет появился десятый ребёнок. Каков бы ни был ваш замысел, он определённо… плодовит.",
    "Dialogue/B75ACEB240F966E7686E3DB9DB938577_642B736C00000000": "Возможно, они вернутся с кошельками потолще — или выберут более удачный момент.",
    "Dialogue/B7D574B44DBB3EC510F4F5AABFBD90BE_642B736C00000000": "Строительство идёт полным ходом. Скоро стены будут готовы.",
    "Dialogue/D65874594253AB192062E988E3405B5B_642B736C00000000": "Ваш навык переговоров вырос. Вскоре должна вырасти и прибыль.",
    "Events/Ambush_Description_0": "{P_ROB_C}Разбойники{##} и {P_GUA_C}стражники{##} всегда были смертельными врагами. Рано или поздно ваш усердный {A_ACT_GUAPAT_C}патруль стражи{##} должен был перехватить шайку негодяев, решивших поживиться плодами чужого труда.",
    "Events/CharacterAtPillory_Description_1": "Говорят, осуждённых преступников будут выставлять у позорного столба вместо {ST_CHR_IMP_C}тюремного заключения{##}, чтобы горожане могли выразить недовольство как им заблагорассудится. Заодно это наверняка сократит расходы на содержание {B_PUB_PRI_C}тюрьмы{##}.",
    "Events/CourtingCandidateDied_Description_3": "Вы повторяете это с горечью, ведь иначе пришлось бы признать, насколько дорог вам был этот человек.",
    "Events/GameChallenge_Description_1": "Вы пытаетесь вспомнить, разрешены ли нынче азартные игры, но отбрасываете эту мысль, когда незнакомец неловко бросает перед вами кости и бормочет что-то вроде:",
    "Events/Onboarding_CraftAndSell_Description_02": "Наверное, с этого и стоит начать. Изготовьте несколько деталей и продайте их на рынке. Скоро они кому-нибудь понадобятся, даже если этот человек пока об этом не знает.",
    "Events/Onboarding_Purge_B2_Description_01": "Теперь накопилось внушительное количество улик, свидетельствующих о проступках горожан.",
    "Events/RelicAcquisition_Description_0": "Измождённый паломник падает без сил у вашего дома. Прерывисто дыша, он шепчет, что пронёс священную реликвию через половину христианского мира, чтобы доставить её в далёкий монастырь.",
    "Events/ShadyCharacter_Description_0": "Вы подслушали, как городские стражники шутят о подозрительных личностях, которые слоняются по городу и выглядят самыми жалкими преступниками в истории.",
    "Events/TutorialBusiness_Description_0": "Добро пожаловать в наш прекрасный город! Человеку вашей {P_C}профессии{##} здесь откроется немало возможностей. Преуспевайте в своём ремесле, зарабатывайте {CHAR_MON_C}деньги{##}, повышайте {P_MASGRA_C}ступень мастерства{##}, копите {CHAR_WEA_C}богатство{##} и обретайте {CHAR_REN_C}славу{##}, чтобы подниматься по {T_C}общественной лестнице{##}.",
    "Events/TutorialBusiness_Description_2": "Используйте {A_OVEPRO_C}Надзор за работой{##}, чтобы ваши рабочие трудились быстрее. Если их {CHAR_STA_C}мнение{##} о вас станет слишком низким, они могут уйти, а чрезмерная нагрузка повышает риск {B_PARAM_ACCPRO_ACC}несчастных случаев{##}.",
    "Events/TutorialCrime_Description_0": "Городские {L_LAW_C}законы{##} влияют на то, как вы ведёте дела и взаимодействуете с {CHAR_CITVSWOR_C}горожанами{##}. В своде законов указано, какие действия считаются {CR_CRI_C}преступлениями{##} по действующим правилам, хотя всем известно, что закон нередко можно обойти.",
    "Events/TutorialFamily_Description_1": "{CHAR_COU_C}Начните ухаживать{##} за наиболее подходящим кандидатом с учётом возраста, {CHAR_HP_C}здоровья{##}, {CHAR_WEA_C}богатства{##}, {S_C}навыков{##} и других желательных качеств. {A_SENGIF_C}Дарите подарки{##} избраннику и {A_SWETAL_C}заискивайте{##} перед ним, чтобы скорее добиться помолвки, иначе вас могут опередить более убедительные соперники.",
    "Events/TutorialFamily_Description_2": "Свадебная церемония состоится на следующем {C_TRN_C}ходу{##} после успешной помолвки. Затем у вас могут родиться дети: это зависит от {CHAR_FER_C}плодовитости{##}, ваших {A_PRITIM_C}супружеских стараний{##} и внимания к {A_SPOHEL_C}другим отношениям{##}. На всякий случай можно усыновить одного-двух сирот.",
    "Events/WaylayingIntercepted_Description_0": "Ваши головорезы сумели перехватить телегу ничего не подозревающего владельца предприятия. Будем надеяться, что её не {A_HIRESC_C}сопровождают{##} умелые защитники, способные доставить неприятности вашим хорошо обученным людям.",
    "Events/WaylayingIntercepted_Option_Attack": "Напасть на телегу. Разбойникам положено грабить.",
    "Events/WaylayingIntercepted_Option_Attack_Outcome_Instigator": "Вы решили напасть на перехваченную телегу. Будем надеяться, что ваши головорезы хорошо обучены, отдохнули и снаряжены.",
    "HistoricalEvents/Kuttenberg_1419_Description": "Вера воюет сама с собой. Под давлением своего брата, {H_SIG_C}короля Сигизмунда{##}, и папы {H_WEN_C}король Вацлав{##} приказал вернуть католических священников в {H_HUS_C}гуситские{##} приходы. В ответ многолюдная процессия гуситов взяла штурмом Новоместскую ратушу в {H_PRA_C}Праге{##} и выбросила бургомистра с католическими советниками на копья стоявшей внизу толпы. Десятки тысяч людей по всему королевству охвачены волнениями!",
    "HistoricalEvents/Kuttenberg_1419_Description_extra1": "Теперь пришла весть о смерти самого короля Вацлава; некоторые даже говорят, что его сразило потрясение от этих новостей. Корону должен унаследовать Сигизмунд, но гуситы не забыли, как он обрёк {H_HUS_C}Яна Гуса{##} на сожжение, и скорее позволят королевству сгореть, чем склонятся перед ним.",
    "Items/InventoryItem_CrystalBall_Description": "Всматривайтесь достаточно долго — и будущее откроется вам именно таким, каким его предначертано увидеть.",
    "Items/InventoryItem_RoyalJelly_Description": "Достойно королевы — и отлично помогает ослабевшему улью.",
    "Items/Potion_ParalysingPoison_Description": "Яд ползёт по венам, пока даже поднять руку не становится всё равно что сдвинуть камень.",
    "Items/Type_Ingredient_RawMaterial_Description_extra1": "Их можно либо {A_ACT_GATING_C}найти в природе{##}, либо получить из {I_TYP_RES_C}ресурсов{##} в {B_TYP_EXTFAC_C}местах добычи{##}.",
    "Notifications/Action_ShootCannonBallFire_Instigator_Success_Message": "Огонь быстро разгорелся. Горят следующие места:",
    "Notifications/Action_UnlockProfession_TravellingEntertainer_Message": "Вашу труппу признали. Удерживайте внимание зрителей — и зарабатывайте их монеты.",
    "Notifications/Courtship_Fail_Death_Header": "Вашу любовь забрала… Смерть",
    "Notifications/Courtship_Fail_Death_Message": "К сожалению, {charactername} больше нет, и ваш роман оборвался без всяких церемоний.",
    "Notifications/Evidence_ErasedByTrial_Message": "{charactername} предстанет перед {CR_TRI_C}судом{##} за свои преступления. Теперь, когда дело передано в суд, вы больше не можете использовать эту {CR_EVI_C}улику{##} против обвиняемого.",
    "Notifications/Manager_NotSpendingMoney_Header": "Из-за нехватки монет мастер прекратил дальнейшие расходы",
    "Notifications/Marriage_Opponent_Success_Header": "Ваш соперник вступил в брак",
    "Notifications/Worker_Death_Message": "К сожалению, {workername} больше нет. Для {buildingtype} это станет тяжёлой утратой.",
    "Politics/Crime_Description": "{L_LAW_C}Незаконные{##} деяния, после которых могут остаться {CR_EVI_C}улики{##}. Преступления различаются тем, насколько легко их {CR_NOI_C}скрыть{##} и насколько они {CR_SEV_C}тяжки{##}.",
    "Politics/HonoraryOffice_PrinceOfThieves_DisplayName": "Принц воров",
    "Politics/Office_EligibilityOfCharacter_DisplayName": "Соответствие {Title} {FirstName} {LastName} требованиям",
    "Professions/Profession_StoneMason_Description": "Руководит обработкой камня и работой {B_OCC_MAS_C}каменотёсной мастерской{##}, {A_ACT_IMPQUA_C}улучшает дороги{##} и {A_ACT_IMPSTR_C}укрепляет фундаменты{##}.",
    "Professions/Profession_Thief_Description": "Руководит тайными операциями из {B_OCC_THI_C}укрытия{##}, организует {A_ACT_PICPOCCIT_C}мошенничество{##}, {A_ACT_PICPOC_C}кражи{##} и {A_ACT_FRAFORCRI_C}подбрасывание улик{##}.",
    "Professions/Profession_Trader_Description": "Управляет торговыми операциями и следит за товарами в {B_OCC_TRA_C}складе{##}.",
    "Professions/XP_ProgressBar_Description": "Когда шкала прогресса заполнится, отправляйтесь в {B_GUILD_C}гильдию{##} и получите следующую {P_MASGRA_C}ступень мастерства{##} в своей профессии.",
    "Status Effects/Dead_Description": "Я был тобой, ты станешь мной.",
    "Status Effects/Infamous_DisplayName": "Печально известный",
    "Status Effects/Quarter_Devastation_Tier00_Worsens": "Город благоустроен и свободен от поветрий.\nЕсли {QUA_DEV_C}грязь и разруха{##} превысят {value}, квартал станет {ST_QTR_DEV_1_C}запущенным{##}.",
    "Status Effects/Quarter_Zeal_Tier02_DisplayName": "Истово верующий",
    "Status Effects/StoneChestDisplayed_Description": "Чтобы открыть его, требуется больше времени, чем может себе позволить большинство воров.",
    "Workers/Employment_Perfumer_Description": "Создаёт духи, масла и ароматические товары в {B_OCC_PER_C}парфюмерной мастерской{##}.",
    "Workers/Employment_StoneMason_Description": "Обрабатывает неподатливый камень для дорог, фундаментов и других нужд в {B_OCC_MAS_C}каменотёсной мастерской{##}.",
    "Workers/Employment_TravellingEntertainer_Description": "Показывает трюки и развлекает публику в {B_OCC_ENT_C}лагере бродячего цирка{##}.",
    "Events/BeeAttack_Option_01_Result_Instigator": "Когда голова проясняется, возвращается и память. Этот рой появился не случайно. Должно быть, вы очень сильно кому-то насолили, раз на вас натравили пчёл-убийц.",
    "Building Improvements/TrainingEquipment_Combat_Description_Robber": "Позволяет отправлять разбойников на {A_ACT_TRACOMROB_C}тренировку боевого мастерства{##}",
    "Building Improvements/TrainingEquipment_Combat_Description_Thief": "Позволяет отправлять воров на {A_ACT_TRACOMTHI_C}тренировку боевого мастерства{##}",
    "Building Improvements/TrainingEquipment_Stealth_Description_Robber": "Позволяет отправлять разбойников на {A_ACT_TRASTEROB_C}тренировку хитрости{##}",
    "Building Improvements/TrainingEquipment_Stealth_Description_Thief": "Позволяет отправлять воров на {A_ACT_TRASTETHI_C}тренировку хитрости{##}",
    "Challenges/Onboarding_SellToMarketplace_LongDescription": "Когда гружёная {CAR_C}телега{##} прибудет на {B_PUB_MKT_C}рынок{##}, {CC_LMB} щёлкните левой кнопкой мыши по товару в телеге, а затем {CC_LMB} по выделенной области, чтобы сразу его продать.",
    "Cities/Kuttenberg_Alchemist_Description": "Самый центр города, {ST_QTR_STRROA_C}приличные дороги{##} и всего несколько минут до {B_PUB_MKT_C}рынка{##} — лучшего места для вашей {B_OCC_ALC_01_C}лаборатории{##} не найти. {A_ACT_GATING_C}Сама земля наполняет ваши полки{##}, позволяя экономить. Одна беда: законы о зельях вынуждают скрывать ремесло, но пока в конце года поступают налоги, властям безразлично, что вы варите, — лишь бы никто этого {CR_CRI_C}не пил{##}. Когда-нибудь вы станете {O_TYP_CIVOFF_C}деканом{##} и, возможно, измените закон. Алхимия — наука. Докажите им это!",
    "Combat/Combat_Description_extra1": "Бой делится на {CO_ROU_C}раунды{##} и {CO_PHA_C}фазы{##}. В каждом раунде приказы меняют {CO_ORD_C}тактику{##}, помогая получить преимущество на {CO_SCOBAR_C}шкале победы{##}.",
    "Events/TutorialBusiness_Description_5": "Продавайте товары, чтобы получать прибыль. Перемещайте их из {B_STOROO_C}кладовой{##} в {B_STOFRO_C}лавку{##} или загружайте в {CAR_C}телеги{##} и отправляйте на {B_PUB_MKT_C}рынок{##}. Ремонтируйте телеги или покупайте новые — более вместительные, прочные и быстрые.",
    "General/MaintenancePhase_BalancePersonal_Description_extra1": "Если отрицательный баланс сохранится надолго, вас могут {ST_CHR_IMP_C}заключить в тюрьму{##}.",
    "HistoricalEvents/BattleOfLipany_Description": "Последняя битва {H_HUS_C}гуситских{##} войн состоялась 30 мая 1434 года близ деревни Липаны, к востоку от {H_PRA_C}Праги{##}. Утраквисты, умеренное крыло гуситов, вместе с католическими союзниками разгромили радикальных таборитов и Сирот {H_PROTHEGRE_C}Прокопа Великого{##}. Так завершились пятнадцать лет войны и открылся путь к позднему признанию {H_SIG_C}Сигизмунда{##} королём {H_BOH_C}Богемии{##}.",
    "HistoricalEvents/JobstOfMoravia_Description_extra1": "Маркграф Моравии и Бранденбурга, в 1410 году избранный {H_KINOFROM_C}римским королём{##}. Двоюродный брат {H_SIG_C}Сигизмунда Люксембургского{##}. Умер в {H_BRN_C}Брно{##} при подозрительных обстоятельствах всего через три месяца после избрания.",
    "HistoricalEvents/Kuttenberg_1411_Description": "Весть пожаром разносится по {H_EMP_C}Империи{##}: {H_JOBOFMOR_C}Йобст Моравский{##}, {H_KINOFROM_C}римский король{##}, умер всего через несколько месяцев после избрания. Официально причиной называют болезнь, но многим такое объяснение кажется слишком удобным. Ходят слухи об отравлении: его двоюродный брат и заклятый соперник {H_SIG_C}Сигизмунд{##}, которому на прошлых выборах не достался престол, теперь остался без соперников.",
    "HistoricalEvents/Prague_Description": "Столица королевства {H_BOH_C}Богемия{##} и один из крупнейших городов {H_EMP_C}Империи{##}. В XIV веке при {H_CHA4_C}Карле IV{##} Прага была имперской столицей, а после сожжения {H_JANHUS_C}Яна Гуса{##} стала центром {H_HUS_C}гуситского{##} движения.",
    "Items/Type_FinishedProduct_Book_Description": "Знание редко бывает опасным — пока однажды не станет.\nПрежде чем перевернуть страницу, сверьтесь с законом {L_BOOLAM_C}о книгах и пасквилях{##}.",
    "Items/Type_FinishedProduct_Perfume_Description": "Аромат разносится дальше сплетен.\nУбедитесь, что закон {L_PER_C}об использовании парфюма{##} разрешает его применять.",
    "Items/Type_FinishedProduct_Potion_Description": "Безобидный тоник — или запрещённое вещество?\nПеред употреблением изучите закон {L_POT_C}об использовании зелий{##}.",
    "Items/Type_FinishedProduct_Witchcraft_Description": "КОЛДОВСТВО!!!\nСверьтесь с {L_WITCRA_C}законом о колдовстве{##}, пока за вами не пришли с факелами.",
    "Items/Type_Ingredient_Resource_Description": "Ресурсы добывают и перерабатывают в {I_TYP_RAWMAT_C}сырьё{##} на {B_TYP_EXTFAC_C}местах добычи{##}.",
    "Politics/Crime_Evidence_Tip_GatheringEvidence_short": "Используйте дома {B_IMP_SPY_C}подзорную трубу{##}, чтобы {A_SPYPER_C}шпионить{##} за другими, или наймите профессионала в {B_OCC_THI_C}укрытии{##}.",
    "Professions/Profession_GraveyardWarden_Description": "Управляет захоронениями и следит за порядком на {B_OCC_GRA_C}кладбище{##}.",
    "Professions/Profession_MoneyLender_Description": "Ведает займами, долгами и финансовыми сделками из {B_OCC_MONLEN_C}счётной конторы{##}.",
    "Professions/XP_Description_extra1": "Если хотите ускорить развитие, пройдите {A_TRAPRO_C}специальное обучение{##} в {B_GUILD_C}гильдии{##}, чтобы быстрее получить {A_MASGRA_C}ступень мастерства{##}.",
    "Titles/Benefit_EligibleForChambers": "Право заседать в {O_TYP_CIVOFF_C}палатах должностных лиц{##}",
    "Workers/Employment_MoneyLender_Description": "Ведёт учёт и финансовые дела в {B_OCC_MONLEN_C}счётной конторе{##}.",
    "Dialogue/892A00584F68DBC6E46E4C9503EAF686_642B736C00000000": "Ваши рабочие выполнили все задания на сегодня. Они вернутся на рассвете.",
}

GLOSSARY_BY_KEY.update(OBVIOUS_REPAIRS_BY_KEY)

MODEL_REPAIRS_BY_KEY: dict[str, str] = {
    "Challenges/Onboarding_BuyMaterials_LongDescription": "Из ничего ничего не бывает, поэтому следите, чтобы у рабочих были все нужные материалы.\n\nЦены колеблются в зависимости от спроса и предложения, поэтому следите за рынком и держите телегу наготове, чтобы воспользоваться выгодной сделкой.\n\nЧтобы купить {I_TYP_RAWMAT_C}материалы для производства{##}, отправьте вашу {CAR_C}телегу{##} на {B_PUB_MKT_C}рынок{##}, поместите купленные товары в её слоты, а затем отправьте телегу обратно в кузницу. Следите за {B_HP_C}прочностью{##} телег, чтобы они не сломались в пути.",
    "Challenges/Onboarding_ClaimTitle_LongDescription": "Люди придумали множество способов упорядочить общество и определить ценность друг друга. В этом обществе {T_C}титулы{##} стоят выше имён и определяют ваши права.\n\nКак только вы накопите достаточно {CHAR_WEA_C}богатства{##}, чтобы быть признанным {T_FULCIT_C}бюргером{##}, оформите титул в ратуше.\n\nВ левой панели нажмите {HB_TOWHAL} {B_PUB_TOWHAL_C}ратуша{##} или откройте {HB_PERACT} Личные действия и выберите {T_C}Получить следующий титул{##}.",
    "Challenges/Onboarding_VisitGuildHouse_DisplayName": "Гильдейское дело",
    "Challenges/Onboarding_VisitGuildHouse_LongDescription": "На карте найдите {B_GUILD_C}здание гильдии{##} для вашего {P_C}ремесла{##}. Используйте {HB_FIL} Фильтры, чтобы выделить {B_TYP_PUBBUI_C}Городскую инфраструктуру{##}, затем выберите здание вашей гильдии и выберите {A_TRAPRO_C}Обучение ремеслу{##}.",
    "Challenges/Onboarding_WinElection_LongDescription": "После подачи заявки на {O_TYP_CIVOFF_C}должность{##} проверьте {HB_UPCOMEVE} Предстоящие события, чтобы узнать, когда состоятся {O_ELE_C}выборы{##}. Улучшите навык {S_RHE_C}риторики{##} или повысьте {CHAR_STA_C}мнение{##} назначателей о вас, чтобы увеличить свои шансы в борьбе с другими кандидатами.",
    "Character/DynastyHead_Description": "Глава династии — самый влиятельный и богатый член каждой {CHAR_DYN_C}семьи{##}.",
    "Cities/AccidentChance_DisplayName_alt": "Риск несчастных случаев в квартале",
    "Cities/QuarterPeriodicEffect_Burglary_DisplayName": "Риск взломов",
    "Cities/Quarter_Description": "Подразделения внутри {C_DIS_C}районов{##}, образующие сеть улиц.\nПри выборе места для строительства важно учитывать расположение. Некоторые кварталы {ST_QTR_ZEA_3_C}набожно настроены{##}, некоторые {ST_QTR_CRI_3_C}плетут интриги{##}, а некоторые просто {ST_QTR_DEV_3_C}плохо пахнут и медленно убивают{##}.",
    "Cities/Quarter_DisplayName": "Квартал",
    "Cities/Quarter_DisplayName_plural": "Кварталы",
    "Cities/Quarter_Parameter_DisplayName": "Показатель квартала",
    "Cities/Quarter_Reputation_DisplayName": "Репутация",
    "Cities/Quarter_Zeal_DisplayName": "Рвение",
    "Combat/AttackCart_DisplayName": "Нападение на телегу",
    "Effects/Standing_RandomCitizens": "{CV_({Value})} {CHAR_STA_C}мнение{##} о {NumCitizens} {NumCitizens}|plural(one=случайном {CHAR_CITVSWOR_C}горожанине{##},other=случайных {CHAR_CITVSWOR_C}горожанах{##})",
    "Events/TutorialOffice_Description_2": "Каждая заявка рассматривается определёнными назначателями, которых можно склонить на свою сторону. Пока вы ждёте {O_ELE_C}выборов{##}, постарайтесь улучшить свой {S_RHE_C}навык риторики{##} и используйте Действия и Предметы, чтобы повысить {CHAR_STA_C}мнение{##} назначателей о вас, а также {A_BLANAM_C}подрывать{##} позиции своих конкурентов.",
    "Events/WaylayingIntercepted_Description_1": "Если, по божественному откровению, вы решите не нападать на эту телегу, ваша сдержанность будет уважена. Впрочем, криминальный мир конкурентен: если вы не ограбите её, это сделает какой-нибудь другой менее совестливый негодяй.",
    "HistoricalEvents/JanHus_Description_extra1": "{H_BOH_C}Чешский{##} священник, учёный и ректор университета в {H_PRA_C}Праге{##}. В начале XV века он стал популярным проповедником в Чехии, известным тем, что читал проповеди на чешском языке и обличал богатство и коррупцию католической церкви. Отлучён от церкви в 1411 году, в 1415 году был сожжён на костре на {H_COUOFCON_C}Констанцском соборе{##} вопреки обещанию {H_SIG_C}короля Сигизмунда{##} о безопасном проезде, что спровоцировало восстание {H_HUS_C}гуситов{##}.",
    "HistoricalEvents/Kuttenberg_Description": "Богатый королевский город с серебряными рудниками в центральной части {H_BOH_C}Чехии{##}, основанный около 1300 года после открытия обширных залежей серебра. Здесь находился королевский монетный двор, чеканивший знаменитые пражские гроши, что сделало город одним из богатейших городов {H_EMP_C}Империи{##}. С большим немецкоязычным католическим населением Куттенберг ожесточённо оспаривался и сильно пострадал во время {H_HUS_C}гуситских{##} войн.",
    "Notifications/Action_Courting_StartCourting_Message_Description": "Ухаживания начались. Сердца не завоёвываются без усилий: {A_SWETAL_C}заискивайте{##} перед избранником и {A_SENGIF_C}дарите подарки{##}, чтобы доказать свою преданность и состоятельность. Но берегитесь соперников: некоторые ухажёры постараются превзойти и даже подорвать ваши усилия.",
    "Notifications/Action_GiveProfession_Instigator_Message": "Наблюдать, как семья идёт по вашим стопам, — особое удовольствие. Теперь, когда их место в гильдии закреплено, они могут служить {B_OCC_MGR_C}мастером{##} в одном из ваших предприятий.",
    "Notifications/Activity_Robbery_Robber_Instigator_Message": "Ваши головорезы взяли, что могли, — если вообще что-то взяли, — и продали это ради вашей прибыли. Они уже возвращаются, оставив квартал опаснее, чем прежде.",
    "Notifications/Activity_Robbery_Thief_Instigator_Message": "Ваши головорезы взяли, что могли, — если вообще что-то взяли, — и продали это ради вашей прибыли. Они уже возвращаются, оставив квартал опаснее, чем прежде.",
    "Notifications/Category_Cart_DisplayName": "Уведомления о телегах",
    "Notifications/Courtship_Fail_Taken_Message": "{charactername} предпочёл {rivalname} вам.",
    "Notifications/Office_Election_Success_Message": "Поздравляем! Вы избраны на должность {officeposition}. Ваши новые обязанности и привилегии вступают в силу немедленно.",
    "Notifications/QuarterPeriodicEffect_Illness_Message": "Квартал страдает от {QUA_DEV_C}разрухи и нечистоты{##}. Улучшение {B_PARAM_HYG_C}санитарного состояния{##} может облегчить худшие последствия, но некоторые риски останутся.",
    "Status Effects/Ruined_Worsens": "Если {B_HP_C}прочность{##} упадёт ниже {value}%, здание станет {ST_STR_DES_C}пустынным{##}.",
    "Status Effects/TerriblyMaintainedCart_Worsens": "Если {B_HP_C}прочность{##} упадёт ниже {value}%, телега станет {ST_CAR_WRE_C}разбитой{##}.",
    "Status Effects/TerriblyMaintained_Improves": "Если {B_HP_C}прочность{##} поднимется выше {value}%, здание станет {ST_STR_BADMAI_C}запущенным{##}.",
    "Status Effects/TerriblyMaintained_Worsens": "Если {B_HP_C}прочность{##} упадёт ниже {value}%, здание станет {ST_STR_RUI_C}разрушенным{##}.",
    "Status Effects/Wrecked_Worsens": "Если {B_HP_C}прочность{##} упадёт ниже {value}%, телега станет {ST_CAR_BRO_C}непригодной{##}.",
    "Titles/Benefit_CanApplyToHigherOffices": "Право на должности {O_CHA_STAAUT_C}государственной власти{##}",
    "Titles/Benefit_CanApplyToOffices": "Право на {O_TYP_CIVOFF_C}должности{##}",
    "General/Parameter_IntegrityDecay_Description_extra1": "Телеги, однако, изнашиваются от эксплуатации. Чем чаще они в пути, тем быстрее приходят в негодность.",
}

GLOSSARY_BY_KEY.update(MODEL_REPAIRS_BY_KEY)

EXACT_REPAIRS_BY_KEY: dict[str, str] = {
    "Actions/Activity_Description": "Задания — это работы, выполняемые {CHAR_CITVSWOR_C}рабочими{##}. Назначайте рабочих в зависимости от требуемых {S_C}навыков{##}, чтобы повысить {CHAR_PRO_C}продуктивность{##} и сократить число {ACC_C}несчастных случаев{##}.",
    "Actions/Ascension_B_03_Description": "Наконец вы можете запросить {I_NOBRIG_C}дворянские права{##}, после чего официально подать прошение на {T_NOB_C}дворянский титул{##}, при условии, что у вас есть всё необходимое {CHAR_WEA_C}богатство{##}.",
    "Actions/BlackenSomeonesName_Description": "Передайте {CR_EVI_C}улики{##}, чтобы подорвать {CHAR_STA_C}мнение{##} о грешнике. {S_RHE_C}Риторика{##} решает всё: вам предстоит убеждать, а ему — выдерживать.",
    "Actions/BuyEvidence_Description": "За кружкой эля информация легко переходит из рук в руки. Оплатите, и чужой секрет станет вашим козырем.",
    "Actions/CharacterAtPillory_Description": "Внесите свой вклад в поддержание порядка в городе, забрасывая камнями осуждённого на позорном столбе.",
    "Actions/DestroyBees_Description": "Проберитесь в улей соперника и выкурите рой, пока никто не успел помешать. Осторожно, пчёлы: улей под атакой склонен защищаться.",
    "Actions/Dinner_Description": "Пригласите их на ужин — впечатлить простых людей дёшево, а {T_C}богатых{##} — нет. В любом случае {CHAR_STA_C}благоволение{##} лучше всего подавать после ужина.",
    "Actions/Dinner_Level_02": "Скудный",
    "Actions/EquipArmor_Description": "Выдайте рабочему доспех из вашего инвентаря, чтобы защитить его в опасных ситуациях.",
    "Actions/EquipWeapon_DisplayName": "Экипировать оружие",
    "Actions/GatherIngredients_Description": "Ищите по окрестностям полезные ингредиенты. Умелые руки — те, что обладают высоким навыком {S_HAN_C}ремеслом{##}, — принесут лучшие находки.",
    "Actions/GiveProfession_Guard_Description": "Выберите члена семьи, который принесёт присягу {P_GUA_C}стражника{##}. Он сможет взять на себя {B_OCC_MGR_C}обязанности{##} {B_OCC_GUA_C}казармы{##}, пока вы сосредоточитесь на других делах.",
    "Actions/ImproveBuilding_Description": "Укрепить здание новыми контрфорсами и свежим раствором — работа, требующая развитых навыков {S_HAN_C}ремесла{##}.",
    "Actions/PickpocketCitizen_Description": "Что-то привлекло ваше внимание — и это не ваше. Отправьте воров: чем они {S_STE_C}убедительны{##}, тем выше их шансы, — при условии, что нынешний владелец не заметит кражу и окажется {S_STE_C}доверчив{##}.",
    "Actions/PrayInShrine_Descriprition": "Произнесите тихую молитву за своих детей, желая им {SE_BLE_C}благословения{##} и крепкого {CHAR_HP_C}здоровья{##}.",
    "Actions/PressCharges_Description": "Используйте {CR_EVI_C}улики{##}, чтобы предъявить обвинение. {CR_TRI_C}судебный процесс{##} против обвиняемого начнётся на следующем {C_TRN_C}ходе{##}.",
    "Actions/Sabotage_Description": "Заплатите нужным людям, чтобы устроить небольшой хаос — пожар, ущерб, срыв. {S_STE_C}хитрость{##} поможет найти того, кто не провалит дело… и не выдаст вас.",
    "Actions/SendHelp_Description": "Отправьте монеты с письмом поддержки.\nЭто может быть благодарность, примирение или просто добрый жест — поданный свободно, без ожиданий.",
    "Actions/Snitch_DisplayName": "Распространить слухи",
    "Actions/TryCatchingBeeQueen_Description": "Попытайтесь поймать {I_BEEQUE_C}матку{##}. С ней вы сможете построить {B_PER_BEEHIV_C}улей{##} — и сотни крошечных рабочих будут приносить вам прибыль.",
    "Actions/UnlockProfession_Innkeeper_Description": "Получите лицензию {P_INN_C}трактирщика{##} и управляйте заведением, где льют эль и свободно расходятся чужие секреты.",
    "Actions/UnlockProfession_Joiner_Description": "Получите лицензию {P_JOI_C}столяра{##} и занимайтесь ремеслом, где надёжные соединения держат крепко — без чужого железа.",
    "AmbientBackstories/Alchemist_018": "Утверждает, что создал золото",
    "AmbientBackstories/Blacksmith_004": "Известен хорошими клинками",
    "AmbientBackstories/Innkeeper_023": "Знает каждого по имени",
    "AmbientBackstories/Joiner_019": "Научился у родителя",
    "AmbientBackstories/MineWorker_015": "Суеверия в подземельях",
    "AmbientBackstories/RobberBaron_002": "Дорожный засадник",
    "AmbientBackstories/RobberBaron_008": "Нацеливается на зажиточных купцов",
    "AmbientBackstories/Thief_006": "Замочник",
    "AmbientBackstories/Thief_007": "Лавочный воришка",
    "AmbientBackstories/Thief_008": "Пробирается по тёмному",
    "AmbientBackstories/Thief_009": "Нацеливается на богатые дома",
    "AmbientBackstories/Thief_013": "Работает с закладчиком",
    "Backstories/MarriageStrategists_DisplayName": "Брачные стратеги",
    "Backstories/SurvivorsBlood_Description": "Ваша династия пережила чуму, пожары, суровые зимы и не одну похорону, предназначавшуюся кому-то другому. Какое бы бедствие ни настигало, её члены упорно переживают его. Одни называют это сильной кровью. Другие задаются вопросом, является ли столь частое выживание вообще удачей.",
    "Building Improvements/CrossI_Description": "Прощает одно {CR_SEV_C}мелкое преступление{##} каждый {C_TRN_C}ход{##}",
    "Building Improvements/Infirmary_DisplayName": "Больница",
    "Building Improvements/Inspection_Description": "Открывает {A_ACT_INSBUS_C}осмотр предприятия{##}",
    "Building Improvements/InstrumentsII_Description": "Музыкальные инструменты вдвое эффективнее на {A_INVDIN_C}ужинах{##}",
    "Building Improvements/LargerBar_Description": "При {A_ACT_WORINN_C}работе за стойкой{##}:\n{CV_({Value})} {CHAR_PRO}",
    "Building Improvements/PrayingShrineII_Description": "При {A_PRAOTH_C}молитве за других{##}:\nстоимость {CHAR_ENE}: {#green}{Value}{##}",
    "Building Improvements/PrayingShrineIIAlt_Description": "При {A_PRAOTH_C}молитве за других{##}:\nстоимость {CHAR_AP}: {#green}{Value}{##}",
    "Building Improvements/PreachingBible_Description": "Открывает {A_ACT_HOLSTRSER_C}уличную проповедь{##}",
    "Building Improvements/StrategicAmbush_Description": "При {A_ACT_WAYTRA_C}нападении на телегу{##}:\n{CV_({Value})} шанс успешно перехватить телегу",
    "Building Name Pools/Cart_Cow_Pool003": "Корова-король",
    "Building Name Pools/Cart_Donkey_Pool006": "Иа",
    "Building Name Pools/Cart_Horse_Pool002": "Сахарная копытка",
    "Building Name Pools/ExtractionFacility_Mine_Pool002": "Крупные самородки",
    "Building Name Pools/ExtractionFacility_WoodlandArea_Pool009": "Лесорубка",
    "Building Name Pools/Guild_GraveyardWardensGuild_Pool003": "Общество «Естественная посмертная активность",
    "Building Name Pools/Guild_GraveyardWardensGuild_Pool008": "Общество «Продвинутое разложение",
    "Building Name Pools/Guild_GuardsGuild_Pool010": "Надо проверить",
    "Building Name Pools/Guild_JoinersGuild_Pool009": "Общество «Тонкое волокно",
    "Building Name Pools/Guild_PerfumersGuild_Pool005": "Братство «Неугасающий аромат",
    "Building Name Pools/Guild_SmithsGuild_Pool001": "Братство «Упорство всё исправит",
    "Building Name Pools/Guild_TailorsGuild_Pool004": "Братство «Свободная нить",
    "Building Name Pools/Guild_TailorsGuild_Pool010": "Братство уроненной булавки",
    "Building Name Pools/Guild_ThievesGuild_Pool003": "Общество «Потерянных вещей",
    "Building Name Pools/OccupationBuilding_Perfumer_Pool009": "Запах без сожаления",
    "Building Name Pools/PersonalBuilding_BeeHives_Pool014": "Долго живи, плодородная",
    "Building Name Pools/PublicBuilding_PhysiciansOffice_Pool008": "Радость лекаря-пиявочника",
    "Building Name Pools/PublicBuilding_Pillory_Pool006": "Гниль и покаяние",
    "Building Name Pools/PublicBuilding_Prison_Pool002": "Иронические решётки",
    "Building Name Pools/PublicBuilding_Well_Pool002": "Глубокое эхо",
    "Building Name Pools/PublicBuilding_Well_Pool011": "Своё ведро",
    "Building Name Pools/PublicBuilding_WorkersAccommodation_Pool009": "Ветреный шалаш",
    "Building Rooms/Alchemist_MustyCellar_Description": "Под грязью и плесенью: настойки, зелья и неприятности.",
    "Building Rooms/Church_Chancel_Description": "За нужную сумму даже Бог может отвернуться.",
    "Building Rooms/House_Villa_MusicRoom_Description": "Чуть-чуть музыки, чуть-чуть вина и чья-то супруга.",
    "Buildings/Category_OutsideBusiness_DisplayName_pural": "Внешние предприятия",
    "Buildings/Manager_ProfessionBuilding_Description_extra1": "Мастер должен быть членом вашей {CHAR_DYN_C}династии{##} и иметь то же {P_C}ремесло{##}, что и дело, которым он призван управлять.",
    "Buildings/Parameter_Attractiveness_Description": "Делает ваше заведение более привлекательным для клиентов, повышая {PM_ATT_C}спрос{##} и цены в вашей {B_STOFRO_C}лавке{##}.",
    "Buildings/Parameter_BurglaryProtection_Description": "Делает сложнее для преступников {A_ACT_STEITESTOMKT_C}воровать{##} из вашего заведения или {A_ACT_ROB_C}вломиться{##} в него.",
    "Buildings/PublicBuilding_CannonTower_Description_EarlyAccess": "Заброшенная пушечная башня… Под этими обломками может скрываться опасное, но рабочее орудие. Загляните позже в раннем доступе, чтобы узнать, удалось ли что-то раскопать.",
    "Carts/Tier_4_HugeCart_Description": "Шесть мест и {B_HP_MAX_C}самая прочная{##} рама на любой дороге. Вол не {CAR_SPE_C}торопится{##} ни для кого — но когда везёшь столько, в спешке нет нужды.",
    "Challenges/Onboarding_ApplyForOffice_LongDescription": "Докажите, что достойны быть услышанными, и начните подниматься по политической лестнице.\n\nВ левой панели нажмите {HB_POL} Политика, чтобы увидеть иерархию {O_TYP_CIVOFF_C}должностей{##}. Выберите должность, требованиям к которой вы соответствуете, затем {O_ELE_C}подайте заявку на должность{##}.",
    "Challenges/Onboarding_Courtship2_Description": "Заискивайте перед тем, на ком хотите жениться",
    "Challenges/Onboarding_EnterBusiness_LongDescription": "В левой панели нажмите {HB_MYBUS} Моё дело, чтобы центрировать камеру на вашей {B_OCC_SMI_C}кузнице{##}. Затем нажмите на дверную ручку, чтобы войти, или приблизьте, чтобы лучше рассмотреть, и дважды щёлкните по зданию.",
    "Challenges/Onboarding_GuardService_LongDescription": "Вы исполнили свой долг: заплатили налоги и соблюдали правила. К сожалению, преступники редко поступают так же. Если вы хотите более безопасных улиц, станьте на короткое время стражником. Вы заработаете немного денег, напоминая злоумышленникам, что за ними наблюдают.\n\nНа карте найдите {B_PUB_PRI_C}тюрьму{##}. Используйте {HB_FIL} Фильтры, чтобы выделить {B_TYP_PUBBUI_C}городскую инфраструктуру{##}, затем выберите тюрьму и нажмите {A_GUASER_C}«Записаться в стражу»{##}.",
    "Challenges/Onboarding_HaveChild_LongDescription": "После свадьбы у вас может быть {CHAR_HEI_C}ребёнок{##}. {CHAR_CHABABY_C}Шанс{##} зависит от {CHAR_HP_C}здоровья{##} обоих супругов, их возраста и {CHAR_STA_C}мнения{##} друг к другу. Использование {A_PRITIM_C}Провести время наедине{##} может повысить шансы, но это не требуется и не является гарантией.",
    "Challenges/Onboarding_PressCharges_LongDescription": "Вы можете получить {CR_EVI_C}улики{##}, если будете {A_SPY_C}подглядывать{##} за другими, для этого вам понадобится {B_IMP_SPY_C}подзорная труба{##}. Другой способ — {A_SNI_C}сообщать слухи{##} {O_CHA_INF_C}информаторам{##}. Внимательно наблюдая за людьми вокруг и зарабатывая на этом, вы можете обнаружить доказательства преступлений!\n\nКогда у вас появятся улики, откройте раздел {HB_CRIEVI}«Преступления и улики» слева и выберите {A_PRECHA_C}«Предъявить обвинение»{##}.",
    "Challenges/Onboarding_SellFromStorefront_LongDescription": "В вашем предприятии {CC_LMB} щёлкните левой кнопкой по предмету в {B_STOROO_C}складе{##}, затем {CC_LMB} щёлкните по свободному месту в {B_STOFRO_C}витрине{##}, чтобы разместить его там.\n\n{B_PARAM_ATT_C}Привлекательность{##} вашей кузницы периодически привлекает покупателей, чтобы они покупали товары с вашей витрины, так что просто убедитесь, что им есть что купить.",
    "Challenges/Onboarding_UnpauseGame_LongDescription": "В панели управления скоростью вверху экрана используйте кнопку или клавишу (например, пробел), чтобы возобновить игру в комфортном для вас темпе.\n\nВсегда следите за шкалой времени и разделом {HB_UPCOMEVE}Предстоящие события, чтобы быть в курсе {WORHOU_C}рабочих часов{##} ваших рабочих и не пропустить выборы.",
    "Challenges/Onboarding_UpgradeBusiness_LongDescription": "Достигнув требуемого {A_MASGRA_C}уровня мастерства{##} и накопив достаточно средств, вы можете улучшить своё предприятие. Это откроет доступ к новым {A_ACT_C}заданиям{##} для ваших рабочих и, возможно, даст вам новые {A_C}действия{##}.",
    "Challenges/Onboarding_UseItem_LongDescription": "Чтобы использовать предмет, он должен находиться в вашем {HB_INV} {CHAR_PERINV_C}Личном инвентаре{##}. {CC_RMB} Щёлкните по предмету правой кнопкой мыши, чтобы открыть контекстное меню, и выберите «Использовать». Наконец, подтвердите, на ком или на чём вы хотите использовать предмет.",
    "Challenges/Onboarding_VisitTownHall_DisplayName": "Доносчики обогащаются",
    "Challenges/Onboarding_VisitTownHall_LongDescription": "Вы можете получить {CR_EVI_C}улики{##}, {A_SPY_C}подглядывая{##} за другими, но для этого вам понадобится {B_IMP_SPY_C}подзорная труба{##} в вашем доме. В качестве альтернативы вы можете {A_SNI_C}сообщить о слухах{##} {O_CHA_INF_C}информаторам{##}, которые могут принести вам немного денег, а также дать шанс выявить улики преступлений!\n\nВ левой панели нажмите {HB_TOWHAL} {B_PUB_TOWHAL_C}«Ратуша»{##} и выберите {A_SNI_C}«Сообщить о слухах»{##}.",
    "Character/BabyChance_Description": "Показывает вероятность появления {CHAR_HEI_C}наследника{##}. Зависит от {CHAR_FER_C}плодовитости{##} обоих партнёров и качества ваших {CHAR_STA_C}отношений{##}.",
    "Character/Dynasty_Size_DisplayName_alt": "Численность",
    "Character/Health_Description_extra2": "Годы берут своё. {A_PHYCHE_C}Посетите врача{##} или принимайте нужные зелья, чтобы поправить здоровье.",
    "Character/Score_Description": "Очки успеха оценивают успех персонажа на основе его поступков, достижений и целей.",
    "Cities/QuarterPeriodicEffect_Burglary_Description_extra1": "{B_PARAM_BURPRO_C}Незащищенные{##} здания в {QUA_CRI_C}опасных районах{##} — открытое приглашение для воров, которые редко уходят, не забрав что-нибудь или {B_HP_C}не сломав{##} что-нибудь.",
    "Cities/QuarterPeriodicEffect_Illness_Description_extra1": "Инвестируйте в {B_PARAM_HYG_C}санитарию{##}, чтобы держать ситуацию под контролем.",
    "Combat/Attacker_DisplayName_plural": "Нападающие на телегу",
    "Combat/CombatEquipment_Effect_CombatBonus_Description": "Влияет на шанс попадания в цель во время {CO_COM_C}боя{##}.",
    "Combat/Effect_DamageReceivedPercentage_Warning": "Несмотря на это снижение, при расчёте {CO_SCO_C}очков{##} используется полный урон.",
    "Combat/MarginOfVictory_Description": "Показывает, насколько решительным является исход, в зависимости от итогового положения {CO_SCOBAR_C}Шкалы победы{##}. Определяет применяемые эффекты.",
    "Combat/Role_CartAttackers_DisplayName": "Нападающие на телегу",
    "Combat/ScoreBar_Description": "Показывает текущий баланс {CO_SCO_C}очков{##}, цвет соответствует ведущей стороне.",
    "Dialogue/3128832A43C160D02C16DF9FF23E7E6D_642B736C00000000": "Похоже, за вами охотятся. Но решать, что вы должны, — всё ещё за вами.",
    "Dialogue/32F6B4F14E8C3C66A606BCB451AA8430_642B736C00000000": "Виновен. Даже невиновные гибнут, если история достаточно убедительна.",
    "Dialogue/66581370432C2EC60AE96B9A9240D6B5_642B736C00000000": "Виновен. Даже невиновные гибнут, если история достаточно убедительна.",
    "Dialogue/6F1C54404F1293C5A79D0F92ADA66350_642B736C00000000": "На свет появился десятый ребёнок. Какой бы план вы ни имели, он, безусловно, … плодотворен.",
    "Dialogue/716552C5421C685521C8F39B2C07C013_642B736C00000000": "Цветущие рынки, растущие стены — прогресс не стоит на месте.",
    "Dialogue/8D5ECE124FD8651BFDAEBDACEFC089F0_642B736C00000000": "Справедливость обладает удобной слепотой. Одно преступление уходит из памяти и записей.",
    "Dialogue/8EC3D7FA451DE906AB5F8B954AC57E3C_642B736C00000000": "Вы уже на полпути к тому, чтобы почти всё сходило с рук.",
    "Dialogue/9B6C698F48B1E0DB520F138D7AB29898_642B736C00000000": "Похоже, приятный запах — вот что было по-настоящему опасно.",
    "Dialogue/B75ACEB240F966E7686E3DB9DB938577_642B736C00000000": "Возможно, они вернутся с более глубокими карманами — или более удачным моментом.",
    "Dialogue/B7D574B44DBB3EC510F4F5AABFBD90BE_642B736C00000000": "Строительство в разгаре. Не за горами, когда стены будут возведены.",
    "Dialogue/C671587345DD2A864AAA16A0F915C64F_642B736C00000000": "На вашу телегу напали разбойники.",
    "Dialogue/D65874594253AB192062E988E3405B5B_642B736C00000000": "Ваши переговоры пошли лучше. Прибыль должна вырасти.",
    "Dialogue/DC00FCC84E9BCE515BB2D0A38E1DCF2D_642B736C00000000": "Мастер всех ремёсел. Власть, зависть, ожидания — и некому больше произвести впечатление, кроме самого себя.",
    "Effects/Evidence_ChanceToLetOut": "шанс раскрыть {CR_EVI_C}улики{##}",
    "Effects/Evidence_LettingOut_Chance": "{value} шанс выдать {CR_EVI_C}улики{##}",
    "Effects/Health_Increase_Chance_High": "Высокая вероятность улучшения {CHAR_HP_C}Здоровья{##}",
    "Effects/Health_Increase_Chance_Medium": "Средняя вероятность улучшения {CHAR_HP_C}Здоровья{##}",
    "Effects/QuarterCrimeRate_perTurn": "{QUA_CRI_C}Уровень преступности{##} каждый {C_TRN_C}ход{##}",
    "Effects/QuarterDevastation_perTurn": "{QUA_DEV_C}Руины и грязь{##} каждый {C_TRN_C}ход{##}",
    "Effects/QuarterHygiene_perTurn": "{QUA_HYG_C}Санитария{##} каждый {C_TRN_C}ход{##}",
    "Effects/QuarterReputation_perTurn": "{QUA_REP_C}Репутация{##} каждый {C_TRN_C}ход{##}",
    "Effects/QuarterUnrest_perTurn": "{QUA_UNR_C}Недовольство{##} каждый {C_TRN_C}ход{##}",
    "Effects/Renown_perTurn": "{CHAR_REN_C}Слава{##} каждый {C_TRN_C}ход{##}",
    "Effects/Standing_AllOfficeHoldersInYourChamber": "со всеми должностными лицами в вашей {O_CHA_C}палате{##}",
    "Events/Ambush_Description_0": "{P_ROB_C}Разбойники{##} и {P_GUA_C}Стража{##} всегда были смертельными врагами. Вопрос лишь в том, когда ваши усердные {A_ACT_GUAPAT_C}Стражники{##} перехватят банду негодяев, пытающихся нажиться на чужом труде.",
    "Events/Ambush_Description_2": "Ваши люди полны {CHAR_ENE_C}энергии{##} и {I_TYP_COMEQU_C}хорошо вооружены{##}, чтобы разрешить вопрос силой, или вы предпочитаете выждать время и сражаться в другой день?",
    "Events/CharacterAtPillory_Description_1": "Говорят, что преступников, признанных виновными, помещают в позорный столб вместо {ST_CHR_IMP_C}заключения{##}, чтобы горожане могли выразить своё недовольство любым удобным для них способом. Это, вероятно, также поможет сократить расходы на содержание {B_PUB_PRI_C}тюрьмы{##}.",
    "Events/CourtingCandidateDied_Description_3": "Вы говорите себе это с горечью, потому что альтернатива — признать, как сильно вы бы любили их.",
    "Events/DynastyHeadWithHeirDied_Option_01": "Продолжить как наследник",
    "Events/GameChallenge_Description_1": "Вы пытаетесь вспомнить, разрешены ли сейчас азартные игры, но отбрасываете эту мысль, когда незнакомец неуклюже бросает кости перед вами, бормоча что-то вроде",
    "Events/Hassle_Description_2": "Если у вас больше {CHAR_ENE_C}энергии{##}, чем рассудка, рискните. Никогда не знаешь, что из этого выйдет.",
    "Events/InjuryVictim_Description_1": "Надеюсь, ваши оставшиеся {CHAR_HP_C}здоровье{##} и {CHAR_ENE_C}энергия{##} позволяют вам продолжать повседневные дела.",
    "Events/Onboarding_BeeHives_Description_00": "Вы только послушайте, что эти парни в таверне сегодня несут? Нет? Вы их не слышали?",
    "Events/Onboarding_CraftAndSell_Description_02": "Вам, вероятно, стоит начать с них. Изготовьте несколько фурнитур и продайте их на рынке. Кому-то они скоро понадобятся, даже если сам того ещё не знает.",
    "Events/Onboarding_PrivateTimeAndHeirs_Description_03": "Дело может наполнить ваш кошелёк, а титул — поднять репутацию, но наследник придаёт всему этому цель. Тот, кто несёт имя рода, унаследует то, что вы создали, и, возможно, приумножит.",
    "Events/Onboarding_Purge_B2_Description_01": "Теперь накоплено впечатляющее количество улик, подтверждающих неправомерные деяния города.",
    "Events/OpponentConvicted_Description_2": "Некоторые люди сближаются, разделяя ненависть к одному человеку, — а можно и попытаться встать на их защиту: это будет плюсом, если они смогут отблагодарить вас в будущем.",
    "Events/OpponentDied_Description_1": "Говорят, будет грандиозная процессия в церковь: гербовые знамёна, священники, свечи — и столько торжественности, что хватило бы, чтобы разорить поскромнее хозяйство. Разумеется, это не просто похороны. Это манифест. Старый глава, возможно, ушёл, но династия — если кто-то сомневался — на удивление жива.",
    "Events/RelicAcquisition_Description_0": "Уставший паломник падает без сил у вашего дома. С хрипом он шепчет, что нёс священную реликвию через полхристианского мира, чтобы доставить её в далёкий монастырь.",
    "Events/RelicAcquisition_Option_Deliver_Result_Target": "Благочестивая душа обрела священную реликвию и доставила её в монастырь. Чудо!",
    "Events/RelicDiscovery_Option_LeaveIt_Result_Instigator": "Некоторые вещи сохраняются веками именно потому, что никто не лезет к ним. Вы вернули реликвию в то место, где она была спрятана.",
    "Events/ShadyCharacter_Description_0": "Городские стражники были подслушаны, когда шутили о некоторых подозрительных личностях, шныряющих по городу и похожих на самых жалких преступников в истории.",
    "Events/StrangeItemFound_Option_DonateCity_Result_Instigator": "Ваше чувство гражданского долга взяло верх. Члены {O_CHA_TOWCOU_C}Городского совета{##} довольны.",
    "Events/ThugsVandalizingYourHouse_Description": "По дороге домой вы увидели, что вашу дверь осквернили. Грубые надписи и позорные слова размазаны по стенам — дело рук бандитов, которые уже скрылись.",
    "Events/TutorialBusiness_Description_0": "Добро пожаловать в наш прекрасный город! Человек вашей {P_C}профессии{##} найдёт здесь немало возможностей. Делайте это хорошо, чтобы заработать {CHAR_MON_C}деньги{##}, профессиональное {P_MASGRA_C}мастерство{##}, {CHAR_WEA_C}богатство{##} и {CHAR_REN_C}славу{##}, чтобы подняться по {T_C}ступеням общества{##}.",
    "Events/TutorialBusiness_Description_2": "{A_OVEPRO_C}Надзор за работой{##}, чтобы мотивировать ваших сотрудников работать быстрее. Если их {CHAR_STA_C}Мнение{##} о вас слишком низко, они могут уйти; если они будут перегружены, они станут подвержены {B_PARAM_ACCPRO_ACC}Происшествиям{##}.",
    "Events/TutorialCrime_Description_0": "Городские {L_LAW_C}Законы{##} влияют на то, как вы ведёте дела и взаимодействуете с {CHAR_CITVSWOR_C}Горожанами{##}. В Своде законов указано, какие действия считаются {CR_CRI_C}Преступлениями{##} по действующим нормам, хотя всем известно, что законы можно гнуть.",
    "Events/TutorialCrime_Description_3": "Если вас признают виновным, вы будете наказаны в соответствии с {CR_SEV_C}тяжестью{##} вашего преступления. Вас могут заставить выплатить крупный штраф, но также могут {ST_CHR_IMP_C}заключить в тюрьму{##} или даже приговорить к смертной казни. Трудно сказать, что из этого принесёт больше позора вашему доброму имени.",
    "Events/TutorialFamily_Description_1": "{CHAR_COU_C}Начните ухаживать{##} за наиболее подходящим кандидатом с учётом возраста, {CHAR_HP_C}здоровья{##}, {CHAR_WEA_C}богатства{##}, {S_C}навыков{##} и других желательных качеств. {A_SENGIF_C}Дарите подарки{##} избраннику и {A_SWETAL_C}льстите{##} ему, чтобы скорее добиться помолвки, иначе вас могут опередить более убедительные ухажёры.",
    "Events/TutorialFamily_Description_2": "Свадебная церемония состоится в {C_TRN_C}ход{##}, следующий за вашей успешной помолвкой. После этого у вас могут родиться дети в зависимости от вашей {CHAR_FER_C}плодовитости{##}, ваших {A_PRITIM_C}усилий в браке{##} и внимания, отвлечённого на {A_SPOHEL_C}другие дела{##}. Возможно, стоит рассмотреть усыновление сироты-другой, просто на всякий случай.",
    "Events/WaylayingIntercepted_Description_0": "Ваши головорезы перехватили телегу неподозревающего о подвохе торговца. Надеюсь, телега не {A_HIRESC_C}сопровождена{##} способными защитниками, которые доставят неприятности вашим хорошо обученным головорезам.",
    "Events/WaylayingIntercepted_Option_Attack": "Атакуйте телегу. Разбойники грабят.",
    "Events/WaylayingIntercepted_Option_Attack_Outcome_Instigator": "Вы решили напасть на перехваченную телегу. Вам лучше, чтобы ваши наёмники были хорошо обучены, отдохнули и снаряжены.",
    "General/Chronicle_DisplayName_WithNumber": "Хроника (NumChronicle)",
    "General/ComingSoon_Warning_Description_Item": "Все предметы доступны для торговли, но некоторые пока нельзя использовать. В некоторых случаях их эффекты могут отражать лишь малую часть того, что мы в итоге для них запланировали.",
    "General/MaintenancePhase_BalancePersonal_Description_extra1": "Слишком долгий отрицательный баланс приведёт к {ST_CHR_IMP_C}Заключению в тюрьму{##}.",
    "General/PriceModifier_Attractiveness_Description": "Более высокая {B_PARAM_ATT_C}привлекательность для покупателей{##} привлекает больше покупателей, что повышает стоимость товаров.",
    "General/PriceModifier_StorefrontRevenue_Description": "Обслуживание настолько хорошо, что покупатели охотно платят немного больше при покупке напрямую у вас. Этот бонус рассчитывается как процент от {B_PARAM_STOREV_C}цены продажи{##}.",
    "General/VictoryCondition_Office_Description": "Быть избранным на {icon_n} {O_TYP_CIVOFF_C}должность{##}",
    "HistoricalEvents/JobstOfMoravia_Description_extra1": "Маркграф Моравии и Бранденбурга, избранный {H_KINOFROM_C}Королём римлян{##} в 1410 году. Кузен {H_SIG_C}Сигизмунда Люксембургского{##}. Скончался в {H_BRN_C}Брно{##} при подозрительных обстоятельствах всего через три месяца после избрания.",
    "HistoricalEvents/Kuttenberg_1419_Description": "Вера воюет сама с собой. {H_WEN_C}Король Вацлав{##}, под давлением брата {H_SIG_C}Короля Сигизмунда{##} и папы, приказал восстановить католических священников в {H_HUS_C}гуситских{##} приходах. В ответ огромная процессия гуситов штурмовала Новую ратушу в {H_PRA_C}Праге{##} и выбросила бургомистра и его католических советников на копья толпы внизу. Десятки тысяч людей в волнении по всему королевству!",
    "HistoricalEvents/Kuttenberg_1419_Description_extra1": "Пришли вести о том, что сам король Вацлав скончался; некоторые даже говорят, что от удара он умер на месте. Единственным наследником короны является Сигизмунд, но гуситы не забыли, как он предал {H_HUS_C}Яна Гуса{##} огню, и скорее сожгут королевство, чем склонят перед ним колени.",
    "HistoricalEvents/Kuttenberg_1422_Description_extra1": "Говорят, что Жижку ведёт голос самого Бога, и он остаётся непобеждённым. Ранее в этом году он разгромил вторую крестоносную армию под {H_NEM_C}Нёмекским Бродом{##}, недалеко от {H_KUT_C}Куттенберга!{##} Король Сигизмунд бежал с поля боя, а его люди сотнями утонули, переправляясь через замёрзшую реку.",
    "HistoricalEvents/Sigismund_Description_extra2": "Сигизмунд был многоязычным и харизматичным лидером. Он созвал {H_COUOFCON_C}Констанцский собор (1414–1418){##}, чтобы положить конец Западному расколу, разделившему Церковь на сорок лет. На том же соборе он позволил сжечь {H_JANHUS_C}Яна Гуса{##}, несмотря на собственное обещание безопасного прохода. {H_HUS_C}Гуситы{##} восприняли это как предательство, и оно стоило ему чешской короны более чем на два десятилетия. Лишь в 1436 году, за год до смерти, гуситы наконец признали его королём Чехии, и только после того, как {H_COMOFBAS_C}Базельские компактаты{##} предоставили их вере частичное признание.",
    "Items/CombatEquipment_Weapon_Handgun_DisplayName": "Ручная пушка",
    "Items/InventoryItem_CrystalBall_Description": "Вглядитесь достаточно долго, и будущее откроется вам — именно так, как тому и суждено.",
    "Items/InventoryItem_HandcraftedBible_DisplayName": "Рукописная Библия",
    "Items/InventoryItem_RoyalJelly_Description": "Годится королеве — и отлично подходит для изнемогающего улья.",
    "Items/Potion_ParalysingPoison_Description": "Оно проникает в вены, пока даже поднять руку не будет казаться движением камня.",
    "Items/Potion_PerfumeOfRomance_Description": "Душистый, дурманящий — сердца теплеют, разум холодеет. Немногие устоят, да и не должны.",
    "Items/RawMaterial_RowanBerry_Description": "Говорят, её красные ягоды отводят блуждающие огни.",
    "Items/Resource_MeterOfBeechwood_DisplayName": "Метр бука",
    "Items/Resource_MeterOfOakwood_DisplayName": "Метр дуба",
    "Items/Resource_MeterOfPinewood_DisplayName": "Метр сосны",
    "Items/Type_FinishedProduct_Book_Description": "Знание редко бывает опасным — пока не станет.\nПроверьте закон о {L_BOOLAM_C}Книгах и пасквилях{##} перед тем, как перевернуть страницу.",
    "Items/Type_FinishedProduct_DisplayName_plural": "Готовые товары",
    "Items/Type_Ingredient_IntermediateProduct_Description": "Изделия, используемые как ингредиенты для дальнейшего {A_ACT_CRA_C}изготовления{##}.",
    "Items/Type_Ingredient_RawMaterial_Description_extra1": "Они могут быть {A_ACT_GATING_C}встречены в природе{##} или получены из {I_TYP_RES_C}ресурсов{##} на {B_TYP_EXTFAC_C}Месторождениях{##}.",
    "Notifications/Action_SeekCure_Message": "Врач дал вам бурлящее зелье — «детокс», сказала она. Вкус отвратительный, но средство подействовало.",
    "Notifications/Action_ShootCannonBallFire_Instigator_Success_Message": "Огонь быстро разгорелся. В огне оказались следующие места",
    "Notifications/Action_UnlockProfession_Tailor_Message": "Мы, гильдия портных, выдаём вам лицензию. Шейте то, что другие будут носить, — и по нему вас будут судить.",
    "Notifications/Action_UnlockProfession_TravellingEntertainer_Message": "Вашу труппу признали. Не отводите от них взгляд — и заработайте их монету.",
    "Notifications/Courtship_Fail_Death_Header": "Ваша возлюбленная была унесена… Смертью",
    "Notifications/Courtship_Fail_Death_Message": "К сожалению, {charactername} больше нет, и ваш роман оборвался без церемоний.",
    "Notifications/Duel_Cancelled_DeathOfOpponent_Header": "Спасён Жнецом",
    "Notifications/Evidence_ErasedByTrial_Message": "{charactername} предстанет перед {CR_TRI_C}судом{##} за свои преступления. Поскольку дело передано в суд, вы больше не можете использовать эту {CR_EVI_C}улику{##} против него.",
    "Notifications/Evidence_Expiring_Header": "Время для справедливости истекает",
    "Notifications/Manager_NotSpendingMoney_Header": "Не хватает монет, ваш хозяин остановил траты",
    "Notifications/Marriage_Opponent_Success_Header": "Ваш соперник только что женился",
    "Notifications/StorefrontSale_Message": "Товары стоимостью {value}{CHAR_MON} были проданы на {buildingtype}.",
    "Notifications/UseItem_Target_StinkBomb_Success_Message": "Кто-то применил зловонную бомбу против вашего заведения {BuildingName}. {amount} рабочих покинули его из-за отвратительного запаха.",
    "Notifications/Worker_Death_Message": "К сожалению, {workername} больше нет. Его отсутствие будет чувствоваться в {buildingtype}.",
    "Notifications/Worker_QuitAboutTo_LowStanding_Message": "Поводок верности ваших рабочих слабеет. Подумайте о том, чтобы {WOR_TRE_C}обращаться{##} с ними лучше — или {A_PAYONETIMBON_C}платить{##} им лучше — прежде чем они уйдут на службу к другим.",
    "Politics/CivicOffice_Informants_CouncilInformer_DisplayName": "Донеситель совета",
    "Politics/Crime_Description": "{L_LAW_C}Незаконные{##} деяния, которые могут оставить {CR_EVI_C}улики{##}. Преступления различаются по тому, насколько легко они {CR_NOI_C}сокрыты{##} и насколько они {CR_SEV_C}тяжки{##}.",
    "Politics/Crime_Noisiness_Loud_DisplayName": "Заметность",
    "Politics/Crime_Severity_Extreme_Description": "В случае поимки за особо тяжкие преступления выносится {CR_TRIPUN_EXT_C}смертный приговор{##}.",
    "Politics/Crime_StatuteOfLimitations_Description": "У правосудия есть срок давности. После совершения {CR_CRI_C}преступления{##} начинается отсчёт. По истечении срока давности все связанные {CR_EVI_C}улики{##} утрачиваются и больше не могут быть использованы.",
    "Politics/HonoraryOffice_PrinceOfThieves_DisplayName": "Князь воров",
    "Politics/Law_Bribery_Description": "Распространённый способ завоевать расположение тех, кто может отправить вас в тюрьму, или проложить путь к высшим должностям.",
    "Politics/Law_ImportDuty_DisplayName": "Ввозная пошлина",
    "Politics/Law_Insult_DisplayName": "Оскорбление",
    "Politics/Law_Torture_DisplayName": "Пытка",
    "Politics/Office_EligibilityOfCharacter_DisplayName": "Пригодность {Title} {FirstName} {LastName}",
    "Politics/Office_TermsInOffice_DisplayName": "Сроки полномочий",
    "Professions/Profession_Alchemist_Description": "Ведёт {B_OCC_ALC_C}лабораторию алхимика{##}, исследуя смеси, лекарства и зелья для {A_ACT_PESCON_C}отпугивания вредителей{##}.",
    "Professions/Profession_Description": "Ремесло определяет ваш промысел и то, каким {B_TYP_OCCBUI_C}производственным зданием{##} вы можете управлять.",
    "Professions/Profession_Guard_Description": "Руководит охраной и командует {B_OCC_GUA_C}казармой{##}, организуя {A_ACT_GUAPAT_C}патрули{##} и поддерживая порядок.",
    "Professions/Profession_Innkeeper_Description": "Владеет и управляет {B_OCC_INN_C}таверной{##}, принимая путников, {A_ACT_FINEVIFROWOR_C}собирая сплетни{##} и организуя {A_ACT_LIVPER_C}развлечения{##}.",
    "Professions/Profession_Joiner_Description": "Руководит {B_OCC_JOI_C}мастерской столяра{##}, снабжая деревянными инструментами и {A_ACT_IMPCAR_C}прочными телегами{##}.",
    "Professions/Profession_MoneyLender_Description": "Управляет займами, долгами и финансовыми делами из {B_OCC_MONLEN_C}Счётной конторы{##}.",
    "Professions/Profession_Perfumer_Description": "Ведёт {B_OCC_PER_C}парфюмерную мастерскую{##}, производя ароматы и благовония, которые {A_ACT_SCEENH_C}привлекают клиентов{##}.",
    "Professions/Profession_Smith_Description": "Владеет и управляет {B_OCC_SMI_C}кузницей{##}, производя металлические изделия, инструменты и оружие. Устанавливает решётки на окна, чтобы {A_ACT_STRBAR_C}укрепить защиту{##}, заставляя потенциальных грабителей серьёзно задуматься.",
    "Professions/Profession_StoneMason_Description": "Руководит каменными работами и управляет {B_OCC_MAS_C}каменным делом{##}, {A_ACT_IMPQUA_C}улучшая дороги{##} и {A_ACT_IMPSTR_C}возводя фундаменты{##}.",
    "Professions/Profession_Tailor_Description": "Ведёт {B_OCC_TAI_C}мастерскую портного{##}, создавая одежду и {A_ACT_SEWCLO_C}улучшая наряды{##} для платёжеспособных клиентов.",
    "Professions/Profession_Thief_Description": "Руководит тайными операциями из скрытого {B_OCC_THI_C}Скрытого убежища{##}, организуя {A_ACT_PICPOCCIT_C}мошенничество{##}, {A_ACT_PICPOC_C}кражи{##} и {A_ACT_FRAFORCRI_C}подброшенные улики{##}.",
    "Professions/Profession_Trader_Description": "Управляет торговлей и следит за товарами, хранящимися в {B_OCC_TRA_C}Складе{##}.",
    "Professions/Profession_TravellingEntertainer_Description": "Руководит выступлениями и управляет {B_OCC_ENT_C}Лагерем бродячего цирка{##}, привлекая толпу зрелищами и развлечениями.",
    "Professions/XP_Description_extra1": "Если вы хотите ускорить это развитие, запишитесь на {A_TRAPRO_C}специальную подготовку{##} в {B_GUILD_C}гильдии{##}, чтобы быстрее получить {A_MASGRA_C}Ступень мастерства{##}.",
    "Professions/XP_ProgressBar_Description": "Когда шкала прогресса заполнится, отправляйтесь в {B_GUILD_C}гильдии{##} и повысьтесь до следующей {P_MASGRA_C}Ступени мастерства{##} в своей профессии.",
    "Status Effects/ArtisanalGoods_Description": "Обаяние — фирменная черта дома. Оно хорошо сочетается со всем, что есть в меню.",
    "Status Effects/BackstoryFamilyCraft_Description": "Зачем тратить время на разговоры, если есть работа? Отличная философия — пока кто-то не попросит их привести убедительные аргументы.",
    "Status Effects/BackstoryHouseOfScholars_DisplayName": "Всезнайки",
    "Status Effects/BadlyMaintained_Worsens": "Если {B_HP_C}прочность{##} упадёт ниже {value}%, здание станет {ST_STR_TERMAI_C}обветшалым{##}.",
    "Status Effects/BadlyMaintainedCart_Worsens": "Если {B_HP_C}прочность{##} упадёт ниже {value}%, телега станет {ST_CAR_TERMAI_C}запущенной{##}.",
    "Status Effects/BeeHoneyComb_Full_DisplayName": "Течёт мёдом",
    "Status Effects/BeeStung_Description": "Один глаз отёк и закрылся, щёки раздулись вдвое, но вы бы видели другого.",
    "Status Effects/BeeStung_DisplayName": "Избитый пчёлами",
    "Status Effects/Cart_WorkedOn_Description": "Столяры устраняют щепки и упрямые гвозди. Скоро она снова покатится.",
    "Status Effects/CrimeRate_Severe_DisplayName": "Тяжкий",
    "Status Effects/Dead_Description": "Я был, ты будешь.",
    "Status Effects/Illness_BubonicPlague_DisplayName": "Бубонная чума",
    "Status Effects/Infamous_DisplayName": "Зловещий",
    "Status Effects/LaughingStock_Cause_01": "Проиграна {A_DUECHA_C}дуэль{##}",
    "Status Effects/ProtectedBuilding_Description": "Здесь безопасно благодаря тем, кому не стоит отказывать.",
    "Status Effects/Quarter_CrimeRate_Tier01_DisplayName": "Сомнительный",
    "Status Effects/Quarter_Devastation_Tier00_Worsens": "Город благоустроен и свободен от чумы.\nЕсли {QUA_DEV_C}Грязь и разруха{##} превысит {value}, квартал станет {ST_QTR_DEV_1_C}плохо ухоженным{##}.",
    "Status Effects/Quarter_Devastation_Tier02_Description": "В углах копится мусор, брусчатка расползается. Квартал выглядит изношенным и усталым.",
    "Status Effects/Quarter_Zeal_Tier02_DisplayName": "Религиозно пылкий",
    "Status Effects/ScentEnhanced_DisplayName": "Привлекательный",
    "Status Effects/StoneChestDisplayed_Description": "Открыть его занимает больше времени, чем могут позволить себе большинство воров.",
    "Status Effects/StoneChestDisplayed_DisplayName": "Богатство Каменного стража",
    "Workers/AssignWorkers_Prompt": "Назначьте рабочих на задачи. Если для задачи требуются материалы, убедитесь, что они есть в {B_STOROO_C}складе{##}.",
    "Workers/Employment_Innkeeper_Description": "Обслуживает гостей и выполняет обязанности в {B_OCC_INN_C}таверне{##}.",
    "Workers/Employment_Perfumer_Description": "Создаёт ароматы, масла и товары с ароматом в {B_OCC_PER_C}Парфюмерии{##}.",
    "Workers/Employment_Preacher_Description": "Служит верующим в {B_OCC_PRE_C}церкви{##}.",
    "Workers/Employment_Smith_Description": "Выковывает металлические изделия и оружие в {B_OCC_SMI_C}кузнице{##}.",
    "Workers/Employment_StoneMason_Description": "Рубит и приручает упрямый камень для дорожных работ, фундаментов и прочего в {B_OCC_MAS_C}Каменном деле{##}.",
    "Workers/Employment_Tailor_Description": "Шьёт одежду и наряды в {B_OCC_TAI_C}мастерской портного{##}.",
    "Workers/Employment_Thief_Description": "Крадёт ценности и действует скрытно из {B_OCC_THI_C}скрытого убежища{##}.",
    "Workers/Employment_TravellingEntertainer_Description": "Выполняет трюки и развлекает публику из {B_OCC_ENT_C}лагеря бродячего цирка{##}.",
    "Workers/Rank_Description": "Каждый рабочий начинает карьеру учеником и может быть {A_PROWOR_C}повышен{##} до подмастерья по мере набора {CHAR_XP_C}опыта{##}.\nПодмастерья более {CHAR_PRO_C}эффективны{##}, реже попадают в {B_PARAM_ACCPRO_ACC}несчастные случаи{##}, но требуют более высокой оплаты.",
}

SKILL_REPAIRS_BY_KEY: dict[str, str] = {
    "Actions/ImproveSkill_Stealth_Button": "Улучшить хитрость",
    "Actions/TrainSkill_Combat_DisplayName": "Улучшить боевое мастерство",
    "Actions/TrainSkill_Combat_Family_Description": "Выберите члена семьи и вложите оружие ему в руки. Пусть он улучшит своё {S_COM_C}боевое мастерство{##}, пока оно ему ещё не понадобилось.",
    "Actions/TrainSkill_Combat_Family_DisplayName": "Обучение семьи боевому мастерству",
    "Actions/TrainSkill_Handicraft_Family_Description": "Выберите члена семьи и дайте ему инструменты для практики своего {S_HAN_C}ремесла{##}. Вы не всегда будете рядом, чтобы всё чинить.",
    "Actions/TrainSkill_Handicraft_Family_DisplayName": "Обучение семьи ремеслу",
    "Actions/TrainSkill_Negotiation_Family_Description": "Выберите члена семьи и научите его уверенно держаться за столом переговоров, улучшив его {S_NEG_C}навыки переговоров{##}.",
    "Actions/TrainSkill_Negotiation_Family_DisplayName": "Обучение семьи переговорам",
    "Actions/TrainSkill_Rhetoric_Description": "Научитесь говорить так, чтобы вас слушали, и улучшите свою {S_RHE_C}риторику{##}.",
    "Actions/TrainSkill_Rhetoric_DisplayName": "Улучшить риторику",
    "Actions/TrainSkill_Rhetoric_Family_DisplayName": "Обучение семьи риторике",
    "Actions/TrainSkill_Stealth_Description": "Станьте искуснее в том, что не должно замечаться, и улучшите свою {S_STE_C}хитрость{##}.",
    "Actions/TrainSkill_Stealth_DisplayName": "Улучшить хитрость",
    "Actions/TrainSkill_Stealth_Family_Description": "Выберите члена семьи и покажите ему, как упражняться в {S_STE_C}хитрости{##}, не попадаясь при этом.",
    "Actions/TrainSkill_Stealth_Family_DisplayName": "Обучение семьи хитрости",
    "Actions/TrainSkill_Stealth_Prompt": "Обучение хитрости",
}

GLOSSARY_BY_KEY.update(SKILL_REPAIRS_BY_KEY)

CACHE_COLLISION_REPAIRS_BY_KEY: dict[str, str] = {
    "Actions/ApplyForOffice_Button_Apply": "Подать заявку на должность",
    "Actions/Courting_SendGift_Description": "Вдумчивый выбор быстрее покоряет сердца, чем пустая лесть.",
    "Actions/Torture_DisplayName": "Пытать пленника",
    "Buildings/OccupationBuilding_Alchemist_III_DisplayName": "Кабинет алхимика",
    "Buildings/OccupationBuilding_Tailor_III_DisplayName": "Мастерская по пошиву мантий",
    "Buildings/PublicBuilding_Blackmarket_DisplayName_plural": "Чёрные рынки",
    "Cities/Nurnberg_Quarter_02_DisplayName": "Городская площадь",
    "Combat/MarginOfVictory_MinorDefeat_Description": "Вы терпите малое поражение.",
    "Combat/MarginOfVictory_MinorVictory_Description": "Вы одерживаете малую победу.",
    "Effects/Standing_RandomCitizens": "{CV_({Value})} {CHAR_STA_C}мнение{##} о {NumCitizens} {NumCitizens}|plural(one=случайном {CHAR_CITVSWOR_C}горожанине{##},other=случайных {CHAR_CITVSWOR_C}горожанах{##})",
    "Items/Potion_DraughtOfThe7thSense_Description": "Ощущение, будто небеса списали ваши грехи.",
    "Items/Potion_FragranceOfComplaisance_DisplayName": "Аромат угодливости",
    "Items/Potion_FragranceOfBlindJealousy_DisplayName": "Аромат слепой ревности",
    "Politics/OfficePosition_ApplyForPosition_Prompt": "Подать заявку на должность",
    "Seasons/StartTurn_Year_Season_Spring": "Перейти к весне {YEAR}",
    "Seasons/StartTurn_Year_Season_Summer": "Перейти к лету {YEAR}",
    "Status Effects/Quarter_Devastation_Tier02_DisplayName": "В плохом состоянии",
    "Workers/AccidentChance_Effect_Base_DisplayName": "Врождённый риск",
    "Workers/Employment_Alchemist_Apprentice_DisplayName": "Ученик-алхимик",
    "Workers/Employment_Alchemist_Journeyman_DisplayName": "Подмастерье-алхимик",
    "Workers/Employment_GraveyardWarden_Apprentice_DisplayName": "Ученик смотрителя кладбища",
    "Workers/Employment_GraveyardWarden_Journeyman_DisplayName": "Подмастерье смотрителя кладбища",
    "Workers/Employment_Guard_Apprentice_DisplayName": "Ученик стражника",
    "Workers/Employment_Guard_Journeyman_DisplayName": "Подмастерье стражника",
    "Workers/Employment_Innkeeper_Apprentice_DisplayName": "Ученик трактирщика",
    "Workers/Employment_Innkeeper_Journeyman_DisplayName": "Подмастерье трактирщика",
    "Workers/Employment_Joiner_Apprentice_DisplayName": "Ученик столяра",
    "Workers/Employment_Joiner_Journeyman_DisplayName": "Подмастерье столяра",
    "Workers/Employment_MoneyLender_Apprentice_DisplayName": "Ученик ростовщика",
    "Workers/Employment_MoneyLender_Journeyman_DisplayName": "Подмастерье ростовщика",
    "Workers/Employment_Perfumer_Apprentice_DisplayName": "Ученик-парфюмер",
    "Workers/Employment_Perfumer_Journeyman_DisplayName": "Подмастерье-парфюмер",
    "Workers/Employment_Preacher_Apprentice_DisplayName": "Ученик проповедника",
    "Workers/Employment_Preacher_Journeyman_DisplayName": "Подмастерье проповедника",
    "Workers/Employment_Robber_Apprentice_DisplayName": "Ученик-разбойник",
    "Workers/Employment_Robber_Journeyman_DisplayName": "Подмастерье-разбойник",
    "Workers/Employment_Smith_Apprentice_DisplayName": "Ученик-кузнец",
    "Workers/Employment_Smith_Journeyman_DisplayName": "Подмастерье-кузнец",
    "Workers/Employment_StoneMason_Apprentice_DisplayName": "Ученик-каменщик",
    "Workers/Employment_StoneMason_Journeyman_DisplayName": "Подмастерье-каменщик",
    "Workers/Employment_Tailor_Apprentice_DisplayName": "Ученик-портной",
    "Workers/Employment_Tailor_Journeyman_DisplayName": "Подмастерье-портной",
    "Workers/Employment_Thief_Apprentice_DisplayName": "Ученик-вор",
    "Workers/Employment_Thief_Journeyman_DisplayName": "Подмастерье-вор",
    "Workers/Employment_Trader_Apprentice_DisplayName": "Ученик-торговец",
    "Workers/Employment_Trader_Journeyman_DisplayName": "Подмастерье-торговец",
    "Workers/Employment_TravellingEntertainer_Apprentice_DisplayName": "Ученик-циркач",
    "Workers/Employment_TravellingEntertainer_Journeyman_DisplayName": "Подмастерье-циркач",
    "Workers/Salary_Total_Tooltip": "Общая сумма зарплат, выплаченных в этом здании.",
    "Workers/Rank_Apprentice_DisplayName": "Ученик",
    "Workers/Rank_Journeyman_DisplayName": "Подмастерье",
    "Actions/Dinner_Level_03": "Посредственный",
    "Actions/TrainProfession_DisplayName": "Обучение профессии",
    "Buildings/ConstructionScreen_ForSale_DisplayName": "На продажу",
    "Buildings/OccupationBuilding_Graveyard_III_DisplayName": "Церковный двор",
    "Buildings/OccupationBuilding_Mason_I_DisplayName": "Каменщик",
    "Buildings/OccupationBuilding_Mason_II_DisplayName": "Каменорез",
    "Buildings/OccupationBuilding_Perfumer_I_DisplayName": "Варильня",
    "Buildings/Parameter_Attractiveness_DisplayName": "Привлекательность для клиентов",
    "Buildings/Requirement_Upgrade_BuildingLevel_2": "Здание II",
    "Combat/MarginOfVictory_MajorDefeat_DisplayName": "Крупное поражение",
    "Combat/MarginOfVictory_MajorVictory_DisplayName": "Крупная победа",
    "Combat/NoneEquipped_Armor_Label": "Без доспехов",
    "Combat/Phase_Melee_DisplayName": "Ближний бой",
    "Combat/Phase_Melee_DisplayName_alt": "Фаза ближнего боя",
    "Combat/Order_Passive_DisplayName": "Пассивная оборона",
    "Combat/ScoreBar_DisplayName": "Шкала победы",
    "Cities/District_DisplayName": "Район",
    "Cities/District_DisplayName_plural": "Районы",
    "Cities/Freiburg_Quarter_08_DisplayName": "Холмистые поля",
    "Items/IntermediateProduct_Fabric_DisplayName": "Ткань",
    "Items/IntermediateProduct_Fittings_DisplayName": "Фурнитура",
    "Items/InventoryItem_CannonBallDung_DisplayName": "Зловонное пушечное ядро",
    "Items/RawMaterial_Flax_DisplayName": "Лён",
    "Politics/Crime_Evidence_DisplayName": "Доказательства",
    "Politics/Crime_Severity_Minor_DisplayName": "Незначительное",
    "Politics/Crime_Severity_Trivial_DisplayName": "Мелкое",
    "Politics/Law_FinancialLaws_DisplayName": "Финансовые законы",
    "Professions/ProfessionRequired_Header": "Профессия",
    "Seasons/StartTurn_Year_Season_Autumn": "Перейти к осени {YEAR}",
    "Workers/EmployeeManagement_Header_DisplayName": "Управление рабочими",
    "Workers/EmployeeManagement_HireEmployee_Prompt": "Нанять нового рабочего",
    "Workers/Productivity_Effect_Base_DisplayName": "Врождённая производительность",
    "Workers/TaskContextMenu_DisplayName": "Меню задач",
    "Buildings/Requirement_Upgrade_BuildingLevel_1": "Здание I",
    "Combat/NoneEquipped_Weapon_Label": "Без оружия",
    "Combat/Result_Victory_DisplayName": "Победа",
    "Items/IntermediateProduct_Ethanol_DisplayName": "Этанол",
    "Politics/CivicOffice_TownBailiffs_FencingMaster_DisplayName": "Мастер фехтования",
    "Seasons/EndOfTurn_Year_Season_Autumn": "Осень {YEAR} завершилась",
    "Settings/Option_Normal": "Обычный",
    "Workers/Treatment_Standard_DisplayName": "Стандартный",
    "Actions/Espionage_DisplayName": "Шпионить",
    "Professions/ProfessionRequired_level_Header": "Профессия",
    "Buildings/StoreroomSlots_Count": "{NumStorage} {B_STOROO_C}{NumStorage}|plural(one=ячейка склада,other=ячеек склада){##}",
    "Carts/CargoSlot_Num": "{NumCargoSlots} {NumCargoSlots}|plural(one=грузовой отсек,other=грузовых отсеков)",
    "Character/DynastyMembersCount_NoneDead": "{NumAll} {NumAll}|plural(one=член семьи,other=членов семьи)",
    "Character/DynastyMembersCount_SomeDead": "{NumAlive}/{NumAll} {NumAll}|plural(one=член семьи,other=членов семьи) ({NumDead}{deadicon})",
    "Cities/NumDynasties": "{NumDynasties} {NumDynasties}|plural(one={CHAR_DYN_C}династия{##},other={CHAR_DYN_C}династий{##})",
    "Cities/NumPlayers": "{NumPlayers} {NumPlayers}|plural(one=игрок,other=игроков)",
    "Combat/Combatants_Count": "{NumCombatants} {CO_COM_C}{NumCombatants}|plural(one=участник боя,other=участников боя){##}",
    "Effects/Target_RandomOfficials": "{NumRandomOfficials} {NumRandomOfficials}|plural(one=случайный чиновник,other=случайных чиновников)",
    "Effects/Target_RandomOfficialsOfOppositeSex": "{NumRandomOfficials} {NumRandomOfficials}|plural(one=случайный чиновник противоположного пола,other=случайных чиновников противоположного пола)",
    "General/Turn_NumberOfTurns": "{NumTurns} {C_TRN_C}{NumTurns}|plural(one=ход,other=ходов){##}",
    "Items/Durability_CombatEncounters": "{NumCombat} {CO_COMENC_C}{NumCombat}|plural(one=сражение,other=сражений){##}",
    "Buildings/Category_Dwelling_DisplayName_pural": "Жилые здания",
    "Buildings/Category_Residence_DisplayName": "Жилые дома",
    "Buildings/OccupationBuilding_Trader_II_DisplayName": "Большой склад",
    "Buildings/PersonalBuilding_BeeHives_DisplayName": "Пасека",
    "Buildings/PersonalBuilding_BeeHives_DisplayName_plural": "Пасеки",
    "Buildings/PersonalBuilding_BeeHives_I_DisplayName": "Пасека",
    "Buildings/PublicBuilding_TownHall_I_DisplayName": "Здание совета",
    "Buildings/Storage_DisplayName": "Хранилище",
    "Carts/Button_ManageCarts": "Управление телегами",
    "Challenges/Onboarding_AdjustWorkerSalary_DisplayName": "День зарплаты",
    "General/General_Button_Previous": "Предыдущий",
    "General/General_Button_Resume": "Возобновить",
    "Settings/Audio_Collection_Name": "Аудио",
    "Settings/Subtitles_Setting_Navigation": "Параметры",
    "Character/Dynasty_Description": "Род, который сохраняется из поколения в поколение, формируясь под влиянием решений, удачи и неудач его членов. Благодаря {CHAR_HEI_C}наследникам{##} ваша династия переживёт любого из своих представителей.",
    "Character/RenownDynasty_Description": "Слава династии — это совокупная {CHAR_REN_C}слава{##} всех её членов.",
    "Combat/CombatEquipment_Effect_DamageEnergy_Description": "{CHAR_ENE_C}Энергия{##} отнимается у цели при попадании во время {CO_COM_C}боя{##}.",
    "Dialogue/21ECBDD74D7868F8020DFEA671A8B51E_642B736C00000000": "Говорят, истинная власть таится в залах, где составляются бюджеты и принимаются законы. Добро пожаловать в один из них. Здесь менее гламурно, чем вы ожидали, и куда более хаотично. Но теперь это ваше место.",
    "Events/BallInvite_Option_Accept_Outcome_Instigator": "Ваше присутствие на этом знаменательном событии было замечено городской аристократией. Как и ваш денежный вклад.",
    "Events/ChildIsBorn_Description_2": "По мере взросления вашего ребёнка не забывайте вкладываться в его {A_TRACHI_C}образование{##}, чтобы однажды он мог достойно послужить вашей династии и даже стать вашим {CHAR_HEI_C}наследником{##}.",
    "Events/Contamination_Description_3": "Возможно, вам стоит {A_PHYCHE_C}посетить лекаря{##}, {A_PRASHR_C}помолиться{##}, чтобы болезнь отступила, или хотя бы использовать некоторые мази или зелья. Не забудьте также позаботиться о благополучии ваших рабочих и членов семьи, если хотите, чтобы ваше дело и династия пережили это.",
    "Events/Imprisoned_Description_1": "Неужели вы никогда не слышали, что {CR_CRI_C}преступление{##} не приносит дохода? На самом деле, преступление окупается, но, похоже, не для вас. Вам {CR_TRIPUN_C}вынесен приговор{##} в виде тюремного заключения в соответствии с {CR_SEV_C}тяжестью вашего преступления{##} и тем, насколько {L_LAWSEV_C}сурово{##} текущее законодательство.",
    "Events/InjuryVictim_Description_1": "Надеюсь, ваши оставшиеся {CHAR_HP_C}здоровье{##} и {CHAR_ENE_C}Энергия{##} позволяют вам продолжать повседневные дела.",
    "Events/TutorialBusiness_Description_1": "Назначайте своих {CHAR_CITVSWOR_C}рабочих{##}, чтобы они {A_ACT_CRA_C}изготавливали{##} изделия из различных материалов, которые вы покупаете или добываете. Нанимайте больше рабочих по мере необходимости и следите за их {CHAR_ENE_C}Энергией{##} — она влияет на {CHAR_PRO_C}производительность{##}.",
    "Events/TutorialFamily_Description_4": "Не забудьте вложиться в обучение вашего {CHAR_HEI_C}наследника{##}, чтобы убедиться, что он действительно готов исполнять свои обязанности и не принесёт позора и разорения вашей династии, когда придёт время передать ему власть — по доброй воле или по необходимости. Начните {A_TRACHI_C}обучение{##} вашего первенца как можно раньше, ведь никто не знает, сколько вам отпущено.",
    "General/Chronicle_DisplayName_WithNumber": "Хроника",
    "General/Warning_SaveLimit_Message": "Вы можете хранить до {NumManual} {NumManual}|plural(one=ручного сохранения, other=ручных сохранений) и {NumAuto} {NumAuto}|plural(one=автосохранения, other=автосохранений) — отдельно для одиночной и многопользовательской игры. При достижении лимита новое сохранение перезапишет самое старое.",
    "Notifications/Action_Snitch_Target_Message": "Кто-то {A_SNI_C}доносит{##} до информаторов о том, что происходит в городе. Необходимое зло или предательство?",
    "Notifications/Evidence_PlayerCrimeExpired_Message": "Одно из ваших преступлений прощено {CR_STALIM_C}временем{##}. Теперь оно не может стать причиной вашего {CR_TRI_C}суда{##} или средством давления на вас.",
    "Politics/Trial_WaitingForJury_Message": "Присяжные совещаются…",
}

GLOSSARY_BY_KEY.update(CACHE_COLLISION_REPAIRS_BY_KEY)

TEXT_REPAIRS_BY_ENGLISH: dict[str, str] = {
    "Donations Welcome!": "Пожертвования приветствуются!",
    "\"\"If intelligence were an art, you'd be an empty canvas\"\". Whether such speech should be punishable is ruled by this law.":
        "«Если бы ум был искусством, вы были бы пустым холстом». Этот закон определяет, подлежит ли подобное высказывание наказанию.",
    "If {B_HP_C}Integrity{##} rises above {value}%, this status is removed.":
        "Если {B_HP_C}прочность{##} превысит {value}%, это состояние будет снято.",
    "Once you have obtained {CR_EVI_C}evidence{##}, use it to gain advantage over your opponents. Open {HB_CRIEVI} Crime and Evidence in the left sidebar, select {A_PRECHA_C}Press Charges{##}, and wait for the accused to go to trial.":
        "Получив {CR_EVI_C}улики{##}, используйте их, чтобы получить преимущество над соперниками. Откройте раздел {HB_CRIEVI} «Преступления и улики» в левой панели, выберите {A_PRECHA_C}«Предъявить обвинение»{##} и дождитесь, пока обвиняемый предстанет перед судом.",
    "You managed to uncover {NumEvidences} {NumEvidences}|plural(one=piece of {CR_EVI_C}evidence{##},other=pieces of {CR_EVI_C}evidence{##}) in total. Enough to drag {charactername} before the {CR_TRI_C}court{##}.":
        "Вам удалось собрать всего {NumEvidences} {NumEvidences}|plural(one={CR_EVI_C}улику{##},other={CR_EVI_C}улики{##}). Этого достаточно, чтобы предать {charactername} {CR_TRI_C}суду{##}.",
    "{CharacterA}'s {CHAR_STA_C}opinion{##} of {CharacterB}":
        "{CHAR_STA_C}Мнение{##} {CharacterA} о {CharacterB}",
    "{Character}'s {CHAR_STA_C}opinion{##} of you":
        "{CHAR_STA_C}Мнение{##} {Character} о вас",
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
        "Игра Europa 1410, XV век, гильдии, ремёсла и городской быт. "
        "cart=телега, quarter=квартал, office=должность, guild=гильдия, "
        "standing=репутация, integrity=прочность, escort=сопровождение, "
        "wealth=богатство, title=титул, worker=рабочий, marketplace=рынок, "
        "joiner=столяр. На «вы». "
        f"Раздел: {entry.namespace or 'Общий интерфейс'}. Ключ: {entry.key}. {extra}"
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
    if entry.namespace in {"Test", "Controls"}:
        return True
    if entry.namespace == "Settings" and entry.key == "Video_Display_Resolution_Format":
        return True
    if entry.key.startswith("RomanNumeral_"):
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


def is_opaque_encoded_text(text: str) -> bool:
    """Keep high-repetition hash-table payloads out of machine translation."""
    letters = re.findall(r"[A-Za-z]", text)
    if len(text) < 18 or len(letters) < 10:
        return False
    counts = {letter.lower(): letters.count(letter) + letters.count(letter.swapcase()) for letter in set(letters)}
    return len(counts) <= 8 and max(counts.values()) / len(letters) >= 0.45


def is_opaque_encoded_text(text: str) -> bool:
    """Keep high-repetition hash-table payloads out of machine translation."""
    letters = re.findall(r"[A-Za-z]", text)
    if len(text) < 18 or len(letters) < 10:
        return False
    counts = {letter.lower(): letters.count(letter) + letters.count(letter.swapcase()) for letter in set(letters)}
    return len(counts) <= 8 and max(counts.values()) / len(letters) >= 0.45


def should_keep_english(entry: LocEntry) -> bool:
    if is_system_entry(entry):
        return True
    if not entry.namespace and re.fullmatch(r"[0-9A-Fa-f]{24,}", entry.key):
        return True
    if is_personal_name(entry):
        return True
    if is_pure_template(entry.english):
        return True
    if is_opaque_encoded_text(entry.english):
        return True
    if is_opaque_encoded_text(entry.english):
        return True
    if cyrillic_ratio(entry.english) > 0.3:
        return True
    return False


def glossary_lookup(entry: LocEntry) -> str | None:
    entry_path = f"{entry.namespace}/{entry.key}"
    human_reviewed = OBVIOUS_REPAIRS_BY_KEY.get(entry_path)
    if human_reviewed is not None:
        return human_reviewed
    reviewed = EXACT_REPAIRS_BY_KEY.get(entry_path)
    if reviewed is not None:
        return reviewed
    by_text = TEXT_REPAIRS_BY_ENGLISH.get(entry.english)
    if by_text is not None:
        return by_text
    by_path = GLOSSARY_BY_KEY.get(entry_path)
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
LLM_SYSTEM = """Ты литературный редактор русской локализации The Guild - Europa 1410.
Это не современная офисная программа, а средневековая экономическая стратегия о династии,
ремесле, торговле, городских кварталах, гильдиях и политике Священной Римской империи около 1410 года.
Переводи так, чтобы текст звучал как качественная русская локализация игры серии The Guild:
естественно, кратко и выразительно, без машинных кальк и канцелярита.

Тематический словарь:
- cart / carts = телега / телеги; это грузовая повозка, никогда не «карта» и не «корзина».
- quarter / quarters = городской квартал / кварталы, не «четверть».
- office в политике = должность; office как помещение = кабинет.
- guild = гильдия; в названии ремесленного предприятия допустим «цех».
- guild master = глава гильдии или мастер цеха по контексту.
- standing = репутация; integrity = прочность здания или телеги.
- escort = вооружённое сопровождение; wealth = богатство.
- title / titles = дворянский титул / титулы; dynasty = династия.
- worker = рабочий в мастерской; joiner = столяр; apprentice = подмастерье.
- marketplace = рынок; town hall = ратуша; council hall = зал заседаний.
- burgher = бюргер или горожанин по смыслу; citizen = гражданин.
- guard = стражник; bodyguard = телохранитель; bailiff = судебный пристав или судебный служитель.
- profession = ремесло или профессия; trade = торговля; law = закон.

Редакторские правила:
- Переводи смысл, а не порядок английских слов. Сначала пойми, что означает строка в игре.
- Для кнопок и названий используй короткие естественные формы: «Подать заявку», «Начать игру», «Гильдия кузнецов».
- Для описаний используй литературный, но понятный русский; не добавляй сведений, которых нет в оригинале.
- Сохраняй средневековый колорит, но не используй чрезмерно архаичные слова и не стилизуй интерфейс под летопись.
- Обращайся к игроку на «вы». Имена и топонимы транслитерируй, не переводи.
- Не меняй числа, регистр значимых названий и смысл терминов. Не превращай должность в помещение или ремесло в абстрактный «бизнес».
- Сохрани без изменений все плейсхолдеры и управляющие конструкции: {ТЕГИ}, {##}, __PH0__, __S0__, |gender(...), |plural(...).
- На вход подаются объекты с полями id и text. Никогда не переноси перевод или смысл
  из одного объекта в другой.
- Верни ТОЛЬКО JSON вида {"items":[{"id":"...","russian":"..."}]}.
  Для каждого входного id должен быть ровно один выходной объект с тем же id.
"""

REVIEW_SYSTEM = """Ты старший редактор русской локализации The Guild - Europa 1410.
Проверяй уже готовый перевод строк игры, а не переводи их механически заново.
Оценивай английский оригинал, черновой русский вариант, раздел и ключ.

Сеттинг: средневековая экономическая стратегия около 1410 года — династия,
ремёсла, торговля, городские кварталы, гильдии и политика.
Используй устойчивую терминологию серии The Guild:
cart=телега, quarter=городской квартал, office в политике=должность,
guild=гильдия или цех по контексту, standing=репутация, integrity=прочность,
escort=сопровождение, wealth=богатство, title=титул, worker=рабочий,
joiner=столяр, marketplace=рынок, town hall=ратуша.

Правила проверки:
- Если черновик естественный и передаёт смысл, верни его без изменений.
- Исправляй буквальные кальки, бессмысленные конструкции, неверный род и число,
  неуместный современный канцелярит и потерянный игровой контекст.
- Для коротких названий выбирай краткий литературный вариант; для описаний сохраняй
  смысл и средневековый колорит без лишней архаики.
- Не выдумывай сведения и не расширяй строку без причины. Обращение к игроку — «вы».
- Сохраняй все плейсхолдеры и управляющие конструкции в точности.
- Текст между тегом вида {P_C} и закрывающим {##} можно и нужно склонять целиком.
  Никогда не приклеивай русское окончание снаружи тега: пиши {P_C}кузнеца{##},
  а не {P_C}кузнец{##}скую.
- Обязательно исправляй перевод, если он относится к другой исходной строке, теряет
  существенную часть смысла, содержит случайные иероглифы или явную ошибку падежа.
- Сверяй каждый объект только с его собственными english, namespace и key. Никогда
  не переноси перевод или смысл между соседними объектами.
- Если сомневаешься и черновик не содержит доказуемой ошибки, не включай его в ответ.

Верни только JSON вида {"items":[{"id":"...","russian":"...","reason":"..."}]}.
В items включай ТОЛЬКО строки, которые действительно нужно исправить. id обязан точно
совпадать с id входного объекта. Поле russian — обязательно уже исправленный финальный
текст, а не ошибочный черновик: если он совпадает с draft или всё ещё содержит описанную
в reason ошибку, объект считается недействительным. Если всё хорошо, верни {"items":[]}.
"""

AUDIT_SYSTEM = """Ты контролёр качества русского перевода The Guild - Europa 1410.
Для каждого объекта независимо сравни english и draft с учётом namespace и key.
Помечай id, если draft относится к другой строке, искажает или теряет существенный смысл,
содержит явную грамматическую ошибку, неверное управление или согласование, случайные
иностранные символы, сломанные теги либо дословную кальку, неестественную для русского.
Особенно внимательно проверяй падеж слов внутри пар {TAG}...{##}, род и число, предлоги,
пунктуацию в строках-перечнях и нейтральность фраз с подставляемыми именами персонажей.
Помечай также формально понятный, но заметно машинный русский: нелепые сочетания слов,
канцелярские кальки и обороты, которые носитель языка в игровом интерфейсе не написал бы.
Не помечай хороший естественный перевод только ради необязательной перестановки слов.
Никогда не переноси смысл между объектами. Ничего не переводи и не объясняй.
Верни только JSON вида {"ids":["..."]}; если ошибок нет, верни {"ids":[]}.
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


_RUNTIME_CONFIG: dict | None = None


def _load_runtime_config() -> dict:
    global _RUNTIME_CONFIG
    if _RUNTIME_CONFIG is not None:
        return _RUNTIME_CONFIG
    config_path = Path(__file__).resolve().parent / "config.json"
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _RUNTIME_CONFIG or {}
    if isinstance(data, dict):
        _RUNTIME_CONFIG = data
        return data
    return _RUNTIME_CONFIG or {}


def _translation_backend() -> str:
    config = _load_runtime_config()
    return (
        os.environ.get("EUROPA1410_TRANSLATION_BACKEND")
        or str(config.get("translation_backend") or "local")
    ).strip().lower()


def _local_enable_thinking(config: dict) -> bool:
    raw = os.environ.get("EUROPA1410_LOCAL_ENABLE_THINKING")
    if raw is None:
        raw = config.get("local_enable_thinking", True)
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _local_translate_batch(texts: list[str]) -> list[str]:
    config = _load_runtime_config()
    base = (
        os.environ.get("EUROPA1410_LOCAL_API_BASE")
        or str(config.get("local_api_base") or "http://127.0.0.1:1234/v1")
    ).rstrip("/")
    model = (
        os.environ.get("EUROPA1410_LOCAL_MODEL")
        or str(config.get("local_model") or "llama-3-8b-lexi-uncensored")
    ).strip()
    api_key = (
        os.environ.get("EUROPA1410_LOCAL_API_KEY")
        or str(config.get("local_api_key") or "")
    ).strip()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request_items = [
        {"id": str(index), "text": text}
        for index, text in enumerate(texts)
    ]
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": LLM_SYSTEM},
            {
                "role": "user",
                "content": json.dumps({"items": request_items}, ensure_ascii=False),
            },
        ],
        "temperature": 0.2,
        "max_tokens": max(
            2048,
            min(12288, sum(len(text) for text in texts) * 2 + 512),
        ),
        "chat_template_kwargs": {"enable_thinking": _local_enable_thinking(config)},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "translation_batch",
                "schema": {
                    "type": "object",
                    "properties": {
                            "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "russian": {"type": "string"},
                                },
                                "required": ["id", "russian"],
                                "additionalProperties": False,
                            },
                            "minItems": len(texts),
                            "maxItems": len(texts),
                        }
                    },
                    "required": ["items"],
                    "additionalProperties": False,
                },
            },
        },
    }
    response = requests.post(
        f"{base}/chat/completions",
        headers=headers,
        json=payload,
        timeout=600,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Local LLM {response.status_code}: {response.text[:300]}")
    body = response.json()
    content = body["choices"][0]["message"]["content"]
    parsed = _extract_json_object(str(content))
    items = parsed.get("items")
    if not isinstance(items, list) or len(items) != len(texts):
        raise RuntimeError(
            f"Local LLM item count {0 if not isinstance(items, list) else len(items)}/{len(texts)}"
        )
    expected_ids = {row["id"] for row in request_items}
    by_id: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            raise RuntimeError("Local LLM returned a non-object item")
        item_id = str(item.get("id") or "")
        if item_id in by_id or item_id not in expected_ids:
            raise RuntimeError(f"Local LLM returned invalid or duplicate id: {item_id!r}")
        by_id[item_id] = unwrap_mt(str(item.get("russian") or ""))
    missing = [row["id"] for row in request_items if row["id"] not in by_id]
    if missing:
        raise RuntimeError(f"Local LLM omitted ids: {', '.join(missing[:5])}")
    return [by_id[str(index)] for index in range(len(texts))]


def _local_review_batch(items: list[dict[str, str]]) -> list[dict[str, str]]:
    if not items:
        return []
    config = _load_runtime_config()
    base = (
        os.environ.get("EUROPA1410_LOCAL_API_BASE")
        or str(config.get("local_api_base") or "http://127.0.0.1:1234/v1")
    ).rstrip("/")
    model = (
        os.environ.get("EUROPA1410_LOCAL_MODEL")
        or str(config.get("local_model") or "llama-3-8b-lexi-uncensored")
    ).strip()
    api_key = (
        os.environ.get("EUROPA1410_LOCAL_API_KEY")
        or str(config.get("local_api_key") or "")
    ).strip()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": REVIEW_SYSTEM},
            {
                "role": "user",
                "content": json.dumps({"items": items}, ensure_ascii=False),
            },
        ],
        "temperature": 0.1,
        "max_tokens": max(
            2048,
            min(
                16384,
                sum(len(json.dumps(item, ensure_ascii=False)) for item in items) * 2
                + 512,
            ),
        ),
        "chat_template_kwargs": {"enable_thinking": _local_enable_thinking(config)},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "translation_review_batch",
                "schema": {
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "russian": {"type": "string"},
                                    "reason": {"type": "string"},
                                },
                                "required": ["id", "russian", "reason"],
                                "additionalProperties": False,
                            },
                            "minItems": 0,
                            "maxItems": len(items),
                        }
                    },
                    "required": ["items"],
                    "additionalProperties": False,
                },
            },
        },
    }
    response = requests.post(
        f"{base}/chat/completions",
        headers=headers,
        json=payload,
        timeout=600,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Local review LLM {response.status_code}: {response.text[:300]}")
    body = response.json()
    content = body["choices"][0]["message"]["content"]
    parsed = _extract_json_object(str(content))
    result = parsed.get("items")
    if not isinstance(result, list):
        raise RuntimeError("Local review did not return an item array")
    expected_ids = {str(item["id"]) for item in items}
    seen_ids: set[str] = set()
    normalized: list[dict[str, str]] = []
    for item in result:
        if not isinstance(item, dict):
            raise RuntimeError("Local review returned a non-object item")
        item_id = str(item.get("id") or "")
        if item_id not in expected_ids or item_id in seen_ids:
            raise RuntimeError(f"Local review returned invalid or duplicate id: {item_id!r}")
        seen_ids.add(item_id)
        normalized.append(
            {
                "id": item_id,
                "russian": unwrap_mt(str(item.get("russian") or "")),
                "reason": str(item.get("reason") or ""),
            }
        )
    return normalized


def _local_audit_batch(items: list[dict[str, str]]) -> set[str]:
    if not items:
        return set()
    config = _load_runtime_config()
    base = (
        os.environ.get("EUROPA1410_LOCAL_API_BASE")
        or str(config.get("local_api_base") or "http://127.0.0.1:1234/v1")
    ).rstrip("/")
    model = (
        os.environ.get("EUROPA1410_LOCAL_MODEL")
        or str(config.get("local_model") or "llama-3-8b-lexi-uncensored")
    ).strip()
    api_key = (
        os.environ.get("EUROPA1410_LOCAL_API_KEY")
        or str(config.get("local_api_key") or "")
    ).strip()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": AUDIT_SYSTEM},
            {
                "role": "user",
                "content": json.dumps({"items": items}, ensure_ascii=False),
            },
        ],
        "temperature": 0.0,
        "max_tokens": 2048,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "translation_audit_batch",
                "schema": {
                    "type": "object",
                    "properties": {
                        "ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": len(items),
                        }
                    },
                    "required": ["ids"],
                    "additionalProperties": False,
                },
            },
        },
    }
    response = requests.post(
        f"{base}/chat/completions",
        headers=headers,
        json=payload,
        timeout=300,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Local audit LLM {response.status_code}: {response.text[:300]}")
    content = response.json()["choices"][0]["message"]["content"]
    result = _extract_json_object(str(content)).get("ids")
    if not isinstance(result, list):
        raise RuntimeError("Local audit did not return an id array")
    expected_ids = {str(item["id"]) for item in items}
    flagged = {str(item_id) for item_id in result}
    invalid = flagged - expected_ids
    if invalid:
        raise RuntimeError(f"Local audit returned invalid ids: {sorted(invalid)[:5]}")
    return flagged


def _review_candidate(entry: LocEntry, russian: str) -> bool:
    if should_keep_english(entry) or glossary_lookup(entry) is not None:
        return False
    english = entry.english.strip()
    draft = (russian or "").strip()
    if not draft or draft == english:
        return True
    if not entry.namespace:
        return True
    if sorted(PLACEHOLDER_RE.findall(entry.english)) != sorted(
        PLACEHOLDER_RE.findall(draft)
    ):
        return True
    if cyrillic_ratio(draft) < 0.35:
        return True
    if len(draft) > max(48, len(english) * 3):
        return True
    thematic_terms = (
        "mine",
        "office",
        "cart",
        "quarter",
        "guild",
        "standing",
        "integrity",
        "escort",
        "wealth",
    )
    if any(term in english.lower() for term in thematic_terms):
        return True
    lowered = draft.lower()
    return any(
        marker in lowered
        for marker in (
            "приветствуются",
            "название настройки",
            "краткое название",
            "переведите на русский",
            "необходимо перевести",
        )
    )


def review_fingerprint(entry: LocEntry, russian: str) -> str:
    payload = "\x1f".join(
        (entry.stable_cache_id, entry.english.strip(), (russian or "").strip())
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def review_translation_drafts(
    entries: list[LocEntry],
    cache: dict[str, str],
    *,
    batch_size: int = 32,
    review_all: bool = False,
    reviewed_fingerprints: set[str] | None = None,
    on_progress: Callable[[dict[str, str]], None] | None = None,
) -> tuple[int, int]:
    """Have the local model actively edit risky fresh drafts, not just report them."""
    if _translation_backend() not in {"local", "lmstudio", "openai"}:
        return 0, 0
    candidates: list[tuple[LocEntry, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if review_all:
            russian = resolve(entry, cache)
        else:
            russian = cache.get(entry.cache_id)
            if russian is None:
                russian = find_cached_translation(entry, cache)
        if russian is None:
            continue
        marker = (entry.stable_cache_id, entry.english)
        if marker in seen:
            continue
        if (
            reviewed_fingerprints is not None
            and review_fingerprint(entry, russian) in reviewed_fingerprints
        ):
            continue
        if review_all:
            if should_keep_english(entry):
                continue
        elif not _review_candidate(entry, russian):
            continue
        seen.add(marker)
        candidates.append((entry, russian))
    if not candidates:
        return 0, 0

    reviewed = 0
    edited = 0
    for start in range(0, len(candidates), max(1, batch_size)):
        group = candidates[start : start + max(1, batch_size)]
        request_items = [
            {
                "id": str(index),
                "namespace": entry.namespace,
                "key": entry.key,
                "english": entry.english,
                "draft": russian,
            }
            for index, (entry, russian) in enumerate(group)
        ]

        def review_group(
            values: list[dict[str, str]],
        ) -> list[dict[str, str]]:
            if len(values) > 8:
                middle = len(values) // 2
                return review_group(values[:middle]) + review_group(values[middle:])
            last_error: Exception | None = None
            for _attempt in range(2):
                try:
                    return _local_review_batch(values)
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
            if len(values) <= 1:
                assert last_error is not None
                raise last_error
            middle = len(values) // 2
            return review_group(values[:middle]) + review_group(values[middle:])

        try:
            if review_all:
                try:
                    flagged_ids = _local_audit_batch(request_items)
                    review_items = [
                        item for item in request_items if item["id"] in flagged_ids
                    ]
                except Exception as exc:  # noqa: BLE001
                    print(f"  fast audit failed, reviewing full batch: {exc}", flush=True)
                    review_items = request_items
            else:
                review_items = request_items
            decisions = review_group(review_items)
        except Exception as exc:  # noqa: BLE001
            print(f"  inline review skipped for {len(group)} drafts: {exc}", flush=True)
            continue
        decisions_by_id = {decision["id"]: decision for decision in decisions}
        for index, (entry, draft) in enumerate(group):
            reviewed += 1
            decision = decisions_by_id.get(str(index))
            if decision is None:
                continue
            proposal = decision.get("russian", "").strip()
            placeholders_ok = sorted(PLACEHOLDER_RE.findall(entry.english)) == sorted(
                PLACEHOLDER_RE.findall(proposal)
            )
            if (
                proposal
                and placeholders_ok
                and not re.search(r"[\u3400-\u9fff]", proposal)
                and proposal != draft
            ):
                remember_cache_translation(cache, entry, proposal)
                edited += 1
        if reviewed_fingerprints is not None:
            for entry, original_draft in group:
                final_draft = resolve(entry, cache) or original_draft
                reviewed_fingerprints.add(review_fingerprint(entry, final_draft))
        if on_progress is not None:
            on_progress(cache)
        print(
            f"  inline review: {reviewed}/{len(candidates)}, edited {edited}",
            flush=True,
        )
    return reviewed, edited


def repair_translation_placeholders(
    entries: list[LocEntry],
    cache: dict[str, str],
    *,
    max_items: int = 48,
) -> tuple[int, int]:
    """Repair only drafts whose runtime placeholders do not match the source."""
    candidates: list[tuple[LocEntry, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        russian = cache.get(entry.cache_id)
        if russian is None:
            russian = find_cached_translation(entry, cache)
        if russian is None:
            continue
        if sorted(PLACEHOLDER_RE.findall(entry.english)) == sorted(
            PLACEHOLDER_RE.findall(russian)
        ):
            continue
        marker = (entry.stable_cache_id, entry.english)
        if marker in seen:
            continue
        seen.add(marker)
        candidates.append((entry, russian))
    if not candidates or _translation_backend() not in {"local", "lmstudio", "openai"}:
        return 0, 0

    checked = 0
    fixed = 0
    for start in range(0, len(candidates), max(1, max_items)):
        group = candidates[start : start + max(1, max_items)]
        request_items = [
            {
                "id": str(index),
                "namespace": entry.namespace,
                "key": entry.key,
                "english": entry.english,
                "draft": russian,
            }
            for index, (entry, russian) in enumerate(group)
        ]
        try:
            decisions = _local_review_batch(request_items)
        except Exception as exc:  # noqa: BLE001
            print(f"  placeholder repair skipped for {len(group)} drafts: {exc}", flush=True)
            continue
        decisions_by_id = {decision["id"]: decision for decision in decisions}
        for index, (entry, _draft) in enumerate(group):
            checked += 1
            decision = decisions_by_id.get(str(index))
            if decision is None:
                continue
            proposal = decision.get("russian", "").strip()
            if (
                proposal
                and sorted(PLACEHOLDER_RE.findall(entry.english))
                == sorted(PLACEHOLDER_RE.findall(proposal))
            ):
                remember_cache_translation(cache, entry, proposal)
                fixed += 1
        print(f"  placeholder repair: {checked}/{len(candidates)}, fixed {fixed}", flush=True)
    return checked, fixed


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
    if _translation_backend() in {"local", "lmstudio", "openai"}:
        return _local_translate_batch(texts)
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
            if _translation_backend() in {"local", "lmstudio", "openai"}:
                raise
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
    on_progress: Callable[[dict[str, str], list[LocEntry]], None] | None = None,
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
        protected, segments, tokens = decompose_text(wrap_for_mt(entry.english, entry))
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
        changed_entries = [entry for entry, *_rest in wave]
        if on_progress:
            on_progress(cache, changed_entries)

    return cache


def resolve(entry: LocEntry, cache: dict[str, str]) -> str:
    glossary = glossary_lookup(entry)
    if glossary is not None:
        return repair_unbalanced_quotes(glossary)
    cached = find_cached_translation(entry, cache)
    if cached is not None:
        return repair_unbalanced_quotes(cached)
    return repair_unbalanced_quotes(entry.english)


def repair_unbalanced_quotes(text: str) -> str:
    """Close accidentally unbalanced Russian guillemets in final UI text."""
    missing = text.count("«") - text.count("»")
    return text + ("»" * missing) if missing > 0 else text
