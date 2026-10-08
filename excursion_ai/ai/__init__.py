"""ИИ-консультант: OpenAI Responses API, лимиты и сессии."""

from excursion_ai.ai.errors import AIError, AIUnavailableError
from excursion_ai.ai.service import AIConsultantService

__all__ = [
    "AIConsultantService",
    "AIError",
    "AIUnavailableError",
]
