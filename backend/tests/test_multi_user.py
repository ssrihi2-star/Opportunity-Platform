"""Multi-tenancy: the wall between users, and the wall between the two scores.

Section 25 of the brief is not a feature, it is a promise: *User A must never
access User B's watchlists, notes, decisions, capital, profile, alerts.* Every
test below tries to break it from the outside, through the real HTTP surface.

Sections 32-34 are the three critical tests the brief names, and they are here
verbatim in behaviour:
  32. the same opportunity, identical global score, different relevance
  33. global 91 / relevance 12 is still discoverable
  34. moderate global / very high relevance surfaces in /for-you
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AnalysisMode
from app.models.models import Opportunity, OpportunityParticipation, Trend

pytestmark = pytest.mark.asyncio


async def make_opportunity(
    session: AsyncSession,
    *,
    slug: str,
    title: str,
    score: float,
    opportunity_type: str = "business",
    industry: str = "technology",
    country: str | None = None,
    risk: str = "moderate",
    capital: float | None = 20_000.0,
    paths: tuple[str, ...] = ("build", "provide_service", "watch"),
    analysis_mode: str = AnalysisMode.LIVE_ONLY,
) -> Opportunity:
    """A stored global opportunity, created directly.

    The full pipeline is exercised elsewhere; these tests are about what happens
    to two different people looking at the same stored row.

    `analysis_mode` defaults to live-only because the surfaces most of these
    tests drive — the personal feed, alerts, digests — read live-only rows. A
    demo-inclusive default would make every one of them assert on an empty page
    and quietly stop testing relevance at all. Tests that are specifically about
    the mode boundary pass `demo_inclusive` explicitly.
    """
    now = datetime.now(UTC)
    trend = Trend(
        name=f"{title} trend",
        subject_type="topic",
        category=industry,
        geo_scope=country or "global",
        trend_score=60.0,
        confidence=70.0,
        stage="early_adoption",
        state="active",
        first_detected_at=now,
        last_evaluated_at=now,
    )
    session.add(trend)
    await session.flush()

    opp = Opportunity(
        slug=slug,
        title=title,
        summary=f"{title} summary",
        opportunity_type=opportunity_type,
        category=industry,
        industry=industry,
        geo_scope=country or "global",
        country=country,
        state="candidate",
        validation_status="demo",
        analysis_mode=analysis_mode,
        maturity_stage="early_adoption",
        risk_level=risk,
        opportunity_score=score,
        adjusted_score=score,
        raw_score=score,
        confidence=70.0,
        peak_score=score,
        capital_required_usd=capital,
        primary_trend_id=trend.id,
        detected_at=now,
        algorithm_version="1.0.0",
    )
    session.add(opp)
    await session.flush()
    for kind in paths:
        session.add(OpportunityParticipation(opportunity_id=opp.id, kind=kind, description=f"{kind} route"))
    await session.commit()
    return opp


NIGERIAN_TRADER = {
    "home_country": "NG",
    "residence_country": "NG",
    "operating_countries": ["NG"],
    "target_countries": ["NG"],
    "interest_ranking": ["import_distribution", "business", "manufacturing"],
    "capital_currency": "USD",
    "max_capital": 20_000.0,
    "skills": ["importing", "logistics", "sales"],
    "experience_industries": ["import_distribution"],
    "assets": ["supplier_network", "distribution_network"],
    "risk_tolerance": "moderate",
    "time_commitment": "full_time",
}

INDIAN_STUDENT = {
    "home_country": "IN",
    "residence_country": "IN",
    "operating_countries": ["IN"],
    "interest_ranking": ["skills_career", "technology"],
    "capital_currency": "USD",
    "max_capital": 500.0,
    "skills": ["programming"],
    "risk_tolerance": "conservative",
    "time_commitment": "few_hours_week",
}


@pytest_asyncio.fixture
async def two_profiles(client, admin_headers, second_headers):
    await client.put("/api/v1/me/profile", json=NIGERIAN_TRADER, headers=admin_headers)
    await client.put("/api/v1/me/profile", json=INDIAN_STUDENT, headers=second_headers)
    return admin_headers, second_headers


# ------------------------------------------------------------------- profiles
async def test_a_new_user_gets_a_working_empty_profile(client, admin_headers):
    response = await client.get("/api/v1/me/profile", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["capital_currency"] == "USD"
    assert body["show_outside_profile"] is True
    assert body["completeness"] < 50
    assert body["missing"], "an empty profile should say what is missing"


async def test_capital_is_stored_in_the_users_own_currency(client, admin_headers):
    response = await client.put(
        "/api/v1/me/profile",
        json={"capital_currency": "NGN", "max_capital": 30_000_000.0},
        headers=admin_headers,
    )
    body = response.json()
    assert body["capital_currency"] == "NGN"
    assert body["max_capital"] == 30_000_000.0
    # No stored NGN->USD rate in the test database, so no conversion is invented.
    assert body["capital_in_usd"]["converted"] is None
    assert "No stored exchange rate" in body["capital_in_usd"]["note"]


async def test_one_user_cannot_read_another_users_profile(client, admin_headers, second_headers):
    """There is no endpoint that takes a user id. This asserts that stays true."""
    await client.put(
        "/api/v1/me/profile",
        json={"max_capital": 999_999.0, "capital_currency": "USD"},
        headers=admin_headers,
    )
    other = (await client.get("/api/v1/me/profile", headers=second_headers)).json()
    assert other["max_capital"] != 999_999.0


# ----------------------------------------------------------------- watchlists
async def test_watchlists_are_private(client, admin_headers, second_headers, session):
    opp = await make_opportunity(session, slug="wl-1", title="Watched", score=70)
    created = await client.post(
        "/api/v1/me/watchlists",
        json={"name": "My imports", "description": "things I might import"},
        headers=admin_headers,
    )
    assert created.status_code == 201
    watchlist_id = created.json()["id"]

    await client.post(
        f"/api/v1/me/watchlists/{watchlist_id}/items",
        json={"item_type": "opportunity", "opportunity_id": str(opp.id)},
        headers=admin_headers,
    )

    # The other user sees nothing of it, in the list or by id.
    assert (await client.get("/api/v1/me/watchlists", headers=second_headers)).json() == []
    for method, url in (
        ("get", f"/api/v1/me/watchlists/{watchlist_id}"),
        ("delete", f"/api/v1/me/watchlists/{watchlist_id}"),
    ):
        response = await getattr(client, method)(url, headers=second_headers)
        assert response.status_code in {404, 405}

    stolen = await client.put(
        f"/api/v1/me/watchlists/{watchlist_id}",
        json={"name": "mine now"},
        headers=second_headers,
    )
    assert stolen.status_code == 404, "another user's watchlist must not even be editable"

    # And the owner still has it, unchanged.
    mine = (await client.get("/api/v1/me/watchlists", headers=admin_headers)).json()
    assert mine[0]["name"] == "My imports"
    assert len(mine[0]["items"]) == 1


# ------------------------------------------- watchlist items must point at something
# opportunity_id, trend_id and entity_id are foreign keys, but the handler used to
# store whatever it was given. The two engines then disagreed: PostgreSQL raised a
# violation nothing handled and answered 500, while SQLite — which does not enforce
# foreign keys under this suite — accepted the row and returned 201 with a reference
# to nothing. These tests pin the behaviour to an explicit check, so both engines
# answer 422 and no dangling row is ever written.
async def _new_watchlist(client, headers, name: str = "Things I follow") -> str:
    created = await client.post("/api/v1/me/watchlists", json={"name": name}, headers=headers)
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def _items_on_only_watchlist(client, headers) -> list[dict]:
    lists = (await client.get("/api/v1/me/watchlists", headers=headers)).json()
    assert len(lists) == 1
    return lists[0]["items"]


@pytest.mark.parametrize(
    ("item_type", "field", "detail"),
    [
        ("opportunity", "opportunity_id", "No such opportunity."),
        ("trend", "trend_id", "No such trend."),
        ("company", "entity_id", "No such entity."),
    ],
)
async def test_an_item_referencing_a_nonexistent_row_is_refused(
    client, admin_headers, item_type, field, detail
):
    """A well-formed uuid that names nothing is a 422, and nothing is stored.

    The uuid is syntactically perfect, so this cannot be caught by the schema. It
    is only wrong about the world, which is exactly the kind of wrong that used to
    reach the database.
    """
    watchlist_id = await _new_watchlist(client, admin_headers)

    response = await client.post(
        f"/api/v1/me/watchlists/{watchlist_id}/items",
        json={"item_type": item_type, field: str(uuid.uuid4())},
        headers=admin_headers,
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == detail
    assert await _items_on_only_watchlist(client, admin_headers) == [], (
        "a refused item must leave no row behind"
    )


async def test_an_item_referencing_a_real_opportunity_is_still_accepted(
    client, admin_headers, session
):
    """The regression guard: the check must not cost the working case."""
    opp = await make_opportunity(session, slug="wl-real", title="Real and followable", score=64)
    watchlist_id = await _new_watchlist(client, admin_headers)

    response = await client.post(
        f"/api/v1/me/watchlists/{watchlist_id}/items",
        json={"item_type": "opportunity", "opportunity_id": str(opp.id), "label": "Keep an eye"},
        headers=admin_headers,
    )

    assert response.status_code == 201, response.text
    assert response.json()["opportunity_id"] == str(opp.id)
    assert response.json()["label"] == "Keep an eye"

    items = await _items_on_only_watchlist(client, admin_headers)
    assert [i["opportunity_id"] for i in items] == [str(opp.id)]


async def test_a_bad_id_on_another_users_watchlist_is_still_a_404(
    client, admin_headers, second_headers
):
    """Ownership is checked before validity, and the error must not leak existence.

    If an invalid id answered 422 here while an unknown watchlist answered 404, the
    pair of responses would confirm that somebody else's watchlist is real. Both
    have to be 404, and the body must say nothing more than that.
    """
    watchlist_id = await _new_watchlist(client, admin_headers, name="Not yours")

    response = await client.post(
        f"/api/v1/me/watchlists/{watchlist_id}/items",
        json={"item_type": "opportunity", "opportunity_id": str(uuid.uuid4())},
        headers=second_headers,
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "No such watchlist."

    # Indistinguishable from a watchlist id that never existed at all.
    invented = await client.post(
        f"/api/v1/me/watchlists/{uuid.uuid4()}/items",
        json={"item_type": "opportunity", "opportunity_id": str(uuid.uuid4())},
        headers=second_headers,
    )
    assert invented.status_code == response.status_code
    assert invented.json() == response.json()

    # And the owner's list is untouched by the attempt.
    assert await _items_on_only_watchlist(client, admin_headers) == []


async def test_a_watchlist_can_follow_a_country_or_an_industry(client, admin_headers):
    """A country is as followable as a company. Section 15."""
    watchlist_id = (
        await client.post("/api/v1/me/watchlists", json={"name": "Places"}, headers=admin_headers)
    ).json()["id"]
    for body in (
        {"item_type": "country", "country_code": "ke", "label": "Kenya"},
        {"item_type": "industry", "industry": "logistics"},
        {"item_type": "keyword", "keyword": "battery recycling"},
    ):
        response = await client.post(
            f"/api/v1/me/watchlists/{watchlist_id}/items", json=body, headers=admin_headers
        )
        assert response.status_code == 201, response.text
    items = (await client.get("/api/v1/me/watchlists", headers=admin_headers)).json()[0]["items"]
    assert {i["item_type"] for i in items} == {"country", "industry", "keyword"}
    assert [i for i in items if i["item_type"] == "country"][0]["country_code"] == "KE"


async def test_removing_an_item_from_another_users_watchlist_is_impossible(
    client, admin_headers, second_headers
):
    watchlist_id = (
        await client.post("/api/v1/me/watchlists", json={"name": "Mine"}, headers=admin_headers)
    ).json()["id"]
    item_id = (
        await client.post(
            f"/api/v1/me/watchlists/{watchlist_id}/items",
            json={"item_type": "keyword", "keyword": "solar"},
            headers=admin_headers,
        )
    ).json()["id"]

    response = await client.delete(
        f"/api/v1/me/watchlists/{watchlist_id}/items/{item_id}", headers=second_headers
    )
    assert response.status_code == 404


# ---------------------------------------------------- the two scores, per user
async def test_identical_global_score_different_relevance(client, two_profiles, session):
    """Section 32. One opportunity, one global score, two different people."""
    admin_headers, second_headers = two_profiles
    await make_opportunity(
        session,
        slug="import-robotics",
        title="Industrial robotics distribution",
        score=78.0,
        opportunity_type="import_distribution",
        industry="import_distribution",
        country="NG",
        capital=15_000.0,
        paths=("import", "distribute", "watch"),
    )

    trader = (await client.get("/api/v1/discover", headers=admin_headers)).json()["items"][0]
    student = (await client.get("/api/v1/discover", headers=second_headers)).json()["items"][0]

    assert trader["opportunity_score"] == student["opportunity_score"] == 78.0
    assert trader["user_relevance"] != student["user_relevance"]
    assert trader["user_relevance"] > student["user_relevance"]


async def test_a_high_global_low_relevance_candidate_stays_discoverable(client, two_profiles, session):
    """Section 33. Global 91, personal 12, and it is still on the list."""
    admin_headers, second_headers = two_profiles
    await make_opportunity(
        session,
        slug="battery-plant",
        title="Battery recycling plant",
        score=91.0,
        opportunity_type="manufacturing",
        industry="manufacturing",
        country="DE",
        capital=4_000_000.0,
        paths=("manufacture",),
    )

    feed = (await client.get("/api/v1/discover", headers=second_headers)).json()["items"]
    row = next(r for r in feed if r["slug"] == "battery-plant")
    assert row["opportunity_score"] == 91.0
    assert row["user_relevance"] < 40
    assert row["outside_profile"] is True, "it is outside their filters, and says so"

    # And it is still returned in the personal feed, flagged rather than hidden.
    personal = (await client.get("/api/v1/for-you", headers=second_headers)).json()["items"]
    assert any(r["slug"] == "battery-plant" for r in personal)


async def test_a_moderate_global_but_very_relevant_candidate_surfaces_for_you(client, two_profiles, session):
    """Section 34. It is not the best opportunity in the world; it is theirs."""
    admin_headers, _ = two_profiles
    await make_opportunity(
        session,
        slug="global-star",
        title="A superb global candidate that this person cannot touch",
        score=88.0,
        opportunity_type="manufacturing",
        industry="manufacturing",
        country="JP",
        capital=3_000_000.0,
        paths=("manufacture",),
    )
    await make_opportunity(
        session,
        slug="local-fit",
        title="A moderate candidate that fits this person exactly",
        score=58.0,
        opportunity_type="import_distribution",
        industry="import_distribution",
        country="NG",
        capital=12_000.0,
        paths=("import", "distribute"),
    )

    discover = (await client.get("/api/v1/discover", headers=admin_headers)).json()["items"]
    assert discover[0]["slug"] == "global-star", "the global feed is ordered globally"

    for_you = (await client.get("/api/v1/for-you", headers=admin_headers)).json()["items"]
    assert for_you[0]["slug"] == "local-fit", "the personal feed is ordered personally"
    # Both numbers travel together; neither has been folded into the other.
    assert for_you[0]["opportunity_score"] == 58.0
    assert for_you[0]["user_relevance"] > for_you[1]["user_relevance"]


async def test_discover_is_the_same_list_for_everybody(client, two_profiles, session):
    admin_headers, second_headers = two_profiles
    for index, score in enumerate((80.0, 65.0, 50.0)):
        await make_opportunity(session, slug=f"g-{index}", title=f"Candidate {index}", score=score)
    first = (await client.get("/api/v1/discover", headers=admin_headers)).json()["items"]
    second = (await client.get("/api/v1/discover", headers=second_headers)).json()["items"]
    assert [r["slug"] for r in first] == [r["slug"] for r in second]
    assert [r["opportunity_score"] for r in first] == [r["opportunity_score"] for r in second]


async def test_changing_the_profile_changes_relevance_immediately(client, admin_headers, session):
    """Nothing about any person is compiled in: change the row, change the answer."""
    await make_opportunity(
        session,
        slug="ke-import",
        title="Something to import into Kenya",
        score=70.0,
        opportunity_type="import_distribution",
        industry="import_distribution",
        country="KE",
        capital=10_000.0,
        paths=("import", "distribute", "watch"),
    )
    await client.put(
        "/api/v1/me/profile",
        json={"operating_countries": ["BR"], "max_capital": 50_000.0, "skills": ["importing"]},
        headers=admin_headers,
    )
    far = (await client.get("/api/v1/discover", headers=admin_headers)).json()["items"][0]

    await client.put(
        "/api/v1/me/profile",
        json={"operating_countries": ["KE"], "target_countries": ["KE"]},
        headers=admin_headers,
    )
    near = (await client.get("/api/v1/discover", headers=admin_headers)).json()["items"][0]

    assert near["user_relevance"] > far["user_relevance"]
    assert near["opportunity_score"] == far["opportunity_score"], "the global score did not move"


async def test_relevance_is_explained_factor_by_factor(client, two_profiles, session):
    admin_headers, _ = two_profiles
    opp = await make_opportunity(session, slug="explain-me", title="Explain me", score=70)
    detail = (await client.get(f"/api/v1/opportunities/{opp.id}", headers=admin_headers)).json()
    parts = detail["user_relevance_parts"]
    assert len(parts) >= 10
    for name, part in parts.items():
        if name.startswith("_"):
            continue
        assert part["why"], f"{name} must explain itself"
        assert 0 <= part["points"] <= part["max"]
    assert sum(p["max"] for k, p in parts.items() if not k.startswith("_")) == 100


async def test_each_participation_route_is_scored_separately(client, two_profiles, session):
    admin_headers, _ = two_profiles
    opp = await make_opportunity(
        session,
        slug="many-ways",
        title="Several ways in",
        score=70,
        paths=("build", "import", "learn_skill", "watch"),
    )
    detail = (await client.get(f"/api/v1/opportunities/{opp.id}", headers=admin_headers)).json()
    assert set(detail["path_relevance"]) == {"build", "import", "learn_skill", "watch"}
    assert len(set(detail["path_relevance"].values())) > 1
    assert detail["best_path"] in detail["path_relevance"]
    assert detail["user_relevance"] == max(detail["path_relevance"].values())


# -------------------------------------------------------------------- feedback
async def test_feedback_is_private_and_never_changes_the_global_score(
    client, admin_headers, second_headers, session
):
    """Section 27, made checkable."""
    opp = await make_opportunity(session, slug="fb", title="Feedback target", score=72.0)

    response = await client.post(
        f"/api/v1/me/opportunities/{opp.id}/feedback",
        json={"feedback": "not_relevant", "note": "wrong country for me"},
        headers=admin_headers,
    )
    assert response.status_code == 201
    assert "never changes the global opportunity score" in response.json()["note_on_use"]

    # The other user's view of the global score is untouched.
    other = (await client.get("/api/v1/discover", headers=second_headers)).json()["items"][0]
    assert other["opportunity_score"] == 72.0
    # And they cannot see the feedback.
    assert (await client.get("/api/v1/me/feedback", headers=second_headers)).json() == []
    assert len((await client.get("/api/v1/me/feedback", headers=admin_headers)).json()) == 1


async def test_feedback_on_an_unknown_opportunity_is_a_404(client, admin_headers):
    response = await client.post(
        f"/api/v1/me/opportunities/{uuid.uuid4()}/feedback",
        json={"feedback": "interesting"},
        headers=admin_headers,
    )
    assert response.status_code == 404


# ------------------------------------------------------------ outside profile
async def test_outside_the_profile_is_flagged_but_still_returned(client, admin_headers, session):
    await make_opportunity(
        session, slug="risky", title="A speculative candidate", score=40.0, risk="very_high"
    )
    await client.put(
        "/api/v1/me/profile",
        json={"min_global_score": 80.0, "max_risk_level": "low"},
        headers=admin_headers,
    )
    items = (await client.get("/api/v1/for-you", headers=admin_headers)).json()["items"]
    row = next(r for r in items if r["slug"] == "risky")
    assert row["outside_profile"] is True
    assert row["user_relevance"] is not None


async def test_a_user_can_narrow_their_feed_but_it_is_their_choice(client, admin_headers, session):
    await make_opportunity(session, slug="ok", title="Fine", score=85.0)
    await make_opportunity(session, slug="risky2", title="Risky", score=30.0, risk="very_high")
    await client.put(
        "/api/v1/me/profile",
        json={"min_global_score": 80.0, "show_outside_profile": False},
        headers=admin_headers,
    )
    narrowed = (
        await client.get("/api/v1/for-you?include_outside_profile=false", headers=admin_headers)
    ).json()["items"]
    assert {r["slug"] for r in narrowed} == {"ok"}

    # The default keeps everything, flagged.
    wide = (await client.get("/api/v1/for-you", headers=admin_headers)).json()["items"]
    assert {r["slug"] for r in wide} == {"ok", "risky2"}


# -------------------------------------------------------------- no hardcoding
@pytest.mark.parametrize(
    ("country", "currency"),
    [("LY", "LYD"), ("TN", "TND"), ("US", "USD"), ("BR", "BRL"), ("CN", "CNY")],
)
async def test_no_country_or_currency_is_privileged(client, admin_headers, session, country, currency):
    """Section 36: the architecture must not depend on any particular country."""
    await make_opportunity(
        session,
        slug=f"x-{country}",
        title=f"Something in {country}",
        score=70.0,
        country=country,
    )
    response = await client.put(
        "/api/v1/me/profile",
        json={
            "residence_country": country,
            "operating_countries": [country],
            "capital_currency": currency,
            "max_capital": 25_000.0,
        },
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["capital_currency"] == currency
    feed = (await client.get("/api/v1/for-you", headers=admin_headers)).json()["items"]
    assert feed and feed[0]["user_relevance"] is not None
