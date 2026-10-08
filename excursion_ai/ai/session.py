from __future__ import annotations

import time
from dataclasses import dataclass, field


MAX_PAIRS = 6
IDLE_TTL_SECONDS = 30 * 60


@dataclass
class TurnPair:
    user_text: str
    assistant_text: str
    created_at: float = field(default_factory=time.monotonic)


@dataclass
class UserSession:
    pairs: list[TurnPair] = field(default_factory=list)
    last_activity: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def is_expired(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return (now - self.last_activity) > IDLE_TTL_SECONDS


class ConversationStore:
    """Контекст консультации в памяти процесса (не переживает перезапуск)."""

    def __init__(self, *, max_pairs: int = MAX_PAIRS, idle_ttl: int = IDLE_TTL_SECONDS) -> None:
        self._sessions: dict[int, UserSession] = {}
        self._max_pairs = max_pairs
        self._idle_ttl = idle_ttl

    def clear(self, telegram_user_id: int) -> None:
        self._sessions.pop(telegram_user_id, None)

    def get_pairs(self, telegram_user_id: int) -> list[TurnPair]:
        session = self._sessions.get(telegram_user_id)
        if session is None:
            return []
        if session.is_expired():
            self.clear(telegram_user_id)
            return []
        session.touch()
        return list(session.pairs)

    def add_pair(self, telegram_user_id: int, user_text: str, assistant_text: str) -> None:
        session = self._sessions.get(telegram_user_id)
        if session is None or session.is_expired():
            session = UserSession()
            self._sessions[telegram_user_id] = session
        session.pairs.append(TurnPair(user_text=user_text, assistant_text=assistant_text))
        if len(session.pairs) > self._max_pairs:
            session.pairs = session.pairs[-self._max_pairs :]
        session.touch()

    def build_input_messages(
        self,
        telegram_user_id: int,
        user_text: str,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for pair in self.get_pairs(telegram_user_id):
            messages.append({"role": "user", "content": pair.user_text})
            messages.append({"role": "assistant", "content": pair.assistant_text})
        messages.append({"role": "user", "content": user_text})
        return messages
