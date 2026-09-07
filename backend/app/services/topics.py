"""Topics: the themes that connect entities.

A company is not a topic. NVIDIA is a company; "AI GPU demand" is a topic, and it
touches NVIDIA, TSMC, data-centre operators and a dozen products at once.

Membership is decided deterministically, by shared distinctive words in entity
names and by co-occurrence in the same collected records. A language model may
later be asked to write a nicer label and description for a cluster it did not
choose - and the row records that the wording was model-authored. The model never
adds, removes or invents a member.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Entity, RawRecord, Signal, SignalObservation, Topic, TopicEntity

_STOPWORDS = frozenset(
    """the a an and or for of in on to with by from at as is are new next best top
    global world international group company corp inc ltd system systems solution
    solutions service services product products platform technology technologies""".split()
)

_TOKEN = re.compile(r"[a-z0-9]+")

#: A word shared by more than this share of all entities is not distinctive - it
#: would glue everything into one useless mega-cluster.
MAX_TOKEN_SHARE = 0.4
#: Entities seen together in at least this many records are related in practice —
#: but only when their names already share a distinctive word. Co-occurrence alone
#: reflects how the data was collected, not what the things are.
MIN_CO_OCCURRENCE = 3
#: Two token groups merge only when this share of their combined membership is
#: common to both. Below it they stay separate topics.
TOKEN_GROUP_MERGE = 0.5
#: A cluster larger than this has stopped being a topic and become a category.
MAX_CLUSTER_SIZE = 12

CATEGORY_BY_ENTITY_TYPE = {
    "technology": "technology",
    "software_project": "technology",
    "repository": "technology",
    "public_company": "public_investment",
    "company": "public_investment",
    "crypto_asset": "crypto",
    "product": "import_distribution",
    "commodity": "import_distribution",
    "industry": "business",
    "problem": "business",
    "skill": "business",
    "keyword": "business",
    "indicator": "macro",
    "country": "macro",
}


def tokens_of(name: str) -> set[str]:
    # Two-character tokens are kept on purpose: AI, EV and 5G are exactly the words
    # that name an emerging topic.
    return {t for t in _TOKEN.findall(name.lower()) if t not in _STOPWORDS and len(t) >= 2}


def _format_token(token: str) -> str:
    """AI stays AI; everything else is title-cased."""
    return token.upper() if len(token) <= 3 else token.title()


def _format_label(
    shared: list[str],
    members: list[str],
    token_map: dict[str, set[str]],
    names: dict[str, str],
) -> str:
    if not shared:
        return names[members[0]]
    words = [_format_token(tok) for tok in shared[:3]]
    if len(words) == 1 and len(shared[0]) <= 3:
        # A bare acronym is a poor label. Add the most common supporting word so
        # "AI" becomes "AI Agents".
        extras = Counter(tok for m in members for tok in token_map[m] if tok not in shared and len(tok) > 3)
        if extras:
            best = min(extras.items(), key=lambda pair: (-pair[1], pair[0]))[0]
            words.append(_format_token(best))
    return " ".join(words)


@dataclass(slots=True)
class Cluster:
    key: str
    entity_ids: list[str] = field(default_factory=list)
    shared_tokens: list[str] = field(default_factory=list)
    category: str | None = None
    label: str = ""


def build_clusters(
    entities: list[tuple[str, str, str]],
    co_occurrence: dict[tuple[str, str], int] | None = None,
) -> list[Cluster]:
    """Group `(entity_id, canonical_name, entity_type)` triples into topics.

    Deliberately simple and order-independent: shared distinctive tokens, plus an
    optional co-occurrence bridge, then connected components. Same input, same
    clusters, every time.
    """
    if not entities:
        return []

    token_map: dict[str, set[str]] = {eid: tokens_of(name) for eid, name, _ in entities}
    frequency: Counter[str] = Counter()
    for toks in token_map.values():
        frequency.update(toks)
    # The floor keeps small corpora usable: with eight entities, a share-based
    # limit alone would reject every word that actually joins two of them.
    limit = max(3, int(len(entities) * MAX_TOKEN_SHARE))
    distinctive = {tok for tok, count in frequency.items() if 2 <= count <= limit}

    # union-find over entity ids
    parent: dict[str, str] = {eid: eid for eid, _, _ in entities}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)  # deterministic winner

    # One provisional group per distinctive token. Building groups per token and
    # then merging only where they genuinely overlap avoids the failure this
    # replaced: a chain of unrelated pairs (A shares "water" with B, B shares
    # nothing with C, C shares "ai" with D) collapsing into one nonsense topic.
    by_token: dict[str, set[str]] = defaultdict(set)
    for eid, toks in sorted(token_map.items()):
        for tok in sorted(toks & distinctive):
            by_token[tok].add(eid)

    groups: list[tuple[str, set[str]]] = [
        (tok, members) for tok, members in sorted(by_token.items()) if len(members) >= 2
    ]

    # Merge two token groups only when they describe substantially the same set.
    merged = True
    while merged:
        merged = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                a, b = groups[i][1], groups[j][1]
                overlap = len(a & b) / len(a | b)
                if overlap >= TOKEN_GROUP_MERGE:
                    groups[i] = (groups[i][0], a | b)
                    del groups[j]
                    merged = True
                    break
            if merged:
                break

    for _tok, members in groups:
        ordered = sorted(members)
        for other in ordered[1:]:
            union(ordered[0], other)

    # Co-occurrence may only *reinforce* a relationship the names already imply.
    # Two things measured in the same collection run are not thereby related: a
    # manual spreadsheet upload puts a water heater and an AI tool in one run.
    for (a, b), count in sorted((co_occurrence or {}).items()):
        if count < MIN_CO_OCCURRENCE or a not in parent or b not in parent:
            continue
        if token_map.get(a, set()) & token_map.get(b, set()) & distinctive:
            union(a, b)

    grouped: dict[str, list[str]] = defaultdict(list)
    for eid, _, _ in entities:
        grouped[find(eid)].append(eid)

    names = {eid: name for eid, name, _ in entities}
    types = {eid: etype for eid, _, etype in entities}

    clusters: list[Cluster] = []
    for root, members in sorted(grouped.items()):
        if len(members) < 2 or len(members) > MAX_CLUSTER_SIZE:
            # A lone entity is not a topic, and an enormous cluster is a category.
            continue
        members = sorted(members, key=lambda e: names[e].lower())
        shared = sorted(set.intersection(*(token_map[m] for m in members)) & distinctive)
        if not shared:
            # Every member must share at least one word with the majority, or the
            # group is a chain rather than a topic and is discarded.
            counts = Counter(t for m in members for t in token_map[m] & distinctive)
            common = [tok for tok, n in counts.most_common(3) if n >= len(members) * 0.6]
            if not common:
                continue
            shared = common
        type_counts = Counter(types[m] for m in members)
        # Break ties by name so the same input never yields two different answers.
        dominant_type = min(type_counts.items(), key=lambda pair: (-pair[1], pair[0]))[0]
        label = _format_label(shared, members, token_map, names)
        clusters.append(
            Cluster(
                key=root,
                entity_ids=members,
                shared_tokens=shared,
                category=CATEGORY_BY_ENTITY_TYPE.get(dominant_type, "technology"),
                label=label,
            )
        )
    return clusters


async def _co_occurrence(session: AsyncSession) -> dict[tuple[str, str], int]:
    """How often two entities appear in records collected from the same source run.

    A cheap stand-in for genuine text co-mention: entities measured together tend
    to belong together, and it needs no model.
    """
    rows = (
        await session.execute(
            sa.select(Signal.entity_id, RawRecord.source_run_id)
            .join(SignalObservation, SignalObservation.signal_id == Signal.id)
            .join(RawRecord, RawRecord.id == SignalObservation.raw_record_id)
            .where(RawRecord.source_run_id.is_not(None))
        )
    ).all()
    by_run: dict[str, set[str]] = defaultdict(set)
    for entity_id, run_id in rows:
        by_run[str(run_id)].add(str(entity_id))

    counts: Counter[tuple[str, str]] = Counter()
    for members in by_run.values():
        ordered = sorted(members)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 :]:
                counts[(a, b)] += 1
    return dict(counts)


async def rebuild_topics(session: AsyncSession, *, now: datetime | None = None) -> list[Topic]:
    """Recompute topic membership from current entities. Idempotent."""
    now = now or datetime.now(UTC)
    entities = (await session.execute(sa.select(Entity))).scalars().all()
    triples = [(str(e.id), e.canonical_name, e.entity_type) for e in entities]
    clusters = build_clusters(triples, await _co_occurrence(session))

    by_entity = {str(e.id): e for e in entities}
    result: list[Topic] = []

    for cluster in clusters:
        normalized = "|".join(sorted(cluster.shared_tokens)) or cluster.key
        topic = (
            await session.execute(sa.select(Topic).where(Topic.normalized == normalized))
        ).scalar_one_or_none()
        member_names = [by_entity[e].canonical_name for e in cluster.entity_ids]
        description = (
            f"Deterministic cluster of {len(cluster.entity_ids)} entities sharing the term(s) "
            f"{', '.join(cluster.shared_tokens) or 'co-occurrence'}: {', '.join(member_names)}."
        )
        if topic is None:
            topic = Topic(
                label=cluster.label,
                normalized=normalized,
                description=description,
                category=cluster.category,
                keywords=cluster.shared_tokens,
                first_seen_at=now,
                last_seen_at=now,
                label_is_ai_generated=False,
            )
            session.add(topic)
            await session.flush()
        else:
            topic.description = description
            topic.category = cluster.category
            topic.keywords = cluster.shared_tokens
            topic.last_seen_at = now

        existing = {
            str(row.entity_id): row
            for row in (await session.execute(sa.select(TopicEntity).where(TopicEntity.topic_id == topic.id)))
            .scalars()
            .all()
        }
        # Add new automatic members from the cluster
        for entity_id in cluster.entity_ids:
            if entity_id not in existing:
                session.add(TopicEntity(topic_id=topic.id, entity_id=by_entity[entity_id].id, weight=1.0))
        # Remove automatic members that are no longer in the cluster, but preserve manual memberships
        cluster_entity_ids = set(cluster.entity_ids)
        for entity_id_str, te in existing.items():
            if entity_id_str not in cluster_entity_ids and not te.is_manual:
                await session.delete(te)
        result.append(topic)

    await session.flush()
    return result
