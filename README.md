# MobilRing-Server2 — поиск совпадений

Сервис читает объявления из Firebase Realtime Database (узлы `lost` и `found`), ищет пары
«пропажа ↔ находка» и записывает их в узел `matches`. Приложение
[MobilRing](https://github.com/dragonlex/MobilRing) показывает эти пары на экране
«Уведомления» тем, чья заявка в них участвует.

Логика гибридная: сначала быстрая эвристика (нормализация русского текста, синонимы,
стемминг, числа, цвета), затем спорные пары проверяет локальная языковая модель
через KoboldCpp. Без модели скрипт тоже работает — по более высокому порогу.

## Состав

| Файл | Назначение |
|---|---|
| `matcher.py` | основной скрипт: чтение базы, оценка пар, проверка моделью, запись результата |
| `config.json` | адрес базы, параметры KoboldCpp, пороги, интервал опроса |
| `setup-kobold.ps1` | установка KoboldCpp и скачивание модели из интернета |
| `start-matcher.ps1` | запуск поиска в цикле (или один проход с `-Once`) |
| `start-kobold.ps1` | запуск KoboldCpp с моделью из `models` |
| `matcher.log` | журнал работы (создаётся сам, в git не хранится) |

`.auth.json`, `models/` и `ai/` в репозиторий не попадают — см. `.gitignore`.

## Установка KoboldCpp и модели

С нуля всё скачивается одним скриптом:

```powershell
.\setup-kobold.ps1
```

Что он делает:

1. Берёт с GitHub последний релиз KoboldCpp, находит в нём `koboldcpp.exe` (~600 МБ) и кладёт
   в `ai\koboldcpp.exe`. Уже скачанный файл повторно не тянет.
2. Скачивает модель `Qwen2.5-7B-Instruct-1M-Q4_K_M.gguf` (~4,4 ГБ) с Hugging Face
   в `models\`, предварительно проверив размер и свободное место.
3. Проверяет, что всё на месте, и подсказывает, если имя модели не совпадает с тем,
   что ждёт `start-kobold.ps1`.

Полезные параметры:

```powershell
.\setup-kobold.ps1 -SkipModel            # только KoboldCpp, модель уже есть
.\setup-kobold.ps1 -SkipKobold           # только модель
.\setup-kobold.ps1 -Force                # перекачать заново
.\setup-kobold.ps1 -KoboldVersion v1.124.2
.\setup-kobold.ps1 -ModelRepo bartowski/Qwen2.5-3B-Instruct-GGUF `
                   -ModelFile Qwen2.5-3B-Instruct-Q4_K_M.gguf   # модель легче
```

Скачивание идёт через `aria2c`, если он лежит в `ai` (в сборке KoboldCpp он есть), иначе через
BITS, иначе обычным потоком с процентами в консоли. Прерванную загрузку можно запустить снова —
скрипт продолжит с того же места.

Требуется PowerShell 5.1+ и свободное место на диске: около 1 ГБ под KoboldCpp и 5 ГБ под модель.


## Запуск

```powershell
# 1. Модель (отдельное окно)
.\start-kobold.ps1

# 2. Поиск в цикле — база опрашивается раз в минуту
.\start-matcher.ps1
```

Полезные режимы:

```powershell
.\start-matcher.ps1 -Once      # один проход и выход
.\start-matcher.ps1 -DryRun    # показать найденные пары, ничего не записывая
.\start-matcher.ps1 -NoLlm     # без модели, только эвристика
```

Прямой запуск без PowerShell-обёртки:

```powershell
python matcher.py --once --dry-run
```

## Как принимается решение

1. Отбрасываются закрытые заявки (`closed = true`) и пары, которые уже есть в `matches`.
2. Каждая пара получает оценку 0..1: совпадение названий (0.42), описаний (0.22),
   места (0.16) и общей схожести текста (0.20), плюс бонусы за одинаковые числа (0.08)
   и цвета (0.06).
3. Пары ниже `min_score` отбрасываются, остальные уходят в модель: она отвечает JSON
   `{"same": true, "confidence": 0.8, "reason": "..."}`.
4. Пара записывается, если модель подтвердила совпадение с уверенностью не ниже
   `llm_min_confidence`. Без модели — по эвристике при оценке не ниже
   `heuristic_only_min_score`.

Пример записи в `matches`:

```json
"auto-<lostKey>-<foundKey>": {
  "lostId": "-P6Sh1pQaWfZXoA5PQ80",
  "findId": "-P6SjWW6nnnNyW10xCK8",
  "createdAt": 1794719744218,
  "score": 0.332,
  "confidence": 0.95,
  "method": "llm",
  "reason": "совпадает тип вещи, приметы и место",
  "details": {"title": 0.31, "info": 0.4, "place": 0.25, "text": 0.3, "bonus": 0.06},
  "source": "matcher"
}
```

Приложение читает только `lostId`, `findId` и `createdAt`; остальные поля нужны для отладки.

## Доступ к базе

Правила базы требуют авторизации (`auth != null`). Скрипт один раз создаёт служебного
анонимного пользователя через Firebase Auth REST, сохраняет refresh-токен в `.auth.json`
и дальше обновляет его по `securetoken.googleapis.com`. Если файл удалить, будет создан
новый пользователь — уже записанные пары от этого не пострадают.

Ключи полей в базе строчные (`name`, `location`, `info`, `userId`), поэтому чтение идёт
через хелпер `field()`, который принимает и `Name`, и `name`.

## Требования

- Python 3.10+, пакет `requests`.
- KoboldCpp и GGUF-модель (например, Qwen2.5-7B-Instruct Q4_K_M) — по желанию,
  без них скрипт работает в режиме эвристики.
- Файл `google-services.json` от Firebase-проекта: из него берётся API-ключ для входа.

## Связанные репозитории

- [MobilRing](https://github.com/dragonlex/MobilRing) — Android-приложение.
- [MobilRing-Project](https://github.com/dragonlex/MobilRing-Project) — общий обзор проекта.
