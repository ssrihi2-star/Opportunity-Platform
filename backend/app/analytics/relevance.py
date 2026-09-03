"""The User Relevance Engine.

The single most important rule in Phase 5:

    GLOBAL OPPORTUNITY SCORE   "How attractive does this look on the evidence?"
                               Identical for every user, forever.

    USER RELEVANCE SCORE       "How realistic and useful is this for THIS person?"
                               Different for every user, by design.

Nothing in this module can reach a global number, and nothing here is ever
blended into one figure. An opportunity scoring 91 globally and 12 for you is a
useful, honest statement — *this is a good opportunity and a poor fit for me* —
and collapsing it into a single number would destroy exactly that information.

There is no country, profession, currency or capital level written into this
code. Everything comes from a stored profile: change the row, and the same
opportunity scores differently. A test asserts precisely that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

RELEVANCE_VERSION: Final[str] = "1.0.0"

#: Section 4 of the Phase 5 brief, verbatim. Configurable and versioned: any
#: change here requires bumping RELEVANCE_VERSION, because stored relevance rows
#: record the version they were computed under.
FACTOR_MAX: Final[dict[str, int]] = {
    "geographic_accessibility": 15,
    "capital_fit": 15,
    "industry_experience": 15,
    "skills_fit": 10,
    "supplier_advantage": 10,
    "distribution_advantage": 10,
    "regulatory_accessibility": 10,
    "time_commitment_fit": 5,
    "risk_tolerance_fit": 5,
    "type_preference": 5,
}

#: What each participation mode actually demands of a person. Used to score each
#: path independently, because "solar is relevant to you" is a far less useful
#: statement than "installing is a 84 for you and building software is a 22".
PATH_REQUIREMENTS: Final[dict[str, dict[str, Any]]] = {
    "build": {
        "skills": ["programming", "engineering", "product"],
        "capital": "medium",
        "time": "full_time",
        "assets": ["technical_team"],
    },
    "import": {
        "skills": ["importing", "logistics", "sales"],
        "capital": "high",
        "time": "part_time",
        "assets": ["supplier_network", "warehouse", "capital"],
    },
    "distribute": {
        "skills": ["sales", "logistics"],
        "capital": "high",
        "time": "part_time",
        "assets": ["distribution_network", "retail_locations"],
    },
    "manufacture": {
        "skills": ["manufacturing", "engineering"],
        "capital": "very_high",
        "time": "full_time",
        "assets": ["manufacturing", "warehouse"],
    },
    "investigate_public_company": {
        "skills": ["finance"],
        "capital": "any",
        "time": "passive",
        "assets": ["capital"],
    },
    "provide_service": {
        "skills": ["sales", "consulting"],
        "capital": "low",
        "time": "part_time",
        "assets": [],
    },
    "consult": {
        "skills": ["consulting"],
        "capital": "none",
        "time": "part_time",
        "assets": ["qualifications"],
    },
    "learn_skill": {"skills": [], "capital": "none", "time": "few_hours_week", "assets": []},
    "license": {
        "skills": ["finance", "legal"],
        "capital": "medium",
        "time": "passive",
        "assets": ["licenses"],
    },
    "franchise": {
        "skills": ["sales"],
        "capital": "very_high",
        "time": "full_time",
        "assets": ["capital", "retail_locations"],
    },
    "partner": {
        "skills": ["sales"],
        "capital": "low",
        "time": "few_hours_week",
        "assets": ["audience", "distribution_network"],
    },
    "create_content": {
        "skills": ["marketing"],
        "capital": "none",
        "time": "few_hours_week",
        "assets": ["audience"],
    },
    "watch": {"skills": [], "capital": "none", "time": "passive", "assets": []},
}

#: Watching is not participating. It asks nothing of anyone, so on the raw
#: factors it scores well for everybody — which would make "you can only observe
#: this from a distance" look like a strong personal fit. It is damped instead.
PATH_DAMPENING: Final[dict[str, float]] = {"watch": 0.55, "create_content": 0.85}

#: Rough capital bands each path implies, as a multiple of the opportunity's own
#: stated requirement. Used only when the opportunity states no figure.
CAPITAL_BAND_USD: Final[dict[str, float | None]] = {
    "none": 0.0,
    "low": 1_000.0,
    "medium": 25_000.0,
    "high": 100_000.0,
    "very_high": 500_000.0,
    "any": None,
}

TIME_RANK: Final[dict[str, int]] = {
    "passive": 0,
    "few_hours_week": 1,
    "part_time": 2,
    "full_time": 3,
}

RISK_RANK: Final[dict[str, int]] = {"low": 0, "moderate": 1, "high": 2, "very_high": 3}

#: How much risk each tolerance is comfortable looking at. This filters and
#: scores what is *shown*; it never softens a risk label. A speculative user
#: still reads VERY HIGH RISK on a very high risk.
TOLERANCE_CEILING: Final[dict[str, int]] = {
    "conservative": 0,
    "moderate": 1,
    "aggressive": 2,
    "speculative": 3,
}


@dataclass(slots=True)
class UserContext:
    """A user's profile, flattened. Built from a stored row, never from code."""

    home_country: str | None = None
    residence_country: str | None = None
    operating_countries: list[str] = field(default_factory=list)
    familiar_countries: list[str] = field(default_factory=list)
    target_countries: list[str] = field(default_factory=list)
    excluded_countries: list[str] = field(default_factory=list)

    interest_ranking: list[str] = field(default_factory=list)
    disabled_categories: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    excluded_industries: list[str] = field(default_factory=list)

    capital_currency: str = "USD"
    max_capital: float | None = None
    #: The user's capital expressed in USD, converted with a *stored* rate. None
    #: when no rate exists — in which case capital fit is scored as unknown
    #: rather than guessed.
    max_capital_usd: float | None = None
    capital_flexibility: str = "somewhat_flexible"

    skills: list[str] = field(default_factory=list)
    experience_industries: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)

    risk_tolerance: str = "moderate"
    time_commitment: str = "part_time"
    time_horizon: str = "1_3_years"

    min_global_score: float = 0.0
    min_confidence: float = 0.0
    max_risk_level: str = "very_high"
    max_capital_required: float | None = None
    show_outside_profile: bool = True


