"""Delivering a stored digest: who gets one, how often, and what is recorded.

A digest used to stop at the database. This file covers the step that takes it
to a person — a daily digest sent to a **verified** Telegram chat through the
provider that already existed — and the record that makes an ordinary repeat
harmless.

The two properties that matter most are asserted directly, not assumed:

* **the delivery record is committed before the send**, so a repeated run finds
  the row and does not send again; and
* **an existing record is never resent** — not `sent`, not `suppressed`, not
  `failed`, not `pending`. An interrupted send therefore leaves a `pending` row
  and a user without a message. That is a real gap, it is visible here and in the
  phase report, and this MVP does not close it automatically.

Delivery is best-effort and **not exactly-once**: a provider can hand the message
to Telegram and then lose the response, leaving a `failed` row for a message the
user did receive. Nothing in these tests claims otherwise.

No test here contacts Telegram. `conftest.py` blocks sockets, and the channel is
replaced by a fake for the duration of each test; the one test that uses the real
provider uses it *unconfigured*, which is the path that reports itself honestly
instead of sending.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
import sqlalchemy as sa
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.telegram import REPLY_OK
from app.core.config import get_settings
from app.models.enums import DeliveryStatus, DigestFrequency
from app.models.models import AlertDelivery, Digest, NotificationChannelLink, User
from app.notifications import base as notification_base
from app.notifications.base import NotificationMessage, ProviderResult
from app.services import alerts
from app.services.alerts import (
    TELEGRAM_TEXT_LIMIT,
    build_digest,
    deliver_digests,
    digest_delivery_key,
    digest_period,
    render_digest_message,
)
from app.workers import tasks
from tests.test_telegram_binding import SECRET_HEADER, WEBHOOK, update
from tests.test_worker_scheduling import make_user

MONDAY_0300 = datetime(2026, 9, 7, 3, 0, tzinfo=UTC)
DAILY_KEY = "daily:2026-09-06"
CHAT = "555000111"
WEBHOOK_SECRET = "test-webhook-secret-value"
SECRET = "bot123456:AAFakeTokenThatMustNeverBeStored"


@pytest_asyncio.fixture
async def worker_db(engine, monkeypatch):
    """Point the workers' own session factory at this test's database."""
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(tasks, "SessionLocal", maker)
    return maker


class FakeTelegram:
    """A provider stand-in that records every message it was handed.

    `refuse` reports a failed send the way a real channel does (data, not an
    exception); `explode` raises, which is the path that has to be contained
    without leaking exception text into a row anybody can read.
    """

    channel = "telegram"
    configured = True

    def __init__(self, *, refuse: tuple[str, ...] = (), explode: Exception | None = None) -> None:
        self.sent: list[tuple[str, NotificationMessage]] = []
        self.refuse = set(refuse)
        self.explode = explode

    async def send(self, *, address: str, message: NotificationMessage) -> ProviderResult:
        self.sent.append((address, message))
        if self.explode is not None:
            raise self.explode
        if address in self.refuse:
            return ProviderResult(delivered=False, detail="Refused by the fake channel.")
        return ProviderResult(delivered=True, detail="Delivered by the fake channel.", live=True)


@pytest.fixture
def fake_telegram(monkeypatch):
    def _install(**kwargs: object) -> FakeTelegram:
        fake = FakeTelegram(**kwargs)  # type: ignore[arg-type]
        monkeypatch.setitem(notification_base._REGISTRY, "telegram", fake)  # noqa: SLF001
        return fake

    return _install


class RecordingLogger:
    """Replaces the bound structlog logger so a test can read what was emitted."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, dict]] = []

    def _record(self, event: str, **kwargs: object) -> None:
        self.entries.append((event, kwargs))

    debug = info = warning = error = exception = _record

    def rendered(self) -> str:
        return json.dumps([[event, {k: str(v) for k, v in kw.items()}] for event, kw in self.entries])


