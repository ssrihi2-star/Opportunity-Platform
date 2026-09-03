from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, db_session
from app.models.models import User, UserPreference
from app.schemas.preferences import PreferenceOut, PreferenceUpdate

router = APIRouter(prefix="/preferences", tags=["preferences"])


async def _get_or_create(session: AsyncSession, user: User) -> UserPreference:
    pref = (
        await session.execute(sa.select(UserPreference).where(UserPreference.user_id == user.id))
    ).scalar_one_or_none()
    if pref is None:
        pref = UserPreference(user_id=user.id)
        session.add(pref)
        await session.flush()
    return pref


@router.get("", response_model=PreferenceOut)
async def read_preferences(
    user: User = Depends(current_user), session: AsyncSession = Depends(db_session)
) -> UserPreference:
    return await _get_or_create(session, user)


@router.patch("", response_model=PreferenceOut)
async def update_preferences(
    payload: PreferenceUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db_session),
) -> UserPreference:
    pref = await _get_or_create(session, user)
    data = payload.model_dump(exclude_unset=True)
    capital_min = data.get("capital_min_usd", pref.capital_min_usd)
    capital_max = data.get("capital_max_usd", pref.capital_max_usd)
    if capital_min > capital_max:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "capital_min_usd cannot be greater than capital_max_usd.",
        )
    for key, value in data.items():
        setattr(pref, key, value)
    await session.flush()
    return pref
