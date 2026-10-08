"""Атомарное хранение OAuth-токенов amoCRM (без записи секретов в логи)."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from excursion_ai.log_context import register_secret_value


@dataclass
class OAuthTokens:
    access_token: str
    refresh_token: str


class TokenStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self, *, fallback: OAuthTokens | None = None) -> OAuthTokens | None:
        if self._path.is_file():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = None
            if isinstance(raw, dict):
                access = str(raw.get("access_token") or "").strip()
                refresh = str(raw.get("refresh_token") or "").strip()
                if access or refresh:
                    register_secret_value(access or None)
                    register_secret_value(refresh or None)
                    return OAuthTokens(access_token=access, refresh_token=refresh)
        return fallback

    def save(self, tokens: OAuthTokens) -> None:
        register_secret_value(tokens.access_token or None)
        register_secret_value(tokens.refresh_token or None)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token,
            },
            ensure_ascii=False,
        )
        fd, tmp_name = tempfile.mkstemp(
            prefix="amocrm_tokens_",
            suffix=".json",
            dir=str(self._path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self._path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
