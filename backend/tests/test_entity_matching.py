"""Entity resolution merges on proof and asks a human about everything else."""

import pytest

from app.models.enums import MatchDecision
from app.models.models import EntityMatchCandidate, Signal
from app.services.entities import (
    apply_decision,
    canonical_identifiers,
    normalize_name,
    resolve_entity,
    similarity,
)


# ------------------------------------------------------------------- name maths
def test_normalize_strips_suffixes_accents_and_punctuation():
    assert normalize_name("NVIDIA Corporation") == "nvidia"
    assert normalize_name("Nvidia Corp.") == "nvidia"
    assert normalize_name("Société Générale S.A.") == "societe generale"


def test_similarity_recognises_spacing_and_word_order():
    assert similarity("openai", "open ai") >= 0.9
    assert similarity("battery solid state", "solid state battery") >= 0.9


def test_similarity_does_not_confuse_containment_with_identity():
    """'Apple' and 'Apple Bank' are similar and are not the same company."""
    score = similarity("apple", "apple bank")
    assert 0.5 <= score < 0.9, "containment must be suggestive, never conclusive"


def test_similarity_of_unrelated_names_is_zero():
    assert similarity("nvidia", "tunisair") == 0.0


def test_bare_ticker_is_not_a_matching_identifier():
    """The same letters mean different companies on different exchanges."""
    assert "ticker_exchange" not in canonical_identifiers({"ticker": "BMW"})
    assert canonical_identifiers({"ticker": "BMW", "exchange": "xetra"})["ticker_exchange"] == "XETRA:BMW"


def test_cik_is_zero_padded_so_two_spellings_match():
    assert canonical_identifiers({"sec_cik": "1045810"})["sec_cik"] == "0001045810"
    assert canonical_identifiers({"sec_cik": "0001045810"})["sec_cik"] == "0001045810"


# ----------------------------------------------------------------- resolution
async def test_official_identifier_merges_even_with_a_different_name(session):
    first = await resolve_entity(
        session,
        "NVIDIA Corporation",
        "public_company",
        external_ids={"sec_cik": "1045810", "ticker": "NVDA", "exchange": "NASDAQ"},
    )
    second = await resolve_entity(
        session, "Nvidia Corp", "public_company", external_ids={"sec_cik": "0001045810"}
    )
    assert second.entity.id == first.entity.id
    assert second.matched_on == "sec_cik"
    assert second.confidence == 1.0
    assert second.candidate is None, "an identifier match needs no human"


async def test_known_alias_merges(session):
    created = await resolve_entity(
        session, "NVIDIA Corporation", "public_company", external_ids={"ticker": "NVDA"}
    )
    again = await resolve_entity(session, "NVDA", "public_company")
    assert again.entity.id == created.entity.id
    assert again.matched_on == "alias"


async def test_similar_name_without_identifier_is_never_merged(session):
    await resolve_entity(session, "Apple", "public_company", external_ids={"sec_cik": "320193"})
    second = await resolve_entity(session, "Apple Bank", "public_company")

    assert second.created is True, "a similar name must not silently merge two companies"
    assert second.candidate is not None
    assert second.candidate.decision == MatchDecision.PENDING
    assert "no shared official identifier" in second.candidate.reason


async def test_unrelated_name_raises_no_question(session):
    await resolve_entity(session, "Apple", "public_company")
    second = await resolve_entity(session, "Tunisair", "public_company")
    assert second.created is True
    assert second.candidate is None, "nothing similar means nothing to ask about"


async def test_openai_variant_is_offered_for_review(session):
    await resolve_entity(session, "OpenAI", "company")
    second = await resolve_entity(session, "Open AI", "company")
    assert second.candidate is not None
    assert second.candidate.confidence >= 0.9


# ------------------------------------------------------------------- decisions
async def test_confirming_a_match_merges_and_moves_the_signals(session, demo_source):
    from app.models.models import Signal as SignalModel

    original = await resolve_entity(session, "OpenAI", "company")
    duplicate = await resolve_entity(session, "Open AI", "company")
    session.add(
        SignalModel(
            entity_id=duplicate.entity.id,
            signal_type="github_stars",
            signal_class="developer",
            geo_scope="global",
            source_id=demo_source.id,
        )
    )
    await session.commit()

    await apply_decision(session, duplicate.candidate, MatchDecision.CONFIRMED)
    await session.commit()

    moved = (await session.execute(__import__("sqlalchemy").select(Signal))).scalars().all()
    assert all(s.entity_id == original.entity.id for s in moved), "signals follow the survivor"


async def test_a_confirmed_decision_is_obeyed_next_time(session):
    await resolve_entity(session, "OpenAI", "company")
    duplicate = await resolve_entity(session, "Open AI", "company")
    await apply_decision(session, duplicate.candidate, MatchDecision.CONFIRMED)
    await session.commit()

    third = await resolve_entity(session, "Open AI", "company")
    assert third.matched_on in ("alias", "human_confirmed")
    assert third.created is False


async def test_keep_separate_stops_the_question_being_asked_again(session):
    await resolve_entity(session, "Apple", "public_company")
    second = await resolve_entity(session, "Apple Bank", "public_company")
    await apply_decision(session, second.candidate, MatchDecision.KEEP_SEPARATE)
    await session.commit()

    third = await resolve_entity(session, "Apple Bank Holdings", "public_company")
    candidates = (
        (await session.execute(__import__("sqlalchemy").select(EntityMatchCandidate))).scalars().all()
    )
    pending = [c for c in candidates if c.decision == MatchDecision.PENDING]
    assert third.entity is not None
    assert all(c.candidate_entity_id != second.candidate.candidate_entity_id for c in pending), (
        "a keep-separate ruling must not be re-litigated against the same entity"
    )


async def test_same_name_different_type_stays_separate(session):
    company = await resolve_entity(session, "Apple", "public_company")
    product = await resolve_entity(session, "Apple", "product")
    assert company.entity.id != product.entity.id


@pytest.mark.parametrize(
    "identifiers",
    [
        {"github_repo_id": 1863329},
        {"iso_country": "tn"},
        {"contract_address": "0xABC", "chain": "Ethereum"},
        {"package_name": "Requests"},
    ],
)
async def test_every_identifier_kind_merges(session, identifiers):
    first = await resolve_entity(session, "Thing One", "technology", external_ids=identifiers)
    second = await resolve_entity(
        session, "Completely Different Name", "technology", external_ids=identifiers
    )
    assert second.entity.id == first.entity.id
