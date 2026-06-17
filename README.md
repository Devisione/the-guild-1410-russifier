# Europa 1410 — русская локализация (инструменты)

Неофициальный инструментарий для сборки русского языкового мода к демо-версии
**The Guild - Europa 1410** (Steam).

Репозиторий: [github.com/Devisione/the-guild-1410-russifier](https://github.com/Devisione/the-guild-1410-russifier)

> **Важно:** это неофициальный **фанатский** русификатор. Не взлом, не пиратство,
> не обход защиты — только текстовая локализация для **легально купленной** игры.
> Авторы **не претендуют** на права на игру и не связаны с издателем.
> Подробнее — [DISCLAIMER.md](DISCLAIMER.md) и [LICENSE](LICENSE).

## Что внутри

| Файл / папка | Назначение |
|--------------|------------|
| `build_russian_mod.py` | Главный скрипт: перевод, сборка `.pak`, установка мода |
| `locmod_lib.py` | Логика перевода, глоссарий, кэш |
| `validate_translations.py` | Проверка качества переводов перед релизом |
| `paths.py` | Разрешение путей из конфига / переменных окружения |
| `translation_manual.json` | Ручные правки перевода (главный файл для редакторов) |
| `entry_cache.json` | Кэш автоматических переводов API |
| `ucas_hash_entries.json` | Строки из хэш-ключей UCAS |
| `config.example.json` | Шаблон локальной конфигурации |

## Требования

- **Python 3.10+**
- Установленная игра через Steam (демо или полная версия)
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

Репозиторий можно разместить в любом месте, например:

- `~/Projects/the-guild-1410-russifier`
- `%LOCALAPPDATA%\Europa1410\the-guild-1410-russifier`

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
  "game_paks_dir": "D:/SteamLibrary/steamapps/common/The Guild - Europa 1410 Demo/Europa1410/Content/Paks",
  "work_dir": null,
  "install_mod_after_build": true
}
```

- `game_paks_dir` — папка `Europa1410/Content/Paks` внутри установки Steam.
- `work_dir` — `null` означает «текущая папка репозитория».
- `install_mod_after_build` — `false`, если не хотите автокопирование мода в игру.

**Альтернатива без config.json** — переменные окружения:

```bash
# Linux / macOS / Git Bash
export EUROPA1410_GAME_PAKS="D:/SteamLibrary/steamapps/common/The Guild - Europa 1410 Demo/Europa1410/Content/Paks"
export EUROPA1410_WORK_DIR="$HOME/Projects/the-guild-1410-russifier"
```

```powershell
# Windows PowerShell
$env:EUROPA1410_GAME_PAKS = "C:\Program Files (x86)\Steam\steamapps\common\The Guild - Europa 1410 Demo\Europa1410\Content\Paks"
$env:EUROPA1410_WORK_DIR = "$env:LOCALAPPDATA\Europa1410\the-guild-1410-russifier"
```

### 3. Скачать инструменты

Положите `repak.exe` и `oo2core_9_win64.dll` в `tools/` — инструкция в
[tools/README.md](tools/README.md).

### 4. Первый запуск

При первой сборке скрипт **автоматически извлечёт** из `.pak` игры:

- таблицы строк (`source/.../StringTables/`)
- исходные `.locres` (если отсутствуют)

Папка `source/` создаётся локально и **не коммитится** в git.

```bash
python build_russian_mod.py --no-install
```

Флаг `--no-install` — только собрать мод в `dist/`, не копировать в игру.

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

Переводит все строки через Google Translate / MyMemory. Долго, нужен интернет.

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

Если `install_mod_after_build: false`, скопируйте их вручную из
`dist/Europa1410/Content/Paks/`.

## Релизы

Готовые архивы публикуются в
[Releases](https://github.com/Devisione/the-guild-1410-russifier/releases).

Каждый архив содержит **только файлы русской локализации**:

- `RussianLocalization_P.pak`
- `RussianLocalization_P.ucas`
- `RussianLocalization_P.utoc`
- `DISCLAIMER.txt` — правовая информация

**Игра в архив не входит.** Для установки нужна ваша копия из Steam.

Распакуйте архив и скопируйте три файла `.pak` / `.ucas` / `.utoc` в:

```
<папка игры>/Europa1410/Content/Paks/
```

## Правовая информация

Этот проект создан **добровольцами** исключительно для того, чтобы
русскоязычные игроки могли играть в **уже купленный** продукт на родном языке.

| | |
|---|---|
| Цель | Перевод интерфейса и текстов на русский |
| Не является | Взломом, читом, пиратством, репаком игры |
| Права на игру | Принадлежат правообладателям, мы на них **не претендуем** |
| Коммерция | Проект **бесплатный**, без монетизации |
| Связь с издателем | **Отсутствует**, проект неофициальный |

Полный текст: [DISCLAIMER.md](DISCLAIMER.md)

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
| Перевод не применился | Проверьте `translation_manual.json`, затем `--skip-translate` |

## Лицензия

- Скрипты сборки — [MIT License](LICENSE)
- Игра и её контент — собственность правообладателей
- Русские переводы — неофициальная фанатская работа, без претензий на ИС игры
