"""Condition checking, change events, alerts, digests and channel security.

The rules being defended:

* Section 16: *never mark a condition MET using an LLM guess.* Nothing is met
  without a stored measurement satisfying a stored comparator, and a condition
  that cannot be checked is UNKNOWN, which is not FAILED.
* Section 18: an alert reaches a person once. The database constraint is the
  guarantee, not the code's good intentions.
* Section 21: never expose another user's data through the bot.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.enums import ConditionCheckState
from app.models.models import (
    AlertDelivery,
    AlertRule,
    ConditionCheck,
    Entity,
    NotificationChannelLink,
    Opportunity,
    OpportunityChangeEvent,
    OpportunityCondition,
    Signal,
    SignalObservation,
    Source,
    Trend,
    TrendSignal,
    User,
)
from app.notifications import get_provider, registered_channels
from app.notifications.base import NotificationMessage
from app.services.alerts import build_digest, dedupe_key, dispatch
from app.services.monitoring import (
    check_condition,
    detect_changes,
    monitor_all,
    run_condition_checks,
)
from tests.test_multi_user import make_opportunity

pytestmark = pytest.mark.asyncio


@pytest.fixture
def telegram_secret(monkeypatch) -> str:
    """Configure a webhook secret for the two chat-binding tests below.

    ``get_settings`` is lru_cached, so the cache is cleared on entry and exit to
    keep this configuration from leaking into the rest of the suite.
    """
    secret = "monitoring-suite-webhook-secret"
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", secret)
    get_settings.cache_clear()
    yield secret
    get_settings.cache_clear()


async def add_observations(
    session: AsyncSession,
    *,
    trend: Trend,
    signal_type: str,
    values: list[float],
    days_apart: int = 7,
) -> Signal:
    """Real stored measurements, so a condition check has something to check."""
    source = Source(
        slug=f"src-{signal_type}",
        name="Source",
        adapter_key="demo_mock",
        source_class="demo",
        reliability=0.7,
        config={},
    )
    session.add(source)
    await session.flush()

    entity = Entity(
        canonical_name=f"{signal_type} subject",
        normalized=f"{signal_type} subject",
        entity_type="technology",
    )
    session.add(entity)
    await session.flush()

    signal = Signal(
        entity_id=entity.id,
        signal_type=signal_type,
        signal_class="adoption",
        geo_scope=trend.geo_scope,
        source_id=source.id,
        unit="count",
    )
    session.add(signal)
    await session.flush()
    session.add(
        TrendSignal(
            trend_id=trend.id,
            signal_id=signal.id,
            source_id=source.id,
            signal_type=signal_type,
            source_group="demo",
            observation_count=len(values),
        )
    )
    now = datetime.now(UTC)
    for index, value in enumerate(reversed(values)):
        session.add(
            SignalObservation(
                signal_id=signal.id,
                observed_at=now - timedelta(days=index * days_apart),
                value=value,
                status="ok",
                confidence=0.8,
                source_reliability=0.7,
                collected_at=now,
            )
        )
    await session.commit()
    return signal


async def add_condition(
    session: AsyncSession, opp: Opportunity, *, kind: str, description: str, measurable: dict
) -> OpportunityCondition:
    condition = OpportunityCondition(
        opportunity_id=opp.id, kind=kind, description=description, measurable=measurable
    )
    session.add(condition)
    await session.commit()
    return condition


# ------------------------------------------------------------------ conditions
async def test_a_prose_condition_is_unknown_not_failed(session):
    """The condition is real. We simply cannot decide it by arithmetic."""
    opp = await make_opportunity(session, slug="c1", title="Candidate", score=70)
    condition = await add_condition(
        session,
        opp,
        kind="confirmation",
        description="A serious distributor signs a contract.",
        measurable={},
    )
    outcome = await check_condition(session, condition, trend_id=opp.primary_trend_id)
    assert outcome.state == ConditionCheckState.UNKNOWN
    assert outcome.state != ConditionCheckState.FAILED
    assert "not marked met or failed on a guess" in outcome.reason


async def test_a_condition_with_no_measurements_is_unknown_not_failed(session):
    """Missing data is not a failed condition. This is the fairness rule again."""
    opp = await make_opportunity(session, slug="c2", title="Candidate", score=70)
    condition = await add_condition(
        session,
        opp,
        kind="confirmation",
        description="Imports grow",
        measurable={"signal_type": "import_growth", "comparator": "gt", "value": 0.0},
    )
    outcome = await check_condition(session, condition, trend_id=opp.primary_trend_id)
    assert outcome.state == ConditionCheckState.UNKNOWN
    assert "Missing data is not a failed condition" in outcome.reason


async def test_a_condition_is_met_only_by_a_stored_measurement(session):
    opp = await make_opportunity(session, slug="c3", title="Candidate", score=70)
    trend = await session.get(Trend, opp.primary_trend_id)
    await add_observations(session, trend=trend, signal_type="import_growth", values=[12.0, 14.0, 16.0, 18.0])
    condition = await add_condition(
        session,
        opp,
        kind="confirmation",
        description="Imports grow above 10",
        measurable={"signal_type": "import_growth", "comparator": "gt", "value": 10.0},
    )
    outcome = await check_condition(session, condition, trend_id=opp.primary_trend_id)
    assert outcome.state == ConditionCheckState.MET
    assert outcome.evidence["observations"] == 4
    assert outcome.evidence["passing"] == 4
    assert outcome.evidence["signal_type"] == "import_growth"
    assert "latest reading" in outcome.reason


async def test_a_condition_that_the_evidence_contradicts_fails(session):
    opp = await make_opportunity(session, slug="c4", title="Candidate", score=70)
    trend = await session.get(Trend, opp.primary_trend_id)
    await add_observations(session, trend=trend, signal_type="import_growth", values=[1.0, 2.0, 1.5, 0.5])
    condition = await add_condition(
        session,
        opp,
        kind="confirmation",
        description="Imports grow above 10",
        measurable={"signal_type": "import_growth", "comparator": "gt", "value": 10.0},
    )
    outcome = await check_condition(session, condition, trend_id=opp.primary_trend_id)
    assert outcome.state == ConditionCheckState.FAILED
    assert "none satisfy it" in outcome.reason


async def test_a_mixed_picture_is_partly_met(session):
    opp = await make_opportunity(session, slug="c5", title="Candidate", score=70)
    trend = await session.get(Trend, opp.primary_trend_id)
    # Latest reading passes, most of the window does not.
    await add_observations(session, trend=trend, signal_type="import_growth", values=[2.0, 1.0, 3.0, 20.0])
    condition = await add_condition(
        session,
        opp,
        kind="confirmation",
        description="Imports grow above 10",
        measurable={"signal_type": "import_growth", "comparator": "gt", "value": 10.0},
    )
    outcome = await check_condition(session, condition, trend_id=opp.primary_trend_id)
    assert outcome.state == ConditionCheckState.PARTIALLY_MET


async def test_condition_checks_are_kept_as_history(session):
    opp = await make_opportunity(session, slug="c6", title="Candidate", score=70)
    await add_condition(
        session,
        opp,
        kind="confirmation",
        description="Something a human must judge",
        measurable={},
    )
    first, _ = await run_condition_checks(session, opportunity=opp)
    second, _ = await run_condition_checks(session, opportunity=opp)
    await session.commit()

    rows = (await session.execute(sa.select(ConditionCheck))).scalars().all()
    assert len(rows) == 2, "each check is a new row; history is never overwritten"
    assert first[0].id != second[0].id
    assert second[0].previous_state == ConditionCheckState.UNKNOWN


# ---------------------------------------------------------------- change events
async def test_a_score_move_becomes_a_change_event(session):
    opp = await make_opportunity(session, slug="e1", title="Mover", score=75.0)
    events = await detect_changes(
        session,
        opportunity=opp,
        previous={"opportunity_score": 55.0, "confidence": 70.0, "risk_level": "moderate"},
    )
    await session.commit()
    assert len(events) == 1
    assert events[0].kind == "score_rose"
    assert events[0].magnitude == 20.0
    assert "55" in events[0].summary and "75" in events[0].summary


async def test_noise_below_the_threshold_is_not_an_event(session):
    opp = await make_opportunity(session, slug="e2", title="Still", score=70.5)
    events = await detect_changes(
        session,
        opportunity=opp,
        previous={"opportunity_score": 70.0, "confidence": 70.0, "risk_level": "moderate"},
    )
    assert events == []


async def test_a_risk_change_is_recorded_in_the_right_direction(session):
    opp = await make_opportunity(session, slug="e3", title="Riskier", score=70.0, risk="high")
    events = await detect_changes(
        session,
        opportunity=opp,
        previous={"opportunity_score": 70.0, "confidence": 70.0, "risk_level": "low"},
    )
    await session.commit()
    assert [e.kind for e in events] == ["risk_rose"]


async def test_monitor_all_checks_and_records(session):
    opp = await make_opportunity(session, slug="e4", title="Monitored", score=70.0)
    trend = await session.get(Trend, opp.primary_trend_id)
    await add_observations(session, trend=trend, signal_type="import_growth", values=[30.0, 40.0])
    await add_condition(
        session,
        opp,
        kind="confirmation",
        description="Imports grow",
        measurable={"signal_type": "import_growth", "comparator": "gt", "value": 10.0},
    )
    counts = await monitor_all(session)
    await session.commit()
    assert counts["opportunities"] == 1
    assert counts["checks"] == 1
    assert counts["events"] == 1, "a confirmation being met is news"

    events = (await session.execute(sa.select(OpportunityChangeEvent))).scalars().all()
    assert events[0].kind == "confirmation_met"


# ---------------------------------------------------------------------- alerts
async def make_rule(session: AsyncSession, user: User, **over: object) -> AlertRule:
    defaults: dict = {
        "name": "Big moves",
        "trigger": "score_threshold",
        "enabled": True,
        "conditions": {},
        "channels": ["in_app"],
        "cooldown_hours": 24,
    }
    defaults.update(over)
    rule = AlertRule(user_id=user.id, **defaults)
    session.add(rule)
    await session.commit()
    return rule


async def test_an_alert_reaches_the_user_once_however_often_we_try(session, admin_user):
    """Section 18. The unique constraint is the guarantee."""
    opp = await make_opportunity(session, slug="a1", title="Alerted", score=80.0)
    # No cooldown, so nothing but the uniqueness constraint can stop the repeat.
    await make_rule(session, admin_user, cooldown_hours=0)
    events = await detect_changes(session, opportunity=opp, previous={"opportunity_score": 60.0})
    await session.commit()

    first = await dispatch(session, events=[(events[0], opp)])
    await session.commit()
    second = await dispatch(session, events=[(events[0], opp)])
    await session.commit()

    assert first.sent == 1
    assert second.sent == 0
    assert "already delivered" in second.reasons
    rows = (await session.execute(sa.select(AlertDelivery))).scalars().all()
    assert len(rows) == 1


async def test_a_rule_stays_quiet_during_its_cooldown(session, admin_user):
    opp_one = await make_opportunity(session, slug="a2", title="First", score=80.0)
    opp_two = await make_opportunity(session, slug="a3", title="Second", score=82.0)
    await make_rule(session, admin_user, cooldown_hours=12)

    now = datetime.now(UTC)
    first_event = (
        await detect_changes(session, opportunity=opp_one, previous={"opportunity_score": 60.0}, now=now)
    )[0]
    await session.commit()
    result = await dispatch(session, events=[(first_event, opp_one)], now=now)
    await session.commit()
    assert result.sent == 1

    second_event = (
        await detect_changes(
            session,
            opportunity=opp_two,
            previous={"opportunity_score": 60.0},
            now=now + timedelta(hours=1),
        )
    )[0]
    await session.commit()
    quiet = await dispatch(session, events=[(second_event, opp_two)], now=now + timedelta(hours=1))
    await session.commit()
    assert quiet.sent == 0
    assert any("cooldown" in r for r in quiet.reasons)

    later = await dispatch(session, events=[(second_event, opp_two)], now=now + timedelta(hours=13))
    await session.commit()
    assert later.sent == 1, "the cooldown expires; it does not silence the rule forever"


async def test_a_rule_only_fires_for_the_user_who_owns_it(session, admin_user, second_user):
    opp = await make_opportunity(session, slug="a4", title="Only mine", score=80.0)
    await make_rule(session, admin_user)
    event = (await detect_changes(session, opportunity=opp, previous={"opportunity_score": 60.0}))[0]
    await session.commit()
    await dispatch(session, events=[(event, opp)])
    await session.commit()

    rows = (await session.execute(sa.select(AlertDelivery))).scalars().all()
    assert len(rows) == 1
    assert rows[0].user_id == admin_user.id
    assert rows[0].user_id != second_user.id


async def test_a_rule_can_require_a_minimum_global_score(session, admin_user):
    opp = await make_opportunity(session, slug="a5", title="Small", score=40.0)
    await make_rule(session, admin_user, conditions={"min_global_score": 70})
    event = (await detect_changes(session, opportunity=opp, previous={"opportunity_score": 20.0}))[0]
    await session.commit()
    result = await dispatch(session, events=[(event, opp)])
    await session.commit()
    assert result.sent == 0
    assert any("below 70" in r for r in result.reasons)


async def test_a_disabled_rule_never_fires(session, admin_user):
    opp = await make_opportunity(session, slug="a6", title="Quiet", score=80.0)
    await make_rule(session, admin_user, enabled=False)
    event = (await detect_changes(session, opportunity=opp, previous={"opportunity_score": 60.0}))[0]
    await session.commit()
    assert (await dispatch(session, events=[(event, opp)])).sent == 0


async def test_the_dedupe_key_is_stable(session, admin_user):
    opp = await make_opportunity(session, slug="a7", title="Stable", score=80.0)
    rule = await make_rule(session, admin_user)
    event = (await detect_changes(session, opportunity=opp, previous={"opportunity_score": 60.0}))[0]
    await session.commit()
    assert dedupe_key(event=event, rule_id=rule.id) == dedupe_key(event=event, rule_id=rule.id)


async def test_an_alert_never_tells_anyone_to_buy(session, admin_user):
    opp = await make_opportunity(session, slug="a8", title="Careful", score=80.0)
    await make_rule(session, admin_user)
    event = (await detect_changes(session, opportunity=opp, previous={"opportunity_score": 60.0}))[0]
    await session.commit()
    await dispatch(session, events=[(event, opp)])
    await session.commit()

    body = (await session.execute(sa.select(AlertDelivery))).scalars().all()[0].body.lower()
    for banned in ("buy now", "guaranteed", "you should", "profit is likely", "risk-free"):
        assert banned not in body
    assert "not advice" in body


# --------------------------------------------------------------------- digests
async def test_an_empty_digest_says_so_rather_than_being_silent(session, admin_user):
    digest = await build_digest(session, user=admin_user, frequency="weekly")
    await session.commit()
    assert digest is not None
    assert digest.item_count == 0
    assert "quiet weeks are the normal case" in digest.sections["empty_note"]


async def test_a_digest_separates_watchlist_changes_from_the_rest(session, admin_user):
    from app.models.models import Watchlist, WatchlistItem

    watched = await make_opportunity(session, slug="d1", title="Watched", score=80.0)
    other = await make_opportunity(session, slug="d2", title="Not watched", score=80.0)
    watchlist = Watchlist(user_id=admin_user.id, name="Mine")
    session.add(watchlist)
    await session.flush()
    session.add(WatchlistItem(watchlist_id=watchlist.id, item_type="opportunity", opportunity_id=watched.id))
    for opp in (watched, other):
        await detect_changes(session, opportunity=opp, previous={"opportunity_score": 50.0})
    await session.commit()

    digest = await build_digest(session, user=admin_user, frequency="daily")
    await session.commit()
    assert [i["title"] for i in digest.sections["watchlist_changes"]] == ["Watched"]
    assert [i["title"] for i in digest.sections["other_changes"]] == ["Not watched"]
    assert "not a recommendation" in digest.sections["note"]


async def test_digests_are_private(client, admin_headers, second_headers):
    await client.post("/api/v1/me/digests?frequency=daily", headers=admin_headers)
    assert (await client.get("/api/v1/me/digests", headers=second_headers)).json() == []
    assert len((await client.get("/api/v1/me/digests", headers=admin_headers)).json()) == 1


# -------------------------------------------------------------------- channels
async def test_the_in_app_channel_always_works(session):
    provider = get_provider("in_app")
    assert provider is not None
    assert provider.configured is True
    result = await provider.send(address="anyone", message=NotificationMessage(title="t", body="b"))
    assert result.delivered is True


async def test_unconfigured_channels_say_so_instead_of_pretending(session):
    for name in ("email", "telegram"):
        provider = get_provider(name)
        assert provider is not None
        assert provider.configured is False, "no credentials are set in tests"
        result = await provider.send(address="someone", message=NotificationMessage(title="t", body="b"))
        assert result.delivered is False
        assert result.live is False
        assert "still readable in the application" in result.detail


async def test_the_system_is_not_coupled_to_telegram(session, admin_user):
    """Section 20. Telegram is one provider among several, not the mechanism.

    Proved by removing it: the alert path must be entirely unaffected by whether
    a Telegram provider exists at all.
    """
    assert set(registered_channels()) >= {"in_app", "email", "telegram"}

    import app.notifications.base as base

    removed = base._REGISTRY.pop("telegram")
    try:
        assert "telegram" not in registered_channels()
        opp = await make_opportunity(session, slug="nt", title="No telegram", score=80.0)
        await make_rule(session, admin_user, channels=["in_app", "telegram"])
        event = (await detect_changes(session, opportunity=opp, previous={"opportunity_score": 60.0}))[0]
        await session.commit()
        result = await dispatch(session, events=[(event, opp)])
        await session.commit()
        assert result.sent == 1, "the in-app alert still lands with Telegram gone"
    finally:
        base._REGISTRY["telegram"] = removed


async def test_an_unverified_channel_link_is_never_written_to(session, admin_user, second_user):
    """Section 21: never expose another user's data through the bot."""
    opp = await make_opportunity(session, slug="ch1", title="Private", score=80.0)
    session.add(
        NotificationChannelLink(
            user_id=admin_user.id,
            channel="telegram",
            external_id="chat-999",
            link_code="pending",
            verified=False,
        )
    )
    await make_rule(session, admin_user, channels=["in_app", "telegram"])
    event = (await detect_changes(session, opportunity=opp, previous={"opportunity_score": 60.0}))[0]
    await session.commit()
    await dispatch(session, events=[(event, opp)])
    await session.commit()

    rows = (await session.execute(sa.select(AlertDelivery))).scalars().all()
    assert {r.channel for r in rows} == {"in_app"}, "the unverified chat got nothing"


