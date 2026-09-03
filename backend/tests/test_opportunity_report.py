"""The opportunity report: the deterministic layer, and the narration guard rails.

No language model runs in the test suite, so these exercise the two checks that
stand between a model's draft and the page: the grounding verifier and the
forbidden-phrase scan. Both must *reject*, never repair.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from app.ai.opportunity_report import (
    FORBIDDEN_PHRASES,
    build_facts,
    build_prompt,
    build_report,
    contains_forbidden,
    verify_sections,
)
from app.core.errors import UngroundedReportError


# --------------------------------------------------------------------- doubles
@dataclass
class FakeOpportunity:
    title: str = "Edge inference tooling"
    opportunity_type: str = "business"
    geo_scope: str = "global"
    risk_level: str = "moderate"
    state: str = "promising"
    validation_status: str = "demo"
    opportunity_score: float = 56.0
    confidence: float = 69.0
    independent_source_count: int = 3
    distinct_signal_types: int = 4
    algorithm_version: str = "1.0.0"
    detected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    components: dict[str, Any] = field(
        default_factory=lambda: {
            "real_adoption": {"points": 14.0, "max": 20, "why": "Real use is growing +40%."}
        }
    )
    penalties: dict[str, float] = field(default_factory=lambda: {"easily_copied": 8})
    warnings: list[str] = field(default_factory=lambda: ["Nothing stops a competitor."])
    why_early: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=lambda: ["No price evidence."])
    next_research_steps: list[str] = field(default_factory=lambda: ["Interview 15 teams."])
    analysis: dict[str, Any] = field(default_factory=lambda: {"mvp_difficulty": "MEDIUM"})
    thesis: str | None = "Adoption is rising faster than coverage."
    counter_thesis: str | None = "Nobody has been shown to pay."
    mechanism: str | None = "The bottleneck moves to deployment plumbing."


@dataclass
class FakeRisk:
    code: str = "easily_copied"
    category: str = "competition"
    severity: str = "medium"
    confidence: float = 0.7
    rationale: str = "Nothing recorded prevents a competitor copying this."
    mitigation: str | None = None
    is_blocking: bool = False


@dataclass
class FakeCondition:
    kind: str = "confirmation"
    description: str = "Five of fifteen customers name a price."
    measurable: dict[str, Any] = field(default_factory=dict)
    state: str = "pending"
    checked_at: datetime | None = None


@dataclass
class FakeParticipation:
    kind: str = "build"
    description: str = "Build a first version."
    difficulty: str = "medium"
    capital_hint: str | None = None


@dataclass
class FakeSkeptic:
    strongest_counterargument: str = "Nobody has been shown to pay for this."
    counterarguments: list[str] = field(default_factory=lambda: ["Survivorship bias applies."])
    alternative_explanations: list[str] = field(default_factory=lambda: ["A media cycle."])
    risk_flags: list[str] = field(default_factory=list)
    too_late_reasons: list[str] = field(default_factory=list)
    inaccessible_reasons: list[str] = field(default_factory=list)
    manipulation_probability: float = 0.0


def _sections():
    return build_report(
        FakeOpportunity(),
        trend=None,
        risks=[FakeRisk()],
        skeptic=FakeSkeptic(),
        conditions=[FakeCondition(), FakeCondition(kind="invalidation", description="It falls.")],
        participation=[FakeParticipation()],
        evidence=[{"kind": "developer", "signal_type": "github_contributors"}],
    )


# ------------------------------------------------------- the deterministic layer
def test_the_report_is_complete_with_no_model():
    keys = {s.key for s in _sections()}
    for required in (
        "header",
        "what_is_happening",
        "why_it_could_matter",
        "thesis",
        "counter_thesis",
        "evidence",
        "why_early",
        "participation",
        "skeptic",
        "risks",
        "missing_evidence",
        "confirmation",
        "invalidation",
        "next_steps",
        "disclaimer",
    ):
        assert required in keys, f"missing {required}"


def test_nothing_is_marked_generated_when_no_model_ran():
    assert all(not s.generated for s in _sections())


def test_an_absent_why_early_says_so_rather_than_staying_blank():
    section = next(s for s in _sections() if s.key == "why_early")
    assert section.body and "absence is itself worth knowing" in section.body


def test_the_disclaimer_is_always_present():
    section = next(s for s in _sections() if s.key == "disclaimer")
    assert "not advice" in section.body
    assert "Candidates fail" in section.body


def test_the_header_carries_the_validation_status():
    header = next(s for s in _sections() if s.key == "header").items[0]
    assert header["validation_status"] == "demo"
    assert header["algorithm_version"] == "1.0.0"


def test_no_deterministic_section_contains_promotional_wording():
    for section in _sections():
        text = (section.body or "") + " " + str(section.items)
        assert contains_forbidden(text) is None, section.key


# ------------------------------------------------------------------- fact set
def test_the_fact_set_is_closed_and_identifiable():
    facts = build_facts(
        FakeOpportunity(),
        risks=[FakeRisk()],
        skeptic=FakeSkeptic(),
        conditions=[FakeCondition()],
    )
    ids = [f.id for f in facts]
    assert len(ids) == len(set(ids)), "fact ids must be unique"
    assert all(f.text for f in facts)
    assert any(f.id == "f_scores" for f in facts)
    assert any(f.id.startswith("f_r_") for f in facts)
    assert any(f.id.startswith("f_cond_") for f in facts)


def test_the_prompt_wraps_the_facts_as_untrusted():
    facts = build_facts(
        FakeOpportunity(),
        risks=[],
        skeptic=None,
        conditions=[],
    )
    prompt = build_prompt(FakeOpportunity(), facts)
    assert "UNTRUSTED" in prompt
    assert "f_scores" in prompt


# --------------------------------------------------------- the narration guards
def _facts():
    return build_facts(
        FakeOpportunity(),
        risks=[FakeRisk()],
        skeptic=FakeSkeptic(),
        conditions=[FakeCondition()],
    )


def test_a_well_grounded_draft_is_accepted():
    facts = _facts()
    claims = [
        {
            "text": "Real use is growing.",
            "evidence_ids": ["f_c_real_adoption"],
            "section": "what_is_happening",
        },
        {
            "text": "Three independent families support it.",
            "evidence_ids": ["f_evidence"],
            "section": "why_it_could_matter",
        },
    ]
    sections = verify_sections(claims, facts)
    assert "what_is_happening" in sections
    assert "why_it_could_matter" in sections


def test_a_fabricated_citation_is_rejected():
    facts = _facts()
    claims = [
        {
            "text": "The market is worth $4 billion.",
            "evidence_ids": ["f_invented"],
            "section": "why_it_could_matter",
        },
    ]
    with pytest.raises(UngroundedReportError):
        verify_sections(claims, facts)


def test_an_uncited_factual_claim_is_rejected():
    facts = _facts()
    claims = [
        {"text": "Revenue tripled last quarter.", "evidence_ids": [], "section": "summary"},
        {"text": "Adoption is rising.", "evidence_ids": [], "section": "summary"},
    ]
    with pytest.raises(UngroundedReportError):
        verify_sections(claims, facts)


@pytest.mark.parametrize("phrase", list(FORBIDDEN_PHRASES))
def test_every_forbidden_phrase_is_caught(phrase: str):
    assert contains_forbidden(f"This is a {phrase} opportunity.") == phrase


@pytest.mark.parametrize(
    "text",
    [
        "You should buy this immediately.",
        "Returns are guaranteed.",
        "This is the next Bitcoin.",
        "A risk-free way to profit.",
        "Invest now before it moves.",
    ],
)
def test_promotional_text_is_rejected_even_when_perfectly_cited(text: str):
    """Correct citations do not license a recommendation."""
    facts = _facts()
    claims = [{"text": text, "evidence_ids": ["f_scores"], "section": "summary"}]
    with pytest.raises(UngroundedReportError) as excinfo:
        verify_sections(claims, facts)
    assert "does not tell anyone to buy" in str(excinfo.value)


def test_a_rejected_draft_is_never_partially_kept():
    """One bad claim discards the whole draft rather than the offending sentence."""
    facts = _facts()
    claims = [
        {
            "text": "Real use is growing.",
            "evidence_ids": ["f_c_real_adoption"],
            "section": "what_is_happening",
        },
        {"text": "Profits are guaranteed.", "evidence_ids": ["f_scores"], "section": "summary"},
    ]
    with pytest.raises(UngroundedReportError):
        verify_sections(claims, facts)


def test_the_case_of_a_forbidden_phrase_does_not_matter():
    assert contains_forbidden("GUARANTEED returns") == "guaranteed"
    assert contains_forbidden("Next Bitcoin") == "next bitcoin"
