"""Isolation: whose failure is whose, and what a failure is allowed to say.

A scheduled batch has a property an interactive request does not: when one unit
blows up, somebody has to decide what happens to the other ninety-nine. This file
pins those decisions for the three units the schedule added — one user's
relevance preparation, one alert rule, one user's digest — and for the two things
that must never be treated as a unit failure: a transaction that is no longer
usable, and a supervisor telling the worker to stop.

It also pins what a failure may *say*. A unit failure is logged, counted, and in
the case of a provider written into `alert_deliveries.suppressed_reason` — a row
an administrator reads through the API. Exception text cannot go into any of
those places, because the exceptions this code catches carry exactly the things
that must not be stored: an httpx exception carries the Telegram send URL, which
contains the bot token; an smtplib exception carries the server's reply and the
recipient; a SQLAlchemy `DBAPIError` carries the statement and its bound
parameters, which for this schema means chat ids and alert bodies. Fixed outcome
codes plus an exception *class name* are enough to act on and cannot carry data.

Nothing here touches the network. `conftest.py` blocks sockets, and the providers
under test are replaced by fakes for the duration of one test, so no Telegram or
SMTP service is ever contacted.
"""

from __future__ import annotations

import ast
import inspect
import json
import smtplib
from datetime import UTC, datetime

import httpx
import pytest
import sqlalchemy as sa
from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import failure_code, is_unrecoverable
from app.models.enums import DeliveryStatus, DigestFrequency
from app.models.models import AlertDelivery, AlertRule, Digest, NotificationChannelLink
from app.notifications import base as notification_base
from app.notifications.base import NotificationMessage, ProviderResult
from app.notifications.providers import EmailProvider, TelegramProvider
from app.services import alerts
from app.services.alerts import digest_period, dispatch, run_digests
from app.services.monitoring import detect_changes
from app.workers import tasks
from tests.test_monitoring_alerts import make_rule
from tests.test_multi_user import make_opportunity
from tests.test_worker_scheduling import make_confirmed_opportunity, make_user

BOT_TOKEN = "123456:AAFakeTokenThatMustNeverBeStored"
SMTP_PASSWORD = "hunter2-not-real"
RECIPIENT = "victim@example.org"
CHAT_ID = "99887766"


@pytest.fixture
def worker_db(engine, monkeypatch):
    """Point the workers' own session factory at this test's database."""
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(tasks, "SessionLocal", maker)
    return maker


class RecordingLogger:
    """Stands in for the structlog logger so a test can read what was emitted.

    `structlog.testing.capture_logs` is the obvious tool, but loggers are cached
    on first use (`cache_logger_on_first_use=True`), so replacing the bound
    logger is the reliable way to see exactly what a failure path logged.
    """

    def __init__(self) -> None:
        self.entries: list[tuple[str, dict]] = []

    def _record(self, event: str, **kwargs: object) -> None:
        self.entries.append((event, kwargs))

    debug = info = warning = error = exception = _record

    def rendered(self) -> str:
        return json.dumps([[event, {k: str(v) for k, v in kw.items()}] for event, kw in self.entries])

    def events(self, name: str) -> list[dict]:
        return [kw for event, kw in self.entries if event == name]


class RecordingThenRaisingChannel:
    """A provider that hands the message over and *then* fails.

    This is the case a savepoint cannot fix: by the time the rule raises, the
    message is already with the channel. Registered for one test only.
    """

    channel = "telegram"
    configured = True

    def __init__(self, error: Exception) -> None:
        self.attempts: list[str] = []
        self.error = error

    async def send(self, *, address: str, message: NotificationMessage) -> ProviderResult:
        self.attempts.append(address)
        raise self.error


async def _event_for(session: AsyncSession, *, slug: str, now: datetime | None = None):
    """One real change event, the way the monitor writes it."""
    opportunity = await make_opportunity(session, slug=slug, title=f"Candidate {slug}", score=80.0)
    events = await detect_changes(
        session, opportunity=opportunity, previous={"opportunity_score": 60.0}, now=now
    )
    await session.commit()
    return events[0], opportunity