async def _linked_user(
    session: AsyncSession,
    *,
    email: str,
    frequency: str = DigestFrequency.DAILY.value,
    chat: str = CHAT,
    verified: bool = True,
) -> None:
    user = await make_user(session, email=email, digest_frequency=frequency)
    session.add(
        NotificationChannelLink(
            user_id=user.id,
            channel="telegram",
            external_id=chat if verified else f"pending:{chat}",
            verified=verified,
            verified_at=MONDAY_0300 if verified else None,
            link_code=None if verified else "abcdef",
            link_code_expires_at=None if verified else MONDAY_0300,
        )
    )
    await session.commit()


async def _stored_digest(session: AsyncSession, *, email: str, frequency: str = "daily") -> Digest:
    """A committed digest for the canonical period, the way the schedule writes it."""
    account = (await session.execute(sa.select(User).where(User.email == email))).scalars().one()
    period = digest_period(frequency, now=MONDAY_0300, timezone_name="UTC")
    digest = await build_digest(session, user=account, frequency=frequency, now=MONDAY_0300, period=period)
    await session.commit()
    assert digest is not None
    return digest


def _same_instant(stored: datetime | None, expected: datetime) -> bool:
    """SQLite hands back naive datetimes; PostgreSQL hands back the instant."""
    if stored is None:
        return False
    if stored.tzinfo is None:
        return stored == expected.replace(tzinfo=None)
    return stored == expected


async def _deliveries(session: AsyncSession) -> list[AlertDelivery]:
    return list((await session.execute(sa.select(AlertDelivery))).scalars())


# ----------------------------------------------------------------- the happy path
async def test_a_daily_digest_reaches_the_verified_chat(session, worker_db, fake_telegram):
    fake = fake_telegram()
    await _linked_user(session, email="reader@example.org")
    await _stored_digest(session, email="reader@example.org")
    period = digest_period("daily", now=MONDAY_0300, timezone_name="UTC")

    outcome = await deliver_digests(session, frequency="daily", period=period, now=MONDAY_0300)

    assert (outcome.sent, outcome.suppressed, outcome.failed) == (1, 0, 0)
    assert outcome.already_recorded == 0
    assert [address for address, _ in fake.sent] == [CHAT]

    rows = await _deliveries(session)
    assert len(rows) == 1
    row = rows[0]
    assert row.channel == "telegram"
    assert row.status == DeliveryStatus.SENT
    assert _same_instant(row.sent_at, MONDAY_0300)
    assert row.dedupe_key == digest_delivery_key(period_key=DAILY_KEY, channel="telegram")
    assert row.dedupe_key == f"digest:{DAILY_KEY}:telegram"
    # A digest delivery references no rule, opportunity or event, and invents none.
    assert row.rule_id is None
    assert row.opportunity_id is None
    assert row.event_id is None
    assert row.title == f"Your daily digest — {DAILY_KEY.split(':')[1]}"
    assert row.suppressed_reason is None
    assert fake.sent[0][1].link == "/for-you"


async def test_a_user_with_two_verified_chats_gets_one_message(session, worker_db, fake_telegram):
    """The delivery key names the period and the channel, not the chat.

    Somebody who links a second Telegram account is still one subscriber: the
    older binding wins deterministically, and the second row comes back as
    `already_recorded` rather than as a duplicate message.
    """
    fake = fake_telegram()
    await _linked_user(session, email="two-chats@example.org", chat="older-chat")
    account = (
        (await session.execute(sa.select(User).where(User.email == "two-chats@example.org"))).scalars().one()
    )
    session.add(
        NotificationChannelLink(
            user_id=account.id,
            channel="telegram",
            external_id="newer-chat",
            verified=True,
            verified_at=MONDAY_0300 + timedelta(hours=1),
        )
    )
    await session.commit()
    await _stored_digest(session, email="two-chats@example.org")

    outcome = await deliver_digests(
        session, frequency="daily", period=digest_period("daily", now=MONDAY_0300), now=MONDAY_0300
    )

    assert [address for address, _ in fake.sent] == ["older-chat"]
    assert (outcome.sent, outcome.already_recorded) == (1, 1)
    assert len(await _deliveries(session)) == 1


