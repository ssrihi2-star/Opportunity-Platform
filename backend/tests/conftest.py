"""Test harness.

Three hard rules enforced here:
  * no test may open a network socket
  * no test may reach a real LLM provider (the default provider is `echo`)
  * every network adapter is driven by a RecordedTransport reading fixtures
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import uuid
from collections.abc import AsyncGenerator

os.environ.setdefault("ENV", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-tests-1234")
os.environ.setdefault("AI_PROVIDER", "echo")
os.environ.setdefault("ADMIN_PASSWORD", "TestAdminPass!1")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.api.deps import db_session  # noqa: E402
from app.core.ratelimit import reset_limiter  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.models.enums import Role  # noqa: E402
from app.models.models import Source, User  # noqa: E402
from app.sources.http import Fetcher, RecordedTransport  # noqa: E402

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

_real_socket = socket.socket


class _BlockedSocket(socket.socket):
    def connect(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError(
            "A test tried to open a network connection. Tests must use recorded "
            "fixtures; see docs/testing.md."
        )


@pytest.fixture(autouse=True, scope="session")
def _no_network():
    socket.socket = _BlockedSocket  # type: ignore[misc]
    yield
    socket.socket = _real_socket  # type: ignore[misc]


@pytest.fixture(autouse=True)
def _fresh_rate_limiter():
    reset_limiter()
    yield
    reset_limiter()


# ----------------------------------------------------------------- fixtures API
@pytest.fixture
def load_fixture():
    def _load(name: str) -> dict:
        return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))

    return _load


@pytest.fixture
def recorded(load_fixture):
    """Build a RecordedTransport from one or more fixture files."""

    def _build(*names: str, extra: dict | None = None) -> RecordedTransport:
        merged: dict = {}
        for name in names:
            merged.update(load_fixture(name))
        if extra:
            merged.update(extra)
        return RecordedTransport(merged)

    return _build


@pytest.fixture
def make_fetcher():
    def _make(
        transport: RecordedTransport,
        *,
        slug: str = "test-source",
        respect_robots: bool = False,
        rate_limit: int = 1000,
        **kwargs,
    ) -> Fetcher:
        return Fetcher(
            source_slug=slug,
            transport=transport,
            rate_limit_per_minute=rate_limit,
            respect_robots=respect_robots,
            **kwargs,
        )

    return _make


# ------------------------------------------------------------------- database
@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncGenerator[AsyncSession, None]:
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        yield s


@pytest_asyncio.fixture
async def client(engine) -> AsyncGenerator[AsyncClient, None]:
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        async with maker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[db_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ----------------------------------------------------------------------- users
@pytest_asyncio.fixture
async def admin_user(session: AsyncSession) -> User:
    user = User(
        email="admin@acme-corp.com",
        full_name="Admin",
        password_hash=hash_password("AdminPass!2026"),
        role=Role.ADMIN,
    )
    session.add(user)
    await session.commit()
    return user


@pytest_asyncio.fixture
async def viewer_user(session: AsyncSession) -> User:
    user = User(
        email="viewer@acme-corp.com",
        full_name="Viewer",
        password_hash=hash_password("ViewerPass!2026"),
        role=Role.VIEWER,
    )
    session.add(user)
    await session.commit()
    return user


async def login(client: AsyncClient, email: str, password: str) -> str:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest_asyncio.fixture
async def admin_headers(client: AsyncClient, admin_user: User) -> dict[str, str]:
    token = await login(client, "admin@acme-corp.com", "AdminPass!2026")
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def viewer_headers(client: AsyncClient, viewer_user: User) -> dict[str, str]:
    token = await login(client, "viewer@acme-corp.com", "ViewerPass!2026")
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def second_user(session: AsyncSession) -> User:
    """A second real account. Multi-tenancy is untestable with one user."""
    user = User(
        email="other@example.org",
        full_name="Other Person",
        password_hash=hash_password("OtherPass!2026"),
        role=Role.ANALYST,
    )
    session.add(user)
    await session.commit()
    return user


@pytest_asyncio.fixture
async def second_headers(client: AsyncClient, second_user: User) -> dict[str, str]:
    token = await login(client, "other@example.org", "OtherPass!2026")
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def analyst_headers(client: AsyncClient, session: AsyncSession) -> dict[str, str]:
    user = User(
        email="analyst@acme-corp.com",
        full_name="Analyst",
        password_hash=hash_password("AnalystPass!2026"),
        role=Role.ANALYST,
    )
    session.add(user)
    await session.commit()
    token = await login(client, "analyst@acme-corp.com", "AnalystPass!2026")
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def demo_source(session: AsyncSession) -> Source:
    source = Source(
        slug=f"demo-{uuid.uuid4().hex[:6]}",
        name="Demo",
        adapter_key="demo_mock",
        source_class="demo",
        reliability=0.5,
        config={"days": 40, "seed": 7},
    )
    session.add(source)
    await session.commit()
    return source
