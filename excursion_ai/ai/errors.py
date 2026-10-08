from __future__ import annotations


class AIError(Exception):
    """Ошибка ИИ-консультанта без персональных данных в сообщении."""

    def __init__(self, code: str, *, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class AIUnavailableError(AIError):
    def __init__(self, code: str = "disabled") -> None:
        super().__init__(code)


class AIConfigError(AIError):
    def __init__(self, code: str = "not_configured") -> None:
        super().__init__(code)


class AILimitError(AIError):
    pass


class AIBusyError(AIError):
    def __init__(self, code: str = "busy") -> None:
        super().__init__(code)


class AIValidationError(AIError):
    def __init__(self, code: str = "invalid_response") -> None:
        super().__init__(code)
