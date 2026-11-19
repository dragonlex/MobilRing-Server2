"""Поиск совпадений между пропажами и находками MobilRing."""

import argparse
import json
import logging
import pathlib
import re
import sys
import time

import requests

BASE_DIR = pathlib.Path(__file__).resolve().parent

STOPWORDS = {
    "и", "в", "на", "у", "с", "со", "к", "ко", "по", "из", "от", "до", "для", "о", "об", "обо",
    "не", "но", "что", "это", "я", "мы", "он", "она", "они", "мой", "моя", "моё", "мое", "мои",
    "наш", "наша", "ваш", "был", "была", "было", "были", "есть", "нет", "же", "ли", "бы", "как",
    "то", "тот", "та", "те", "если", "при", "над", "под", "за", "без", "или", "так", "там", "тут",
    "очень", "ещё", "еще", "уже", "только", "потом", "когда", "где", "который", "которая",
}

SYNONYMS = {
    "телефон": {"телефон", "мобильник", "мобилка", "смартфон", "айфон", "iphone", "андроид", "сотовый"},
    "кошелёк": {"кошелёк", "кошелек", "бумажник", "портмоне"},
    "ключи": {"ключи", "ключ", "связка", "брелок", "ключница"},
    "сумка": {"сумка", "сумочка", "рюкзак", "портфель", "барсетка", "шоппер"},
    "обувь": {"обувь", "сменка", "сменная", "кроссовки", "кеды", "ботинки", "туфли", "тапки", "сапоги", "мешок"},
    "форма": {"форма", "спортивная", "спортивка", "физра", "физкультура", "костюм", "футболка", "шорты", "трико"},
    "пенал": {"пенал", "ручка", "карандаш", "ластик", "канцелярия"},
    "учебник": {"учебник", "книга", "тетрадь", "дневник", "конспект", "атлас", "зачетка"},
    "очки": {"очки", "оправа", "очков"},
    "документы": {"документы", "паспорт", "права", "удостоверение", "свидетельство", "снилс", "студенческий"},
    "карта": {"карта", "карточка", "банковская", "пропуск", "сбербанк", "школьная", "проездной", "бейдж"},
    "ноутбук": {"ноутбук", "лэптоп", "макбук", "macbook", "нетбук"},
    "планшет": {"планшет", "ipad", "айпад"},
    "наушники": {"наушники", "наушник", "гарнитура", "airpods", "эйрподс"},
    "часы": {"часы", "смарт", "фитнес", "браслет"},
    "кошка": {"кошка", "кот", "котёнок", "котенок", "котик", "киса"},
    "собака": {"собака", "пёс", "пес", "щенок", "собачка", "щенок"},
    "велосипед": {"велосипед", "вело", "байк", "самокат"},
    "зонт": {"зонт", "зонтик"},
    "куртка": {"куртка", "пуховик", "пальто", "ветровка", "плащ"},
    "шапка": {"шапка", "кепка", "шляпа", "берет"},
    "перчатки": {"перчатки", "варежки", "рукавицы"},
    "игрушка": {"игрушка", "мягкая", "мишка", "кукла", "плюшевый"},
    "кольцо": {"кольцо", "серьги", "серьга", "цепочка", "браслет", "украшение", "ювелирное"},
}

CANON = {}
for canon, forms in SYNONYMS.items():
    for form in forms:
        CANON[form] = canon

SUFFIXES = (
    "ами", "ями", "ов", "ев", "ые", "ая", "ое", "ий", "ый", "ой", "ую", "юю", "ах", "ях", "ом",
    "ем", "ам", "ям", "ии", "ия", "ов", "а", "я", "ы", "и", "у", "ю", "е", "о", "ь",
)
SUFFIXES = tuple(sorted(SUFFIXES, key=len, reverse=True))

TOKEN_RE = re.compile(r"[а-яa-z0-9]+")
NUM_RE = re.compile(r"\d+")
COLORS = {
    "черный", "белый", "красный", "синий", "голубой", "зелёный", "зеленый", "жёлтый", "желтый",
    "серый", "коричневый", "розовый", "фиолетовый", "оранжевый", "бежевый", "бордовый", "золотой",
    "серебристый", "бирюзовый", "сиреневый", "мятный",
}

log = logging.getLogger("matcher")


