"""Controlled vocabularies. Stored as strings for readability in the database."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class SourceStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    ERROR = "error"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    STALE = "stale"


class EntityType(StrEnum):
    COMPANY = "company"
    TECHNOLOGY = "technology"
    PRODUCT = "product"
    INDUSTRY = "industry"
    COUNTRY = "country"
    REGULATION = "regulation"
    PERSON = "person"
    REPOSITORY = "repository"
    PUBLIC_COMPANY = "public_company"
    TOKEN = "token"  # noqa: S105 - a crypto token entity type, not a credential
    KEYWORD = "keyword"
    PROBLEM = "problem"
    INDICATOR = "indicator"
    SOFTWARE_PROJECT = "software_project"
    CRYPTO_ASSET = "crypto_asset"
    COMMODITY = "commodity"
    SKILL = "skill"


class OpportunityCategory(StrEnum):
    TECHNOLOGY = "technology"
    PUBLIC_INVESTMENT = "public_investment"
    BUSINESS = "business"
    IMPORT_DISTRIBUTION = "import_distribution"


class MaturityStage(StrEnum):
    WEAK_SIGNAL = "weak_signal"
    EMERGING = "emerging"
    EARLY_ADOPTION = "early_adoption"
    ACCELERATING = "accelerating"
    MAINSTREAM = "mainstream"
    MATURE = "mature"
    DECLINING = "declining"


class RiskLevel(StrEnum):
    """The four risk levels, exactly as the risk engine emits them.

    "moderate", not "medium": the enum previously disagreed with the engine, so
    a user could store a maximum risk level that nothing would ever match.
    """

    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"


class RiskSeverity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKING = "blocking"


class SkepticStatus(StrEnum):
    STRONG_EVIDENCE = "strong_evidence"
    CONTINUE_RESEARCH = "continue_research"
    WATCH_ONLY = "watch_only"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    HIGH_SPECULATION = "high_speculation"
    POSSIBLE_MANIPULATION = "possible_manipulation"
    REJECT = "reject"


class OpportunityStatus(StrEnum):
    NEW = "new"
    RESEARCHING = "researching"
    WATCHING = "watching"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


class TimeHorizon(StrEnum):
    SHORT = "short"  # < 6 months
    MEDIUM = "medium"  # 6-24 months
    LONG = "long"  # > 24 months


class DecisionKind(StrEnum):
    WATCH = "watch"
    CONFIRM = "confirm"
    REJECT = "reject"
    ACT = "act"
    IGNORE = "ignore"


class NotificationChannel(StrEnum):
    IN_APP = "in_app"
    EMAIL = "email"
    TELEGRAM = "telegram"


class OutcomeKind(StrEnum):
    PRICE = "price"
    ADOPTION = "adoption"
    REVENUE = "revenue"
    DEMAND_VALIDATION = "demand_validation"
    CUSTOMER_ACQUISITION = "customer_acquisition"
    IMPORT_MARGIN = "import_margin"
    SUPPLIER_AVAILABILITY = "supplier_availability"
    MARKET_ADOPTION = "market_adoption"
    NO_OUTCOME = "no_outcome"


class ObservationStatus(StrEnum):
    """Missing data is not zero. A gap is recorded, never invented."""

    OK = "ok"
    MISSING = "missing"  # the source has no value for this period
    FAILED = "failed"  # collection failed; we do not know the value


class MatchDecision(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"  # same thing: merge
    REJECTED = "rejected"  # not the same thing
    KEEP_SEPARATE = "keep_separate"  # related, but must never be merged


class TrendState(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    CONFIRMED = "confirmed"
    WEAKENING = "weakening"
    ENDED = "ended"
    INVALIDATED = "invalidated"


class SubjectType(StrEnum):
    ENTITY = "entity"
    TOPIC = "topic"


# ------------------------------------------------------------------ phase 4
class OpportunityType(StrEnum):
    """The four ways a person can actually act on a trend.

    Deliberately not a synonym for the entity's industry: an AI trend can produce
    a business opportunity, an import opportunity and an equity opportunity, and
    they are judged by completely different criteria.
    """

    BUSINESS = "business"
    IMPORT_DISTRIBUTION = "import_distribution"
    PUBLIC_INVESTMENT = "public_investment"
    CRYPTO = "crypto"


class OpportunityState(StrEnum):
    """Lifecycle of an opportunity candidate.

    There is no "buy now", no "guaranteed" and no "100x". The most confident
    label the system can reach is STRONG_EVIDENCE, which still means "go and
    check this yourself".
    """

    CANDIDATE = "candidate"
    RESEARCHING = "researching"
    WATCHLIST = "watchlist"
    PROMISING = "promising"
    STRONG_EVIDENCE = "strong_evidence"
    WEAKENING = "weakening"
    INVALIDATED = "invalidated"
    ARCHIVED = "archived"


class ValidationStatus(StrEnum):
    """Where the evidence under an opportunity actually came from.

    Until the live-data gate has been run against the real internet, everything
    is UNVALIDATED or DEMO, and the UI says so on every card.
    """

    DEMO = "demo"  # generated scenario data
    UNVALIDATED = "unvalidated"  # real adapters, but replayed fixtures only
    LIVE_VALIDATED = "live_validated"  # collected from the live internet


class RiskCategory(StrEnum):
    MARKET = "market"
    EXECUTION = "execution"
    FINANCIAL = "financial"
    REGULATORY = "regulatory"
    COMPETITION = "competition"
    LIQUIDITY = "liquidity"
    FRAUD_MANIPULATION = "fraud_manipulation"
    SUPPLY_CHAIN = "supply_chain"
    GEOGRAPHIC = "geographic"
    TECHNOLOGY = "technology"
    CUSTOMER_CONCENTRATION = "customer_concentration"


class ParticipationKind(StrEnum):
    BUILD = "build"
    IMPORT = "import"
    DISTRIBUTE = "distribute"
    INVESTIGATE_PUBLIC_COMPANY = "investigate_public_company"
    PROVIDE_SERVICE = "provide_service"
    LEARN_SKILL = "learn_skill"
    PARTNER = "partner"
    WATCH = "watch"


class ConditionKind(StrEnum):
    CONFIRMATION = "confirmation"
    INVALIDATION = "invalidation"


class ConditionState(StrEnum):
    PENDING = "pending"
    MET = "met"
    BROKEN = "broken"


class UserInterest(StrEnum):
    """What the human said about an opportunity. Kept forever for backtesting."""

    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    RESEARCHING = "researching"
    REJECTED = "rejected"
    ACTED_ON = "acted_on"
    WATCHING = "watching"


class EvidenceKind(StrEnum):
    """Categories used when a report groups its evidence."""

    ADOPTION = "adoption"
    ATTENTION = "attention"
    TRADE = "trade"
    FINANCIAL = "financial"
    DEVELOPER = "developer"
    REGULATORY = "regulatory"
    COMMERCIAL = "commercial"


# ------------------------------------------------------------------ phase 5
class ParticipationMode(StrEnum):
    """Ways a person can take part. Superset of the Phase 4 list.

    The same trend usually creates several of these, and they are scored for a
    user *independently* — a solar trend can be a 91 as a skill to learn and a
    22 as software to build, for the same person on the same day.
    """

    BUILD = "build"
    IMPORT = "import"
    DISTRIBUTE = "distribute"
    MANUFACTURE = "manufacture"
    INVESTIGATE_PUBLIC_COMPANY = "investigate_public_company"
    PROVIDE_SERVICE = "provide_service"
    CONSULT = "consult"
    LEARN_SKILL = "learn_skill"
    LICENSE = "license"
    FRANCHISE = "franchise"
    PARTNER = "partner"
    CREATE_CONTENT = "create_content"
    WATCH = "watch"


class InterestCategory(StrEnum):
    """Opportunity categories a user can rank or switch off entirely."""

    BUSINESS = "business"
    IMPORT_DISTRIBUTION = "import_distribution"
    PUBLIC_INVESTMENT = "public_investment"
    TECHNOLOGY = "technology"
    SKILLS_CAREER = "skills_career"
    MANUFACTURING = "manufacturing"
    ECOMMERCE = "ecommerce"
    SERVICES = "services"
    SAAS = "saas"
    FRANCHISING = "franchising"
    LICENSING = "licensing"
    PARTNERSHIPS = "partnerships"
    CRYPTO = "crypto"


class RiskTolerance(StrEnum):
    """What a user is willing to look at.

    This filters what is *shown*. It never softens a factual risk label: a
    speculative user still sees VERY HIGH RISK written on a very high risk.
    """

    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"
    SPECULATIVE = "speculative"


class TimeCommitment(StrEnum):
    PASSIVE = "passive"
    FEW_HOURS_WEEK = "few_hours_week"
    PART_TIME = "part_time"
    FULL_TIME = "full_time"


class UserTimeHorizon(StrEnum):
    WEEKS = "weeks"
    MONTHS = "months"
    ONE_TO_THREE_YEARS = "1_3_years"
    THREE_TO_TEN_YEARS = "3_10_years"


class CapitalFlexibility(StrEnum):
    FIXED = "fixed"
    SOMEWHAT_FLEXIBLE = "somewhat_flexible"
    FLEXIBLE = "flexible"


class ConditionCheckState(StrEnum):
    """How a confirmation or invalidation condition currently stands.

    NOT_CHECKED and UNKNOWN are different answers, and both differ from FAILED.
    A condition is only ever MET on stored evidence, never on a model's opinion.
    """

    NOT_CHECKED = "not_checked"
    PENDING = "pending"
    PARTIALLY_MET = "partially_met"
    MET = "met"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ChangeEventKind(StrEnum):
    SCORE_ROSE = "score_rose"
    SCORE_FELL = "score_fell"
    CONFIDENCE_ROSE = "confidence_rose"
    CONFIDENCE_FELL = "confidence_fell"
    RISK_ROSE = "risk_rose"
    RISK_FELL = "risk_fell"
    CONFIRMATION_MET = "confirmation_met"
    INVALIDATION_TRIGGERED = "invalidation_triggered"
    NEW_COMPETITOR = "new_competitor"
    REGULATION_CHANGED = "regulation_changed"
    TREND_STAGE_CHANGED = "trend_stage_changed"
    NEW_GEOGRAPHY = "new_geography"
    STATE_CHANGED = "state_changed"
    CREATED = "created"


class WatchTargetKind(StrEnum):
    OPPORTUNITY = "opportunity"
    TREND = "trend"
    COMPANY = "company"
    TECHNOLOGY = "technology"
    PRODUCT = "product"
    COUNTRY = "country"
    INDUSTRY = "industry"
    KEYWORD = "keyword"


class AlertTrigger(StrEnum):
    NEW_OPPORTUNITY = "new_opportunity"
    SCORE_THRESHOLD = "score_threshold"
    CONFIDENCE_THRESHOLD = "confidence_threshold"
    RISK_CHANGE = "risk_change"
    CONFIRMATION_MET = "confirmation_met"
    INVALIDATION_TRIGGERED = "invalidation_triggered"
    TREND_ACCELERATION = "trend_acceleration"
    COUNTRY_GAP = "country_gap"
    NEW_GEOGRAPHY = "new_geography"
    WATCHLIST_CHANGE = "watchlist_change"


class DigestFrequency(StrEnum):
    OFF = "off"
    DAILY = "daily"
    WEEKLY = "weekly"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SUPPRESSED = "suppressed"  # deduplicated or inside a cooldown window


class UserFeedback(StrEnum):
    """What the user told us. Stored, but never fed back into the global score."""

    RELEVANT = "relevant"
    NOT_RELEVANT = "not_relevant"
    ALREADY_KNOWN = "already_known"
    TOO_EXPENSIVE = "too_expensive"
    NOT_AVAILABLE_HERE = "not_available_here"
    NOT_INTERESTED = "not_interested"
    BAD_DATA = "bad_data"
    INTERESTING = "interesting"
    INVESTIGATING = "investigating"
    ACTED_ON = "acted_on"


class EvidenceStatus(StrEnum):
    """The distinction section 30 of the Phase 5 brief calls mandatory.

    NO_DATA means we have not looked, or could not. NO_SIGNAL means we looked
    and there was nothing there. Reporting the first as the second is how a
    global system quietly writes off whole countries.
    """

    MEASURED = "measured"
    NO_SIGNAL = "no_signal"
    NO_DATA = "no_data"


class LiveValidationState(StrEnum):
    NOT_VERIFIED = "not_verified"
    VERIFIED = "verified"
    FAILED = "failed"


class AnalysisMode(StrEnum):
    """Which evidence an evaluation was allowed to read.

    Not a display filter. A trend or opportunity carries the mode it was
    *computed* under, because a score produced from demo and live evidence
    together is a different number from one produced from live evidence alone,
    and showing the first under a live-only heading would be a lie. The two live
    side by side; neither overwrites the other.
    """

    #: Everything stored: live sources, offline generators, demo scenarios, and
    #: the seeded manual CSV. This is what the platform has always done.
    DEMO_INCLUSIVE = "demo_inclusive"
    #: Only evidence from sources that actually contact a live upstream. See
    #: `app.sources.provenance` for the exact rule and why it is the adapter.
    LIVE_ONLY = "live_only"
