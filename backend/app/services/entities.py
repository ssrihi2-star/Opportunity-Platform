"""Entity resolution.

The rule that shapes this whole module: **the system merges on proof, never on
resemblance.** An official identifier is proof. An exact alias someone already
confirmed is proof. A similar-looking name is a question, and questions go to a
human review queue rather than being answered by a threshold.

Resolution order:
  1. official identifier  (SEC CIK, GitHub repo id, ticker+exchange, ISO country,
     contract address + chain, package name)  -> merge, confidence 1.0
  2. exact normalised alias of the same type                -> merge, confidence 1.0
  3. a prior human decision about this exact pair           -> obey it, forever
  4. strong structural similarity                           -> new entity + review candidate
  5. nothing similar                                        -> new entity, no candidate
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import MatchDecision
from app.models.models import Entity, EntityAlias, EntityMatchCandidate

# Corporate and project suffixes that carry no identifying information.
_SUFFIXES = {
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "ltd",
    "limited",
    "llc",
    "plc",
    "sa",
    "sarl",
    "gmbh",
    "ag",
    "nv",
    "bv",
    "holdings",
    "holding",
    "group",
    "company",
    "technologies",
    "technology",
    "labs",
    "lab",
    "sas",
    "spa",
    "oy",
    "ab",
    "as",
    "pte",
    "pty",
    "kk",
    "srl",
    "sl",
}

_PUNCT = re.compile(r"[^\w\s-]", re.UNICODE)
_SPACE = re.compile(r"\s+")

#: Identifier keys that uniquely name a thing in the outside world. Order matters
#: only for the explanation text; any single hit is conclusive.
IDENTIFIER_KEYS: tuple[str, ...] = (
    "sec_cik",
    "github_repo_id",
    "iso_country",
    "chain_address",  # synthesised below from contract address + chain
    "package_name",
    "ticker_exchange",  # synthesised below from ticker + exchange
)


#: A bare ticker is ambiguous across exchanges ("BMW" is different in Frankfurt and
#: on the US OTC market), so it is only conclusive together with its exchange.
def _ticker_key(external_ids: dict[str, Any]) -> str | None:
    ticker = (external_ids.get("ticker") or "").strip().upper()
    exchange = (external_ids.get("exchange") or "").strip().upper()
    if ticker and exchange:
        return f"{exchange}:{ticker}"
    return None


def _chain_key(external_ids: dict[str, Any]) -> str | None:
    address = (external_ids.get("contract_address") or "").strip().lower()
    chain = (external_ids.get("chain") or "").strip().lower()
    if address and chain:
        return f"{chain}:{address}"
    return None


def canonical_identifiers(external_ids: dict[str, Any] | None) -> dict[str, str]:
    """Reduce a loose identifier dict to the keys we will actually match on."""
    ids = external_ids or {}
    out: dict[str, str] = {}
    if ids.get("sec_cik"):
        out["sec_cik"] = str(ids["sec_cik"]).strip().zfill(10)
    if ids.get("github_repo_id"):
        out["github_repo_id"] = str(ids["github_repo_id"]).strip()
    if ids.get("iso_country"):
        out["iso_country"] = str(ids["iso_country"]).strip().upper()
    if ids.get("package_name"):
        out["package_name"] = str(ids["package_name"]).strip().lower()
    # Composite keys are also accepted in their already-canonical form, because
    # this function runs again over what it previously stored - and an identifier
    # that vanishes on the second pass is an identifier that never matches.
    if ids.get("ticker_exchange"):
        out["ticker_exchange"] = str(ids["ticker_exchange"]).strip().upper()
    elif _ticker_key(ids):
        out["ticker_exchange"] = _ticker_key(ids)  # type: ignore[assignment]
    if ids.get("chain_address"):
        out["chain_address"] = str(ids["chain_address"]).strip().lower()
    elif _chain_key(ids):
        out["chain_address"] = _chain_key(ids)  # type: ignore[assignment]
    return out


def normalize_name(name: str) -> str:
    """Lowercase, strip accents, punctuation and corporate suffixes."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Collapse dotted abbreviations first so "S.A." becomes the suffix token "sa".
    text = text.lower().replace(".", "")
    text = _PUNCT.sub(" ", text)
    tokens = [t for t in _SPACE.split(text.strip()) if t]
    while tokens and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return " ".join(tokens) or name.strip().lower()


