"""Declarative base, common mixins and the append-only guard."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.core.errors import ImmutableRowError

# pgvector is optional: on SQLite (tests) we fall back to JSON storage.
try:  # pragma: no cover - trivial import guard
    from pgvector.sqlalchemy import Vector  # type: ignore

    _HAS_PGVECTOR = True
except Exception:  # noqa: BLE001
    Vector = None  # type: ignore[assignment]
    _HAS_PGVECTOR = False

EMBEDDING_DIM = 768


class EmbeddingType(sa.types.TypeDecorator):
    """`vector(768)` on PostgreSQL, JSON everywhere else."""

    impl = sa.JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: sa.Dialect) -> Any:
        if dialect.name == "postgresql" and _HAS_PGVECTOR:
            return dialect.type_descriptor(Vector(EMBEDDING_DIM))
        return dialect.type_descriptor(sa.JSON())


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """Force a datetime to be timezone-aware UTC.

    SQLite (used in tests) drops timezone information on round-trip, so values read
    back from the database can be naive even though the column is timestamptz.
    Comparing those against aware datetimes raises TypeError, which is exactly the
    class of bug that makes a scheduled job fail on its second run.
    """
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: sa.JSON, list[str]: sa.JSON}


class UUIDMixin:
    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4, sort_order=-100)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utcnow, nullable=False, sort_order=100
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False, sort_order=101
    )


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utcnow, nullable=False, sort_order=100
    )


class ImmutableMixin(CreatedAtMixin):
    """Rows that must never be updated or deleted once written.

    Enforced here in the ORM. A database trigger is planned for Phase 6; until
    then, direct SQL can still bypass this (documented in docs/security.md).
    """

    __immutable__ = True


@event.listens_for(Session, "before_flush")
def _block_immutable_changes(session: Session, _flush_context: Any, _instances: Any) -> None:
    for obj in session.dirty:
        if getattr(obj, "__immutable__", False) and session.is_modified(obj, include_collections=False):
            raise ImmutableRowError(
                f"{type(obj).__name__} rows are append-only and cannot be updated. Insert a new row instead."
            )
    for obj in session.deleted:
        if getattr(obj, "__immutable__", False):
            raise ImmutableRowError(
                f"{type(obj).__name__} rows are append-only and cannot be deleted. "
                "Failed predictions and their evidence are kept on purpose."
            )