@dataclass(slots=True)
class OpportunityContext:
    """The global facts about an opportunity that relevance may look at.

    Read-only by construction: this is a copy, and nothing here writes back.
    """

    opportunity_id: str
    opportunity_type: str
    category: str | None = None
    industry: str | None = None
    geo_scope: str = "global"
    country: str | None = None
    #: Where the evidence is strongest, e.g. {"CN": 0.9, "DE": 0.6}.
    evidence_countries: dict[str, float] = field(default_factory=dict)
    global_score: float = 0.0
    confidence: float = 0.0
    risk_level: str = "moderate"
    capital_required_usd: float | None = None
    technical_difficulty: str | None = None
    participation_paths: list[str] = field(default_factory=list)
    #: 0-100 coverage for the country this opportunity concerns, when known.
    country_data_coverage: float | None = None


@dataclass(slots=True)
class RelevanceResult:
    version: str
    relevance: float
    parts: dict[str, Any]
    path_relevance: dict[str, float]
    best_path: str | None
    outside_profile: bool
    notes: list[str] = field(default_factory=list)


def _norm(values: list[str]) -> set[str]:
    return {v.strip().lower() for v in values if v and v.strip()}


def _country_set(values: list[str]) -> set[str]:
    return {v.strip().upper() for v in values if v and v.strip()}


