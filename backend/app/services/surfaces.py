"""Which analysis mode the *default* surfaces present.

Trends and Opportunities let a reader choose their evidence explicitly. The
surfaces that push results at somebody — the personal feed, scheduled alerts and
scheduled digests — have no such selector: they arrive unbidden, often as a
Telegram message read on a phone, with no dropdown next to them saying what the
number was computed from. A demo-backed candidate is least defensible exactly
there.

So those surfaces are pinned to `live_only`. One constant rather than five
literals, because the failure mode is a surface quietly keeping the old default
after the others move.

Deliberately **not** a per-user preference. A preference would mean alerts could
fire once per mode for the same underlying fact, and the dedupe key is built
from the event, not from the mode — so the same change would reach a person
twice. One mode for the push surfaces keeps that impossible.

An empty live-only result is presented as empty. Nothing here ever falls back to
demo-inclusive rows to make a feed or a digest look populated.
"""

from __future__ import annotations

from typing import TypeVar

import sqlalchemy as sa
from sqlalchemy.sql import Select

from app.models.enums import AnalysisMode
from app.models.models import Opportunity

#: The mode the personal feed, scheduled alerts and scheduled digests read.
DEFAULT_SURFACE_MODE: str = AnalysisMode.LIVE_ONLY

_S = TypeVar("_S", bound=Select)


def live_only(stmt: _S) -> _S:
    """Restrict a statement already selecting/joining `Opportunity` to the surface mode."""
    return stmt.where(Opportunity.analysis_mode == DEFAULT_SURFACE_MODE)


def surface_opportunity_filter() -> sa.ColumnElement[bool]:
    """The bare predicate, for callers assembling their own filter list."""
    return Opportunity.analysis_mode == DEFAULT_SURFACE_MODE
