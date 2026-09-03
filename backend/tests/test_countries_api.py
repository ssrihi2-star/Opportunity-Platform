"""Country facts, coverage, and the cross-country engine, through the API.

The mandatory rule from section 30 is asserted here at the HTTP boundary, not
just in the analytics module: a country we have not measured must never be
reported as a country where nothing is happening.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from app.models.models import CountryAdoption
from app.services.countries import record_fact, refresh_all_coverage

pytestmark = pytest.mark.asyncio

NOW = datetime.now(UTC)

RICH_FACTS = {
    "internet_penetration": (92.0, "%"),
    "payment_infrastructure": (88.0, "index"),
    "logistics_performance": (4.1, "index"),
    "electricity_reliability": (97.0, "%"),
    "business_formation_days": (4.0, "days"),
    "import_restrictions": (18.0, "index"),
    "regulatory_environment": (81.0, "index"),
    "gdp_per_capita_ppp": (58_000.0, "USD"),
    "population": (84_000_000.0, "people"),
    "currency": (None, None),
}


async def seed_country(session, code: str, *, facts: dict, currency: str = "USD") -> None:
    for key, (value, unit) in facts.items():
        await record_fact(
            session,
            iso_code=code,
            key=key,
            source_name="World Bank (fixture)",
            source_url="https://example.org/fixture",
            as_of=NOW - timedelta(days=200),
            value_numeric=value,
            value_text=currency if key == "currency" else None,
            unit=unit,
            confidence=0.8,
        )
    await session.commit()


async def seed_adoption(session, *, subject: str, code: str, **over) -> None:
    row = CountryAdoption(
        subject_key=subject,
        country_code=code,
        status=over.pop("status", "measured"),
        as_of=NOW,
        **over,
    )
    session.add(row)
    await session.commit()


@pytest_asyncio.fixture
async def world(session):
    """Two well-covered markets, one thin one, one we have never touched."""
    await seed_country(session, "DE", facts=RICH_FACTS, currency="EUR")
    await seed_country(session, "US", facts=RICH_FACTS, currency="USD")
    await seed_country(
        session,
        "NG",
        facts={
            "internet_penetration": (55.0, "%"),
            "logistics_performance": (2.8, "index"),
            "gdp_per_capita_ppp": (5_400.0, "USD"),
            "population": (223_000_000.0, "people"),
        },
        currency="NGN",
    )
    await seed_country(session, "KE", facts={"population": (55_000_000.0, "people")})
    await refresh_all_coverage(session)
    await session.commit()


# ------------------------------------------------------------------- coverage
async def test_coverage_is_reported_with_its_disclaimer(client, admin_headers, world):
    response = await client.get("/api/v1/countries/coverage", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert "not a judgement of the country" in body["disclaimer"]
    codes = {row["iso_code"]: row for row in body["countries"]}
    assert set(codes) == {"DE", "US", "NG", "KE"}
    assert codes["DE"]["data_coverage"] > codes["KE"]["data_coverage"]
    assert codes["KE"]["is_low"] is True
    assert "Absence of evidence is not evidence of absence" in codes["KE"]["caveat"]


async def test_a_thin_country_names_what_is_missing(client, admin_headers, world):
    response = await client.get("/api/v1/countries/KE", headers=admin_headers)
    body = response.json()
    assert body["coverage_is_low"] is True
    assert "gdp_per_capita_ppp" in body["missing_facts"]
    assert "not a judgement of the country" in body["coverage_disclaimer"]


async def test_every_country_fact_carries_its_source_and_date(client, admin_headers, world):
    body = (await client.get("/api/v1/countries/DE", headers=admin_headers)).json()
    assert body["facts"]
    for fact in body["facts"]:
        assert fact["source_name"], "a fact with no source cannot be stored"
        assert fact["as_of"]
        assert fact["status"] == "measured"
    gdp = next(f for f in body["facts"] if f["key"] == "gdp_per_capita_ppp")
    assert gdp["unit"] == "USD", "the original unit is preserved"


async def test_an_unknown_country_explains_itself(client, admin_headers, world):
    response = await client.get("/api/v1/countries/ZZ", headers=admin_headers)
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "not that there is nothing there" in detail


# -------------------------------------------------------- NO DATA vs NO SIGNAL
async def test_an_unmeasured_market_is_named_not_scored(client, admin_headers, world, session):
    """The rule, at the HTTP boundary."""
    await seed_adoption(
        session,
        subject="solar-pumps",
        code="DE",
        level="high",
        adoption_growth=70.0,
        supplier_count=30,
        competitor_count=12,
    )
    await seed_adoption(
        session,
        subject="solar-pumps",
        code="US",
        level="high",
        adoption_growth=65.0,
        supplier_count=25,
        competitor_count=10,
    )
    await seed_adoption(session, subject="solar-pumps", code="NG", status="no_data")

    response = await client.get(
        "/api/v1/geographic/gap?subject=solar-pumps&target=NG", headers=admin_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert "NG" in body["unmeasured"]
    adoption = body["dimensions"]["adoption_gap"]
    assert adoption["measured"] is False
    assert adoption["points"] == 0.0
    assert "missing data, not low adoption" in adoption["why"]
    assert "UNKNOWN is never treated as ZERO" in body["no_data_note"]
    assert any("NO DATA" in c for c in body["caveats"])


async def test_a_checked_empty_market_is_a_finding(client, admin_headers, world, session):
    await seed_adoption(
        session, subject="solar-pumps", code="DE", level="high", adoption_growth=70.0
    )
    await seed_adoption(
        session, subject="solar-pumps", code="US", level="high", adoption_growth=65.0
    )
    await seed_adoption(
        session,
        subject="solar-pumps",
        code="NG",
        status="no_signal",
        supplier_count=0,
        competitor_count=0,
    )
    body = (
        await client.get(
            "/api/v1/geographic/gap?subject=solar-pumps&target=NG", headers=admin_headers
        )
    ).json()
    assert body["gap"] is not None and body["gap"] > 0
    assert body["dimensions"]["adoption_gap"]["measured"] is True
    assert "checked absence" in body["dimensions"]["adoption_gap"]["why"]


async def test_the_subject_coverage_endpoint_separates_the_two(
    client, admin_headers, world, session
):
    await seed_adoption(
        session, subject="s", code="DE", level="high", adoption_growth=70.0
    )
    await seed_adoption(session, subject="s", code="NG", status="no_signal")
    await seed_adoption(session, subject="s", code="KE", status="no_data")
    body = (
        await client.get("/api/v1/geographic/coverage?subject=s", headers=admin_headers)
    ).json()
    assert body["measured"] == ["DE"]
    assert body["no_signal"] == ["NG"]
    assert body["no_data"] == ["KE"]
    assert "not measured at all (KE)" in body["sentence"]


async def test_a_gap_for_a_subject_we_have_never_measured_is_a_404(client, admin_headers, world):
    response = await client.get(
        "/api/v1/geographic/gap?subject=nothing-here&target=DE", headers=admin_headers
    )
    assert response.status_code == 404
    assert "empty measurement set" in response.json()["detail"]


# --------------------------------------------------------------- localization
async def test_localization_never_proposes_an_unmeasured_market(
    client, admin_headers, world, session
):
    await seed_adoption(
        session,
        subject="pumps",
        code="DE",
        level="high",
        adoption_growth=70.0,
        supplier_count=30,
        competitor_count=14,
    )
    await seed_adoption(
        session,
        subject="pumps",
        code="US",
        level="high",
        adoption_growth=68.0,
        supplier_count=28,
        competitor_count=12,
    )
    await seed_adoption(session, subject="pumps", code="NG", status="no_data")

    rows = (
        await client.get("/api/v1/geographic/localization?subject=pumps", headers=admin_headers)
    ).json()
    assert all(row["target_market"] != "NG" for row in rows)


async def test_transferability_is_labelled_experimental(client, admin_headers, world, session):
    await seed_adoption(
        session,
        subject="pumps",
        code="DE",
        level="high",
        adoption_growth=70.0,
        supplier_count=30,
        competitor_count=14,
    )
    await seed_adoption(
        session,
        subject="pumps",
        code="US",
        level="high",
        adoption_growth=68.0,
        supplier_count=28,
        competitor_count=12,
    )
    await seed_adoption(
        session,
        subject="pumps",
        code="NG",
        status="no_signal",
        supplier_count=0,
        competitor_count=0,
    )
    rows = (
        await client.get(
            "/api/v1/geographic/localization?subject=pumps&min_similarity=0.3",
            headers=admin_headers,
        )
    ).json()
    if rows:
        row = rows[0]
        assert row["transferability_version"].endswith("-experimental")
        assert "contributing nothing to the Global" in row["transferability_note"]
        assert row["caveats"], "a gap is a question, and the caveats say so"


# ----------------------------------------------------------- no special cases
@pytest.mark.parametrize("code", ["LY", "TN", "US", "CN", "IN"])
async def test_every_country_gets_the_same_treatment(client, admin_headers, session, code):
    """Section 36: nothing is hardcoded for any particular country."""
    await seed_country(session, code, facts=RICH_FACTS)
    await refresh_all_coverage(session)
    await session.commit()
    body = (await client.get(f"/api/v1/countries/{code}", headers=admin_headers)).json()
    assert body["iso_code"] == code
    assert body["data_coverage"] > 0
    assert len(body["facts"]) == len(RICH_FACTS)


async def test_a_fact_without_a_source_cannot_be_stored(session):
    with pytest.raises(ValueError, match="needs a source"):
        await record_fact(
            session,
            iso_code="ZZ",
            key="population",
            source_name="   ",
            as_of=NOW,
            value_numeric=1.0,
        )
