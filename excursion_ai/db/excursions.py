from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from excursion_ai.catalog import Catalog, Excursion, ExcursionPhoto, TranslationMeta
from excursion_ai.config import SUPPORTED_LANGS
from excursion_ai.db.models import (
    ExcursionPhotoRow,
    ExcursionRow,
    ExcursionSession,
    ExcursionStatus,
    ExcursionTranslationRow,
)
from excursion_ai.db.translation_meta import fingerprint_translation_block
from excursion_ai.log_context import log_event

logger = logging.getLogger(__name__)

MAX_PHOTOS = 3
REVIEW_CONFIRMED = "confirmed"
REVIEW_PENDING = "pending_review"
REVIEW_STALE = "stale"


class ExcursionRepositoryError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ExcursionVersionConflict(ExcursionRepositoryError):
    def __init__(self) -> None:
        super().__init__("version_conflict")


def _langs_to_str(langs: tuple[str, ...] | list[str]) -> str:
    return json.dumps(list(langs), ensure_ascii=False)


def _langs_from_str(raw: str) -> tuple[str, ...]:
    data = json.loads(raw)
    return tuple(str(item) for item in data)


def _translation_block_from_row(tr: ExcursionTranslationRow) -> dict[str, object]:
    return {
        "title": tr.title,
        "city": tr.city_label,
        "description": tr.description,
        "route": json.loads(tr.route_json),
        "meeting_point": tr.meeting_point,
    }


def row_to_excursion(row: ExcursionRow) -> Excursion:
    translations: dict[str, dict[str, object]] = {}
    translation_meta: dict[str, TranslationMeta] = {}
    for tr in row.translations:
        translations[tr.language] = _translation_block_from_row(tr)
        translation_meta[tr.language] = TranslationMeta(
            review_status=getattr(tr, "review_status", None) or REVIEW_CONFIRMED,
            origin=getattr(tr, "origin", None) or "manual",
            source_fingerprint=getattr(tr, "source_fingerprint", None),
        )
    photos = tuple(
        ExcursionPhoto(
            file_id=photo.file_id,
            file_unique_id=photo.file_unique_id,
            sort_order=photo.sort_order,
        )
        for photo in sorted(row.photos, key=lambda item: item.sort_order)
    )
    photo_id = photos[0].file_id if photos else None
    return Excursion(
        id=row.id,
        city_id=row.city_id,
        duration_minutes=row.duration_minutes,
        price_per_person=Decimal(row.price_per_person),
        currency=row.currency,
        conducted_languages=_langs_from_str(row.conducted_languages),
        translations=translations,
        photo_file_id=photo_id,
        photos=photos,
        source_language=getattr(row, "source_language", None),
        translation_meta=translation_meta,
        version=row.version,
        status=row.status,
    )


def _apply_translation_row(
    *,
    session: AsyncSession,
    row: ExcursionRow,
    language: str,
    title: str,
    city_label: str,
    description: str,
    route: list[str],
    meeting_point: str,
    review_status: str,
    origin: str,
    source_fingerprint: str | None,
) -> None:
    existing = next((t for t in row.translations if t.language == language), None)
    route_json = json.dumps(route, ensure_ascii=False)
    if existing is None:
        session.add(
            ExcursionTranslationRow(
                excursion_id=row.id,
                language=language,
                title=title,
                city_label=city_label,
                description=description,
                route_json=route_json,
                meeting_point=meeting_point,
                review_status=review_status,
                origin=origin,
                source_fingerprint=source_fingerprint,
            )
        )
        return
    existing.title = title
    existing.city_label = city_label
    existing.description = description
    existing.route_json = route_json
    existing.meeting_point = meeting_point
    existing.review_status = review_status
    existing.origin = origin
    existing.source_fingerprint = source_fingerprint


def _replace_photos(
    session: AsyncSession,
    row: ExcursionRow,
    photos: list[dict[str, Any]],
) -> None:
    row.photos.clear()
    for index, photo in enumerate(photos[:MAX_PHOTOS]):
        session.add(
            ExcursionPhotoRow(
                excursion_id=row.id,
                file_id=str(photo["file_id"]),
                file_unique_id=str(photo["file_unique_id"]),
                sort_order=int(photo.get("sort_order", index)),
            )
        )


class ExcursionRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    def _base_query(self):
        return select(ExcursionRow).options(
            selectinload(ExcursionRow.translations),
            selectinload(ExcursionRow.photos),
        )

    async def list_by_status(self, status: str) -> list[Excursion]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    self._base_query()
                    .where(ExcursionRow.status == status)
                    .order_by(ExcursionRow.updated_at.desc())
                )
            ).all()
            return [row_to_excursion(row) for row in rows]

    async def list_published(self) -> list[Excursion]:
        return await self.list_by_status(ExcursionStatus.PUBLISHED.value)

    async def get(self, excursion_id: str) -> Excursion | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                self._base_query().where(ExcursionRow.id == excursion_id)
            )
            return row_to_excursion(row) if row else None

    async def get_row(self, excursion_id: str) -> ExcursionRow | None:
        async with self._session_factory() as session:
            return await session.scalar(
                self._base_query().where(ExcursionRow.id == excursion_id)
            )

    async def exists(self, excursion_id: str) -> bool:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(ExcursionRow.id).where(ExcursionRow.id == excursion_id)
            )
            return row is not None

    async def create_draft(
        self,
        *,
        excursion_id: str,
        city_id: str,
        duration_minutes: int,
        price_per_person: Decimal,
        currency: str,
        conducted_languages: list[str],
        source_language: str | None = None,
    ) -> Excursion:
        async with self._session_factory() as session:
            async with session.begin():
                if await session.scalar(
                    select(ExcursionRow.id).where(ExcursionRow.id == excursion_id)
                ):
                    raise ExcursionRepositoryError("id_exists")
                row = ExcursionRow(
                    id=excursion_id,
                    city_id=city_id,
                    duration_minutes=duration_minutes,
                    price_per_person=price_per_person,
                    currency=currency,
                    conducted_languages=_langs_to_str(conducted_languages),
                    source_language=source_language,
                    status=ExcursionStatus.DRAFT.value,
                    version=1,
                )
                session.add(row)
            log_event(
                logger,
                action="admin.excursion",
                stage="create_draft",
                result="ok",
                excursion_id=excursion_id,
            )
        loaded = await self.get(excursion_id)
        assert loaded is not None
        return loaded

    async def upsert_translation(
        self,
        *,
        excursion_id: str,
        language: str,
        title: str,
        city_label: str,
        description: str,
        route: list[str],
        meeting_point: str,
        expected_version: int | None = None,
        apply_immediately: bool = True,
        review_status: str = REVIEW_CONFIRMED,
        origin: str = "manual",
        source_fingerprint: str | None = None,
        mark_others_stale_from_source: bool = False,
    ) -> Excursion:
        if language not in SUPPORTED_LANGS:
            raise ExcursionRepositoryError("bad_language")
        block = {
            "title": title,
            "description": description,
            "route": route,
            "meeting_point": meeting_point,
        }
        fingerprint = source_fingerprint or fingerprint_translation_block(block)
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    self._base_query().where(ExcursionRow.id == excursion_id)
                )
                if row is None:
                    raise ExcursionRepositoryError("not_found")
                if expected_version is not None and row.version != expected_version:
                    raise ExcursionVersionConflict()
                if row.status == ExcursionStatus.PUBLISHED.value and not apply_immediately:
                    patch = json.loads(row.pending_patch or "{}")
                    translations = patch.get("translations") or {}
                    translations[language] = {
                        "title": title,
                        "city": city_label,
                        "description": description,
                        "route": route,
                        "meeting_point": meeting_point,
                        "review_status": review_status,
                        "origin": origin,
                        "source_fingerprint": fingerprint,
                    }
                    patch["translations"] = translations
                    row.pending_patch = json.dumps(patch, ensure_ascii=False)
                else:
                    _apply_translation_row(
                        session=session,
                        row=row,
                        language=language,
                        title=title,
                        city_label=city_label,
                        description=description,
                        route=route,
                        meeting_point=meeting_point,
                        review_status=review_status,
                        origin=origin,
                        source_fingerprint=fingerprint,
                    )
                    if mark_others_stale_from_source:
                        row.source_language = language
                        for other in row.translations:
                            if other.language == language:
                                continue
                            other.review_status = REVIEW_STALE
                    if apply_immediately:
                        row.version += 1
            log_event(
                logger,
                action="admin.excursion",
                stage="upsert_translation",
                result="ok",
                excursion_id=excursion_id,
                fields="translation",
            )
        loaded = await self.get(excursion_id)
        assert loaded is not None
        return loaded

    async def set_translation_review(
        self,
        *,
        excursion_id: str,
        language: str,
        review_status: str,
        expected_version: int | None = None,
    ) -> Excursion:
        if language not in SUPPORTED_LANGS:
            raise ExcursionRepositoryError("bad_language")
        if review_status not in {REVIEW_CONFIRMED, REVIEW_PENDING, REVIEW_STALE}:
            raise ExcursionRepositoryError("bad_status")
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    self._base_query().where(ExcursionRow.id == excursion_id)
                )
                if row is None:
                    raise ExcursionRepositoryError("not_found")
                if expected_version is not None and row.version != expected_version:
                    raise ExcursionVersionConflict()
                existing = next((t for t in row.translations if t.language == language), None)
                if existing is None:
                    raise ExcursionRepositoryError("not_found")
                existing.review_status = review_status
                row.version += 1
            log_event(
                logger,
                action="admin.excursion",
                stage="set_translation_review",
                result="ok",
                excursion_id=excursion_id,
                fields=f"{language}:{review_status}",
            )
        loaded = await self.get(excursion_id)
        assert loaded is not None
        return loaded

    async def update_fields(
        self,
        *,
        excursion_id: str,
        expected_version: int,
        fields: dict[str, Any],
        apply_immediately: bool | None = None,
    ) -> Excursion:
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    self._base_query().where(ExcursionRow.id == excursion_id)
                )
                if row is None:
                    raise ExcursionRepositoryError("not_found")
                if row.version != expected_version:
                    raise ExcursionVersionConflict()
                immediate = (
                    apply_immediately
                    if apply_immediately is not None
                    else row.status != ExcursionStatus.PUBLISHED.value
                )
                if not immediate:
                    patch = json.loads(row.pending_patch or "{}")
                    patch.update(fields)
                    row.pending_patch = json.dumps(patch, ensure_ascii=False)
                else:
                    if "city_id" in fields:
                        row.city_id = str(fields["city_id"])
                    if "duration_minutes" in fields:
                        row.duration_minutes = int(fields["duration_minutes"])
                    if "price_per_person" in fields:
                        row.price_per_person = Decimal(str(fields["price_per_person"]))
                    if "currency" in fields:
                        row.currency = str(fields["currency"])
                    if "conducted_languages" in fields:
                        row.conducted_languages = _langs_to_str(fields["conducted_languages"])
                    if "source_language" in fields:
                        value = fields["source_language"]
                        row.source_language = None if value is None else str(value)
                    row.version += 1
            log_event(
                logger,
                action="admin.excursion",
                stage="update_fields",
                result="ok",
                excursion_id=excursion_id,
                fields=",".join(sorted(fields.keys())),
            )
        loaded = await self.get(excursion_id)
        assert loaded is not None
        return loaded

    async def apply_pending(
        self,
        *,
        excursion_id: str,
        expected_version: int,
    ) -> Excursion:
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    self._base_query().where(ExcursionRow.id == excursion_id)
                )
                if row is None:
                    raise ExcursionRepositoryError("not_found")
                if row.version != expected_version:
                    raise ExcursionVersionConflict()
                if not row.pending_patch:
                    raise ExcursionRepositoryError("no_pending")
                patch = json.loads(row.pending_patch)
                if "city_id" in patch:
                    row.city_id = str(patch["city_id"])
                if "duration_minutes" in patch:
                    row.duration_minutes = int(patch["duration_minutes"])
                if "price_per_person" in patch:
                    row.price_per_person = Decimal(str(patch["price_per_person"]))
                if "currency" in patch:
                    row.currency = str(patch["currency"])
                if "conducted_languages" in patch:
                    row.conducted_languages = _langs_to_str(patch["conducted_languages"])
                if "source_language" in patch:
                    value = patch["source_language"]
                    row.source_language = None if value is None else str(value)
                translations = patch.get("translations") or {}
                for language, block in translations.items():
                    _apply_translation_row(
                        session=session,
                        row=row,
                        language=str(language),
                        title=str(block["title"]),
                        city_label=str(block.get("city") or block.get("city_label") or row.city_id),
                        description=str(block["description"]),
                        route=list(block["route"]),
                        meeting_point=str(block["meeting_point"]),
                        review_status=str(block.get("review_status") or REVIEW_CONFIRMED),
                        origin=str(block.get("origin") or "manual"),
                        source_fingerprint=block.get("source_fingerprint"),
                    )
                if "photos" in patch:
                    _replace_photos(session, row, list(patch["photos"] or []))
                elif "photo" in patch:
                    photo = patch["photo"]
                    photos = [] if not photo else [photo]
                    _replace_photos(session, row, photos)
                row.pending_patch = None
                row.version += 1
            log_event(
                logger,
                action="admin.excursion",
                stage="apply_pending",
                result="ok",
                excursion_id=excursion_id,
            )
        loaded = await self.get(excursion_id)
        assert loaded is not None
        return loaded

    async def discard_pending(
        self,
        *,
        excursion_id: str,
        expected_version: int,
    ) -> Excursion:
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    select(ExcursionRow).where(ExcursionRow.id == excursion_id)
                )
                if row is None:
                    raise ExcursionRepositoryError("not_found")
                if row.version != expected_version:
                    raise ExcursionVersionConflict()
                row.pending_patch = None
            log_event(
                logger,
                action="admin.excursion",
                stage="discard_pending",
                result="ok",
                excursion_id=excursion_id,
            )
        loaded = await self.get(excursion_id)
        assert loaded is not None
        return loaded

    async def add_photos(
        self,
        *,
        excursion_id: str,
        expected_version: int,
        photos: list[dict[str, str]],
        apply_immediately: bool | None = None,
    ) -> tuple[Excursion, list[dict[str, str]], list[dict[str, str]]]:
        """Добавляет фото до MAX_PHOTOS. Возвращает (excursion, added, rejected)."""
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    self._base_query().where(ExcursionRow.id == excursion_id)
                )
                if row is None:
                    raise ExcursionRepositoryError("not_found")
                if row.version != expected_version:
                    raise ExcursionVersionConflict()
                immediate = (
                    apply_immediately
                    if apply_immediately is not None
                    else row.status != ExcursionStatus.PUBLISHED.value
                )
                if not immediate:
                    patch = json.loads(row.pending_patch or "{}")
                    current = list(patch.get("photos") or [])
                    if not current and row.photos:
                        current = [
                            {
                                "file_id": p.file_id,
                                "file_unique_id": p.file_unique_id,
                                "sort_order": p.sort_order,
                            }
                            for p in sorted(row.photos, key=lambda item: item.sort_order)
                        ]
                    existing_unique = {str(p["file_unique_id"]) for p in current}
                    added: list[dict[str, str]] = []
                    rejected: list[dict[str, str]] = []
                    for photo in photos:
                        unique = str(photo["file_unique_id"])
                        if unique in existing_unique:
                            rejected.append({**photo, "reason": "duplicate"})
                            continue
                        if len(current) >= MAX_PHOTOS:
                            rejected.append({**photo, "reason": "limit"})
                            continue
                        item = {
                            "file_id": str(photo["file_id"]),
                            "file_unique_id": unique,
                            "sort_order": len(current),
                        }
                        current.append(item)
                        existing_unique.add(unique)
                        added.append(item)
                    patch["photos"] = current
                    row.pending_patch = json.dumps(patch, ensure_ascii=False)
                else:
                    existing_unique = {p.file_unique_id for p in row.photos}
                    added = []
                    rejected = []
                    next_order = max((p.sort_order for p in row.photos), default=-1) + 1
                    for photo in photos:
                        unique = str(photo["file_unique_id"])
                        if unique in existing_unique:
                            rejected.append({**photo, "reason": "duplicate"})
                            continue
                        if len(row.photos) + len(added) >= MAX_PHOTOS:
                            rejected.append({**photo, "reason": "limit"})
                            continue
                        item = {
                            "file_id": str(photo["file_id"]),
                            "file_unique_id": unique,
                            "sort_order": next_order,
                        }
                        session.add(
                            ExcursionPhotoRow(
                                excursion_id=excursion_id,
                                file_id=item["file_id"],
                                file_unique_id=unique,
                                sort_order=next_order,
                            )
                        )
                        next_order += 1
                        existing_unique.add(unique)
                        added.append(item)
                    if added:
                        row.version += 1
            log_event(
                logger,
                action="admin.excursion",
                stage="add_photos",
                result="ok",
                excursion_id=excursion_id,
                fields=f"added:{len(added)},rejected:{len(rejected)}",
            )
        loaded = await self.get(excursion_id)
        assert loaded is not None
        return loaded, added, rejected

    async def delete_photo(
        self,
        *,
        excursion_id: str,
        expected_version: int,
        file_unique_id: str,
        apply_immediately: bool | None = None,
    ) -> Excursion:
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    self._base_query().where(ExcursionRow.id == excursion_id)
                )
                if row is None:
                    raise ExcursionRepositoryError("not_found")
                if row.version != expected_version:
                    raise ExcursionVersionConflict()
                immediate = (
                    apply_immediately
                    if apply_immediately is not None
                    else row.status != ExcursionStatus.PUBLISHED.value
                )
                if not immediate:
                    patch = json.loads(row.pending_patch or "{}")
                    current = list(patch.get("photos") or [])
                    if not current:
                        current = [
                            {
                                "file_id": p.file_id,
                                "file_unique_id": p.file_unique_id,
                                "sort_order": index,
                            }
                            for index, p in enumerate(
                                sorted(row.photos, key=lambda item: item.sort_order)
                            )
                        ]
                    current = [
                        p for p in current if str(p["file_unique_id"]) != file_unique_id
                    ]
                    for index, photo in enumerate(current):
                        photo["sort_order"] = index
                    patch["photos"] = current
                    row.pending_patch = json.dumps(patch, ensure_ascii=False)
                else:
                    kept = [
                        p for p in row.photos if p.file_unique_id != file_unique_id
                    ]
                    if len(kept) == len(row.photos):
                        raise ExcursionRepositoryError("not_found")
                    row.photos.clear()
                    for index, photo in enumerate(
                        sorted(kept, key=lambda item: item.sort_order)
                    ):
                        session.add(
                            ExcursionPhotoRow(
                                excursion_id=excursion_id,
                                file_id=photo.file_id,
                                file_unique_id=photo.file_unique_id,
                                sort_order=index,
                            )
                        )
                    row.version += 1
            log_event(
                logger,
                action="admin.excursion",
                stage="delete_photo",
                result="ok",
                excursion_id=excursion_id,
            )
        loaded = await self.get(excursion_id)
        assert loaded is not None
        return loaded

    async def replace_photo(
        self,
        *,
        excursion_id: str,
        expected_version: int,
        old_file_unique_id: str,
        file_id: str,
        file_unique_id: str,
        apply_immediately: bool | None = None,
    ) -> Excursion:
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    self._base_query().where(ExcursionRow.id == excursion_id)
                )
                if row is None:
                    raise ExcursionRepositoryError("not_found")
                if row.version != expected_version:
                    raise ExcursionVersionConflict()
                immediate = (
                    apply_immediately
                    if apply_immediately is not None
                    else row.status != ExcursionStatus.PUBLISHED.value
                )
                if not immediate:
                    patch = json.loads(row.pending_patch or "{}")
                    current = list(patch.get("photos") or [])
                    if not current:
                        current = [
                            {
                                "file_id": p.file_id,
                                "file_unique_id": p.file_unique_id,
                                "sort_order": index,
                            }
                            for index, p in enumerate(
                                sorted(row.photos, key=lambda item: item.sort_order)
                            )
                        ]
                    replaced = False
                    for photo in current:
                        if str(photo["file_unique_id"]) == old_file_unique_id:
                            photo["file_id"] = file_id
                            photo["file_unique_id"] = file_unique_id
                            replaced = True
                            break
                    if not replaced:
                        raise ExcursionRepositoryError("not_found")
                    patch["photos"] = current
                    row.pending_patch = json.dumps(patch, ensure_ascii=False)
                else:
                    target = next(
                        (p for p in row.photos if p.file_unique_id == old_file_unique_id),
                        None,
                    )
                    if target is None:
                        raise ExcursionRepositoryError("not_found")
                    if any(
                        p.file_unique_id == file_unique_id and p is not target
                        for p in row.photos
                    ):
                        raise ExcursionRepositoryError("duplicate")
                    target.file_id = file_id
                    target.file_unique_id = file_unique_id
                    row.version += 1
            log_event(
                logger,
                action="admin.excursion",
                stage="replace_photo",
                result="ok",
                excursion_id=excursion_id,
            )
        loaded = await self.get(excursion_id)
        assert loaded is not None
        return loaded

    async def set_cover_photo(
        self,
        *,
        excursion_id: str,
        expected_version: int,
        file_unique_id: str,
        apply_immediately: bool | None = None,
    ) -> Excursion:
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    self._base_query().where(ExcursionRow.id == excursion_id)
                )
                if row is None:
                    raise ExcursionRepositoryError("not_found")
                if row.version != expected_version:
                    raise ExcursionVersionConflict()
                immediate = (
                    apply_immediately
                    if apply_immediately is not None
                    else row.status != ExcursionStatus.PUBLISHED.value
                )
                ordered = sorted(row.photos, key=lambda item: item.sort_order)
                cover = next(
                    (p for p in ordered if p.file_unique_id == file_unique_id),
                    None,
                )
                if cover is None and immediate:
                    raise ExcursionRepositoryError("not_found")
                if not immediate:
                    patch = json.loads(row.pending_patch or "{}")
                    current = list(patch.get("photos") or [])
                    if not current:
                        current = [
                            {
                                "file_id": p.file_id,
                                "file_unique_id": p.file_unique_id,
                                "sort_order": index,
                            }
                            for index, p in enumerate(ordered)
                        ]
                    cover_item = next(
                        (
                            p
                            for p in current
                            if str(p["file_unique_id"]) == file_unique_id
                        ),
                        None,
                    )
                    if cover_item is None:
                        raise ExcursionRepositoryError("not_found")
                    rest = [
                        p
                        for p in current
                        if str(p["file_unique_id"]) != file_unique_id
                    ]
                    reordered = [cover_item, *rest]
                    for index, photo in enumerate(reordered):
                        photo["sort_order"] = index
                    patch["photos"] = reordered
                    row.pending_patch = json.dumps(patch, ensure_ascii=False)
                else:
                    rest = [p for p in ordered if p.file_unique_id != file_unique_id]
                    reordered = [cover, *rest]
                    for index, photo in enumerate(reordered):
                        photo.sort_order = index
                    row.version += 1
            log_event(
                logger,
                action="admin.excursion",
                stage="set_cover_photo",
                result="ok",
                excursion_id=excursion_id,
            )
        loaded = await self.get(excursion_id)
        assert loaded is not None
        return loaded

    async def set_photo(
        self,
        *,
        excursion_id: str,
        expected_version: int,
        file_id: str | None,
        file_unique_id: str | None,
        apply_immediately: bool | None = None,
    ) -> Excursion:
        """Совместимость: заменить весь набор одной фотографией или очистить."""
        if file_id and file_unique_id:
            photos = [{"file_id": file_id, "file_unique_id": file_unique_id, "sort_order": 0}]
        else:
            photos = []
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    self._base_query().where(ExcursionRow.id == excursion_id)
                )
                if row is None:
                    raise ExcursionRepositoryError("not_found")
                if row.version != expected_version:
                    raise ExcursionVersionConflict()
                immediate = (
                    apply_immediately
                    if apply_immediately is not None
                    else row.status != ExcursionStatus.PUBLISHED.value
                )
                if not immediate:
                    patch = json.loads(row.pending_patch or "{}")
                    patch["photos"] = photos
                    row.pending_patch = json.dumps(patch, ensure_ascii=False)
                else:
                    _replace_photos(session, row, photos)
                    row.version += 1
            log_event(
                logger,
                action="admin.excursion",
                stage="set_photo",
                result="ok",
                excursion_id=excursion_id,
            )
        loaded = await self.get(excursion_id)
        assert loaded is not None
        return loaded

    def translation_complete(self, excursion: Excursion, lang: str) -> bool:
        block = excursion.translations.get(lang)
        if not block:
            return False
        for field in ("title", "description", "meeting_point"):
            if not str(block.get(field, "")).strip():
                return False
        route = block.get("route")
        return isinstance(route, list) and 3 <= len(route) <= 4

    def translation_confirmed(self, excursion: Excursion, lang: str) -> bool:
        if not self.translation_complete(excursion, lang):
            return False
        return excursion.meta_for(lang).review_status == REVIEW_CONFIRMED

    def is_publishable(self, excursion: Excursion) -> bool:
        if excursion.duration_minutes <= 0:
            return False
        if excursion.price_per_person < 0:
            return False
        if not excursion.conducted_languages:
            return False
        for lang in SUPPORTED_LANGS:
            if not self.translation_confirmed(excursion, lang):
                return False
            block = excursion.translations[lang]
            if not str(block.get("city", "")).strip():
                return False
        return True

    async def set_status(
        self,
        *,
        excursion_id: str,
        expected_version: int,
        status: str,
    ) -> Excursion:
        if status not in {s.value for s in ExcursionStatus}:
            raise ExcursionRepositoryError("bad_status")
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    self._base_query().where(ExcursionRow.id == excursion_id)
                )
                if row is None:
                    raise ExcursionRepositoryError("not_found")
                if row.version != expected_version:
                    raise ExcursionVersionConflict()
                if status == ExcursionStatus.PUBLISHED.value:
                    current = row_to_excursion(row)
                    if not self.is_publishable(current):
                        raise ExcursionRepositoryError("not_ready")
                    if row.pending_patch:
                        raise ExcursionRepositoryError("pending_exists")
                    session_count = await session.scalar(
                        select(func.count())
                        .select_from(ExcursionSession)
                        .where(
                            ExcursionSession.excursion_id == excursion_id,
                            ExcursionSession.is_active.is_(True),
                        )
                    )
                    if int(session_count or 0) < 1:
                        raise ExcursionRepositoryError("no_sessions")
                row.status = status
                row.version += 1
            log_event(
                logger,
                action="admin.excursion",
                stage="set_status",
                result="ok",
                excursion_id=excursion_id,
                fields=f"status:{status}",
            )
        loaded = await self.get(excursion_id)
        assert loaded is not None
        return loaded

    async def import_seed_item(self, excursion: Excursion) -> str:
        """Импорт без перезаписи существующих. Возвращает: inserted|skipped."""
        async with self._session_factory() as session:
            async with session.begin():
                existing = await session.scalar(
                    select(ExcursionRow.id).where(ExcursionRow.id == excursion.id)
                )
                if existing is not None:
                    return "skipped"
                row = ExcursionRow(
                    id=excursion.id,
                    city_id=excursion.city_id,
                    duration_minutes=excursion.duration_minutes,
                    price_per_person=excursion.price_per_person,
                    currency=excursion.currency,
                    conducted_languages=_langs_to_str(excursion.conducted_languages),
                    source_language="ru",
                    status=ExcursionStatus.PUBLISHED.value,
                    version=1,
                )
                session.add(row)
                await session.flush()
                for lang, block in excursion.translations.items():
                    fp = fingerprint_translation_block(block)
                    session.add(
                        ExcursionTranslationRow(
                            excursion_id=excursion.id,
                            language=lang,
                            title=str(block["title"]),
                            city_label=str(block["city"]),
                            description=str(block["description"]),
                            route_json=json.dumps(block["route"], ensure_ascii=False),
                            meeting_point=str(block["meeting_point"]),
                            review_status=REVIEW_CONFIRMED,
                            origin="seed",
                            source_fingerprint=fp if lang == "ru" else fp,
                        )
                    )
                return "inserted"

    async def published_catalog(self) -> Catalog:
        return Catalog(await self.list_published())
