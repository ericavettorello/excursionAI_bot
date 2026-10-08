from __future__ import annotations

from pydantic import BaseModel, Field


class ConsultantReply(BaseModel):
    """Структурированный ответ консультанта."""

    answer: str = Field(min_length=1, max_length=4000)
    recommended_excursion_ids: list[str] = Field(default_factory=list)


class TranslatedLanguageBlock(BaseModel):
    """Перевод текстовых полей экскурсии на один язык."""

    language: str = Field(min_length=2, max_length=2)
    title: str = Field(min_length=2, max_length=200)
    description: str = Field(min_length=10, max_length=2000)
    route: list[str] = Field(min_length=3, max_length=4)
    meeting_point: str = Field(min_length=5, max_length=500)


class ExcursionTranslationsReply(BaseModel):
    """Структурированный ответ автоперевода на два целевых языка."""

    translations: list[TranslatedLanguageBlock] = Field(min_length=2, max_length=2)