# --------------------------------------------------------------------- factors
def _geographic(user: UserContext, opp: OpportunityContext) -> tuple[float, str]:
    """Can this person reach the place where the opportunity is?

    Five different questions live behind one word here — where they live, where
    they may operate, where they understand, where they want to look, and where
    they refuse to. They are asked separately because they genuinely differ: a
    Chinese manufacturer may operate globally, understand China, and want
    African distribution partners.
    """
    maximum = FACTOR_MAX["geographic_accessibility"]
    target = (opp.country or opp.geo_scope or "").upper()

    excluded = _country_set(user.excluded_countries)
    if target and target in excluded:
        return 0.0, f"{target} is on the excluded list."

    if target in {"GLOBAL", ""}:
        # A global opportunity is reachable from anywhere, but not equally: it
        # still has to be executed somewhere.
        return round(maximum * 0.7, 2), ("Global in scope, so not tied to any one market.")

    operating = _country_set(user.operating_countries)
    familiar = _country_set(user.familiar_countries)
    wanted = _country_set(user.target_countries)
    home = (user.home_country or "").upper()
    residence = (user.residence_country or "").upper()

    score = 0.0
    reasons: list[str] = []
    if target in wanted:
        rank = [c.upper() for c in user.target_countries].index(target)
        score = max(score, 1.0 - min(0.3, rank * 0.1))
        reasons.append(f"a market you asked for (#{rank + 1})")
    if target in operating:
        score = max(score, 0.9)
        reasons.append("you can operate there")
    if target in {home, residence} and target:
        score = max(score, 0.85)
        reasons.append("your home market")
    if target in familiar:
        score = max(score, 0.6)
        reasons.append("you know that market")

    if score == 0.0:
        # Not reachable, but the evidence may still be somewhere they can act.
        overlap = _country_set(list(opp.evidence_countries)) & (operating | wanted)
        if overlap:
            return round(maximum * 0.35, 2), (
                f"{target} is outside your markets, but the evidence also covers "
                f"{', '.join(sorted(overlap))}, where you can act."
            )
        return 0.0, f"{target} is outside the countries you listed."

    return round(maximum * score, 2), f"{target} is " + " and ".join(reasons) + "."


def _capital(user: UserContext, opp: OpportunityContext) -> tuple[float, str]:
    """Does the money work? An unknown requirement is a half-mark, not a pass."""
    maximum = FACTOR_MAX["capital_fit"]
    required = opp.capital_required_usd
    ceiling = user.max_capital_usd

    if required is None:
        return round(maximum * 0.4, 2), (
            "The capital requirement is unknown, so this is a neutral partial mark rather than a guess."
        )
    if ceiling is None:
        return round(maximum * 0.4, 2), (
            f"About {required:,.0f} USD is needed. Your ceiling is set in "
            f"{user.capital_currency} with no stored exchange rate, so no comparison "
            "is made rather than an invented one."
        )
    if required <= ceiling:
        headroom = 1 - (required / ceiling) if ceiling else 0.0
        return round(maximum * (0.6 + 0.4 * headroom), 2), (
            f"About {required:,.0f} USD against your ceiling of {ceiling:,.0f} USD."
        )

    # Over budget — but flexibility is a real thing people have.
    overshoot = required / ceiling
    flexibility = {"fixed": 0.0, "somewhat_flexible": 0.15, "flexible": 0.3}.get(
        user.capital_flexibility, 0.15
    )
    if overshoot <= 1.5 and flexibility:
        return round(maximum * flexibility, 2), (
            f"About {required:,.0f} USD is {overshoot:.1f}x your stated ceiling — "
            "reachable only because you marked your capital as flexible."
        )
    return 0.0, (f"About {required:,.0f} USD is {overshoot:.1f}x your ceiling of {ceiling:,.0f} USD.")


def _industry(user: UserContext, opp: OpportunityContext) -> tuple[float, str]:
    maximum = FACTOR_MAX["industry_experience"]
    industry = (opp.industry or opp.category or "").lower()
    if not industry:
        return round(maximum * 0.3, 2), "The industry is not recorded for this candidate."

    excluded = _norm(user.excluded_industries)
    if industry in excluded:
        return 0.0, f"{industry} is on your excluded list."

    experience = _norm(user.experience_industries)
    interests = _norm(user.industries)
    if industry in experience:
        return float(maximum), f"You have stated experience in {industry}."
    # An industry the user ranked as an interest category counts too — someone
    # who ranked "saas" first has told us something about software.
    ranked = {c.strip().lower() for c in user.interest_ranking}
    if INDUSTRY_ALIASES.get(industry, set()) & ranked:
        overlap = ", ".join(sorted(INDUSTRY_ALIASES[industry] & ranked))
        return round(maximum * 0.7, 2), (f"{industry} sits in {overlap}, which you ranked as an interest.")
    if industry in interests:
        return round(maximum * 0.6, 2), f"{industry} is an industry you follow, without experience."
    # Partial credit for a related word, which is better than a binary miss.
    words = set(industry.replace("_", " ").split())
    for known in experience:
        if words & set(known.replace("_", " ").split()):
            return round(maximum * 0.5, 2), f"Related to your experience in {known}."
    return 0.0, f"No stated experience or interest in {industry}."


