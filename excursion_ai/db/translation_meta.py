from __future__ import annotations

import hashlib
import json
from typing import Any

from excursion_ai.config import SUPPORTED_LANGS

TRANSLATION_TEXT_FIELDS = ("title", "description", "route", "meeting_point")


def fingerprint_translation_block(block: dict[str, Any]) -> str:
    """Хеш исходного текста перевода (без city и метаданных)."""
    route = block.get("route") or []
    if not isinstance(route, list):
        route = []
    payload = {
        "title": str(block.get("title") or "").strip(),
        "description": str(block.get("description") or "").strip(),
        "route": [str(item).strip() for item in route],
        "meeting_point": str(block.get("meeting_point") or "").strip(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def other_languages(source_lang: str) -> tuple[str, ...]:
    return tuple(code for code in SUPPORTED_LANGS if code != source_lang)
