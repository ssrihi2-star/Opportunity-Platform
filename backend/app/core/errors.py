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


# ------------------------------------------------------------- failure reporting
def failure_code(exc: BaseException) -> str:
    """The one part of an exception that is always safe to log or store.

    Never interpolate `str(exc)` into a log line, an audit row or a
    `ProviderResult.detail` that is persisted:

    * SQLAlchemy's `DBAPIError` text carries the statement **and its bound
      parameters**, which for this schema means user ids, chat ids and alert
      bodies;
    * httpx exceptions carry the request URL, and the Telegram send URL contains
      the bot token;
    * smtplib exceptions carry the server's reply and the recipient address.

    Truncating does not help — the sensitive part is usually at the front. A
    class name is enough to act on ("which kind of thing broke") and cannot
    carry data. Where a caller needs more, it must add a *fixed* code it chose
    itself, not text from the exception.
    """
    return type(exc).__name__


_UNRECOVERABLE_NAMES = frozenset(
    {
        # Session/transaction is no longer usable: isolating the unit would mean
        # continuing on a connection that is gone.
        "PendingRollbackError",
        "InterfaceError",
        "OperationalError",
        "DisconnectionError",
        "InvalidatePoolError",
        # "Stop now" from the supervisor, not "this unit failed". Swallowing
        # these would turn a requested shutdown into a batch of skipped units.
        "SoftTimeLimitExceeded",
        "TimeLimitExceeded",
        "WorkerShutdown",
        "WorkerTerminate",
    }
)


def is_unrecoverable(exc: BaseException) -> bool:
    """True when `exc` means "stop the run", not "this one unit failed".

    Per-unit isolation (`begin_nested()` around one user or one rule) must let
    these through. Celery's shutdown and time-limit signals are matched by class
    name rather than by import, so this module — and therefore the API, which
    imports it — never depends on Celery being installed or on `celery` being
    imported into the request path.
    """
    if isinstance(exc, KeyboardInterrupt | SystemExit):
        return True
    for klass in type(exc).__mro__:
        if klass.__name__ in _UNRECOVERABLE_NAMES:
            return True
    invalidated = getattr(exc, "connection_invalidated", False)
    return bool(invalidated)