def _skills(user: UserContext, opp: OpportunityContext, path: str | None = None) -> tuple[float, str]:
    """Skills are user-defined free text; there is no fixed vocabulary of skill."""
    maximum = FACTOR_MAX["skills_fit"]
    have = _norm(user.skills)
    if not have:
        return round(maximum * 0.3, 2), "No skills recorded on your profile."

    wanted: set[str] = set()
    for mode in ([path] if path else opp.participation_paths) or []:
        wanted |= _norm(PATH_REQUIREMENTS.get(mode, {}).get("skills", []))
    if not wanted:
        return round(maximum * 0.5, 2), "No particular skill is required by the available paths."

    overlap = have & wanted
    if overlap:
        fraction = min(1.0, len(overlap) / max(1, min(len(wanted), 2)))
        return round(maximum * fraction, 2), (f"You have {', '.join(sorted(overlap))}, which this needs.")
    return 0.0, f"This needs {', '.join(sorted(wanted))}; none is on your profile."


def _regulatory(user: UserContext, opp: OpportunityContext) -> tuple[float, str]:
    """Where may this person legally register, trade and operate?"""
    maximum = FACTOR_MAX["regulatory_accessibility"]
    target = (opp.country or opp.geo_scope or "").upper()
    operating = _country_set(user.operating_countries)

    if target in {"GLOBAL", ""}:
        return round(maximum * 0.7, 2), "Global in scope, so no single regulator governs it."
    if target in _country_set(user.excluded_countries):
        return 0.0, f"You excluded {target}."
    if not operating:
        return round(maximum * 0.4, 2), (
            "You have not listed where you can legally operate, so this is a partial mark."
        )
    if target in operating:
        return float(maximum), f"You listed {target} as somewhere you can operate."
    return 0.0, f"{target} is not among the countries you can operate in."


def _time(user: UserContext, opp: OpportunityContext, path: str | None = None) -> tuple[float, str]:
    maximum = FACTOR_MAX["time_commitment_fit"]
    available = TIME_RANK.get(user.time_commitment, 2)
    modes = [path] if path else (opp.participation_paths or ["watch"])
    needed = min(
        (TIME_RANK.get(PATH_REQUIREMENTS.get(m, {}).get("time", "part_time"), 2) for m in modes),
        default=2,
    )
    if available >= needed:
        return float(maximum), (
            f"Your {user.time_commitment.replace('_', ' ')} availability covers the least demanding way in."
        )
    return round(maximum * 0.3, 2), (
        f"The lightest way in still needs more than {user.time_commitment.replace('_', ' ')}."
    )


def _risk_fit(user: UserContext, opp: OpportunityContext) -> tuple[float, str]:
    """How comfortable is this person with this level of risk?

    Note carefully what this does NOT do: it does not change the risk label. A
    speculative user scores well here on a very high risk *and still sees it
    marked VERY HIGH RISK*.
    """
    maximum = FACTOR_MAX["risk_tolerance_fit"]
    ceiling = TOLERANCE_CEILING.get(user.risk_tolerance, 1)
    actual = RISK_RANK.get(opp.risk_level, 2)
    if actual <= ceiling:
        return float(maximum), (
            f"{opp.risk_level.replace('_', ' ')} risk is within your stated "
            f"{user.risk_tolerance} tolerance. The risk label is unchanged."
        )
    gap = actual - ceiling
    return round(max(0.0, maximum * (1 - gap * 0.5)), 2), (
        f"{opp.risk_level.replace('_', ' ')} risk is above your {user.risk_tolerance} "
        "tolerance. It is still shown, still labelled, and still risky."
    )


#: Which of the thirteen user-facing categories each coarse opportunity type and
#: industry can satisfy. Users rank thirteen categories; the engine stores four
#: types, so matching only on the type would throw away most of what they said.
CATEGORY_ALIASES: Final[dict[str, set[str]]] = {
    "business": {"business", "services", "ecommerce", "saas", "franchising", "partnerships"},
    "import_distribution": {"import_distribution", "ecommerce", "manufacturing", "partnerships"},
    "public_investment": {"public_investment"},
    "crypto": {"crypto"},
}
INDUSTRY_ALIASES: Final[dict[str, set[str]]] = {
    "technology": {"technology", "saas", "skills_career"},
    "software": {"technology", "saas", "skills_career"},
    "saas": {"saas", "technology"},
    "manufacturing": {"manufacturing"},
    "import_distribution": {"import_distribution"},
    "energy": {"business", "services"},
    "agriculture": {"business", "services"},
    "construction": {"business", "manufacturing"},
}


