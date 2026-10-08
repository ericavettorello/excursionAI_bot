"""Публичный номер заявки: 0001, 0002, … (минимум 4 цифры)."""

from __future__ import annotations


def format_public_number(value: int) -> str:
    if value <= 0:
        raise ValueError("public_number_must_be_positive")
    return f"{value:04d}"
