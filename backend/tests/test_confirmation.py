"""Independent corroboration, and the syndication that pretends to be it."""

from app.analytics.confirmation import SignalRef, assess_confirmation, proxy_share
from app.analytics.dedup import (
    are_near_duplicates,
    duplication_ratio,
    group_duplicates,
    normalize_title,
    title_fingerprint,
)

WIRE = "Nvidia unveils new AI chip for data centres - Reuters"
WIRE_COPIES = [
    WIRE,
    "Nvidia unveils new AI chip for data centres | The Verge",
    "Nvidia unveils new AI chip for data centres - AP",
    "Nvidia unveils new AI chip for data centres, report says",
]
DISTINCT = "Tunisia raises tariffs on imported ceramic tiles"


def test_outlet_suffix_is_stripped_so_copies_match():
    assert title_fingerprint(WIRE) == title_fingerprint(WIRE_COPIES[1])
    assert title_fingerprint(WIRE) == title_fingerprint(WIRE_COPIES[2])


def test_reworded_headline_is_still_the_same_story():
    assert are_near_duplicates(WIRE, "New AI chip for data centres unveiled by Nvidia")


def test_a_genuinely_different_story_is_not_a_duplicate():
    assert title_fingerprint(WIRE) != title_fingerprint(DISTINCT)
    assert not are_near_duplicates(WIRE, DISTINCT)


def test_very_short_headlines_are_not_fingerprinted():
    """Two-word headlines collide constantly; refusing to guess is safer."""
    assert title_fingerprint("Nvidia rises") is None
    assert title_fingerprint("") is None
    assert title_fingerprint(None) is None


def test_stopwords_do_not_carry_identity():
    assert normalize_title("The new AI chip from Nvidia") == normalize_title("AI chip Nvidia")


def test_duplication_ratio_rises_with_syndication():
    independent = [
        ("s1", "Tunisia raises tariffs on imported ceramic tiles"),
        ("s2", "Solid-state battery pilot line opens in Nevada"),
        ("s3", "Heat pump installers report a shortage of trained fitters"),
        ("s4", "Freight rates from Shanghai fall for a third week"),
    ]
    assert duplication_ratio(independent) == 0.0

    syndicated = [(f"s{i}", copy) for i, copy in enumerate(WIRE_COPIES)]
    assert duplication_ratio(syndicated) >= 0.5


def test_a_syndicated_group_knows_it_spans_several_outlets():
    groups = group_duplicates([(f"s{i}", c) for i, c in enumerate(WIRE_COPIES)])
    biggest = max(groups, key=lambda g: g.size)
    assert biggest.is_syndicated is True
    assert biggest.size >= 3


# ------------------------------------------------------------------ counting
def test_four_kinds_of_evidence_from_four_owners_is_strong():
    signals = [
        SignalRef("1", "github_commit_velocity", "s1", "github", 0.8, False, 60),
        SignalRef("2", "import_growth", "s2", "comtrade", 0.9, False, 40),
        SignalRef("3", "media_coverage_growth", "s3", "press", 0.6, False, 30),
        SignalRef("4", "wiki_pageview_growth", "s4", "wikimedia", 0.7, True, 90),
    ]
    report = assess_confirmation(signals)
    assert report.independent_sources == 4
    assert report.distinct_signal_types == 4
    # News volume and encyclopaedia reads are both "attention": four measurements,
    # three genuinely different kinds of evidence.
    assert report.distinct_signal_classes == 3
    assert report.non_proxy_classes == 3
    assert report.is_multi_source
    assert report.warnings == []


def test_sources_sharing_an_owner_count_once():
    signals = [
        SignalRef("1", "media_coverage_growth", "s1", "same_publisher", 0.6, False, 10),
        SignalRef("2", "media_coverage_growth", "s2", "same_publisher", 0.6, False, 10),
        SignalRef("3", "media_coverage_growth", "s3", "same_publisher", 0.6, False, 10),
    ]
    report = assess_confirmation(signals)
    assert report.raw_sources == 3
    assert report.independent_sources == 1
    assert any("share an owner" in w for w in report.warnings)
    assert any("cannot corroborate itself" in w for w in report.warnings)


def test_one_dominant_source_is_called_out():
    signals = [
        SignalRef("1", "github_stars", "s1", "github", 0.8, False, 900),
        SignalRef("2", "media_coverage_growth", "s2", "press", 0.6, False, 5),
    ]
    report = assess_confirmation(signals)
    assert report.independent_sources == 2
    assert report.dominant_source_share > 0.9
    assert any("of all observations" in w for w in report.warnings)


def test_all_proxy_evidence_is_called_out():
    signals = [
        SignalRef("1", "wiki_pageview_growth", "s1", "wikimedia", 0.7, True, 90),
        SignalRef("2", "search_growth", "s2", "trends", 0.5, True, 90),
    ]
    report = assess_confirmation(signals)
    assert report.non_proxy_classes == 0
    assert any("proxy" in w for w in report.warnings)
    assert proxy_share(signals) == 1.0


def test_no_signals_at_all_is_reported_not_scored():
    report = assess_confirmation([])
    assert report.independent_sources == 0
    assert report.warnings == ["No supporting signals at all."]
