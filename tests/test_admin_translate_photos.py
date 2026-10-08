"""Проверки fingerprint, multi-photo и publishable с review_status."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

from excursion_ai.catalog import Excursion, TranslationMeta
from excursion_ai.config import load_settings
from excursion_ai.db.excursions import (
    MAX_PHOTOS,
    REVIEW_CONFIRMED,
    REVIEW_PENDING,
    ExcursionRepository,
)
from excursion_ai.db.session import dispose_engine, init_engine
from excursion_ai.db.translation_meta import fingerprint_translation_block, other_languages
from excursion_ai.handlers.admin import _missing_required, _ready_map
from excursion_ai.i18n import load_i18n


def test_fingerprint_and_targets() -> None:
    block = {
        "title": "Rome walk",
        "description": "A long enough description",
        "route": ["a", "b", "c"],
        "meeting_point": "Colosseum",
    }
    fp1 = fingerprint_translation_block(block)
    assert fp1 == fingerprint_translation_block(block)
    assert fp1 == fingerprint_translation_block({**block, "title": "Rome walk "})
    assert fp1 != fingerprint_translation_block({**block, "title": "Other title"})
    assert other_languages("ru") == ("en", "it")
    assert other_languages("en") == ("ru", "it")


def test_publishable_requires_confirmed() -> None:
    i18n = load_i18n()
    block = {
        "title": "Title ok",
        "city": "rome",
        "description": "Description long enough",
        "route": ["a", "b", "c"],
        "meeting_point": "Meet point",
    }
    pending = Excursion(
        id="x",
        city_id="rome",
        duration_minutes=60,
        price_per_person=Decimal("10"),
        currency="EUR",
        conducted_languages=("en",),
        translations={"ru": block, "en": block, "it": block},
        translation_meta={
            "ru": TranslationMeta(review_status=REVIEW_CONFIRMED),
            "en": TranslationMeta(review_status=REVIEW_PENDING, origin="ai"),
            "it": TranslationMeta(review_status=REVIEW_PENDING, origin="ai"),
        },
        status="draft",
    )
    repo = ExcursionRepository.__new__(ExcursionRepository)
    assert repo.is_publishable(pending) is False
    assert len(_missing_required(pending, lang="ru", i18n=i18n)) >= 1
    confirmed = Excursion(
        id="y",
        city_id="rome",
        duration_minutes=60,
        price_per_person=Decimal("10"),
        currency="EUR",
        conducted_languages=("en",),
        translations={"ru": block, "en": block, "it": block},
        translation_meta={
            code: TranslationMeta(review_status=REVIEW_CONFIRMED) for code in ("ru", "en", "it")
        },
        status="draft",
    )
    assert repo.is_publishable(confirmed) is True
    assert _ready_map(confirmed) == {"ru": True, "en": True, "it": True}


async def _photo_roundtrip() -> None:
    settings = load_settings(require_db=True)
    factory = init_engine(settings)
    repo = ExcursionRepository(factory)
    excursion_id = f"photo-test-{uuid4().hex[:8]}"
    try:
        await repo.create_draft(
            excursion_id=excursion_id,
            city_id="rome",
            duration_minutes=60,
            price_per_person=Decimal("1.00"),
            currency="EUR",
            conducted_languages=["ru"],
            source_language="ru",
        )
        current = await repo.get(excursion_id)
        assert current is not None
        photos = [
            {"file_id": f"fid-{i}", "file_unique_id": f"uniq-{i}"}
            for i in range(1, 5)
        ]
        updated, added, rejected = await repo.add_photos(
            excursion_id=excursion_id,
            expected_version=current.version,
            photos=photos,
            apply_immediately=True,
        )
        assert len(added) == MAX_PHOTOS
        assert len(rejected) == 1
        assert rejected[0]["reason"] == "limit"
        assert updated.photo_count == 3
        # duplicate
        again = await repo.get(excursion_id)
        assert again is not None
        _, added2, rejected2 = await repo.add_photos(
            excursion_id=excursion_id,
            expected_version=again.version,
            photos=[{"file_id": "fid-1", "file_unique_id": "uniq-1"}],
            apply_immediately=True,
        )
        assert added2 == []
        assert rejected2 and rejected2[0]["reason"] == "duplicate"
        cover = await repo.set_cover_photo(
            excursion_id=excursion_id,
            expected_version=(await repo.get(excursion_id)).version,  # type: ignore[union-attr]
            file_unique_id="uniq-2",
            apply_immediately=True,
        )
        assert cover.photos[0].file_unique_id == "uniq-2"
    finally:
        # leave row for inspection optional — delete via SQL not exposed; soft skip
        await dispose_engine()


def test_photo_roundtrip() -> None:
    asyncio.run(_photo_roundtrip())


if __name__ == "__main__":
    test_fingerprint_and_targets()
    test_publishable_requires_confirmed()
    test_photo_roundtrip()
    print("OK admin translate/photo helpers")