async def test_linking_a_chat_requires_the_one_time_code(client, admin_headers, telegram_secret):
    """A code alone proves nothing; only the bot delivering it can bind a chat.

    Rewritten for the secure flow. This test used to hand ``external_id`` to
    ``/me/channels/verify``, which is exactly the bypass that was removed: the
    caller minted the code, so presenting it back demonstrated no control of
    any Telegram chat. Binding now happens only through the authenticated
    webhook. Full coverage lives in ``tests/test_telegram_binding.py``.
    """
    started = await client.post("/api/v1/me/channels", json={"channel": "telegram"}, headers=admin_headers)
    assert started.status_code == 201
    code = started.json()["link_code"]
    assert code and started.json()["verified"] is False
    assert "Until you do, nothing is sent" in started.json()["instructions"]

    # An unknown code is still a 404 from the status endpoint.
    wrong = await client.post(
        "/api/v1/me/channels/verify",
        json={"channel": "telegram", "link_code": "not-the-code", "external_id": "chat-1"},
        headers=admin_headers,
    )
    assert wrong.status_code == 404

    # The user's own code, replayed with a chat id they do not control, no
    # longer verifies anything: the endpoint only reports status now.
    replay = await client.post(
        "/api/v1/me/channels/verify",
        json={"channel": "telegram", "link_code": code, "external_id": "chat-1"},
        headers=admin_headers,
    )
    assert replay.status_code == 200
    assert replay.json()["verified"] is False, "self-service verification is gone"

    # The bot reporting a private message carrying that code does bind it.
    delivered = await client.post(
        "/api/v1/integrations/telegram/webhook",
        json={
            "update_id": 1,
            "message": {"chat": {"id": 1234, "type": "private"}, "text": f"/link {code}"},
        },
        headers={"X-Telegram-Bot-Api-Secret-Token": telegram_secret},
    )
    assert delivered.status_code == 200
    assert delivered.json()["handled"] is True

    listed = (await client.get("/api/v1/me/channels", headers=admin_headers)).json()
    assert listed[0]["verified"] is True
    assert listed[0]["link_code"] is None, "the code is never echoed back once used"


