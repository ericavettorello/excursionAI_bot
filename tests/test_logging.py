"""Проверки единой системы логирования ExcursionAI."""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

from excursion_ai.console import configure_console
from excursion_ai.log_context import (
    log_event,
    log_exception,
    new_event_id,
    redact_secrets,
    reset_event_id,
    set_event_id,
)
from excursion_ai.logging_setup import reset_logging_for_tests, setup_logging


def test_redact_fake_telegram_token() -> None:
    fake = "123456789:AAExampleFakeTokenValue_1234567890"
    text = f"Authorization failed for bot{fake} in url https://api.telegram.org/bot{fake}/getMe"
    redacted = redact_secrets(text)
    assert fake not in redacted
    assert "[REDACTED_TOKEN]" in redacted
    assert "BOT_TOKEN=super-secret-value-12345" not in redact_secrets(
        "BOT_TOKEN=super-secret-value-12345"
    )


def test_log_file_utf8_and_no_duplicate_setup() -> None:
    configure_console()
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "app.log"
        try:
            setup_logging(log_level="INFO", log_file=log_path, force=True)
            setup_logging(log_level="DEBUG", log_file=log_path, force=False)

            root = logging.getLogger()
            assert len(root.handlers) == 2

            event_id = new_event_id()
            token = set_event_id(event_id)
            logger = logging.getLogger("excursion_ai.tests.logging")
            log_event(logger, action="catalog.open", stage="list", result="ok", count=4)
            log_event(
                logger,
                action="catalog.view",
                stage="card",
                result="ok",
                excursion_id="padua-intro",
            )
            log_event(
                logger,
                action="menu.about",
                stage="open",
                result="ok",
                note="Проверка кириллицы: экскурсия",
            )
            reset_event_id(token)

            for handler in root.handlers:
                handler.flush()

            content = log_path.read_text(encoding="utf-8")
            assert "Проверка кириллицы: экскурсия" in content
            assert f"event_id={event_id}" in content
            assert "action=catalog.open" in content
            assert "action=catalog.view" in content
            assert content.count(f"event_id={event_id}") == 3
            assert "user_id=" not in content
        finally:
            reset_logging_for_tests()


def test_traceback_once_for_controlled_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "errors.log"
        try:
            setup_logging(log_level="INFO", log_file=log_path, force=True)
            logger = logging.getLogger("excursion_ai.tests.errors")
            event_id = new_event_id()
            token = set_event_id(event_id)
            try:
                raise RuntimeError(
                    "controlled failure with token 999000111:AAFakeTokenForMaskingTests_XYZ"
                )
            except RuntimeError as exc:
                log_exception(
                    logger,
                    action="error.unhandled",
                    stage="handler",
                    exc=exc,
                    error_id=event_id,
                )
                log_exception(
                    logger,
                    action="error.unhandled",
                    stage="handler",
                    exc=exc,
                    error_id=event_id,
                )
            reset_event_id(token)
            for handler in logging.getLogger().handlers:
                handler.flush()

            content = log_path.read_text(encoding="utf-8")
            assert "RuntimeError" in content
            assert "Traceback" in content
            assert content.count("Traceback (most recent call last)") == 1
            assert "AAFakeTokenForMaskingTests_XYZ" not in content
            assert "[REDACTED_TOKEN]" in content
            assert f"event_id={event_id}" in content
        finally:
            reset_logging_for_tests()


def test_rotation_on_temp_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "rotate.log"
        try:
            setup_logging(
                log_level="INFO",
                log_file=log_path,
                max_bytes=800,
                backup_count=3,
                force=True,
            )
            logger = logging.getLogger("excursion_ai.tests.rotation")
            for index in range(80):
                log_event(
                    logger,
                    action="app.bootstrap",
                    stage="validate",
                    result="ok",
                    index=index,
                    payload="x" * 40,
                )
            for handler in logging.getLogger().handlers:
                handler.flush()
            backups = list(Path(tmp).glob("rotate.log*"))
            assert log_path.exists()
            assert len(backups) >= 2
        finally:
            reset_logging_for_tests()


def test_event_id_links_stages() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "flow.log"
        try:
            setup_logging(log_level="INFO", log_file=log_path, force=True)
            logger = logging.getLogger("excursion_ai.tests.flow")
            event_id = new_event_id()
            token = set_event_id(event_id)
            log_event(
                logger,
                action="event.receive",
                stage="accepted",
                result="ok",
                payload="text",
            )
            log_event(
                logger,
                action="language.select",
                stage="apply",
                result="ok",
                lang="en",
            )
            log_event(
                logger,
                action="catalog.open",
                stage="list",
                result="ok",
                count=4,
            )
            reset_event_id(token)
            for handler in logging.getLogger().handlers:
                handler.flush()

            lines = [
                line
                for line in log_path.read_text(encoding="utf-8").splitlines()
                if f"event_id={event_id}" in line
            ]
            assert len(lines) == 3
            actions = re.findall(r"action=([^\s]+)", "\n".join(lines))
            assert actions == ["event.receive", "language.select", "catalog.open"]
        finally:
            reset_logging_for_tests()


def main() -> None:
    configure_console()
    tests = [
        test_redact_fake_telegram_token,
        test_log_file_utf8_and_no_duplicate_setup,
        test_traceback_once_for_controlled_error,
        test_rotation_on_temp_files,
        test_event_id_links_stages,
    ]
    try:
        for test in tests:
            test()
            print(f"OK {test.__name__}")
        print(f"Все проверки пройдены: {len(tests)}")
    finally:
        reset_logging_for_tests()


if __name__ == "__main__":
    main()
