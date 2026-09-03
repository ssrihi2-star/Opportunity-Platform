import pytest

from app.ai.grounding import is_factual, verify_report
from app.core.errors import UngroundedReportError

ALLOWED = {"e1", "e2"}


def test_report_with_valid_citations_passes():
    claims = [
        {"text": "Contributor count rose 42% in 30 days.", "evidence_ids": ["e1"]},
        {"text": "Two suppliers are listed in the trade registry.", "evidence_ids": ["e2"]},
    ]
    assert verify_report(claims, ALLOWED).citation_density == 1.0


def test_fabricated_evidence_id_is_rejected():
    with pytest.raises(UngroundedReportError) as exc:
        verify_report([{"text": "Revenue grew 80%.", "evidence_ids": ["e99"]}], ALLOWED)
    assert "e99" in str(exc.value)
    assert "discarded" in str(exc.value)


def test_uncited_factual_claim_fails_density():
    claims = [
        {"text": "Adoption grew 300% in Tunisia.", "evidence_ids": []},
        {"text": "Contributors rose 12%.", "evidence_ids": ["e1"]},
    ]
    with pytest.raises(UngroundedReportError):
        verify_report(claims, ALLOWED)


def test_hedged_statements_do_not_need_evidence():
    claims = [
        {"text": "Market size: Unknown.", "evidence_ids": []},
        {"text": "Landed cost is an Estimate pending supplier quotes.", "evidence_ids": []},
        {"text": "Contributors rose 12%.", "evidence_ids": ["e1"]},
    ]
    assert verify_report(claims, ALLOWED).citation_density == 1.0


def test_explicit_unverified_claim_type_is_allowed():
    claims = [
        {"text": "Roughly 5,000 units may ship in 2027.", "evidence_ids": [], "claim_type": "unverified"},
        {"text": "Contributors rose 12%.", "evidence_ids": ["e1"]},
    ]
    assert verify_report(claims, ALLOWED).citation_density == 1.0


def test_is_factual_detects_numbers_and_proper_nouns():
    assert is_factual("Revenue rose 12%.")
    assert is_factual("Tunisia introduced a new tariff.")
    assert not is_factual("this is unknown")


async def test_echo_provider_invents_nothing():
    from app.ai.provider import get_provider

    response = await get_provider("echo").complete(system="s", user="u", model="echo")
    payload = response.json()
    assert payload["claims"] == []
    assert payload["status"] == "insufficient_evidence"
    assert response.cost_usd == 0.0


async def test_unconfigured_provider_fails_loudly():
    from app.ai.provider import get_provider

    with pytest.raises(RuntimeError) as exc:
        await get_provider("openai").complete(system="s", user="u", model="x")
    assert "OPENAI_API_KEY" in str(exc.value)


async def test_budget_guard_blocks_when_cap_is_reached(session, monkeypatch):
    from app.ai.budget import assert_within_budget
    from app.core import config
    from app.core.errors import BudgetExceededError
    from app.models.models import ModelRun

    session.add(ModelRun(provider="x", model="m", purpose="report", cost_usd=5.0))
    await session.commit()
    monkeypatch.setattr(config.settings, "AI_DAILY_BUDGET_USD", 1.0)
    with pytest.raises(BudgetExceededError) as exc:
        await assert_within_budget(session)
    assert "Daily AI budget" in str(exc.value)