async def test_one_chat_cannot_be_claimed_by_two_accounts(
    client, admin_headers, second_headers, telegram_secret
):
    """Otherwise a stranger could attach your chat and read your opportunities."""
    webhook = "/api/v1/integrations/telegram/webhook"
    auth = {"X-Telegram-Bot-Api-Secret-Token": telegram_secret}

    first_code = (
        await client.post("/api/v1/me/channels", json={"channel": "telegram"}, headers=admin_headers)
    ).json()["link_code"]
    await client.post(
        webhook,
        json={
            "update_id": 1,
            "message": {"chat": {"id": 42, "type": "private"}, "text": f"/link {first_code}"},
        },
        headers=auth,
    )

    second_code = (
        await client.post("/api/v1/me/channels", json={"channel": "telegram"}, headers=second_headers)
    ).json()["link_code"]
    stolen = await client.post(
        webhook,
        json={
            "update_id": 2,
            "message": {"chat": {"id": 42, "type": "private"}, "text": f"/link {second_code}"},
        },
        headers=auth,
    )
    assert stolen.status_code == 200
    assert "already linked" in stolen.json()["reply"].lower()

    # The chat still belongs to the first account, and the second is unverified.
    theirs = (await client.get("/api/v1/me/channels", headers=second_headers)).json()
    assert theirs[0]["verified"] is False, "one chat, one account"


