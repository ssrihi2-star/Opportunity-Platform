"""How many genuinely independent things are saying the same thing.

Four sources agreeing is only meaningful if they are four sources. Twenty news
sites carrying one wire story are one source wearing twenty hats, and a system
that cannot tell the difference will confidently promote every press release it
sees.

Independence here is judged on three axes, all deterministic:

* **Ownership** - sources sharing a `source_group` count once.
* **Kind of evidence** - developer activity and shipping volumes are different
  kinds of fact; two news feeds are the same kind.
* **Content** - the same headline republished is one story, not two.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.analytics.dedup import duplication_ratio
from app.analytics.signal_types import class_of, is_proxy_signal


@dataclass(slots=True)
class SignalRef:
    """One measured series backing a trend."""

    signal_id: str
    signal_type: str
    source_id: str
    source_group: str
    source_reliability: float = 0.5
    is_proxy: bool = False
    observation_count: int = 0

    @property
    def signal_class(self) -> str:
        return class_of(self.signal_type)


@dataclass(slots=True)
class ConfirmationReport:
    independent_sources: int = 0
    raw_sources: int = 0
    distinct_signal_types: int = 0
    distinct_signal_classes: int = 0
    non_proxy_classes: int = 0
    mean_reliability: float = 0.0
    dominant_source_share: float = 0.0
    duplication_ratio: float = 0.0
    groups: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counted: dict[str, bool] = field(default_factory=dict)

    @property
    def is_multi_source(self) -> bool:
        return self.independent_sources >= 2

    def to_dict(self) -> dict:
        return {
            "independent_sources": self.independent_sources,
            "raw_sources": self.raw_sources,
            "distinct_signal_types": self.distinct_signal_types,
            "distinct_signal_classes": self.distinct_signal_classes,
            "non_proxy_classes": self.non_proxy_classes,
            "mean_reliability": self.mean_reliability,
            "dominant_source_share": self.dominant_source_share,
            "duplication_ratio": self.duplication_ratio,
            "groups": self.groups,
            "warnings": self.warnings,
        }


def assess_confirmation(
    signals: list[SignalRef],
    *,
    headlines: list[tuple[str, str | None]] | None = None,
) -> ConfirmationReport:
    """Count independent corroboration, and say where it was discounted.

    `headlines` is an optional list of `(source_id, title)` for the text records
    behind these signals; supplying it lets syndication be measured rather than
    assumed absent.
    """
    report = ConfirmationReport()
    if not signals:
        report.warnings.append("No supporting signals at all.")
        return report

    report.raw_sources = len({s.source_id for s in signals})
    by_group: dict[str, list[SignalRef]] = defaultdict(list)
    for signal in signals:
        by_group[signal.source_group or signal.source_id].append(signal)

    report.groups = sorted(by_group)
    report.independent_sources = len(by_group)
    report.distinct_signal_types = len({s.signal_type for s in signals})
    classes = {s.signal_class for s in signals}
    report.distinct_signal_classes = len(classes)
    report.non_proxy_classes = len({s.signal_class for s in signals if not s.is_proxy})
    report.mean_reliability = round(sum(s.source_reliability for s in signals) / len(signals), 4)

    # Weight by observation count: a source with 200 readings dominates one with 3.
    total_observations = sum(max(s.observation_count, 1) for s in signals) or 1
    per_group = {
        group: sum(max(s.observation_count, 1) for s in members) for group, members in by_group.items()
    }
    report.dominant_source_share = round(max(per_group.values()) / total_observations, 4)

    for signal in signals:
        report.counted[signal.signal_id] = True

    if headlines:
        report.duplication_ratio = duplication_ratio(headlines)

    # -- warnings the user should see, in the words they should see -----------
    if report.raw_sources > report.independent_sources:
        report.warnings.append(
            f"{report.raw_sources} sources collapse to {report.independent_sources} independent "
            "one(s) because some share an owner or publisher."
        )
    if report.independent_sources < 2:
        report.warnings.append("Only one independent source. A single source cannot corroborate itself.")
    if report.dominant_source_share > 0.7 and report.independent_sources > 1:
        report.warnings.append(
            f"One source contributes {report.dominant_source_share:.0%} of all observations, so "
            "the corroboration is thinner than the source count suggests."
        )
    if report.duplication_ratio >= 0.5:
        report.warnings.append(
            f"{report.duplication_ratio:.0%} of the supporting articles look like the same story "
            "republished, not independent reporting."
        )
    if classes and report.non_proxy_classes == 0:
        report.warnings.append(
            "Every supporting signal is a proxy measurement; nothing here observes the thing itself."
        )
    elif report.distinct_signal_classes == 1:
        only = next(iter(classes))
        report.warnings.append(
            f"All evidence is of one kind ({only}). Different kinds of evidence would be stronger."
        )
    return report


def proxy_share(signals: list[SignalRef]) -> float:
    if not signals:
        return 0.0
    return round(sum(1 for s in signals if s.is_proxy or is_proxy_signal(s.signal_type)) / len(signals), 4)