def _type_preference(user: UserContext, opp: OpportunityContext) -> tuple[float, str]:
    """Match against everything the user ranked, not only the coarse type."""
    maximum = FACTOR_MAX["type_preference"]
    kind = opp.opportunity_type
    disabled = _norm(user.disabled_categories)
    if kind in disabled:
        return 0.0, f"You switched {kind.replace('_', ' ')} off."

    satisfies = set(CATEGORY_ALIASES.get(kind, {kind}))
    for source in ((opp.industry or "").lower(), (opp.category or "").lower()):
        satisfies |= INDUSTRY_ALIASES.get(source, {source} if source else set())
    if satisfies & disabled:
        blocked = ", ".join(sorted(satisfies & disabled))
        return 0.0, f"You switched {blocked} off."

    ranking = [c.strip().lower() for c in user.interest_ranking]
    best_rank: int | None = None
    matched = ""
    for index, wanted in enumerate(ranking):
        if wanted in satisfies and (best_rank is None or index < best_rank):
            best_rank, matched = index, wanted
    if best_rank is not None:
        return round(maximum * max(0.3, 1.0 - best_rank * 0.15), 2), (
            f"{matched.replace('_', ' ')} is priority #{best_rank + 1} for you."
        )
    return round(maximum * 0.2, 2), (
        f"{kind.replace('_', ' ')} is not in your stated priorities, but is not disabled either."
    )


# ------------------------------------------------------------------- the score
def _score_for_path(user: UserContext, opp: OpportunityContext, path: str) -> tuple[float, dict[str, Any]]:
    """Every factor, evaluated for one specific way of taking part."""
    parts: dict[str, Any] = {}
    for key, (points, why) in {
        "geographic_accessibility": _geographic(user, opp),
        "capital_fit": _capital_for_path(user, opp, path),
        "industry_experience": _industry(user, opp),
        "skills_fit": _skills(user, opp, path=path),
        "supplier_advantage": _asset_for_path(
            user, path, "supplier_network", "supplier_advantage", "supplier network"
        ),
        "distribution_advantage": _asset_for_path(
            user, path, "distribution_network", "distribution_advantage", "distribution network"
        ),
        "regulatory_accessibility": _regulatory(user, opp),
        "time_commitment_fit": _time(user, opp, path=path),
        "risk_tolerance_fit": _risk_fit(user, opp),
        "type_preference": _type_preference(user, opp),
    }.items():
        parts[key] = {"points": points, "max": FACTOR_MAX[key], "why": why}

    total = sum(p["points"] for p in parts.values())
    damp = PATH_DAMPENING.get(path)
    if damp is not None:
        total *= damp
        parts["_dampening"] = {
            "points": 0.0,
            "max": 0,
            "why": (
                f"Scaled to {damp:.0%} because {path.replace('_', ' ')} is a way of "
                "keeping an eye on something, not a way of taking part in it."
            ),
        }
    return round(min(100.0, total), 2), parts


def score_relevance(user: UserContext, opp: OpportunityContext) -> RelevanceResult:
    """0-100, per user, fully explained, never mixed with the global score.

    The headline number is the score of the user's **best available way in**, not
    an average across paths. Averaging would punish an opportunity for offering
    a route this person cannot use, when what they actually want to know is
    whether *any* route works for them.
    """
    paths = opp.participation_paths or ["watch"]
    path_scores: dict[str, float] = {}
    path_parts: dict[str, dict[str, Any]] = {}
    for mode in paths:
        total, parts = _score_for_path(user, opp, mode)
        path_scores[mode] = total
        path_parts[mode] = parts

    best_path = max(path_scores, key=lambda k: path_scores[k])
    total = path_scores[best_path]
    parts = path_parts[best_path]

    outside, notes = _outside_profile(user, opp)
    if len(path_scores) > 1:
        worst = min(path_scores, key=lambda k: path_scores[k])
        if path_scores[best_path] - path_scores[worst] >= 15:
            notes.append(
                f"The way in matters here: {best_path.replace('_', ' ')} scores "
                f"{path_scores[best_path]:.0f} for you while "
                f"{worst.replace('_', ' ')} scores {path_scores[worst]:.0f}."
            )
    if opp.country_data_coverage is not None and opp.country_data_coverage < 40:
        notes.append(
            f"We hold little evidence about {opp.country or opp.geo_scope} "
            f"({opp.country_data_coverage:.0f}% coverage). That is a gap in our data, "
            "not a finding about that market."
        )

    return RelevanceResult(
        version=RELEVANCE_VERSION,
        relevance=total,
        parts=parts,
        path_relevance=path_scores,
        best_path=best_path,
        outside_profile=outside,
        notes=notes,
    )