# ------------------------------------------------------------------- relevance
async def test_a_user_whose_relevance_cannot_be_prepared_is_skipped_entirely(session, worker_db, monkeypatch):
    """Not evaluated at all — never evaluated with a relevance that is missing.

    The rules here have no relevance floor and no watchlist, so a batch that
    carried on with an absent relevance would have delivered to both users. What
    must happen instead is that the unlucky user's rule is not evaluated, is
    counted, and says why; and that the other user is unaffected.
    """
    unlucky = await make_user(session, email="unlucky@example.org")
    lucky = await make_user(session, email="lucky@example.org")
    await make_rule(session, unlucky, name="unlucky rule", cooldown_hours=0)
    await make_rule(session, lucky, name="lucky rule", cooldown_hours=0)
    event, opportunity = await _event_for(session, slug="relevance-failure")

    real_compute = alerts.compute_for_user

    async def compute(session_, **kwargs):  # noqa: ANN001, ANN003
        if kwargs["user_id"] == unlucky.id:
            raise RuntimeError("the profile for this user could not be read")
        return await real_compute(session_, **kwargs)

    monkeypatch.setattr(alerts, "compute_for_user", compute)
    outcome = await dispatch(
        session, events=[(event, opportunity)], now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC)
    )
    await session.commit()

    assert outcome.sent == 1, "the healthy user lost their alert"
    assert outcome.failed == 1, "the relevance failure was not counted"
    assert any("was not evaluated" in reason for reason in outcome.reasons), outcome.reasons

    rows = (await session.execute(sa.select(AlertDelivery))).scalars().all()
    assert {row.user_id for row in rows} == {lucky.id}, (
        "a rule was evaluated for a user whose relevance could not be prepared"
    )
    unlucky_rule = (
        (await session.execute(sa.select(AlertRule).where(AlertRule.user_id == unlucky.id))).scalars().one()
    )
    assert unlucky_rule.last_fired_at is None, "a rule that never ran recorded that it fired"


