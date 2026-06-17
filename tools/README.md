# Инструменты

В репозиторий **не входят** бинарные файлы. Скачайте их вручную в эту папку:

| Файл | Назначение | Где взять |
|------|------------|-----------|
| `repak.exe` | Распаковка и сборка `.pak` (UE5 IoStore) | [trumank/repak](https://github.com/trumank/repak/releases) |
| `oo2core_9_win64.dll` | Декомпрессия Oodle (нужна repak для некоторых паков) | Из установленной игры или [radgametools/oodle](https://github.com/trumank/repak#oodle) |

После скачивания положите оба файла сюда:

```
tools/repak.exe
tools/oo2core_9_win64.dll
```

Проверка:

```bash
tools/repak.exe --help
```