def _capital_for_path(user: UserContext, opp: OpportunityContext, path: str) -> tuple[float, str]:
    """What this particular route actually costs.

    A path can never cost more than the whole opportunity, and a cheap route
    through an expensive opportunity is genuinely cheap: learning the skill
    behind a $500,000 franchise costs a course, not a franchise fee.
    """
    band = CAPITAL_BAND_USD.get(PATH_REQUIREMENTS.get(path, {}).get("capital", "medium"))
    stated = opp.capital_required_usd
    if band is None:
        required = stated
    elif stated is None:
        required = band
    else:
        required = min(stated, band)

    if required is None:
        return _capital(user, opp)

    shadow = OpportunityContext(
        opportunity_id=opp.opportunity_id,
        opportunity_type=opp.opportunity_type,
        capital_required_usd=required,
        geo_scope=opp.geo_scope,
        country=opp.country,
    )
    points, why = _capital(user, shadow)
    if stated is not None and required < stated:
        why += f" Taking part by {path.replace('_', ' ')} costs far less than the full route."
    return points, why


def _asset_for_path(user: UserContext, path: str, asset: str, key: str, label: str) -> tuple[float, str]:
    maximum = FACTOR_MAX[key]
    have = asset in _norm(user.assets)
    needed = asset in PATH_REQUIREMENTS.get(path, {}).get("assets", [])
    if have and needed:
        return float(maximum), f"Your {label} is exactly what this route uses."
    if have:
        return round(maximum * 0.6, 2), (f"You have a {label}; this route does not lean on it.")
    if needed:
        return 0.0, f"This route depends on a {label} and none is recorded for you."
    return round(maximum * 0.4, 2), f"A {label} is not needed for this route."


def _outside_profile(user: UserContext, opp: OpportunityContext) -> tuple[bool, list[str]]:
    """Is this beyond the user's stated filters?

    Flagged, never hidden. A profile that silently removes everything unfamiliar
    is a cage, and discovery is the reason this product exists.
    """
    reasons: list[str] = []
    if opp.global_score < user.min_global_score:
        reasons.append(
            f"global score {opp.global_score:.0f} is below your minimum of {user.min_global_score:.0f}"
        )
    if opp.confidence < user.min_confidence:
        reasons.append(f"confidence {opp.confidence:.0f} is below your minimum of {user.min_confidence:.0f}")
    if RISK_RANK.get(opp.risk_level, 2) > RISK_RANK.get(user.max_risk_level, 3):
        reasons.append(f"{opp.risk_level.replace('_', ' ')} risk exceeds your maximum")
    if (
        user.max_capital_required is not None
        and opp.capital_required_usd is not None
        and opp.capital_required_usd > user.max_capital_required
    ):
        reasons.append("the capital requirement exceeds your stated maximum")
    elif (
        user.max_capital_usd
        and opp.capital_required_usd is not None
        and opp.capital_required_usd > user.max_capital_usd
    ):
        # Not a filter the user set, but a fact about them they told us. Worth
        # flagging so a 91 they cannot fund does not read as an oversight — and
        # flagged only, because a cheaper way in may still exist.
        reasons.append(
            f"it needs about {opp.capital_required_usd:,.0f} USD against the "
            f"{user.max_capital_usd:,.0f} USD you recorded"
        )
    if opp.opportunity_type in _norm(user.disabled_categories):
        reasons.append(f"you switched {opp.opportunity_type.replace('_', ' ')} off")

    if not reasons:
        return False, []
    return True, ["Outside your usual filters: " + "; ".join(reasons) + "."]
