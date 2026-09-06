"""Which stored evidence was actually collected from a live source.

The platform ships with generated scenarios, an offline demo generator and a
seeded manual CSV, all of which land in exactly the same tables as a real
Hacker News or Wikipedia fetch. Once they are mixed together a trend can read
"confidence 93" on evidence that is mostly invented, which is the failure this
module exists to prevent.

**The authoritative test is the adapter, not the source class.**
``Source.source_class`` is a *trust* label (how much a kind of publisher is
believed), not a *provenance* label (where this row actually came from). Two
concrete cases in this repository prove it cannot be used on its own:

* ``manual_csv`` is seeded with ``source_class = "manual_import"``. Nothing in
  that string says "demo", yet its rows are a hard-coded fixture in
  ``scripts/seed.py``.
* ``wikipedia_pageviews`` declares ``source_class = "official"``. That is a
  statement about Wikimedia, not proof that any particular row was fetched.

So eligibility is decided by whether the adapter behind the source has to reach
the network at all (``BaseDataSource.requires_network``). An adapter that never
opens a socket cannot have produced live evidence, whatever it is labelled.
Demo classes and the two generator adapters are additionally refused by name, so
a network-capable adapter deliberately registered as demo data stays out.

Product decision for this MVP: seeded ``manual_csv`` rows are **excluded** from
live-only mode. Support for verified user-imported evidence is deferred, and
until it exists a CSV cannot be distinguished from the seeded one.
"""

from __future__ import annotations

from app.sources.registry import get_adapter_class

#: Source classes that describe generated data rather than a real publisher.
DEMO_SOURCE_CLASSES: frozenset[str] = frozenset({"demo"})

#: Adapters that manufacture their own data. Refused by name as well as by
#: `requires_network`, so relabelling one cannot smuggle it into live results.
DEMO_ADAPTER_KEYS: frozenset[str] = frozenset({"scenario", "demo_mock"})


def _requires_network(adapter_key: str) -> bool | None:
    """True/False from the registry, or None when the adapter is unknown."""
    try:
        return bool(get_adapter_class(adapter_key).requires_network)
    except KeyError:
        return None


def live_eligibility(adapter_key: str, source_class: str | None) -> tuple[bool, str]:
    """Is evidence from this source eligible for live-only analysis, and why not.

    Returns ``(eligible, reason)``. ``reason`` is empty when eligible and is
    written for a reader, because it is shown next to the excluded source.
    """
    if (source_class or "") in DEMO_SOURCE_CLASSES:
        return False, f"source is classified as {source_class!r} (generated demo data)"
    if adapter_key in DEMO_ADAPTER_KEYS:
        return False, f"adapter {adapter_key!r} generates its own data"

    requires_network = _requires_network(adapter_key)
    if requires_network is None:
        # An adapter we cannot inspect is not assumed to be live. Failing closed
        # is the whole point: an unknown provenance is not a live provenance.
        return False, f"adapter {adapter_key!r} is not registered, so its provenance is unknown"
    if not requires_network:
        return False, (
            f"adapter {adapter_key!r} never contacts a live source "
            "(offline generator, or a manually imported file)"
        )
    return True, ""


def is_live_source(adapter_key: str, source_class: str | None) -> bool:
    return live_eligibility(adapter_key, source_class)[0]
