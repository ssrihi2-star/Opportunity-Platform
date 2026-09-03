"""Hard spend caps. Exceeding the cap degrades the pipeline, it does not crash it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import BudgetExceededError
from app.models.models import ModelRun


async def spent_since(session: AsyncSession, since: datetime) -> float:
    stmt = sa.select(sa.func.coalesce(sa.func.sum(ModelRun.cost_usd), 0.0)).where(
        ModelRun.created_at >= since
    )
    return float((await session.execute(stmt)).scalar_one())


async def assert_within_budget(session: AsyncSession, projected_cost_usd: float = 0.0) -> None:
    now = datetime.now(UTC)
    daily = await spent_since(session, now - timedelta(days=1))
    if daily + projected_cost_usd > settings.AI_DAILY_BUDGET_USD:
        raise BudgetExceededError(
            f"Daily AI budget of ${settings.AI_DAILY_BUDGET_USD:.2f} would be exceeded "
            f"(spent ${daily:.4f}). Raise AI_DAILY_BUDGET_USD or wait for the window to roll."
        )
    monthly = await spent_since(session, now - timedelta(days=30))
    if monthly + projected_cost_usd > settings.AI_MONTHLY_BUDGET_USD:
        raise BudgetExceededError(
            f"Monthly AI budget of ${settings.AI_MONTHLY_BUDGET_USD:.2f} would be exceeded "
            f"(spent ${monthly:.4f})."
        )
