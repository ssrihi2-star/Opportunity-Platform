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