def _squash(text: str) -> str:
    """Remove separators entirely: 'open ai' and 'openai' squash to the same key."""
    return re.sub(r"[\s\-_]", "", text)


def similarity(a: str, b: str) -> float:
    """A deliberately conservative 0-1 score over two normalised names.

    Not a general-purpose string distance. It rewards the two patterns that
    actually produce the same real-world thing - identical words in a different
    order, and identical letters with different spacing - and treats everything
    else as weak evidence, because that is what sends a pair to review rather
    than merging it.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if _squash(a) == _squash(b):
        return 0.95  # "Open AI" vs "OpenAI"
    tokens_a, tokens_b = set(a.split()), set(b.split())
    if tokens_a == tokens_b:
        return 0.9  # same words, different order
    overlap = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    jaccard = overlap / union if union else 0.0
    # One name fully containing the other is suggestive but far from conclusive:
    # "Apple" vs "Apple Bank" is exactly this shape and must not merge.
    containment = 0.0
    if tokens_a < tokens_b or tokens_b < tokens_a:
        containment = 0.6
    return round(max(jaccard, containment), 4)


#: At or above this, the pair is worth a human's attention. Below it, the names
#: simply are not alike and no question is raised.
REVIEW_THRESHOLD = 0.6
#: There is no "merge automatically" threshold. That is the point.


@dataclass(slots=True)
class Resolution:
    entity: Entity
    matched_on: str  # identifier key, "alias", "created"
    confidence: float
    created: bool
    candidate: EntityMatchCandidate | None = None


async def _find_by_identifier(
    session: AsyncSession, entity_type: str, identifiers: dict[str, str]
) -> tuple[Entity, str] | None:
    if not identifiers:
        return None
    rows = (await session.execute(sa.select(Entity).where(Entity.entity_type == entity_type))).scalars().all()
    for entity in rows:
        stored = canonical_identifiers(entity.external_ids)
        for key, value in identifiers.items():
            if stored.get(key) and stored[key] == value:
                return entity, key
    return None


async def _find_by_alias(session: AsyncSession, entity_type: str, normalized: str) -> Entity | None:
    alias = (
        await session.execute(
            sa.select(EntityAlias).where(
                EntityAlias.normalized == normalized, EntityAlias.entity_type == entity_type
            )
        )
    ).scalar_one_or_none()
    if alias is not None:
        return await session.get(Entity, alias.entity_id)
    entity = (
        await session.execute(
            sa.select(Entity).where(Entity.normalized == normalized, Entity.entity_type == entity_type)
        )
    ).scalar_one_or_none()
    return entity


async def _prior_decision(
    session: AsyncSession, normalized: str, entity_type: str, candidate_id: Any
) -> EntityMatchCandidate | None:
    return (
        await session.execute(
            sa.select(EntityMatchCandidate).where(
                EntityMatchCandidate.observed_normalized == normalized,
                EntityMatchCandidate.entity_type == entity_type,
                EntityMatchCandidate.candidate_entity_id == candidate_id,
            )
        )
    ).scalar_one_or_none()


async def find_similar(
    session: AsyncSession, entity_type: str, normalized: str, limit: int = 3
) -> list[tuple[Entity, float]]:
    """Entities of the same type whose names resemble this one, best first."""
    rows = (await session.execute(sa.select(Entity).where(Entity.entity_type == entity_type))).scalars().all()
    scored = [
        (entity, similarity(normalized, entity.normalized))
        for entity in rows
        if entity.normalized != normalized
    ]
    scored = [pair for pair in scored if pair[1] >= REVIEW_THRESHOLD]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:limit]


async def resolve_entity(
    session: AsyncSession,
    name: str,
    entity_type: str,
    *,
    external_ids: dict[str, Any] | None = None,
    country: str | None = None,
    source_id: Any = None,
) -> Resolution:
    """Resolve a mentioned name to an entity, raising a review candidate if unsure."""
    normalized = normalize_name(name)
    identifiers = canonical_identifiers(external_ids)

    # 1. An official identifier is proof.
    hit = await _find_by_identifier(session, entity_type, identifiers)
    if hit is not None:
        entity, key = hit
        await _merge_identifiers(session, entity, identifiers)
        await add_alias(session, entity, name)
        return Resolution(entity, matched_on=key, confidence=1.0, created=False)

    # 2. An exact known alias is proof.
    entity = await _find_by_alias(session, entity_type, normalized)
    if entity is not None:
        await _merge_identifiers(session, entity, identifiers)
        return Resolution(entity, matched_on="alias", confidence=1.0, created=False)

    # 3. Nothing conclusive. Look for anything close enough to be worth asking about.
    similar = await find_similar(session, entity_type, normalized)

    # 3a. A human may already have ruled on this exact pair. Obey that ruling.
    for candidate_entity, _score in similar:
        prior = await _prior_decision(session, normalized, entity_type, candidate_entity.id)
        if prior is None:
            continue
        if prior.decision == MatchDecision.CONFIRMED:
            await add_alias(session, candidate_entity, name)
            await _merge_identifiers(session, candidate_entity, identifiers)
            return Resolution(candidate_entity, matched_on="human_confirmed", confidence=1.0, created=False)
        if prior.decision in (MatchDecision.REJECTED, MatchDecision.KEEP_SEPARATE):
            similar = [(e, s) for e, s in similar if e.id != candidate_entity.id]

    # 4. Create the entity. It stands on its own until someone says otherwise -
    #    guessing a merge here is how two different companies become one row.
    entity = await _create_entity(
        session, name, normalized, entity_type, identifiers, country, raw_ids=external_ids or {}
    )

    candidate = None
    if similar:
        best_entity, best_score = similar[0]
        candidate = await _raise_candidate(
            session,
            observed_name=name,
            normalized=normalized,
            entity_type=entity_type,
            candidate_entity=best_entity,
            created_entity=entity,
            score=best_score,
            source_id=source_id,
        )
    return Resolution(entity, matched_on="created", confidence=0.0, created=True, candidate=candidate)


async def _create_entity(
    session: AsyncSession,
    name: str,
    normalized: str,
    entity_type: str,
    identifiers: dict[str, str],
    country: str | None,
    raw_ids: dict[str, Any] | None = None,
) -> Entity:
    entity = Entity(
        entity_type=entity_type,
        canonical_name=name.strip(),
        normalized=normalized,
        ticker=((raw_ids or {}).get("ticker") or "").strip().upper() or None,
        country=country or identifiers.get("iso_country"),
        external_ids=dict(identifiers),
    )
    session.add(entity)
    await session.flush()
    session.add(
        EntityAlias(entity_id=entity.id, alias=name.strip(), normalized=normalized, entity_type=entity_type)
    )
    # A ticker is a name people type, so it becomes an alias - but a bare ticker is
    # never a matching *identifier*, because the same letters mean different
    # companies on different exchanges.
    ticker = ((raw_ids or {}).get("ticker") or "").strip()
    if ticker:
        await add_alias(session, entity, ticker, confidence=0.9)
    await session.flush()
    return entity


async def _merge_identifiers(session: AsyncSession, entity: Entity, identifiers: dict[str, str]) -> None:
    """Add newly learned identifiers, never overwrite a conflicting one."""
    if not identifiers:
        return
    stored = dict(entity.external_ids or {})
    changed = False
    for key, value in identifiers.items():
        if key not in stored or not stored[key]:
            stored[key] = value
            changed = True
    if changed:
        entity.external_ids = stored
        await session.flush()


async def _raise_candidate(
    session: AsyncSession,
    *,
    observed_name: str,
    normalized: str,
    entity_type: str,
    candidate_entity: Entity,
    created_entity: Entity,
    score: float,
    source_id: Any,
) -> EntityMatchCandidate:
    existing = await _prior_decision(session, normalized, entity_type, candidate_entity.id)
    if existing is not None:
        return existing
    reason = (
        f"'{observed_name}' resembles the existing {entity_type} "
        f"'{candidate_entity.canonical_name}' ({score:.0%} name similarity), but no shared "
        "official identifier was found. Kept separate pending review."
    )
    candidate = EntityMatchCandidate(
        observed_name=observed_name,
        observed_normalized=normalized,
        entity_type=entity_type,
        candidate_entity_id=candidate_entity.id,
        created_entity_id=created_entity.id,
        confidence=score,
        reason=reason,
        evidence={
            "observed_normalized": normalized,
            "candidate_normalized": candidate_entity.normalized,
            "candidate_external_ids": candidate_entity.external_ids or {},
            "similarity": score,
        },
        source_id=source_id,
        decision=MatchDecision.PENDING,
    )
    session.add(candidate)
    await session.flush()
    return candidate


async def apply_decision(
    session: AsyncSession,
    candidate: EntityMatchCandidate,
    decision: str,
    *,
    user_id: Any = None,
    note: str | None = None,
) -> EntityMatchCandidate:
    """Record a human ruling and, for a confirmation, actually merge the entities.

    Merging moves aliases and signals onto the surviving row rather than deleting
    history: the observations already collected under the duplicate stay valid.
    """
    from app.models.models import Signal  # local import avoids a cycle

    candidate.decision = decision
    candidate.decided_by_user_id = user_id
    candidate.decided_at = datetime.now(UTC)
    candidate.note = note

    if decision == MatchDecision.CONFIRMED and candidate.created_entity_id:
        survivor = await session.get(Entity, candidate.candidate_entity_id)
        duplicate = await session.get(Entity, candidate.created_entity_id)
        if survivor is not None and duplicate is not None and survivor.id != duplicate.id:
            await session.execute(
                sa.update(Signal).where(Signal.entity_id == duplicate.id).values(entity_id=survivor.id)
            )
            aliases = (
                (await session.execute(sa.select(EntityAlias).where(EntityAlias.entity_id == duplicate.id)))
                .scalars()
                .all()
            )
            for alias in aliases:
                clash = (
                    await session.execute(
                        sa.select(EntityAlias).where(
                            EntityAlias.normalized == alias.normalized,
                            EntityAlias.entity_type == alias.entity_type,
                            EntityAlias.entity_id == survivor.id,
                        )
                    )
                ).scalar_one_or_none()
                if clash is None:
                    alias.entity_id = survivor.id
                else:
                    await session.delete(alias)
            await _merge_identifiers(session, survivor, canonical_identifiers(duplicate.external_ids))
            await session.flush()
            await session.delete(duplicate)
            candidate.created_entity_id = None
    await session.flush()
    return candidate


async def add_alias(
    session: AsyncSession, entity: Entity, alias: str, confidence: float = 0.9
) -> EntityAlias:
    normalized = normalize_name(alias)
    existing = (
        await session.execute(
            sa.select(EntityAlias).where(
                EntityAlias.normalized == normalized, EntityAlias.entity_type == entity.entity_type
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = EntityAlias(
        entity_id=entity.id,
        alias=alias,
        normalized=normalized,
        entity_type=entity.entity_type,
        confidence=confidence,
    )
    session.add(row)
    await session.flush()
    return row


async def resolve_or_create_entity(
    session: AsyncSession,
    name: str,
    entity_type: str,
    *,
    ticker: str | None = None,
    country: str | None = None,
    external_ids: dict[str, Any] | None = None,
    source_id: Any = None,
) -> Entity:
    """Backwards-compatible wrapper used by the ingestion pipeline."""
    ids = dict(external_ids or {})
    if ticker and "ticker" not in ids:
        ids["ticker"] = ticker
    return (
        await resolve_entity(
            session, name, entity_type, external_ids=ids, country=country, source_id=source_id
        )
    ).entity