async def test_alerts_are_private(client, admin_headers, second_headers):
    assert (await client.get("/api/v1/me/alerts", headers=admin_headers)).json() == []
    assert (await client.get("/api/v1/me/alerts", headers=second_headers)).json() == []

    created = await client.post(
        "/api/v1/me/alert-rules",
        json={"name": "Mine", "trigger": "score_threshold", "channels": ["in_app"]},
        headers=admin_headers,
    )
    assert created.status_code == 201
    rule_id = created.json()["id"]

    assert (await client.get("/api/v1/me/alert-rules", headers=second_headers)).json() == []
    stolen = await client.delete(f"/api/v1/me/alert-rules/{rule_id}", headers=second_headers)
    assert stolen.status_code == 404
    assert len((await client.get("/api/v1/me/alert-rules", headers=admin_headers)).json()) == 1


async def test_a_rule_cannot_be_attached_to_another_users_watchlist(client, admin_headers, second_headers):
    watchlist_id = (
        await client.post("/api/v1/me/watchlists", json={"name": "Theirs"}, headers=admin_headers)
    ).json()["id"]
    response = await client.post(
        "/api/v1/me/alert-rules",
        json={
            "name": "Sneaky",
            "trigger": "watchlist_change",
            "watchlist_id": watchlist_id,
            "channels": ["in_app"],
        },
        headers=second_headers,
    )
    assert response.status_code == 404


async def test_channel_status_is_honest_about_what_is_configured(client, admin_headers):
    rows = (await client.get("/api/v1/monitoring/channels", headers=admin_headers)).json()
    by_channel = {r["channel"]: r for r in rows}
    assert by_channel["in_app"]["configured"] is True
    assert by_channel["telegram"]["configured"] is False
    assert "Not configured" in by_channel["telegram"]["note"]
