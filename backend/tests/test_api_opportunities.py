"""The opportunities API: listing, filtering, detail, report, decisions."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

#: One well-evidenced scenario is enough to exercise the endpoints; the engine
#: itself is covered end to end in test_opportunity_engine.py.
SCENARIOS = ("edge_inference", "luna9_token")

RELIABILITY = {
    "github": 0.80,
    "packages": 0.80,
    "jobs": 0.75,
    "complaints": 0.55,
    "news": 0.60,
    "social": 0.35,
    "volume": 0.40,
    "onchain": 0.75,
}


async def _seed(client: AsyncClient, headers) -> dict:
    from app.sources.adapters.scenario import STREAMS

    for scenario in SCENARIOS:
        for stream in STREAMS[scenario]:
            created = await client.post(
                "/api/v1/sources",
                headers=headers,
                json={
                    "slug": f"op-{scenario.replace('_', '-')}-{stream.replace('_', '-')}",
                    "name": f"{scenario} {stream}",
                    "adapter_key": "scenario",
                    "source_group": f"stream_{stream}",
                    "reliability": RELIABILITY.get(stream, 0.5),
                    "config": {"scenario": scenario, "stream": stream, "days": 200},
                },
            )
            assert created.status_code == 201, created.text
            run = await client.post(f"/api/v1/sources/{created.json()['id']}/run", headers=headers)
            assert run.status_code == 200, run.text

    assert (await client.post("/api/v1/trends/evaluate", headers=headers)).status_code == 200
    generated = await client.post("/api/v1/opportunities/generate", headers=headers)
    assert generated.status_code == 200, generated.text
    return generated.json()


async def test_generate_then_list(client, admin_headers):
    result = await _seed(client, admin_headers)
    assert result["created"] >= 1
    assert result["algorithm_version"]
    assert result["validation_status"] == "demo"

    listing = await client.get("/api/v1/opportunities?sort=score", headers=admin_headers)
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert items
    scores = [i["opportunity_score"] for i in items]
    assert scores == sorted(scores, reverse=True)


async def test_every_candidate_carries_its_validation_label(client, admin_headers):
    """The reader must always be able to see where the evidence came from."""
    await _seed(client, admin_headers)
    items = (await client.get("/api/v1/opportunities", headers=admin_headers)).json()["items"]
    assert items
    for item in items:
        assert item["validation_status"] in {"demo", "unvalidated", "live_validated"}


async def test_refusals_are_returned_with_their_reasons(client, admin_headers):
    result = await _seed(client, admin_headers)
    assert result["rejected"] >= 0
    for rejection in result["rejections"]:
        assert rejection["reasons"]


@pytest.mark.parametrize(
    "sort", ["score", "confidence", "relevance", "lowest_risk", "newest", "fastest_trend"]
)
async def test_every_sort_order_works(client, admin_headers, sort: str):
    await _seed(client, admin_headers)
    response = await client.get(f"/api/v1/opportunities?sort={sort}", headers=admin_headers)
    assert response.status_code == 200, response.text


async def test_filters_narrow_the_list(client, admin_headers):
    await _seed(client, admin_headers)
    all_items = (await client.get("/api/v1/opportunities", headers=admin_headers)).json()
    crypto = (await client.get("/api/v1/opportunities?opportunity_type=crypto", headers=admin_headers)).json()
    assert crypto["total"] <= all_items["total"]
    for item in crypto["items"]:
        assert item["opportunity_type"] == "crypto"

    high_bar = (await client.get("/api/v1/opportunities?min_score=90", headers=admin_headers)).json()
    assert high_bar["total"] <= all_items["total"]


async def test_an_unknown_sort_is_rejected(client, admin_headers):
    response = await client.get("/api/v1/opportunities?sort=profit", headers=admin_headers)
    assert response.status_code == 422


async def test_detail_returns_the_whole_case(client, admin_headers):
    await _seed(client, admin_headers)
    items = (await client.get("/api/v1/opportunities", headers=admin_headers)).json()["items"]
    detail = await client.get(f"/api/v1/opportunities/{items[0]['id']}", headers=admin_headers)
    assert detail.status_code == 200
    body = detail.json()

    assert body["components"], "the calculation must be visible"
    assert body["confidence_parts"]
    assert body["conditions"], "there must be conditions"
    assert body["participation"]
    assert body["evidence"]
    assert body["skeptic"]["strongest_counterargument"]
    assert body["history"]

    kinds = {c["kind"] for c in body["conditions"]}
    assert {"confirmation", "invalidation"} <= kinds

    # The two scores are separate fields, and the personal one is explained.
    assert "opportunity_score" in body
    assert body["user_relevance"] is not None
    assert body["user_relevance_parts"], "relevance must be explained factor by factor"
    assert body["path_relevance"], "each way in is scored on its own"
    assert body["relevance_version"]


async def test_detail_404s_for_an_unknown_id(client, admin_headers):
    response = await client.get(
        "/api/v1/opportunities/00000000-0000-0000-0000-000000000000", headers=admin_headers
    )
    assert response.status_code == 404


async def test_the_report_is_complete_without_any_language_model(client, admin_headers):
    """No provider is configured in tests, and the report must still be whole."""
    await _seed(client, admin_headers)
    items = (await client.get("/api/v1/opportunities", headers=admin_headers)).json()["items"]
    report = await client.get(f"/api/v1/opportunities/{items[0]['id']}/report", headers=admin_headers)
    assert report.status_code == 200
    body = report.json()
    assert body["narrated"] is False

    keys = {s["key"] for s in body["sections"]}
    for required in (
        "header",
        "what_is_happening",
        "why_it_could_matter",
        "evidence",
        "why_early",
        "participation",
        "skeptic",
        "risks",
        "missing_evidence",
        "confirmation",
        "invalidation",
        "next_steps",
        "disclaimer",
    ):
        assert required in keys, f"the report is missing {required}"
    for section in body["sections"]:
        assert section["generated"] is False


async def test_the_report_never_tells_anyone_to_buy(client, admin_headers):
    await _seed(client, admin_headers)
    items = (await client.get("/api/v1/opportunities", headers=admin_headers)).json()["items"]
    for item in items:
        report = await client.get(f"/api/v1/opportunities/{item['id']}/report", headers=admin_headers)
        text = report.text.lower()
        for banned in ("guaranteed", "sure thing", "100x", "next bitcoin", "buy now", "risk-free"):
            assert banned not in text, f"{item['title']} report contains {banned!r}"


async def test_a_decision_is_recorded_with_the_numbers_frozen(client, admin_headers):
    await _seed(client, admin_headers)
    items = (await client.get("/api/v1/opportunities", headers=admin_headers)).json()["items"]
    target = items[0]

    created = await client.post(
        f"/api/v1/opportunities/{target['id']}/decisions",
        headers=admin_headers,
        json={"interest": "researching", "note": "getting quotes"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["score_at_decision"] == target["opportunity_score"]
    assert body["confidence_at_decision"] == target["confidence"]
    assert body["risk_at_decision"] == target["risk_level"]
    assert body["algorithm_version"] == target["algorithm_version"]


async def test_decisions_are_append_only(client, admin_headers):
    """Changing your mind adds a row; it never edits one."""
    await _seed(client, admin_headers)
    items = (await client.get("/api/v1/opportunities", headers=admin_headers)).json()["items"]
    oid = items[0]["id"]

    for interest in ("interested", "researching", "not_interested"):
        response = await client.post(
            f"/api/v1/opportunities/{oid}/decisions",
            headers=admin_headers,
            json={"interest": interest},
        )
        assert response.status_code == 201

    listing = await client.get(f"/api/v1/opportunities/{oid}/decisions", headers=admin_headers)
    assert len(listing.json()) == 3


async def test_an_invalid_interest_is_rejected(client, admin_headers):
    await _seed(client, admin_headers)
    items = (await client.get("/api/v1/opportunities", headers=admin_headers)).json()["items"]
    response = await client.post(
        f"/api/v1/opportunities/{items[0]['id']}/decisions",
        headers=admin_headers,
        json={"interest": "buy_immediately"},
    )
    assert response.status_code == 422


async def test_a_decision_on_an_unknown_opportunity_is_a_404(client, admin_headers):
    response = await client.post(
        "/api/v1/opportunities/00000000-0000-0000-0000-000000000000/decisions",
        headers=admin_headers,
        json={"interest": "watching"},
    )
    assert response.status_code == 404


async def test_generation_requires_admin(client, viewer_headers):
    response = await client.post("/api/v1/opportunities/generate", headers=viewer_headers)
    assert response.status_code == 403


async def test_listing_requires_a_token(client):
    response = await client.get("/api/v1/opportunities")
    assert response.status_code == 401