def normalize(text: str) -> str:
    text = (text or "").lower().replace("ё", "е")
    text = re.sub(r"[^а-яa-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def stem(word: str) -> str:
    if len(word) <= 3:
        return word
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def tokens(text: str) -> set:
    result = set()
    for raw in TOKEN_RE.findall(normalize(text)):
        if raw in STOPWORDS or len(raw) < 2:
            continue
        result.add(CANON.get(raw, stem(raw)))
    return result


def numbers(text: str) -> set:
    return set(NUM_RE.findall(text or ""))


def colors(text: str) -> set:
    words = set(TOKEN_RE.findall(normalize(text)))
    return {CANON.get(w, w) for w in words if w in COLORS}


def dice(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return 2.0 * len(a & b) / (len(a) + len(b))


def ratio(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def field(record: dict, name: str, default=""):
    if name in record:
        return record[name]
    for alt in (name.lower(), name.capitalize(), name.upper()):
        if alt in record:
            return record[alt]
    return default


def score_pair(lost: dict, found: dict) -> tuple:
    lost_name, found_name = field(lost, "Name"), field(found, "Name")
    lost_info, found_info = field(lost, "Info"), field(found, "Info")
    lost_place, found_place = field(lost, "Location"), field(found, "Location")

    # Названия сравниваем и по словам, и целиком: «кружка» и «кружас» — опечатка,
    # поэтому берём лучшую из двух оценок, а не только точное совпадение слов.
    title_score = max(
        dice(tokens(lost_name), tokens(found_name)),
        ratio(normalize(lost_name), normalize(found_name)),
    )
    info_score = dice(tokens(lost_info), tokens(found_info))
    place_score = dice(tokens(lost_place), tokens(found_place))
    text_score = ratio(normalize(f"{lost_name} {lost_info}"), normalize(f"{found_name} {found_info}"))

    base = 0.42 * title_score + 0.22 * info_score + 0.16 * place_score + 0.20 * text_score

    bonus = 0.0
    if numbers(lost_name + " " + lost_info) & numbers(found_name + " " + found_info):
        bonus += 0.08
    if colors(lost_name + " " + lost_info) & colors(found_name + " " + found_info):
        bonus += 0.06

    # Категория вещи: совпадение — плюс, разные категории — минус.
    category_score = 0.0
    lost_category, found_category = field(lost, "Category"), field(found, "Category")
    if lost_category and found_category:
        if normalize(lost_category) == normalize(found_category):
            category_score = 1.0
            bonus += 0.10
        else:
            bonus -= 0.05

    details = {
        "title": round(title_score, 3),
        "info": round(info_score, 3),
        "place": round(place_score, 3),
        "text": round(text_score, 3),
        "category": round(category_score, 3),
        "bonus": round(bonus, 3),
    }
    return round(max(0.0, min(1.0, base + bonus)), 3), details


class FirebaseClient:
    def __init__(self, config: dict):
        self.base = config["database_url"].rstrip("/")
        self.cache = BASE_DIR / config.get("auth_cache", ".auth.json")
        raw_services = pathlib.Path(config["google_services"])
        candidates = [
            raw_services if raw_services.is_absolute() else (BASE_DIR / raw_services),
            BASE_DIR / "google-services.json",
            BASE_DIR.parent / "MobilRing" / "app" / "google-services.json",
            BASE_DIR.parent / "MobilRing" / "google-services.json",
        ]
        services = next((p.resolve() for p in candidates if p.exists()), candidates[0])
        data = json.loads(services.read_text(encoding="utf-8"))
        self.api_key = data["client"][0]["api_key"][0]["current_key"]
        self.token = ""
        self.uid = ""
        self._auth()

    def _auth(self):
        if self.cache.exists():
            try:
                stored = json.loads(self.cache.read_text(encoding="utf-8"))
                self.token, self.uid = self._refresh(stored["refresh_token"])
                return
            except Exception as error:
                log.warning("Не удалось обновить сессию (%s), вхожу заново", error)
        payload = self._post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={self.api_key}",
            {"returnSecureToken": True},
        )
        self.token = payload["idToken"]
        self.uid = payload["localId"]
        self.cache.write_text(
            json.dumps({"refresh_token": payload["refreshToken"], "uid": self.uid}, indent=2),
            encoding="utf-8",
        )
        log.info("Создана служебная сессия, uid=%s", self.uid)

    def _refresh(self, refresh_token: str):
        response = requests.post(
            f"https://securetoken.googleapis.com/v1/token?key={self.api_key}",
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data["id_token"], data["user_id"]

    @staticmethod
    def _post(url: str, payload: dict) -> dict:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()

    def get(self, path: str):
        response = requests.get(f"{self.base}/{path}.json", params={"auth": self.token}, timeout=40)
        response.raise_for_status()
        return response.json()

    def put(self, path: str, payload: dict) -> dict:
        response = requests.put(
            f"{self.base}/{path}.json", params={"auth": self.token}, json=payload, timeout=40
        )
        response.raise_for_status()
        return response.json()

    def delete(self, path: str):
        response = requests.delete(
            f"{self.base}/{path}.json", params={"auth": self.token}, timeout=40
        )
        response.raise_for_status()
        return response.json()


class KoboldClient:
    def __init__(self, config: dict):
        self.enabled = bool(config.get("enabled"))
        self.base = config.get("base_url", "http://127.0.0.1:5001").rstrip("/")
        self.api = config.get("api", "openai")
        self.timeout = int(config.get("timeout", 180))
        self.max_tokens = int(config.get("max_tokens", 220))
        self.available = False
        if self.enabled:
            self.available = self._probe()
            if not self.available:
                log.warning("KoboldCpp недоступен по %s — работаю в режиме эвристики", self.base)

    def _probe(self) -> bool:
        for path in ("/health", "/v1/models", "/api/v1/model"):
            try:
                response = requests.get(f"{self.base}{path}", timeout=5)
                if response.status_code == 200:
                    log.info("Локальная LLM доступна (%s%s)", self.base, path)
                    return True
            except requests.RequestException:
                continue
        return False

    def ensure_available(self) -> bool:
        if not self.enabled:
            return False
        if not self.available:
            self.available = self._probe()
        return self.available

    def ask(self, prompt: str) -> str:
        if not self.available:
            raise RuntimeError("KoboldCpp недоступен")
        if self.api == "raw":
            response = requests.post(
                f"{self.base}/api/v1/generate",
                json={
                    "prompt": prompt,
                    "max_length": self.max_tokens,
                    "temperature": 0.0,
                    "top_p": 1.0,
                    "stop_sequence": ["<|im_end|>", "\n\n\n"],
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()["results"][0]["text"]

        response = requests.post(
            f"{self.base}/v1/chat/completions",
            json={
                "messages": [
                    {
                        "role": "system",
                        "content": "Ты помогаешь бюро находок. Отвечаешь только JSON без пояснений.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": self.max_tokens,
                "stop": ["<|im_end|>"],
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    def verdict(self, lost: dict, found: dict) -> dict:
        prompt = build_prompt(lost, found)
        raw = self.ask(prompt)
        parsed = parse_json(raw)
        if parsed is None:
            log.warning("Модель вернула не JSON: %s", raw.strip()[:200])
            return {"ok": False, "reason": "нечитаемый ответ модели", "raw": raw.strip()[:400]}
        same = bool(parsed.get("same") or parsed.get("match"))
        try:
            confidence = float(parsed.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        return {
            "ok": True,
            "same": same,
            "confidence": max(0.0, min(1.0, confidence)),
            "reason": str(parsed.get("reason", ""))[:300],
            "raw": raw.strip()[:400],
        }


def build_prompt(lost: dict, found: dict) -> str:
    return (
        "Сравни два объявления бюро находок. Реши, могут ли они описывать одну и ту же вещь.\n"
        "Правила:\n"
        "- Названия могут быть с опечатками и искажениями: «кружка» и «кружас» — это одна и та же вещь.\n"
        "- Короткие и бессмысленные описания («крутая», «приуольная») ничего не доказывают "
        "и не могут быть признаком разных вещей.\n"
        "- Совпадение места и типа вещи — сильный довод в пользу совпадения.\n"
        "- Считай вещи разными, только если различается сам тип (ключи и рюкзак) "
        "или в описаниях есть явно несовместимые приметы (цвет, размер, надпись).\n"
        "Пропажа:\n"
        f"  название: {field(lost, 'Name')}\n"
        f"  категория: {field(lost, 'Category')}\n"
        f"  место: {field(lost, 'Location')}\n"
        f"  описание: {field(lost, 'Info')}\n"
        "Находка:\n"
        f"  название: {field(found, 'Name')}\n"
        f"  категория: {field(found, 'Category')}\n"
        f"  место: {field(found, 'Location')}\n"
        f"  описание: {field(found, 'Info')}\n"
        'Ответ строго в формате: {"same": true или false, "confidence": число от 0 до 1, '
        '"reason": "короткое объяснение по-русски"}'
    )


def parse_json(text: str):
    if not text:
        return None
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None


def pair_key(lost_key: str, found_key: str) -> str:
    return f"auto-{lost_key}-{found_key}"


def existing_pairs(matches: dict) -> set:
    result = set()
    for item in (matches or {}).values():
        if not isinstance(item, dict):
            continue
        lost = str(item.get("lostId", "")).strip()
        found = str(item.get("findId", "")).strip()
        if lost and found:
            result.add(frozenset((lost, found)))
    return result


def run_once(firebase: FirebaseClient, kobold: KoboldClient, settings: dict, dry_run: bool) -> int:
    lost_raw = firebase.get("lost") or {}
    found_raw = firebase.get("found") or {}
    matches_raw = firebase.get("matches") or {}

    if settings.get("closed_skip", True):
        lost_raw = {k: v for k, v in lost_raw.items() if not v.get("closed")}
        found_raw = {k: v for k, v in found_raw.items() if not v.get("closed")}

    if not lost_raw or not found_raw:
        log.info("Недостаточно данных: потерь %d, находок %d", len(lost_raw), len(found_raw))
        return 0

    known = existing_pairs(matches_raw)
    min_score = float(settings.get("min_score", 0.22))
    heuristic_min = float(settings.get("heuristic_only_min_score", 0.45))
    llm_min = float(settings.get("llm_min_confidence", 0.6))
    allow_same_user = bool(settings.get("allow_same_user", True))
    max_candidates = int(settings.get("max_candidates", 15))

    candidates = []
    for lost_key, lost in lost_raw.items():
        for found_key, found in found_raw.items():
            if frozenset((lost_key, found_key)) in known:
                continue
            if not allow_same_user and lost.get("userId") and lost.get("userId") == found.get("userId"):
                continue
            score, details = score_pair(lost, found)
            if score < min_score:
                continue
            candidates.append((score, details, lost_key, lost, found_key, found))

    candidates.sort(key=lambda item: item[0], reverse=True)
    candidates = candidates[:max_candidates]
    log.info("Кандидатов после отсева: %d", len(candidates))

    created = 0
    llm_ready = kobold.ensure_available()
    for score, details, lost_key, lost, found_key, found in candidates:
        verdict = {"ok": False, "reason": "LLM выключена"}
        if llm_ready:
            try:
                verdict = kobold.verdict(lost, found)
            except Exception as error:
                log.warning("Ошибка запроса к модели: %s", error)
                verdict = {"ok": False, "reason": f"ошибка модели: {error}"}

        if verdict.get("ok"):
            accepted = bool(verdict.get("same")) and float(verdict.get("confidence", 0)) >= llm_min
            method = "llm"
            confidence = float(verdict.get("confidence", 0))
            reason = verdict.get("reason", "")
        else:
            accepted = score >= heuristic_min
            method = "heuristic"
            confidence = score
            reason = verdict.get("reason", "")

        summary = (
            f"{field(lost, 'Name') or '?'} <-> {field(found, 'Name') or '?'} | score={score} "
            f"conf={confidence} метод={method}"
        )
        if not accepted:
            log.info("Отклонено: %s | %s", summary, reason)
            continue

        payload = {
            "lostId": lost_key,
            "findId": found_key,
            "createdAt": int(time.time() * 1000),
            "score": score,
            "confidence": round(confidence, 3),
            "method": method,
            "reason": reason,
            "details": details,
            "source": "matcher",
        }
        key = pair_key(lost_key, found_key)
        if dry_run:
            log.info("DRY RUN: %s -> %s", summary, key)
            continue
        firebase.put(f"matches/{key}", payload)
        created += 1
        log.info("Совпадение записано: %s | %s | %s", summary, reason, key)

    log.info("Готово, новых совпадений: %d", created)
    return created


def setup_logging(settings: dict):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    level = getattr(logging, str(settings.get("level", "INFO")).upper(), logging.INFO)
    log.setLevel(level)
    log.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    log.addHandler(console)

    file_name = settings.get("file")
    if file_name:
        handler = logging.FileHandler(BASE_DIR / file_name, encoding="utf-8")
        handler.setFormatter(formatter)
        log.addHandler(handler)


def main() -> int:
    parser = argparse.ArgumentParser(description="Поиск совпадений пропаж и находок MobilRing")
    parser.add_argument("--config", default=str(BASE_DIR / "config.json"))
    parser.add_argument("--once", action="store_true", help="один проход и выход")
    parser.add_argument("--no-llm", action="store_true", help="только эвристика, без KoboldCpp")
    parser.add_argument("--dry-run", action="store_true", help="не писать в базу")
    args = parser.parse_args()

    config = json.loads(pathlib.Path(args.config).read_text(encoding="utf-8"))
    setup_logging(config.get("logging", {}))

    if args.no_llm:
        config.setdefault("kobold", {})["enabled"] = False

    settings = config.get("matching", {})
    settings["max_candidates"] = config.get("kobold", {}).get("max_candidates", 15)

    firebase = FirebaseClient(config["firebase"])
    kobold = KoboldClient(config.get("kobold", {}))

    interval = max(10, int(settings.get("interval_sec", 60)))
    if args.once:
        run_once(firebase, kobold, settings, args.dry_run)
        return 0

    log.info("Запущен цикл поиска совпадений, интервал %d с", interval)
    try:
        while True:
            try:
                run_once(firebase, kobold, settings, args.dry_run)
            except requests.HTTPError as error:
                if error.response is not None and error.response.status_code == 401:
                    log.warning("Токен отклонён, обновляю сессию")
                    firebase._auth()
                else:
                    log.error("HTTP ошибка: %s", error)
            except requests.RequestException as error:
                log.error("Сеть недоступна: %s", error)
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Остановлено пользователем")
    return 0


if __name__ == "__main__":
    sys.exit(main())
