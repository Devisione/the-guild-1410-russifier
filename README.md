> **Поддержать автора:** [DonationAlerts](https://www.donationalerts.com/r/link_it)

# Europa 1410 — русская локализация

Неофициальный фанатский русификатор полной версии **The Guild - Europa 1410** (Steam).

Репозиторий: [github.com/Devisione/the-guild-1410-russifier](https://github.com/Devisione/the-guild-1410-russifier)

> **Важно:** это неофициальный **фанатский** русификатор. Не взлом, не пиратство,
> не обход защиты — только текстовая локализация для **легально купленной** игры.
> Авторы **не претендуют** на права на игру и не связаны с издателем.
> Подробнее — [DISCLAIMER.md](DISCLAIMER.md) и [LICENSE](LICENSE).

## Установка (игрокам)

1. Скачайте архив из [Releases](https://github.com/Devisione/the-guild-1410-russifier/releases).
2. Распакуйте его **в папку игры**, например:

```
C:\Program Files (x86)\Steam\steamapps\common\The Guild - Europa 1410
```

3. В игре откройте **Settings → Language → Text Language**.
   - Выберите **русский**, если пункт появился.
   - Если русского нет — выберите **Deutsch** (немецкий текст в моде заменён на русский).
4. **Полностью перезапустите** игру.

В архиве только файлы локализации (`.pak` / `.ucas` / `.utoc`). Сама игра в архив не входит.

## Что внутри

| Файл / папка | Назначение |
|--------------|------------|
| `build_russian_mod.py` | Главный скрипт: перевод, сборка `.pak`, zip-архив |
| `locmod_lib.py` | Логика перевода, глоссарий, кэш |
| `validate_translations.py` | Проверка качества переводов перед релизом |
| `paths.py` | Разрешение путей из конфига / переменных окружения |
| `translation_manual.json` | Ручные правки перевода (главный файл для редакторов) |
| `entry_cache.json` | Кэш автоматических переводов API |
| `ucas_hash_entries.json` | Строки из хэш-ключей UCAS |
| `config.example.json` | Шаблон локальной конфигурации |

## Откуда игра берёт текст

В релизной версии тексты лежат в IoStore-паках:

```
Europa1410/Content/Paks/Europa1410-Windows.pak
```

| Ресурс | Что это |
|--------|---------|
| `Content/Localization/Game/{en,de,ash,pt-BR}/Game.locres` | Основной игровой текст (~7000 строк) |
| `Content/Localization/Game_VO/{...}/Game_VO.locres` | Реплики диалогов |
| `Content/Localization/Uncategorized Texts/en/` | Собранные UI-строки |
| `Content/StringTables/ST_*.csv` | Исходные таблицы строк |

В `Game.locmeta` разработчики уже заранее прописали культуру `ru`. Мод добавляет
`Game/ru/Game.locres` как третий язык и параллельно подменяет немецкий `de`,
если пункт «русский» в меню не появится.

## Требования для сборки

- **Python 3.10+**
- Установленная полная игра через Steam
- Инструменты в `tools/` — см. [tools/README.md](tools/README.md)

```bash
pip install -r requirements.txt
```

## Настройка на другом ПК

### 1. Клонировать репозиторий

**HTTPS:**

```bash
git clone https://github.com/Devisione/the-guild-1410-russifier.git
cd the-guild-1410-russifier
```

**SSH:**

```bash
git clone git@github.com:Devisione/the-guild-1410-russifier.git
cd the-guild-1410-russifier
```

### 2. Указать путь к игре

Скопируйте шаблон конфигурации:

```bash
# Linux / macOS / Git Bash
cp config.example.json config.json

# Windows (cmd)
copy config.example.json config.json
```

Отредактируйте `config.json`:

```json
{
  "game_paks_dir": "C:/Program Files (x86)/Steam/steamapps/common/The Guild - Europa 1410/Europa1410/Content/Paks",
  "work_dir": null,
  "install_mod_after_build": true
}
```

- `game_paks_dir` — папка `Europa1410/Content/Paks` внутри установки Steam.
- `work_dir` — `null` означает «текущая папка репозитория».
- `install_mod_after_build` — `false`, если не хотите автокопирование мода в игру.

`config.json` в git не попадает. Ключи API держите только там или в переменной `GEMINI_API_KEY`.

**Альтернатива без config.json** — переменные окружения:

```bash
export EUROPA1410_GAME_PAKS="C:/Program Files (x86)/Steam/steamapps/common/The Guild - Europa 1410/Europa1410/Content/Paks"
export EUROPA1410_WORK_DIR="$HOME/Projects/the-guild-1410-russifier"
export GEMINI_API_KEY="..."
```

### 3. Скачать инструменты

Положите `repak.exe` и `oo2core_9_win64.dll` в `tools/` — инструкция в
[tools/README.md](tools/README.md).

### 4. Первый запуск

При первой сборке скрипт **автоматически извлечёт** из `.pak` игры:

- таблицы строк (`source/.../StringTables/`)
- исходные `.locres` / `.locmeta`

Папка `source/` создаётся локально и **не коммитится** в git.

```bash
python build_russian_mod.py --no-install
```

Флаг `--no-install` — только собрать мод в `dist/` и zip в `release/`, не копировать в игру.

Перевод идёт параллельно (`--workers 8` по умолчанию). Кэш сохраняется после каждой волны.

## Как обновлять переводы

### Ручные правки (рекомендуется)

1. Откройте `translation_manual.json`.
2. Найдите нужную строку по полю `"english"`.
3. Измените `"russian"`.
4. Пересоберите мод:

```bash
python build_russian_mod.py --skip-translate
```

`--skip-translate` использует кэш и ручные правки без обращения к API.

### Полный перевод заново

```bash
python build_russian_mod.py --force
```

Переводит все строки через Google Translate / MyMemory. Нужен интернет.

### Только новые строки после обновления игры

```bash
python build_russian_mod.py
```

Без `--force` — переводит только то, чего нет в `entry_cache.json`.

### Проверка перед релизом

```bash
python validate_translations.py
```

Создаёт `validation_report.txt`. Код выхода `1` — есть ошибки.

## Установка мода в игру

После успешной сборки в папку игры копируются три файла:

```
Europa1410/Content/Paks/
  RussianLocalization_P.pak
  RussianLocalization_P.ucas
  RussianLocalization_P.utoc
```

Готовый zip лежит в `release/Europa1410-RussianLocalization.zip`.
Распакуйте его в корень папки игры.

## Релизы

Готовые архивы публикуются в
[Releases](https://github.com/Devisione/the-guild-1410-russifier/releases).

Каждый архив содержит **только файлы русской локализации**:

- `Europa1410/Content/Paks/RussianLocalization_P.pak`
- `Europa1410/Content/Paks/RussianLocalization_P.ucas`
- `Europa1410/Content/Paks/RussianLocalization_P.utoc`
- `README.txt` — установка
- `DISCLAIMER.txt` — правовая информация

**Игра в архив не входит.** Для установки нужна ваша копия из Steam.

## Правовая информация

Этот проект создан **добровольцами** исключительно для того, чтобы
русскоязычные игроки могли играть в **уже купленный** продукт на родном языке.

| | |
|---|---|
| Цель | Перевод интерфейса и текстов на русский |
| Не является | Взломом, читом, пиратством, репаком игры |
| Права на игру | Принадлежат правообладателям, мы на них **не претендуем** |
| Коммерция | Проект **бесплатный**, без продажи игры |
| Связь с издателем | **Отсутствует**, проект неофициальный |

Полный текст: [DISCLAIMER.md](DISCLAIMER.md)

Поддержать работу можно здесь: [DonationAlerts](https://www.donationalerts.com/r/link_it)

## Структура рабочих папок (локальные, не в git)

```
the-guild-1410-russifier/
├── source/          ← извлечено из игры (gitignored)
├── build/           ← промежуточная сборка мода
├── dist/            ← готовые .pak/.ucas/.utoc
├── release/         ← zip-архивы для распространения
└── tmp_*/           ← временные файлы repak
```

## Частые проблемы

| Проблема | Решение |
|----------|---------|
| `repak.exe` not found | Скачайте в `tools/` — см. tools/README.md |
| `Missing source file: Game.locres` | Запустите без `--skip-translate` — скрипт извлечёт файлы |
| Игра не на диске C: | Укажите путь в `config.json` |
| Перевод не применился | Выберите русский или Deutsch и полностью перезапустите игру |
| Перевод не применился | Проверьте `translation_manual.json`, затем `--skip-translate` |

## Лицензия

- Скрипты сборки — [MIT License](LICENSE)
- Игра и её контент — собственность правообладателей
- Русские переводы — неофициальная фанатская работа, без претензий на ИС игры
