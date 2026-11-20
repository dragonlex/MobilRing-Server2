"""assist.py — помощь в составлении описания объявления.

Два режима, оба используют локальную модель (KoboldCpp / llama.cpp):

1. Узел базы `assist` (основной режим для приложения).
   Приложение кладёт заявку со status=pending и подписывается на свой ключ.
   Сервис держит живую подписку на узел, генерирует текст и пишет его обратно
   в ту же запись (status=done), после чего приложение само забирает описание
   и отписывается.

2. HTTP (оставлен для ручных проверок).
    GET  /health    — {"ok": true, "kobold": true/false}
    POST /describe  — {"type": "lost"|"found", "title": "...", "place": "...", "category": "..."}
                      отвечает {"text": "..."} либо {"error": "..."}

Описание всегда ограничено 110 словами.

Запуск:
    python assist.py                # адрес и порт из config.json
    python assist.py --port 5002
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import db as firebase_db
import text_clean

BASE_DIR = pathlib.Path(__file__).resolve().parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("assist")


def load_config() -> dict:
    path = BASE_DIR / "config.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


CONFIG = load_config()
KOBOLD = CONFIG.get("kobold", {})
ASSIST = CONFIG.get("assist", {})

KOBOLD_URL = KOBOLD.get("base_url", "http://127.0.0.1:5001").rstrip("/")
KOBOLD_TIMEOUT = int(KOBOLD.get("timeout", 180))
MAX_TOKENS = int(ASSIST.get("max_tokens", 220))
TEMPERATURE = float(ASSIST.get("temperature", 0.7))
FALLBACK_WITHOUT_LLM = bool(ASSIST.get("fallback_without_llm", True))

# Сколько слов максимум в готовом описании. Ограничение действует и в запросе к модели,
# и на выходе: модель иногда пишет длиннее, чем просили.
MAX_WORDS = int(ASSIST.get("max_words", 110))

# Узел базы, через который разговаривают приложение и сервис.
NODE = "assist"


def limit_words(text: str, limit: int = MAX_WORDS) -> str:
    """Обрезает текст до [limit] слов, стараясь закончить на границе предложения."""
    words = text.split()
    if len(words) <= limit:
        return text.strip()
    cut = words[:limit]
    trimmed = " ".join(cut)
    # Если последняя точка далеко позади — просто добавляем многоточие.
    end = max(trimmed.rfind("."), trimmed.rfind("!"), trimmed.rfind("?"))
    if end > len(trimmed) * 0.6:
        return trimmed[: end + 1]
    return trimmed.rstrip(",;:—") + "…"

# Что можно прислать в поле category. Список совпадает с приложением,
# чтобы нейросеть не придумывала категории вроде «прочее-непонятное».
CATEGORIES = [
    "одежда",
    "обувь",
    "ключи",
    "телефон и гаджеты",
    "рюкзак и сумка",
    "документы и карты",
    "деньги и кошелёк",
    "учебники и книги",
    "спортивная форма",
    "украшения и аксессуары",
    "другое",
]


def build_prompt(payload: dict) -> str:
    """Собирает запрос к модели из того, что заполнил человек."""
    ad_type = "пропажа" if str(payload.get("type", "")).lower() == "lost" else "находка"
    title = str(payload.get("title", "")).strip() or "вещь"
    place = str(payload.get("place", "")).strip() or "не указано"
    category = str(payload.get("category", "")).strip()

    lines = [
        "Ты помогаешь составить описание вещи для бюро находок школы.",
        f"Тип объявления: {ad_type}.",
        f"Вещь: {title}.",
        f"Место: {place}.",
    ]
    if category:
        lines.append(f"Категория: {category}.")
    action_hint = (
        "куда обратиться нашедшему" if ad_type == "пропажа" else "как владельцу забрать вещь"
    )
    lines.append(
        f"Напиши нейтральное описание объявления: 2–3 коротких предложения по-русски, "
        f"не больше {MAX_WORDS} слов. "
        "Опирайся только на данные выше: тип объявления, название вещи, место и категорию. "
        "Не описывай внешний вид вещи, которого не знаешь: цвет, материал, бренд, модель, "
        "размер, состояние и приметы не придумывай. "
        f"Кратко укажи место и {action_hint}. "
        "Не придумывай имена, телефоны, номера классов и точные адреса. "
        "Пиши только по-русски, без иероглифов. Не начинай со слов «описание» и не используй списки. Ответ — только текст описания."
    )
    return "\n".join(lines)


def ask_model(prompt: str) -> str:
    """Отправляет промпт в локальную LLM (llama.cpp / KoboldCpp через OpenAI API /v1/chat/completions)."""
    body = {
        "model": "local-llm",
        "messages": [
            {"role": "system", "content": (
                "Ты аккуратный помощник бюро находок школы и колледжа. Отвечай по-русски. "
                "Не выдумывай сведения о вещи: цвет, материал, бренд, модель, размер, "
                "состояние и приметы, которых нет в запросе, не упоминай. "
                "Пиши нейтрально и только по фактам из запроса. Отвечай только по-русски: без иероглифов и слов на других языках."
            )},
            {"role": "user", "content": prompt},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }
    request = urllib.request.Request(
        f"{KOBOLD_URL}/v1/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=KOBOLD_TIMEOUT) as response:
        data = json.loads(response.read().decode("utf-8"))
    text = data["choices"][0]["message"]["content"].strip()
    # Снимаем think-блоки, кавычки или звёздочки, если модель их добавила.
    if "</think>" in text:
        text = text.split("</think>", 1)[1].strip()
    text = text.strip().strip('"').strip("«»").strip("*").strip()
    return limit_words(text_clean.strip_foreign(" ".join(text.split())))


def build_fallback_description(payload: dict) -> str:
    """Резервный генератор: нейтральный текст без выдуманных примет."""
    is_lost = str(payload.get("type", "")).lower() == "lost"
    title = str(payload.get("title", "")).strip() or "вещь"
    place = str(payload.get("place", "")).strip()

    where = f" в районе «{place}»" if place else " на территории учебного заведения"
    if is_lost:
        return (
            f"Пропала вещь: {title}{where}. "
            "Нашедшего просьба написать во встроенный чат или связаться по контактам в профиле: "
            "приметы сверим при встрече."
        )
    return (
        f"Найдена вещь: {title}{where}. "
        "Владельца просьба написать в чат и назвать отличительные приметы для возврата по коду."
    )


def kobold_alive() -> bool:
    for path in ("/health", "/v1/models", "/api/v1/model"):
        try:
            with urllib.request.urlopen(f"{KOBOLD_URL}{path}", timeout=3) as response:
                if response.status == 200:
                    return True
        except Exception:
            continue
    return False


def describe_payload(payload: dict) -> tuple[str, str]:
    """
    Готовит описание: сначала модель, при её отсутствии — шаблон.
    Возвращает (текст, источник: llm | template). Текст всегда не длиннее MAX_WORDS.
    """
    source = "llm"
    try:
        text = ask_model(build_prompt(payload))
    except Exception as error:
        if not FALLBACK_WITHOUT_LLM:
            raise
        log.warning("LLM недоступна (%s), использую шаблонное описание", error)
        text = build_fallback_description(payload)
        source = "template"
    return limit_words(text), source


def now_ms() -> int:
    return int(time.time() * 1000)


def process_request(client: firebase_db.Firebase, key: str, value: dict) -> None:
    """Обрабатывает одну заявку из узла assist и пишет результат обратно в неё."""
    title = str(value.get("title", "")).strip()
    if not title:
        client.patch(
            f"{NODE}/{key}",
            {"status": "error", "error": "не указано название вещи", "finishedAt": now_ms()},
        )
        return

    client.patch(f"{NODE}/{key}", {"status": "running", "startedAt": now_ms()})
    try:
        text, source = describe_payload(value)
    except Exception as error:
        log.error("Заявка %s: модель не ответила (%s)", key, error)
        client.patch(
            f"{NODE}/{key}",
            {"status": "error", "error": str(error), "finishedAt": now_ms()},
        )
        return

    if not text:
        client.patch(
            f"{NODE}/{key}",
            {"status": "error", "error": "пустой ответ модели", "finishedAt": now_ms()},
        )
        return

    client.patch(
        f"{NODE}/{key}",
        {
            "status": "done",
            "text": text,
            "source": source,
            "words": len(text.split()),
            "finishedAt": now_ms(),
        },
    )
    log.info("Заявка %s: готово (%s, %d слов)", key, source, len(text.split()))


def scan_pending(client: firebase_db.Firebase) -> None:
    """Разбирает все заявки со статусом pending, которые сейчас лежат в узле."""
    try:
        items = client.get(NODE) or {}
    except Exception as error:
        log.warning("Не удалось прочитать /%s: %s", NODE, error)
        return
    for key, value in items.items():
        if isinstance(value, dict) and value.get("status") == "pending":
            process_request(client, key, value)


def poll_pending(client: firebase_db.Firebase, stop: threading.Event, every: int = 15) -> None:
    """
    Страховка на случай, если подписка отвалилась молча (сеть, сон ноутбука).
    Раз в 15 секунд просто смотрит узел: заявки не должны зависать в pending.
    """
    while not stop.wait(every):
        scan_pending(client)


def serve_node(client: firebase_db.Firebase, stop: threading.Event) -> None:
    """Держит подписку на узел assist и обрабатывает новые заявки сразу."""
    client.listen(NODE, lambda: scan_pending(client), stop)


class Handler(BaseHTTPRequestHandler):
    server_version = "MobilRingAssist/1.0"

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        log.info("%s — %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0] in ("/health", "/"):
            self._send(200, {"ok": True, "kobold": kobold_alive()})
            return
        self._send(404, {"error": "неизвестный путь"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?")[0] != "/describe":
            self._send(404, {"error": "неизвестный путь"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._send(400, {"error": "пустой запрос"})
            return

        try:
            # utf-8-sig — на случай BOM в начале тела (бывает у PowerShell и Windows-клиентов).
            payload = json.loads(self.rfile.read(length).decode("utf-8-sig"))
        except Exception as error:
            self._send(400, {"error": f"не разобрал JSON: {error}"})
            return

        title = str(payload.get("title", "")).strip()
        if not title:
            self._send(400, {"error": "не указано название вещи"})
            return

        category = str(payload.get("category", "")).strip()
        if category and category not in CATEGORIES:
            log.warning("неизвестная категория: %s", category)

        try:
            text, source = describe_payload(payload)
        except Exception as error:
            log.error("модель не ответила: %s", error)
            self._send(502, {"error": f"модель недоступна: {error}"})
            return

        if not text:
            self._send(502, {"error": "модель вернула пустой ответ"})
            return

        log.info("готово описание (%s) для «%s» (%d слов)", source, title, len(text.split()))
        self._send(200, {"text": text, "source": source, "words": len(text.split())})


def main() -> int:
    parser = argparse.ArgumentParser(description="Помощник описаний для MobilRing")
    parser.add_argument("--host", default=ASSIST.get("host", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(ASSIST.get("port", 5002)))
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="не подписываться на узел assist (только HTTP)",
    )
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    log.info("Слушаю http://%s:%d (LLM: %s)", args.host, args.port, KOBOLD_URL)
    if not kobold_alive():
        log.warning("Локальная LLM сейчас недоступна — запустите start-llama.ps1 (или start-kobold.ps1)")

    stop = threading.Event()
    worker: threading.Thread | None = None
    if not args.no_db:
        try:
            client = firebase_db.Firebase(CONFIG.get("firebase", {}))
        except Exception as error:
            log.error("Нет доступа к базе (%s) — работаю только по HTTP", error)
        else:
            worker = threading.Thread(
                target=serve_node, args=(client, stop), name="assist-db", daemon=True
            )
            worker.start()
            poller = threading.Thread(
                target=poll_pending, args=(client, stop), name="assist-poll", daemon=True
            )
            poller.start()
            log.info("Подписался на узел /%s (и опрашиваю его раз в 15 с)", NODE)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("остановлено")
    finally:
        stop.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
