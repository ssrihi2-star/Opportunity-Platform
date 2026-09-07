"""Topic clustering is deterministic; a model may rename a cluster, never form one."""

from app.services.topics import build_clusters, tokens_of

ENTITIES = [
    ("1", "AI coding agents", "technology"),
    ("2", "AI code review", "technology"),
    ("3", "AI pair programming", "technology"),
    ("4", "solid-state battery", "technology"),
    ("5", "battery gigafactory", "technology"),
    ("6", "ceramic sanitary ware", "product"),
    ("7", "NVIDIA Corporation", "public_company"),
    ("8", "heat pump water heater", "product"),
    ("9", "heat pump installer skills", "skill"),
]


def test_short_acronyms_survive_tokenisation():
    """AI, EV and 5G are exactly the words that name an emerging topic."""
    assert "ai" in tokens_of("AI coding agents")
    assert "the" not in tokens_of("The AI platform")


def test_related_entities_cluster_and_unrelated_ones_do_not():
    clusters = {c.label: set(c.entity_ids) for c in build_clusters(ENTITIES)}
    assert {"1", "2", "3"} in clusters.values()
    assert {"4", "5"} in clusters.values()
    assert {"8", "9"} in clusters.values()
    assert all("7" not in members for members in clusters.values()), "a lone company is not a topic"


def test_clustering_is_order_independent():
    a = build_clusters(ENTITIES)
    b = build_clusters(list(reversed(ENTITIES)))
    assert [sorted(c.entity_ids) for c in a] == [sorted(c.entity_ids) for c in b]


def test_a_bare_acronym_gets_a_readable_label():
    labels = [c.label for c in build_clusters(ENTITIES)]
    assert "AI Agents" in labels or any(label.startswith("AI ") for label in labels)


def test_category_comes_from_the_dominant_member_type():
    by_label = {c.label: c.category for c in build_clusters(ENTITIES)}
    assert by_label["Battery"] == "technology"


def test_an_empty_corpus_produces_no_topics():
    assert build_clusters([]) == []


def test_a_single_entity_is_not_a_topic():
    assert build_clusters([("1", "lonely thing", "technology")]) == []


# --------------------------------------------------------------------------
# Investigation: why "Pump Heat" groups solar water pumps with heat pumps.
#
# This is a characterisation test, not a fix. It pins the CURRENT documented
# behaviour so the finding is reproducible and any future change to it is a
# deliberate, visible diff rather than an accident. Section 2 of
# docs/trend-methodology.md says topics are union-find over shared distinctive
# tokens — so this grouping is exactly what the existing rules specify, and it
# is a limitation of token overlap rather than a bug in the implementation.
# Fixing it properly means entity resolution, which is deliberately out of scope.
SEEDED_PUMP_ENTITIES = [
    ("hp", "heat pump", "product"),  # Wikipedia / Hacker News, live
    ("hpwh", "heat pump water heater", "product"),  # demo generator + manual CSV
    ("swp", "solar water pumps", "product"),  # demo scenario D
    ("ssb", "solid-state battery", "technology"),
    ("lfs", "local-first sync", "technology"),
    ("sw", "sanitary ware", "product"),
    ("aiw", "AI workflow automation", "industry"),
    ("nv", "NVIDIA Corporation", "public_company"),
]


def test_pump_heat_groups_solar_pumps_with_heat_pumps_by_shared_tokens():
    """Documented behaviour: the three names share `pump`, `heat` and `water`.

    `heat pump` and `heat pump water heater` share `heat` and `pump`.
    `heat pump water heater` and `solar water pumps` share `water` and `pump`
    (`pumps` and `pump` are separate tokens, but `water` alone bridges them, and
    `pump` bridges via the first pair). Union-find then merges all three into one
    component. Nothing about the physical devices is consulted, because the
    algorithm has no notion of what a device is.
    """
    clusters = build_clusters(SEEDED_PUMP_ENTITIES)
    pump = next(c for c in clusters if "swp" in c.entity_ids)

    assert set(pump.entity_ids) == {"hp", "hpwh", "swp"}
    # The label is built from the shared tokens, which is where "Pump Heat" comes from.
    assert set(pump.shared_tokens) >= {"pump", "heat"}
    assert "Pump" in pump.label and "Heat" in pump.label


def test_the_pump_grouping_is_pure_name_overlap_not_co_occurrence():
    """Co-occurrence is not what joined them, so removing it changes nothing.

    Worth pinning separately: the co-occurrence bridge may only reinforce a
    relationship the names already imply, so the grouping stands or falls on the
    shared tokens alone. That is what makes this a topic-matching finding rather
    than an artefact of how the demo data happened to be collected.
    """
    without = build_clusters(SEEDED_PUMP_ENTITIES)
    with_co = build_clusters(SEEDED_PUMP_ENTITIES, {("hp", "swp"): 99})
    assert [sorted(c.entity_ids) for c in without] == [sorted(c.entity_ids) for c in with_co]


def test_dropping_the_bridging_name_separates_the_two_devices():
    """Remove `heat pump water heater` and the two devices stop being one topic.

    Proof that the merge runs through the shared-token bridge rather than any
    judgement that a solar pump and a heat pump are the same kind of thing:
    `heat pump` and `solar water pumps` share no distinctive token at all.
    """
    reduced = [e for e in SEEDED_PUMP_ENTITIES if e[0] != "hpwh"]
    clusters = build_clusters(reduced)
    for cluster in clusters:
        assert not {"hp", "swp"} <= set(cluster.entity_ids)
