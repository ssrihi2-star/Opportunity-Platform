from fastapi import APIRouter

from app.api.v1 import (
    auth,
    countries,
    health,
    me,
    monitoring,
    opportunities,
    overview,
    preferences,
    signals,
    sources,
    trends,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(preferences.router)
api_router.include_router(sources.router)
api_router.include_router(signals.router)
api_router.include_router(trends.router)
api_router.include_router(opportunities.router)
api_router.include_router(me.router)
api_router.include_router(countries.router)
api_router.include_router(monitoring.router)
api_router.include_router(overview.router)
api_router.include_router(health.router)
