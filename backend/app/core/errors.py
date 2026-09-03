"""Domain errors. Every message must tell the caller what to do next."""

from __future__ import annotations


class OISError(Exception):
    """Base class for all application errors."""


class ImmutableRowError(OISError):
    """Raised when code tries to modify an append-only row."""


class PartialFetchError(OISError):
    """A source returned some records then failed. Carries what was collected."""

    def __init__(self, message: str, records: list | None = None) -> None:
        super().__init__(message)
        self.records = records or []


class SourceUnavailableError(OISError):
    """The source could not be reached at all. Retry later."""


class SourceConfigError(OISError):
    """The source is misconfigured (missing key, bad parameter). Fix and re-run."""


class RobotsDisallowedError(OISError):
    """robots.txt forbids this path. The adapter must not fetch it."""


class RateLimitedError(OISError):
    """A local or remote rate limit was hit. Carries the retry hint in seconds."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class BudgetExceededError(OISError):
    """The AI spend cap was reached. The pipeline degrades to deterministic-only."""


class UngroundedReportError(OISError):
    """A generated report cited evidence that does not exist. The report is discarded."""


class SSRFBlockedError(OISError):
    """An outbound request targeted a private or disallowed address."""