async def test_the_message_fits_inside_telegrams_limit():
    """Telegram rejects anything over 4096 characters, and a digest can be huge.

    Measured the way the provider actually renders it — title, body and the
    absolute link built from `APP_BASE_URL` — so the guarantee is about the text
    that goes on the wire, not about the body alone.
    """
    sections = {
        "period": {"key": DAILY_KEY, "frequency": "daily"},
        "watchlist_changes": [
            {"opportunity_id": str(i), "title": "W" * 300, "kind": "score_rose", "summary": "S" * 300}
            for i in range(60)
        ],
        "other_changes": [
            {"opportunity_id": str(i), "title": "O" * 300, "kind": "confirmation_met", "summary": "S" * 300}
            for i in range(60)
        ],
        "alerts": [],
        "note": "This is a summary of what changed, not a recommendation.",
    }
    digest = Digest(
        frequency="daily",
        period_start=datetime(2026, 9, 6, tzinfo=UTC),
        period_end=MONDAY_0300,
        period_key=DAILY_KEY,
        sections=sections,
        item_count=120,
        generated_at=MONDAY_0300,
    )
    message = render_digest_message(digest)
    base = get_settings().APP_BASE_URL.rstrip("/")
    rendered = f"{message.title}\n\n{message.body}\n\n{base}{message.link}"

    assert len(rendered) <= TELEGRAM_TEXT_LIMIT
    assert len(message.title) <= 300, "alert_deliveries.title is VARCHAR(300)"
    assert base in rendered, "the message has to carry a usable way into the app"
    assert "not a recommendation" in message.body, "the disclaimer survives the trim"


async def test_a_quiet_day_is_delivered_as_a_quiet_day(session, worker_db, fake_telegram):
    """Nothing changed is still worth one message; silence looks like a outage."""
    fake = fake_telegram()
    await _linked_user(session, email="quiet@example.org")
    await _stored_digest(session, email="quiet@example.org")

    outcome = await deliver_digests(
        session, frequency="daily", period=digest_period("daily", now=MONDAY_0300), now=MONDAY_0300
    )

    assert outcome.sent == 1
    body = fake.sent[0][1].body
    assert "Nothing on your watchlists changed" in body


# ------------------------------------------------------------------- who is told
async def test_an_unverified_chat_is_never_written_to(session, worker_db, fake_telegram):
    """The security property the linking flow exists for, on the digest path too."""
    fake = fake_telegram()
    await _linked_user(session, email="unverified@example.org", verified=False)
    await _stored_digest(session, email="unverified@example.org")

    outcome = await deliver_digests(
        session, frequency="daily", period=digest_period("daily", now=MONDAY_0300), now=MONDAY_0300
    )

    assert fake.sent == [], "a chat that never proved it belongs to this user was written to"
    assert (outcome.sent, outcome.suppressed, outcome.failed) == (0, 0, 0)
    assert outcome.not_eligible == 1, "an undeliverable user vanished without being counted"
    assert await _deliveries(session) == []


async def test_a_user_who_switched_off_is_not_sent_the_digest_they_used_to_want(
    session, worker_db, fake_telegram
):
    """The preference is read at delivery time, not at generation time."""
    fake = fake_telegram()
    await _linked_user(session, email="changed-mind@example.org", frequency=DigestFrequency.WEEKLY.value)
    await _stored_digest(session, email="changed-mind@example.org", frequency="daily")

    outcome = await deliver_digests(
        session, frequency="daily", period=digest_period("daily", now=MONDAY_0300), now=MONDAY_0300
    )

    assert fake.sent == []
    assert outcome.sent == 0
    assert outcome.not_eligible == 1


