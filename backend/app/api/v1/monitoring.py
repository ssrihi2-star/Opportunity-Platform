"""Running the monitor, and reading what it found.

The monitor is deliberately a single explicit action rather than something that
happens invisibly: it checks every condition against stored measurements,
records what changed, and hands the resulting events to the alert engine, which
decides who should hear about them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, db_session, require_admin
from app.models.models import ConditionCheck, OpportunityCondition, SystemAuditLog, User
from app.notifications import registered_channels
from app.notifications.base import get_provider
from app.schemas.personal import ChangeEventOut, ConditionCheckOut, MonitorResult
from app.services.alerts import dispatch
from app.services.monitoring import monitor_all, recent_events

router = APIRouter(tags=["monitoring"])


@router.post("/monitoring/run", response_model=MonitorResult)
async def run_monitor(
    session: AsyncSession = Depends(db_session),
    user: User = Depends(require_admin),
) -> MonitorResult:
    """Check every condition, record every change, then alert whoever asked."""
    now = datetime.now(UTC)
    counts = await monitor_all(session, now=now)
    events = await recent_events(session, since=now - timedelta(minutes=1))
    outcome = await dispatch(session, events=events, now=now)
    session.add(
        SystemAuditLog(
            actor_user_id=user.id,
            action="monitoring.run",
            object_type="opportunity",
            object_id=None,
            after={**counts, "alerts_sent": outcome.sent},
        )
    )
    await session.commit()
    return MonitorResult(
        opportunities=counts["opportunities"],
        checks=counts["checks"],
        events=counts["events"],
        alerts_sent=outcome.sent,
        alerts_suppressed=outcome.suppressed,
        notes=outcome.reasons[:50],
    )


@router.get("/monitoring/events", response_model=list[ChangeEventOut])
async def list_events(
    days: int = Query(default=7, ge=1, le=90),
    session: AsyncSession = Depends(db_session),
    _: User = Depends(current_user),
) -> list[ChangeEventOut]:
    """What changed, globally. The same list for every user."""
    rows = await recent_events(session, since=datetime.now(UTC) - timedelta(days=days))
    return [ChangeEventOut.model_validate(event) for event, _ in rows]


@router.get("/opportunities/{opportunity_id}/condition-checks", response_model=list[ConditionCheckOut])
async def condition_history(
    opportunity_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    _: User = Depends(current_user),
) -> list[ConditionCheckOut]:
    """Every check ever run on this candidate's conditions, newest first.

    An `unknown` row is not a failure: it means the condition could not be
    checked against stored measurements, and nothing was assumed either way.
    """
    rows = (
        await session.execute(
            sa.select(ConditionCheck)
            .join(
                OpportunityCondition,
                OpportunityCondition.id == ConditionCheck.condition_id,
            )
            .where(OpportunityCondition.opportunity_id == opportunity_id)
            .order_by(ConditionCheck.checked_at.desc())
            .limit(200)
        )
    ).scalars()
    return [ConditionCheckOut.model_validate(r) for r in rows]


class ChannelStatus(BaseModel):
    channel: str
    configured: bool
    note: str


@router.get("/monitoring/channels", response_model=list[ChannelStatus])
async def channel_status(
    _: User = Depends(current_user),
) -> list[ChannelStatus]:
    """Which notification channels can actually deliver right now.

    An unconfigured channel is shown as unconfigured rather than quietly
    swallowing messages: a user who believes they will be emailed and is not is
    worse off than one who knows email is off.
    """
    out: list[ChannelStatus] = []
    for name in registered_channels():
        provider = get_provider(name)
        configured = bool(provider and provider.configured)
        out.append(
            ChannelStatus(
                channel=name,
                configured=configured,
                note=(
                    "Ready to deliver."
                    if configured
                    else (
                        "Not configured on this deployment. Alerts are still recorded "
                        "and readable in the application."
                    )
                ),
            )
        )
    return out