async def test_a_relevance_failure_is_logged_as_a_code_and_a_class_name(session, worker_db, monkeypatch):
    recorder = RecordingLogger()
    monkeypatch.setattr(alerts, "logger", recorder)
    user = await make_user(session, email="logged-relevance@example.org")
    await make_rule(session, user, cooldown_hours=0)
    event, opportunity = await _event_for(session, slug="relevance-logging")

    async def compute(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError(f"chat {CHAT_ID} token {BOT_TOKEN} body of a private alert")

    monkeypatch.setattr(alerts, "compute_for_user", compute)
    outcome = await dispatch(session, events=[(event, opportunity)])
    await session.commit()

    assert outcome.failed == 1
    entry = recorder.events("alerts.relevance_failed")
    assert len(entry) == 1
    assert entry[0] == {"outcome": "user_skipped", "error_type": "RuntimeError"}
    logged = recorder.rendered()
    for secret in (BOT_TOKEN, CHAT_ID, "private alert"):
        assert secret not in logged
    # The reasons are returned to the administrator who ran the phase, so they
    # get the same treatment as a log line.
    assert all(BOT_TOKEN not in reason and CHAT_ID not in reason for reason in outcome.reasons)


# ----------------------------------------------------------------------- rules
async def test_one_rules_failure_does_not_stop_the_users_other_rules(session, worker_db, monkeypatch):
    user = await make_user(session, email="two-rules@example.org")
    broken = await make_rule(session, user, name="broken rule", cooldown_hours=0)
    working = await make_rule(session, user, name="working rule", cooldown_hours=0)
    event, opportunity = await _event_for(session, slug="rule-isolation")

    real_deliver = alerts._deliver  # noqa: SLF001

    async def deliver(session_, *, rule, **kwargs):  # noqa: ANN001, ANN003
        if rule.id == broken.id:
            raise RuntimeError("this rule could not be delivered")
        return await real_deliver(session_, rule=rule, **kwargs)

    monkeypatch.setattr(alerts, "_deliver", deliver)
    outcome = await dispatch(session, events=[(event, opportunity)])
    await session.commit()

    assert outcome.sent == 1
    assert outcome.failed == 1
    assert any("broken rule" in reason and "skipped" in reason for reason in outcome.reasons)

    rows = (await session.execute(sa.select(AlertDelivery))).scalars().all()
    assert [row.rule_id for row in rows] == [working.id], (
        "the failed rule's half-written rows survived its savepoint"
    )
    refreshed_broken = (
        (await session.execute(sa.select(AlertRule).where(AlertRule.id == broken.id))).scalars().one()
    )
    assert refreshed_broken.last_fired_at is None, "a rule that failed recorded that it fired"


async def test_a_savepoint_holds_database_work_but_not_a_message_already_sent(
    session, worker_db, monkeypatch
):
    """The limitation, measured rather than asserted in a docstring.

    The provider below takes the message and then fails. The savepoint rolls the
    rule's rows back — including the delivery record and the cooldown timestamp —
    but it cannot recall a message that already left. The consequence is concrete
    and worth seeing: because the database no longer knows the message was sent,
    the next run sends it again.
    """
    user = await make_user(session, email="sent-then-failed@example.org")
    rule = await make_rule(session, user, name="sent then failed", cooldown_hours=24, channels=["telegram"])
    session.add(
        NotificationChannelLink(user_id=user.id, channel="telegram", external_id=CHAT_ID, verified=True)
    )
    await session.commit()
    first_event, opportunity = await _event_for(session, slug="sent-then-failed")

    channel = RecordingThenRaisingChannel(RuntimeError("the connection dropped after the send"))
    monkeypatch.setitem(notification_base._REGISTRY, "telegram", channel)  # noqa: SLF001

    outcome = await dispatch(session, events=[(first_event, opportunity)])
    await session.commit()

    assert channel.attempts == [CHAT_ID], "the message was never handed to the channel"
    assert outcome.failed == 1
    assert outcome.sent == 0, "a send that raised was counted as delivered"
    rows = (await session.execute(sa.select(AlertDelivery))).scalars().all()
    assert rows == [], (
        "the rule's rows survived its savepoint — including the in-app record, "
        "which is the point: nothing anywhere says the message went out"
    )
    refreshed = (await session.execute(sa.select(AlertRule).where(AlertRule.id == rule.id))).scalars().one()
    assert refreshed.last_fired_at is None, "the cooldown was applied to a rule whose work was rolled back"

    # And the part nobody wants but everybody should know: the next run repeats
    # the send, because nothing recorded the first one.
    second_event, _ = await _event_for(session, slug="sent-then-failed-again")
    await dispatch(session, events=[(second_event, opportunity)])
    await session.commit()
    assert channel.attempts == [CHAT_ID, CHAT_ID], "the documented duplicate did not happen"


async def test_a_rule_failure_logs_a_code_and_never_the_exception_text(session, worker_db, monkeypatch):
    recorder = RecordingLogger()
    monkeypatch.setattr(alerts, "logger", recorder)
    user = await make_user(session, email="logged-rule@example.org")
    await make_rule(session, user, name="logged rule", cooldown_hours=0)
    event, opportunity = await _event_for(session, slug="rule-logging")

    async def deliver(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError(f"INSERT INTO alert_deliveries VALUES ('{BOT_TOKEN}', '{CHAT_ID}')")

    monkeypatch.setattr(alerts, "_deliver", deliver)
    outcome = await dispatch(session, events=[(event, opportunity)])
    await session.commit()

    entry = recorder.events("alerts.rule_failed")
    assert entry == [{"outcome": "rule_skipped", "error_type": "RuntimeError"}]
    logged = recorder.rendered()
    assert BOT_TOKEN not in logged and CHAT_ID not in logged
    assert "logged rule" in json.dumps(outcome.reasons), "the reason must still say which rule failed"
    assert BOT_TOKEN not in json.dumps(outcome.reasons)


# ---------------------------------------------------------------------- digests
async def test_one_users_digest_failure_does_not_cost_the_next_user_theirs(session, worker_db, monkeypatch):
    broken = await make_user(
        session, email="digest-broken@example.org", digest_frequency=DigestFrequency.DAILY.value
    )
    fine = await make_user(
        session, email="digest-fine@example.org", digest_frequency=DigestFrequency.DAILY.value
    )
    period = digest_period("daily", now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC), timezone_name="UTC")
    real_build = alerts.build_digest

    async def build(session_, *, user, **kwargs):  # noqa: ANN001, ANN003
        if user.id == broken.id:
            raise RuntimeError("this user's digest could not be assembled")
        return await real_build(session_, user=user, **kwargs)

    monkeypatch.setattr(alerts, "build_digest", build)
    outcome = await run_digests(session, frequency="daily", period=period)
    await session.commit()

    assert (outcome.written, outcome.failed, outcome.duplicates) == (1, 1, 0)
    rows = (await session.execute(sa.select(Digest))).scalars().all()
    assert [row.user_id for row in rows] == [fine.id], (
        "the healthy user's digest was rolled back with the failed one"
    )
    assert rows[0].period_key == "daily:2026-09-06"


async def test_a_digest_failure_is_counted_once_per_user_not_once_per_attempt(
    session, worker_db, monkeypatch
):
    """Counters describe successful and failed units, and nothing else."""
    for index in range(3):
        await make_user(
            session,
            email=f"counted{index}@example.org",
            digest_frequency=DigestFrequency.DAILY.value,
        )
    period = digest_period("daily", now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC), timezone_name="UTC")

    async def always_fail(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("no digest for anyone")

    monkeypatch.setattr(alerts, "build_digest", always_fail)
    outcome = await run_digests(session, frequency="daily", period=period)
    await session.commit()

    assert (outcome.written, outcome.failed, outcome.duplicates) == (0, 3, 0)
    assert (await session.execute(sa.select(Digest))).scalars().all() == []


# ------------------------------------------------------- not a unit failure
@pytest.mark.parametrize(
    "error",
    [
        sa.exc.PendingRollbackError("the transaction is unusable"),
        sa.exc.InterfaceError("statement", {}, Exception("gone")),
        sa.exc.OperationalError("statement", {}, Exception("gone")),
        SoftTimeLimitExceeded(),
        TimeLimitExceeded(),
        KeyboardInterrupt(),
        SystemExit(),
    ],
    ids=[
        "pending-rollback",
        "interface",
        "operational",
        "soft-time-limit",
        "time-limit",
        "keyboard-interrupt",
        "system-exit",
    ],
)
async def test_a_stop_signal_is_never_contained_by_a_savepoint(session, worker_db, monkeypatch, error):
    """A stop signal is not a unit failure, in all three isolation points.

    Containing one of these would keep a worker running past the limit its
    supervisor set, or keep issuing statements on a connection that is gone, and
    Celery's hard kill would then land in the middle of a transaction.

    Objects are reloaded before each case: the rollback that follows a propagated
    error expires everything in the session, and an expired attribute read inside
    a synchronous expression is exactly the ORM trap the isolation code has to
    avoid (it reads `rule.name` *before* opening a savepoint for the same reason).
    """
    from app.models.models import Opportunity, OpportunityChangeEvent

    user = await make_user(session, email="fatal@example.org", digest_frequency=DigestFrequency.DAILY.value)
    await make_rule(session, user, cooldown_hours=0)
    await _event_for(session, slug="fatal")

    async def reloaded():  # noqa: ANN202
        event = (await session.execute(sa.select(OpportunityChangeEvent))).scalars().one()
        opportunity = (await session.execute(sa.select(Opportunity))).scalars().one()
        return [(event, opportunity)]

    async def relevance_explodes(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise error

    monkeypatch.setattr(alerts, "compute_for_user", relevance_explodes)
    with pytest.raises(type(error)):
        await dispatch(session, events=await reloaded())
    await session.rollback()

    async def deliver_explodes(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise error

    monkeypatch.setattr(alerts, "_deliver", deliver_explodes)
    with pytest.raises(type(error)):
        await dispatch(session, events=await reloaded())
    await session.rollback()

    async def digest_explodes(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise error

    monkeypatch.setattr(alerts, "build_digest", digest_explodes)
    with pytest.raises(type(error)):
        await run_digests(
            session,
            frequency="daily",
            period=digest_period("daily", now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC)),
        )
    await session.rollback()


def test_is_unrecoverable_separates_stop_signals_from_unit_failures():
    unit_failures = [
        ValueError("a bad value"),
        RuntimeError("anything"),
        ZeroDivisionError("division by zero"),
        sa.exc.IntegrityError("statement", {}, Exception("unique")),
        httpx.ConnectError("refused"),
        smtplib.SMTPException("refused"),
    ]
    for error in unit_failures:
        assert is_unrecoverable(error) is False, f"{type(error).__name__} would have stopped the run"

    invalidated = sa.exc.DBAPIError("statement", {}, Exception("gone"))
    invalidated.connection_invalidated = True
    stops = [
        sa.exc.PendingRollbackError("unusable"),
        sa.exc.InterfaceError("statement", {}, Exception("gone")),
        sa.exc.OperationalError("statement", {}, Exception("gone")),
        invalidated,
        SoftTimeLimitExceeded(),
        TimeLimitExceeded(),
        KeyboardInterrupt(),
        SystemExit(),
    ]
    for error in stops:
        assert is_unrecoverable(error) is True, f"{type(error).__name__} would have been contained"


def test_the_stop_signals_are_matched_by_name_so_the_api_never_imports_celery():
    """A design constraint, pinned: `app.core.errors` is imported by the request path.

    Importing Celery there would put a worker library in every API process. So
    the signals are recognised by class name, which also means a signal class
    that arrives from a different module — a future supervisor, a test double —
    is still treated as a stop.
    """

    import app.core.errors as errors_module

    class SoftTimeLimitExceeded(Exception):
        """Same name, different module — still a stop signal."""

    assert is_unrecoverable(SoftTimeLimitExceeded()) is True

    tree = ast.parse(inspect.getsource(errors_module))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported |= {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    }
    assert not any(name and name.split(".")[0] == "celery" for name in imported), (
        "app.core.errors is imported by the API; a Celery import would put a worker "
        "library in every request process"
    )


def test_failure_code_is_a_class_name_and_carries_no_data():
    """The only part of an exception that is always safe to log or store."""
    assert failure_code(RuntimeError(" ".join([BOT_TOKEN, CHAT_ID, SMTP_PASSWORD]))) == "RuntimeError"

    class TelegramRefused(RuntimeError):
        """A project-specific failure keeps its own name."""

    assert failure_code(TelegramRefused(BOT_TOKEN)) == "TelegramRefused"

    # The case that motivated the rule: a DBAPIError renders its statement and
    # its bound parameters, which here would be chat ids and alert bodies.
    database_error = sa.exc.OperationalError(
        "INSERT INTO alert_deliveries (body) VALUES (:body)",
        {"body": f"private alert for chat {CHAT_ID}"},
        Exception("connection lost"),
    )
    assert failure_code(database_error) == "OperationalError"
    assert CHAT_ID not in failure_code(database_error)
    assert CHAT_ID in str(database_error), "and the exception text really does carry it"


# ------------------------------------------------- provider failure details
class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _install_failing_telegram(monkeypatch, error: Exception) -> list[str]:
    """Replace httpx inside the provider module with one that fails.

    The URLs the provider builds are recorded, so a test can show that the token
    really was in the request — and really is not in what got stored.
    """
    requested: list[str] = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        async def __aenter__(self):  # noqa: ANN204
            return self

        async def __aexit__(self, *_exc) -> None:  # noqa: ANN002, ANN204
            return None

        async def post(self, url, json=None):  # noqa: A002, ANN001, ANN204
            requested.append(url)
            raise error

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    return requested


async def test_a_telegram_failure_stores_a_class_name_not_the_token(session, worker_db, monkeypatch):
    """The detail is persisted to `alert_deliveries.suppressed_reason`.

    That column is read back through the API by the user and by administrators,
    so a token in an exception message would not merely be logged — it would be
    written into a row people can query. httpx puts the request URL in its
    exception text, and the Telegram send URL contains the bot token.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    from app.core.config import get_settings

    get_settings.cache_clear()
    recorder = RecordingLogger()
    monkeypatch.setattr(alerts, "logger", recorder)
    from app.notifications import providers as provider_module

    monkeypatch.setattr(provider_module, "logger", recorder)
    requested = _install_failing_telegram(
        monkeypatch,
        httpx.ConnectError(
            f"[Errno 111] Connection refused while posting to "
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        ),
    )

    try:
        user = await make_user(session, email="telegram-failure@example.org")
        await make_rule(session, user, name="telegram rule", cooldown_hours=0, channels=["telegram"])
        session.add(
            NotificationChannelLink(user_id=user.id, channel="telegram", external_id=CHAT_ID, verified=True)
        )
        await session.commit()
        event, opportunity = await _event_for(session, slug="telegram-failure")

        outcome = await dispatch(session, events=[(event, opportunity)])
        await session.commit()

        assert BOT_TOKEN in requested[0], "the test must prove the token was in the request"
        row = (
            (await session.execute(sa.select(AlertDelivery).where(AlertDelivery.channel == "telegram")))
            .scalars()
            .one()
        )
        assert row.status == DeliveryStatus.SUPPRESSED
        assert row.suppressed_reason == (
            "Telegram delivery failed (ConnectError). The alert is still readable in the application."
        )
        assert BOT_TOKEN not in row.suppressed_reason
        assert CHAT_ID not in row.suppressed_reason
        # The same text is returned to whoever ran the phase, so check it there too.
        assert all(BOT_TOKEN not in reason for reason in outcome.reasons)
        assert BOT_TOKEN not in recorder.rendered()
        assert recorder.events("notification.telegram_failed") == [
            {"outcome": "delivery_failed", "error_type": "ConnectError"}
        ]
        assert outcome.sent == 1, "the in-app fallback still worked"
    finally:
        get_settings.cache_clear()


async def test_an_smtp_failure_stores_a_class_name_not_the_reply(monkeypatch):
    """smtplib's exception text is the server's reply, plus who it was about."""

    class FakeSMTP:
        def __init__(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *_exc) -> None:  # noqa: ANN002, ANN204
            return None

        def starttls(self) -> None:
            return None

        def login(self, user, password):  # noqa: ANN001, ANN204
            raise smtplib.SMTPAuthenticationError(
                535, f"authentication failed for {user} with password {SMTP_PASSWORD}".encode()
            )

        def send_message(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
            raise AssertionError("a server that refused the login was used to send")

    monkeypatch.setenv("SMTP_HOST", "smtp.example.org")
    monkeypatch.setenv("SMTP_FROM", "alerts@example.org")
    monkeypatch.setenv("SMTP_USERNAME", "alerts@example.org")
    monkeypatch.setenv("SMTP_PASSWORD", SMTP_PASSWORD)
    from app.core.config import get_settings

    get_settings.cache_clear()
    recorder = RecordingLogger()
    from app.notifications import providers as provider_module

    monkeypatch.setattr(provider_module, "logger", recorder)
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)

    try:
        result = await EmailProvider().send(
            address=RECIPIENT,
            message=NotificationMessage(title="An opportunity moved", body="Score 60 -> 80."),
        )

        assert result.delivered is False
        assert result.live is False, "nothing reached an SMTP server, so nothing was really sent"
        assert result.detail == (
            "SMTP delivery failed (SMTPAuthenticationError). The alert is still readable in the application."
        )
        for secret in (SMTP_PASSWORD, RECIPIENT, "alerts@example.org", "535"):
            assert secret not in result.detail, f"the stored detail carried {secret}"
        assert recorder.events("notification.email_failed") == [
            {"outcome": "delivery_failed", "error_type": "SMTPAuthenticationError"}
        ]
        assert SMTP_PASSWORD not in recorder.rendered()
    finally:
        get_settings.cache_clear()


async def test_an_http_refusal_is_reported_as_a_status_not_as_a_body(monkeypatch):
    """A 4xx from Telegram is a fact about the message, and the body may quote it back.

    The status code is safe to store; the response body is not, since Telegram
    echoes the request — including the chat id — in some error payloads.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    from app.core.config import get_settings

    get_settings.cache_clear()

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        async def __aenter__(self):  # noqa: ANN204
            return self

        async def __aexit__(self, *_exc) -> None:  # noqa: ANN002, ANN204
            return None

        async def post(self, url, json=None):  # noqa: A002, ANN001, ANN204
            return _FakeResponse(403)

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    try:
        result = await TelegramProvider().send(
            address=CHAT_ID,
            message=NotificationMessage(title="An opportunity moved", body="Score 60 -> 80."),
        )
        assert result.delivered is False
        assert result.detail == "Telegram refused the message: HTTP 403."
        assert BOT_TOKEN not in result.detail and CHAT_ID not in result.detail
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("provider_name", ["telegram", "email"])
async def test_a_provider_stops_on_a_shutdown_signal_instead_of_reporting_a_failed_send(
    monkeypatch, provider_name
):
    """A time limit inside a send is not a failed send."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.org")
    monkeypatch.setenv("SMTP_FROM", "alerts@example.org")
    from app.core.config import get_settings

    get_settings.cache_clear()

    requested = _install_failing_telegram(monkeypatch, SoftTimeLimitExceeded())

    class FakeSMTP:
        def __init__(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
            raise SoftTimeLimitExceeded()

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    message = NotificationMessage(title="t", body="b")
    try:
        provider = TelegramProvider() if provider_name == "telegram" else EmailProvider()
        with pytest.raises(SoftTimeLimitExceeded):
            await provider.send(address=CHAT_ID, message=message)
    finally:
        get_settings.cache_clear()
    assert provider_name == "email" or requested, "the telegram path never reached the client"


# ------------------------------------------------------------------ the wiring
async def test_a_contained_unit_failure_is_not_a_failed_phase(session, worker_db, monkeypatch):
    """The phase reports the failure as a count, and still finishes.

    A run where one rule broke is a run that happened: the audit row says how
    many alerts were sent and how many units failed, and the operator can see
    both without the whole night being marked as lost.
    """
    user = await make_user(session, email="wired@example.org")
    await make_confirmed_opportunity(session, slug="wired")
    await make_rule(session, user, trigger="confirmation_met", cooldown_hours=0)

    async def deliver(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("this rule could not be delivered")

    monkeypatch.setattr(alerts, "_deliver", deliver)
    result = await tasks.run_monitoring_phase(now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))

    assert result["status"] == "ok", "a contained unit failure is not a failed phase"
    assert result["alerts_failed"] == 1
    assert result["alerts_sent"] == 0
    assert result["events"] == 1, "the event was still recorded; only the alert failed"