async def test_weekly_digests_are_generated_but_not_delivered(session, worker_db, fake_telegram):
    """The UI labels weekly as in-app only, and the backend has to agree."""
    fake = fake_telegram()
    await _linked_user(session, email="weekly@example.org", frequency=DigestFrequency.WEEKLY.value)
    await _stored_digest(session, email="weekly@example.org", frequency="weekly")

    outcome = await deliver_digests(
        session,
        frequency="weekly",
        period=digest_period("weekly", now=MONDAY_0300, timezone_name="UTC"),
        now=MONDAY_0300,
    )

    assert outcome.reason_code == "frequency_not_delivered"
    assert fake.sent == []
    assert await _deliveries(session) == []
    weekly = (await session.execute(sa.select(Digest))).scalars().one()
    assert weekly.period_key == "weekly:2026-W36", "weekly generation was disturbed"


async def test_an_on_demand_digest_is_not_sent_anywhere(session, worker_db, fake_telegram):
    """`POST /me/digests` stays a read-in-the-app action with no period identity."""
    fake = fake_telegram()
    outcome = await deliver_digests(session, frequency="daily", period=None, now=MONDAY_0300)

    assert outcome.reason_code == "no_period"
    assert fake.sent == []


async def test_an_unconfigured_provider_records_a_suppression_not_a_lie(session, worker_db, monkeypatch):
    """No bot token: nothing leaves the machine, and the row says exactly that."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    get_settings.cache_clear()
    try:
        await _linked_user(session, email="unconfigured@example.org")
        await _stored_digest(session, email="unconfigured@example.org")

        outcome = await deliver_digests(
            session, frequency="daily", period=digest_period("daily", now=MONDAY_0300), now=MONDAY_0300
        )

        assert (outcome.sent, outcome.suppressed, outcome.failed) == (0, 1, 0)
        row = (await _deliveries(session))[0]
        assert row.status == DeliveryStatus.SUPPRESSED
        assert "No telegram provider is configured" in row.suppressed_reason
        assert row.sent_at is None
    finally:
        get_settings.cache_clear()


# ------------------------------------------------------------------ not twice
async def test_a_repeated_run_does_not_send_the_same_digest_twice(session, worker_db, fake_telegram):
    """The dedupe key names the period, so a retry or a re-run collides with itself."""
    fake = fake_telegram()
    await _linked_user(session, email="repeat@example.org")
    await _stored_digest(session, email="repeat@example.org")
    period = digest_period("daily", now=MONDAY_0300, timezone_name="UTC")

    first = await deliver_digests(session, frequency="daily", period=period, now=MONDAY_0300)
    second = await deliver_digests(
        session, frequency="daily", period=period, now=MONDAY_0300 + timedelta(hours=6)
    )
    third = await deliver_digests(session, frequency="daily", period=period, now=MONDAY_0300)

    assert first.sent == 1
    assert (second.sent, second.already_recorded) == (0, 1)
    assert (third.sent, third.already_recorded) == (0, 1)
    assert len(fake.sent) == 1, "the same digest was sent more than once"
    assert len(await _deliveries(session)) == 1


async def test_a_different_period_is_a_different_message(session, worker_db, fake_telegram):
    fake = fake_telegram()
    await _linked_user(session, email="two-days@example.org")
    await _stored_digest(session, email="two-days@example.org")
    account = (
        (await session.execute(sa.select(User).where(User.email == "two-days@example.org"))).scalars().one()
    )
    earlier = digest_period("daily", now=datetime(2026, 9, 6, 3, 0, tzinfo=UTC), timezone_name="UTC")
    await build_digest(session, user=account, frequency="daily", now=MONDAY_0300, period=earlier)
    await session.commit()

    await deliver_digests(session, frequency="daily", period=earlier, now=MONDAY_0300)
    await deliver_digests(
        session, frequency="daily", period=digest_period("daily", now=MONDAY_0300), now=MONDAY_0300
    )

    assert len(fake.sent) == 2
    keys = sorted(row.dedupe_key for row in await _deliveries(session))
    assert keys == ["digest:daily:2026-09-05:telegram", "digest:daily:2026-09-06:telegram"]


async def test_an_interrupted_send_is_not_repeated_and_is_left_visible(session, worker_db, fake_telegram):
    """The gap this MVP accepts, pinned so nobody mistakes it for a retry.

    A `pending` row means the record was committed and the send did not complete.
    Nothing resends it: an automatic retry would risk a duplicate for a message
    that may well have arrived. So the row stays `pending`, the user is counted as
    `already_recorded`, and an operator has to look.
    """
    fake = fake_telegram()
    await _linked_user(session, email="interrupted@example.org")
    digest = await _stored_digest(session, email="interrupted@example.org")
    account = (
        (await session.execute(sa.select(User).where(User.email == "interrupted@example.org")))
        .scalars()
        .one()
    )
    session.add(
        AlertDelivery(
            user_id=account.id,
            channel="telegram",
            title="Your daily digest",
            body="interrupted",
            dedupe_key=digest_delivery_key(period_key=digest.period_key or DAILY_KEY, channel="telegram"),
            status=DeliveryStatus.PENDING,
        )
    )
    await session.commit()

    outcome = await deliver_digests(
        session, frequency="daily", period=digest_period("daily", now=MONDAY_0300), now=MONDAY_0300
    )

    assert fake.sent == [], "a pending record was resent"
    assert (outcome.sent, outcome.already_recorded) == (0, 1)
    row = (await _deliveries(session))[0]
    assert row.status == DeliveryStatus.PENDING, "the interrupted record was quietly rewritten"


async def test_the_record_is_durable_before_the_message_is_sent(session, worker_db, fake_telegram):
    """Commit-before-send, proved by a send that raises.

    The row exists even though nothing was delivered, which is what stops the
    next run from trying again — and what makes the failure investigable instead
    of invisible. This is the opposite order from alert dispatch, and it is
    affordable here because the digest itself is already committed.
    """
    fake = fake_telegram(explode=RuntimeError(f"connection dropped posting to {SECRET}"))
    await _linked_user(session, email="durable@example.org")
    await _stored_digest(session, email="durable@example.org")

    outcome = await deliver_digests(
        session, frequency="daily", period=digest_period("daily", now=MONDAY_0300), now=MONDAY_0300
    )
    await session.rollback()  # even a rollback after the fact cannot un-record it

    assert outcome.failed == 1
    assert len(fake.sent) == 1, "the send was attempted"
    rows = await _deliveries(session)
    assert len(rows) == 1, "the delivery record died with the transaction"
    assert rows[0].status == DeliveryStatus.FAILED
    assert SECRET not in (rows[0].suppressed_reason or ""), "exception text reached a readable row"
    assert "RuntimeError" in (rows[0].suppressed_reason or "")


# --------------------------------------------------------------------- failures
async def test_one_users_failed_send_does_not_stop_the_next_user(session, worker_db, fake_telegram):
    fake = fake_telegram(refuse=("refusing-chat",))
    await _linked_user(session, email="refused@example.org", chat="refusing-chat")
    await _linked_user(session, email="delivered@example.org", chat="good-chat")
    await _stored_digest(session, email="refused@example.org")
    await _stored_digest(session, email="delivered@example.org")

    outcome = await deliver_digests(
        session, frequency="daily", period=digest_period("daily", now=MONDAY_0300), now=MONDAY_0300
    )

    assert (outcome.sent, outcome.suppressed, outcome.failed) == (1, 1, 0)
    assert sorted(address for address, _ in fake.sent) == ["good-chat", "refusing-chat"]
    by_status = {row.status: row for row in await _deliveries(session)}
    assert by_status[DeliveryStatus.SUPPRESSED].suppressed_reason == "Refused by the fake channel."
    assert _same_instant(by_status[DeliveryStatus.SENT].sent_at, MONDAY_0300)


async def test_a_delivery_failure_is_logged_as_a_code_and_a_class_name(
    session, worker_db, fake_telegram, monkeypatch
):
    recorder = RecordingLogger()
    monkeypatch.setattr(alerts, "logger", recorder)
    fake = fake_telegram(explode=RuntimeError(f"https://api.telegram.org/{SECRET}/sendMessage chat {CHAT}"))
    await _linked_user(session, email="logged@example.org")
    await _stored_digest(session, email="logged@example.org")

    outcome = await deliver_digests(
        session, frequency="daily", period=digest_period("daily", now=MONDAY_0300), now=MONDAY_0300
    )

    assert outcome.failed == 1
    assert fake.sent, "the send was attempted"
    logged = recorder.rendered()
    assert SECRET not in logged and CHAT not in logged
    assert "digest.delivery_failed" in logged
    entry = next(kw for event, kw in recorder.entries if event == "digest.delivery_failed")
    assert entry == {"outcome": "delivery_failed", "frequency": "daily", "error_type": "RuntimeError"}


async def test_a_shutdown_signal_stops_delivery_instead_of_being_counted(session, worker_db, fake_telegram):
    fake_telegram(explode=SoftTimeLimitExceeded())
    await _linked_user(session, email="stopped@example.org")
    await _stored_digest(session, email="stopped@example.org")

    with pytest.raises(SoftTimeLimitExceeded):
        await deliver_digests(
            session, frequency="daily", period=digest_period("daily", now=MONDAY_0300), now=MONDAY_0300
        )


# --------------------------------------------------------------- phase wiring
async def test_the_phase_delivers_after_the_period_commits(session, worker_db, fake_telegram):
    fake = fake_telegram()
    await _linked_user(session, email="phase@example.org")

    result = await tasks.run_digest_phase(frequencies=["daily"], now=MONDAY_0300)

    assert result["status"] == "ok"
    entry = result["periods"][0]
    assert entry["period_key"] == DAILY_KEY
    assert entry["written"] == 1
    assert entry["delivery"] == {
        "status": "ok",
        "sent": 1,
        "suppressed": 0,
        "failed": 0,
        "already_recorded": 0,
        "not_eligible": 0,
        "reason_code": None,
    }
    assert result["delivery_failed_periods"] == []
    assert [address for address, _ in fake.sent] == [CHAT]


async def test_a_digest_from_an_earlier_run_is_delivered_when_generation_is_a_duplicate(
    session, worker_db, fake_telegram
):
    """Generation skipped as a duplicate must not mean the user gets nothing.

    The digest already exists — an earlier run wrote it — but no delivery record
    does, so this run delivers it. Selection is by stored row, not by what this
    run happened to write.
    """
    fake = fake_telegram()
    await _linked_user(session, email="earlier@example.org")
    await _stored_digest(session, email="earlier@example.org")

    result = await tasks.run_digest_phase(frequencies=["daily"], now=MONDAY_0300)

    entry = result["periods"][0]
    assert (entry["written"], entry["duplicates"]) == (0, 1), "the existing digest was rewritten"
    assert entry["delivery"]["sent"] == 1, "an already-generated digest never reached the user"
    assert len(fake.sent) == 1

    # And the run after that one finds both records already there.
    again = await tasks.run_digest_phase(frequencies=["daily"], now=MONDAY_0300)
    assert again["periods"][0]["delivery"] == {
        "status": "ok",
        "sent": 0,
        "suppressed": 0,
        "failed": 0,
        "already_recorded": 1,
        "not_eligible": 0,
        "reason_code": None,
    }
    assert len(fake.sent) == 1


async def test_a_delivery_failure_degrades_the_run_without_losing_the_digest(
    session, worker_db, fake_telegram
):
    """The digest is committed; the failure to deliver it is reported, not hidden."""
    fake_telegram(explode=RuntimeError("telegram unreachable"))
    await _linked_user(session, email="degraded@example.org")

    result = await tasks.run_digest_phase(frequencies=["daily"], now=MONDAY_0300)

    entry = result["periods"][0]
    assert entry["written"] == 1, "a delivery problem cost the user their digest"
    assert entry["delivery"]["status"] == "failed"
    assert entry["delivery"]["failed"] == 1
    assert result["delivery_failed_periods"] == [DAILY_KEY]
    assert result["failed_periods"] == [], (
        "a delivery failure must not queue a regeneration retry: it would not resend anything"
    )
    assert len((await session.execute(sa.select(Digest))).scalars().all()) == 1


async def test_the_pipeline_reports_a_delivery_failure_as_degraded(
    session, worker_db, fake_telegram, monkeypatch
):
    fake_telegram(explode=RuntimeError("telegram unreachable"))
    await _linked_user(session, email="pipeline@example.org")

    async def no_sources():  # noqa: ANN202
        return []

    monkeypatch.setattr(tasks, "_run_all", no_sources)
    report = await tasks.run_nightly_pipeline(now=MONDAY_0300)

    assert report["status"] == "degraded"
    assert report["phases"]["digests"]["delivery_failed_periods"] == [DAILY_KEY]
    assert report["phases"]["monitoring"]["status"] == "ok"


# ----------------------------------------------------------------- the API side
async def test_the_channels_api_exposes_a_real_expiry_and_never_the_code(client, admin_headers, monkeypatch):
    """Authoritative expiry, so the panel shows a deadline or shows nothing.

    Driven through the real webhook rather than by editing the row, because the
    claim is about what the listing reports across the whole lifecycle: the code
    appears once, in the response that created it; the listing reports the
    deadline the webhook itself enforces and no code; and redemption clears both
    together, leaving the UI with nothing to expire.
    """
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", WEBHOOK_SECRET)
    get_settings.cache_clear()
    try:
        created = await client.post(
            "/api/v1/me/channels", json={"channel": "telegram"}, headers=admin_headers
        )
        assert created.status_code == 201, created.text
        body = created.json()
        code = body["link_code"]
        assert code, "the creating response is the only place the code appears"
        assert code in (body["instructions"] or ""), "the instructions have to carry the code"
        assert body["link_code_expires_at"] is not None
        assert body["verified"] is False

        listed = await client.get("/api/v1/me/channels", headers=admin_headers)
        row = next(item for item in listed.json() if item["id"] == body["id"])
        assert row["link_code"] is None, "the code was echoed back after creation"
        assert row["link_code_expires_at"] == body["link_code_expires_at"], (
            "the deadline shown to the user is not the one the webhook enforces"
        )

        bound = await client.post(
            WEBHOOK,
            json=update(CHAT, f"/link {code}"),
            headers={SECRET_HEADER: WEBHOOK_SECRET},
        )
        assert bound.status_code == 200, bound.text
        assert bound.json()["reply"] == REPLY_OK

        after = await client.get("/api/v1/me/channels", headers=admin_headers)
        verified_row = next(item for item in after.json() if item["id"] == body["id"])
        assert verified_row["verified"] is True
        assert verified_row["link_code"] is None
        assert verified_row["link_code_expires_at"] is None, (
            "a verified link kept a deadline, which a UI would render as an expiry "
            "that no longer means anything"
        )
    finally:
        get_settings.cache_clear()


async def test_a_digest_is_listed_for_the_user_who_owns_it(client, admin_headers, second_headers):
    """The panel reads `GET /me/digests`, which is already private per user."""
    first = await client.post("/api/v1/me/digests?frequency=daily", headers=admin_headers)
    assert first.status_code == 201, first.text

    mine = await client.get("/api/v1/me/digests", headers=admin_headers)
    theirs = await client.get("/api/v1/me/digests", headers=second_headers)
    assert len(mine.json()) == 1
    assert theirs.json() == []
    assert mine.json()[0]["sections"]["period"]["frequency"] == "daily"
