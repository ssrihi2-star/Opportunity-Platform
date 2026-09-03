"""Telling twenty copies of one wire story apart from twenty independent reports.

The distinction matters more than it looks: a trend "confirmed by 20 news sites"
that are all republishing the same Reuters piece has exactly one piece of
evidence behind it. Everything here is deterministic and cheap - no model, no
embeddings - because it runs over every collected record.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

# Words that carry no identifying weight in a headline.
_STOPWORDS = frozenset(
    """a an and are as at be by for from has have how in into is it its of on or
    that the their they this to was were what when which who will with your you
    about after all also amid among any been before between can could may more
    most new now not over said says than them there these those up via would""".split()
)

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")
# Publisher suffixes appended to syndicated headlines, e.g. " - Reuters".
# Two-letter outlets are common (AP, FT, PA), so one trailing character is enough.
_OUTLET_SUFFIX = re.compile(r"\s+[-|–—]\s+[A-Z][\w .&'']{1,40}$")


def normalize_title(title: str | None) -> str:
    if not title:
        return ""
    text = unicodedata.normalize("NFKD", title)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _OUTLET_SUFFIX.sub("", text)
    text = _PUNCT.sub(" ", text.lower())
    tokens = [t for t in _SPACE.split(text.strip()) if t and t not in _STOPWORDS]
    return " ".join(tokens)


def title_fingerprint(title: str | None) -> str | None:
    """A stable hash of a headline's meaningful words, order-independent.

    Order-independent because syndicators reorder and re-punctuate headlines far
    more often than they change the nouns.
    """
    normalized = normalize_title(title)
    if not normalized:
        return None
    tokens = sorted(set(normalized.split()))
    if len(tokens) < 3:
        # Too short to fingerprint safely: two-word headlines collide constantly.
        return None
    return hashlib.sha256(" ".join(tokens).encode()).hexdigest()[:32]


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def are_near_duplicates(title_a: str | None, title_b: str | None, threshold: float = 0.7) -> bool:
    """True when two headlines are plausibly the same story.

    Used for the cases an exact fingerprint misses: a rewritten lead, an added
    subtitle, a translated dateline.
    """
    a, b = set(normalize_title(title_a).split()), set(normalize_title(title_b).split())
    if len(a) < 3 or len(b) < 3:
        return False
    return jaccard(a, b) >= threshold


@dataclass(slots=True)
class DuplicateGroup:
    fingerprint: str
    titles: list[str] = field(default_factory=list)
    source_ids: set[str] = field(default_factory=set)

    @property
    def size(self) -> int:
        return len(self.titles)

    @property
    def is_syndicated(self) -> bool:
        """The same story carried by more than one source is syndication."""
        return len(self.source_ids) > 1


def group_duplicates(records: list[tuple[str, str | None]], threshold: float = 0.7) -> list[DuplicateGroup]:
    """Group `(source_id, title)` pairs into probable same-story clusters.

    Exact fingerprints first (cheap, exact), then a single fuzzy pass to fold in
    near-misses. Deliberately greedy rather than optimal: a stable, explainable
    grouping is worth more here than a marginally better one.
    """
    exact: dict[str, DuplicateGroup] = {}
    unmatched: list[tuple[str, str]] = []

    for source_id, title in records:
        fp = title_fingerprint(title)
        if fp is None:
            continue
        group = exact.get(fp)
        if group is None:
            group = DuplicateGroup(fingerprint=fp)
            exact[fp] = group
        group.titles.append(title or "")
        group.source_ids.add(source_id)

    groups = list(exact.values())
    for source_id, title in unmatched:  # pragma: no cover - reserved for future use
        for group in groups:
            if any(are_near_duplicates(title, t, threshold) for t in group.titles):
                group.titles.append(title)
                group.source_ids.add(source_id)
                break

    # Fold near-duplicate groups together so a rewritten headline does not read as
    # a second independent report.
    merged: list[DuplicateGroup] = []
    for group in sorted(groups, key=lambda g: -g.size):
        for target in merged:
            if any(are_near_duplicates(group.titles[0], t, threshold) for t in target.titles):
                target.titles.extend(group.titles)
                target.source_ids |= group.source_ids
                break
        else:
            merged.append(group)
    return merged


def duplication_ratio(records: list[tuple[str, str | None]]) -> float:
    """0 when every record is a distinct story, approaching 1 when all are copies."""
    fingerprintable = [r for r in records if title_fingerprint(r[1])]
    if len(fingerprintable) < 2:
        return 0.0
    groups = group_duplicates(fingerprintable)
    distinct = len(groups)
    return round(1.0 - (distinct / len(fingerprintable)), 4)


def independent_source_groups(
    signal_sources: list[tuple[str, str]],
) -> set[str]:
    """Collapse `(source_id, source_group)` pairs to the set of independent groups.

    Two sources in the same `source_group` count once. That is what stops the two
    demo generators, or three feeds from one publisher, from looking like
    independent corroboration.
    """
    by_group: dict[str, set[str]] = defaultdict(set)
    for source_id, group in signal_sources:
        by_group[group or source_id].add(source_id)
    return set(by_group)
