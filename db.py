"""db.py — тонкий клиент Firebase Realtime Database по REST.

Нужен скриптам из Server2 (assist.py, matcher.py): вход служебным анонимным
пользователем, чтение и запись узлов, а также живая подписка на изменения (SSE).

Firebase Admin SDK в проекте не используется: всё, что нужно, доступно по REST
с токеном обычного пользователя. Токен обновляется по refresh-токену из `.auth.json`.
"""

from __future__ import annotations

import json
import logging
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_DIR = pathlib.Path(__file__).resolve().parent

log = logging.getLogger("db")


class Firebase:
    """
    Доступ к базе проекта MobilRing.

    server — секция `firebase` из config.json:
      database_url, google_services, auth_cache
    """

    def __init__(self, config: dict):
        self.base = str(config["database_url"]).rstrip("/")
        self.cache = BASE_DIR / config.get("auth_cache", ".auth.json")
        self.api_key = self._read_api_key(config["google_services"])
        self.token = ""
        self.uid = ""
        self._auth()

    # --- вход -------------------------------------------------------------

    def _read_api_key(self, raw_path: str) -> str:
        """Ищет google-services.json: сначала по конфигу, потом рядом с проектом."""
        raw = pathlib.Path(raw_path)
        candidates = [
            raw if raw.is_absolute() else (BASE_DIR / raw),
            BASE_DIR / "google-services.json",
            BASE_DIR.parent / "AndroidApp" / "app" / "google-services.json",
            BASE_DIR.parent / "MobilRing" / "app" / "google-services.json",
        ]
        services = next((p.resolve() for p in candidates if p.exists()), candidates[0])
        data = json.loads(services.read_text(encoding="utf-8"))
        return data["client"][0]["api_key"][0]["current_key"]

    def _auth(self) -> None:
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

    def _refresh(self, refresh_token: str) -> tuple[str, str]:
        url = f"https://securetoken.googleapis.com/v1/token?key={self.api_key}"
        body = urllib.parse.urlencode(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        ).encode()
        request = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data["id_token"], data["user_id"]

    def _post(self, url: str, payload: dict) -> dict:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def renew(self) -> None:
        """Продлевает сессию перед долгой подпиской или после истечения токена."""
        try:
            stored = json.loads(self.cache.read_text(encoding="utf-8"))
            self.token, self.uid = self._refresh(stored["refresh_token"])
        except Exception as error:
            log.warning("Продлить сессию не удалось (%s), вхожу заново", error)
            self._auth()

    # --- данные -----------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.base}/{path}.json?auth={self.token}"

    def get(self, path: str):
        request = urllib.request.Request(self._url(path))
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def put(self, path: str, value) -> None:
        request = urllib.request.Request(
            self._url(path),
            data=json.dumps(value, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="PUT",
        )
        urllib.request.urlopen(request, timeout=30).close()

    def patch(self, path: str, value: dict) -> None:
        request = urllib.request.Request(
            self._url(path),
            data=json.dumps(value, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="PATCH",
        )
        urllib.request.urlopen(request, timeout=30).close()

    def delete(self, path: str) -> None:
        request = urllib.request.Request(self._url(path), method="DELETE")
        urllib.request.urlopen(request, timeout=30).close()

    # --- подписка ---------------------------------------------------------

    def listen(self, path: str, on_change, stop) -> None:
        """
        Живая подписка на узел через REST streaming (SSE).

        on_change() вызывается на каждое изменение, stop — threading.Event для выхода.
        При разрыве связи переподключается: сначала через renew(), потом с паузой.
        """
        while not stop.is_set():
            try:
                request = urllib.request.Request(
                    self._url(path),
                    headers={"Accept": "text/event-stream"},
                )
                with urllib.request.urlopen(request, timeout=300) as response:
                    log.info("Подписка на /%s открыта", path)
                    on_change()
                    for raw in response:
                        if stop.is_set():
                            return
                        line = raw.decode("utf-8", "replace").strip()
                        if line.startswith("event:") or not line:
                            continue
                        if line.startswith("data:"):
                            payload = line[5:].strip()
                            if payload and payload != "null":
                                on_change()
            except Exception as error:
                if stop.is_set():
                    return
                log.warning("Подписка на /%s прервалась (%s), переподключаюсь", path, error)
                time.sleep(2)
                try:
                    self.renew()
                except Exception as renew_error:
                    log.warning("Продлить сессию не удалось: %s", renew_error)
