"""text_clean.py — чистка ответов локальной модели от иероглифов.

Модель иногда дописывает к русскому тексту кусок на китайском. Для объявления
это мусор: читатель видит иероглифы вместо примет вещи. Поэтому все ответы
модели проходят через [strip_cjk].

Логика простая: режем текст на предложения и выбрасываем те, где есть иероглифы.
Если после этого ничего не осталось — вырезаем иероглифы посимвольно, чтобы
сохранить русские слова.
"""

from __future__ import annotations

import re

# Иероглифы, китайская пунктуация и полноширинные формы.
CJK_RE = re.compile(
    "[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef"
    "\U00020000-\U0002ffff]"
)

# Корейский и японские слоговые азбуки — тоже не нужны в русском описании.
OTHER_SCRIPTS_RE = re.compile(
    "[\u3040-\u30ff\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]"
)

STRIP_RE = re.compile(f"(?:{CJK_RE.pattern}|{OTHER_SCRIPTS_RE.pattern})+")


def has_foreign(text: str) -> bool:
    return bool(text) and bool(STRIP_RE.search(text))


def strip_foreign(text: str) -> str:
    """Убирает предложения с иероглифами, сохраняя остальной русский текст."""
    if not text:
        return text or ""

    parts = re.split(r"(?<=[.!?…。！？])\s*", text.strip())
    kept = [part for part in parts if part and not STRIP_RE.search(part)]
    cleaned = " ".join(kept).strip()

    if not cleaned:
        cleaned = STRIP_RE.sub(" ", text)

    return " ".join(cleaned.split())
