"""assist.py — помощь в составлении описания объявления.

Маленький HTTP-сервис, который принимает черновик объявления от приложения
и возвращает готовый текст описания, сгенерированный локальной моделью (KoboldCpp).

Зачем отдельный сервис, а не прямой вызов модели из телефона:
  * адрес и параметры модели живут в одном месте (config.json);
  * телефону не нужно знать про порт KoboldCpp и его формат запросов;
  * промпт правится здесь же, без пересборки APK.

Эндпоинты:
    GET  /health    — проверка живости: {"ok": true, "kobold": true/false}
    POST /describe  — {"type": "lost"|"found", "title": "...", "place": "...", "category": "..."}
                      отвечает {"text": "..."} либо {"error": "..."}

Запуск:
    python assist.py                # адрес и порт из config.json
    python assist.py --port 5002
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
    lines.append(
        "Напиши описание вещи для объявления: 2–3 коротких предложения по-русски. "
        "Опиши только то, что можно предположить о такой вещи: цвет, материал, размер, "
        "характерные приметы, состояние, что делать нашедшему. "
        "Не придумывай имена, телефоны, номера классов и точные адреса. "
        "Не начинай со слов «описание» и не используй списки. Ответ — только текст описания."
    )
    return "\n".join(lines)


def ask_model(prompt: str) -> str:
    """Отправляет промпт в KoboldCpp (OpenAI-совместимый /v1/chat/completions)."""
    body = {
        "model": "koboldcpp",
        "messages": [
            {"role": "system", "content": "Ты аккуратный помощник бюро находок. Отвечай по-русски."},
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
    # Модель любит обрамлять ответ кавычками или звёздочками — снимаем это.
    text = text.strip().strip('"').strip("«»").strip()
    return " ".join(text.split())


def kobold_alive() -> bool:
    try:
        with urllib.request.urlopen(f"{KOBOLD_URL}/api/v1/model", timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


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
            text = ask_model(build_prompt(payload))
        except Exception as error:
            log.error("модель не ответила: %s", error)
            self._send(502, {"error": f"модель недоступна: {error}"})
            return

        if not text:
            self._send(502, {"error": "модель вернула пустой ответ"})
            return

        log.info("готово описание для «%s» (%d символов)", title, len(text))
        self._send(200, {"text": text})


def main() -> int:
    parser = argparse.ArgumentParser(description="Помощник описаний для MobilRing")
    parser.add_argument("--host", default=ASSIST.get("host", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(ASSIST.get("port", 5002)))
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    log.info("Слушаю http://%s:%d (KoboldCpp: %s)", args.host, args.port, KOBOLD_URL)
    if not kobold_alive():
        log.warning("KoboldCpp сейчас недоступен — запустите start-kobold.ps1")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("остановлено")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
