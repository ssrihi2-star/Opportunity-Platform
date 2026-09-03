"""The trends dashboard API, the review queue, and the currency rule."""

import io

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _seed_scenarios(client: AsyncClient, headers, scenarios=("ai_coding_agents", "zephyr_token")):
    from app.sources.adapters.scenario import STREAMS

    reliability = {
        "github": 0.8,
        "news": 0.6,
        "wikipedia": 0.7,
        "jobs": 0.75,
        "packages": 0.8,
        "social": 0.35,
        "news_wire": 0.45,
    }
    for scenario in scenarios:
        for stream in STREAMS[scenario]:
            created = await client.post(
                "/api/v1/sources",
                headers=headers,
                json={
                    "slug": f"sc-{scenario.replace('_', '-')}-{stream.replace('_', '-')}",
                    "name": f"{scenario} {stream}",
                    "adapter_key": "scenario",
                    "source_group": f"stream_{stream}",
                    "reliability": reliability.get(stream, 0.5),
                    "config": {"scenario": scenario, "stream": stream, "days": 60},
                },
            )
            assert created.status_code == 201, created.text
            run = await client.post(f"/api/v1/sources/{created.json()['id']}/run", headers=headers)
            assert run.status_code == 200, run.text
    evaluate = await client.post("/api/v1/trends/evaluate", headers=headers)
    assert evaluate.status_code == 200, evaluate.text
    return evaluate.json()


async def test_evaluate_then_list_and_sort(client, admin_headers):
    result = await _seed_scenarios(client, admin_headers)
    assert result["evaluated"] > 0
    assert "updated in place" in result["detail"]

    listing = await client.get("/api/v1/trends?sort=score", headers=admin_headers)
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert items
    scores = [i["trend_score"] for i in items]
    assert scores == sorted(scores, reverse=True)

    by_confidence = await client.get("/api/v1/trends?sort=confidence", headers=admin_headers)
    confidences = [i["confidence"] for i in by_confidence.json()["items"]]
    assert confidences == sorted(confidences, reverse=True)

    by_acceleration = await client.get("/api/v1/trends?sort=acceleration", headers=admin_headers)
    assert by_acceleration.status_code == 200


async def test_filters_narrow_the_list(client, admin_headers):
    await _seed_scenarios(client, admin_headers)

    everything = (await client.get("/api/v1/trends", headers=admin_headers)).json()["total"]
    no_spikes = (await client.get("/api/v1/trends?include_spikes=false", headers=admin_headers)).json()
    assert no_spikes["total"] < everything
    assert all(not i["is_spike"] for i in no_spikes["items"])

    tech = (await client.get("/api/v1/trends?category=technology", headers=admin_headers)).json()
    assert all(i["category"] == "technology" for i in tech["items"])

    strong = (await client.get("/api/v1/trends?min_score=50", headers=admin_headers)).json()
    assert all(i["trend_score"] >= 50 for i in strong["items"])


async def test_detail_shows_the_whole_calculation(client, admin_headers):
    await _seed_scenarios(client, admin_headers)
    listing = await client.get("/api/v1/trends?sort=score", headers=admin_headers)
    trend_id = listing.json()["items"][0]["id"]

    detail = (await client.get(f"/api/v1/trends/{trend_id}", headers=admin_headers)).json()
    assert set(detail["components"]) >= {"growth", "acceleration", "source_diversity"}
    for component in detail["components"].values():
        assert component["why"], "every component must explain itself"
    assert detail["metrics"]["confidence_parts"]
    assert detail["metrics"]["stage_evidence"]
    assert detail["signals"]
    assert detail["series"]
    assert detail["history"]
    assert detail["evidence"]


async def test_detail_series_marks_gaps_rather_than_zeroes(client, admin_headers, session):
    """A period the source never reported must arrive as a gap, not a zero."""
    created = await client.post(
        "/api/v1/sources",
        headers=admin_headers,
        json={"slug": "gapcsv", "name": "Gap CSV", "adapter_key": "csv_import", "config": {}},
    )
    csv = (
        "observed_at,entity_name,entity_type,signal_type,value,unit,status\n"
        "2026-05-01,gap widget,product,import_growth,100,tonnes,ok\n"
        "2026-06-01,gap widget,product,import_growth,,tonnes,missing\n"
        "2026-07-01,gap widget,product,import_growth,140,tonnes,ok\n"
        "2026-08-01,gap widget,product,import_growth,180,tonnes,ok\n"
        "2026-08-15,gap widget,product,import_growth,210,tonnes,ok\n"
    )
    upload = await client.post(
        f"/api/v1/sources/{created.json()['id']}/upload-csv",
        headers=admin_headers,
        files={"file": ("gaps.csv", io.BytesIO(csv.encode()), "text/csv")},
    )
    assert upload.status_code == 200, upload.text
    await client.post(f"/api/v1/sources/{created.json()['id']}/run", headers=admin_headers)

    records = await client.get("/api/v1/raw-records?limit=50", headers=admin_headers)
    assert records.status_code == 200

    from app.models.models import SignalObservation

    rows = (await session.execute(__import__("sqlalchemy").select(SignalObservation))).scalars().all()
    missing = [r for r in rows if r.status == "missing"]
    assert len(missing) == 1
    assert missing[0].value is None, "a gap stores nothing, not zero"


async def test_money_keeps_its_currency_and_is_never_converted(client, admin_headers, session):
    created = await client.post(
        "/api/v1/sources",
        headers=admin_headers,
        json={"slug": "tndcsv", "name": "Tunisian customs", "adapter_key": "csv_import", "config": {}},
    )
    csv = (
        "observed_at,entity_name,entity_type,signal_type,value,currency,unit,geo_scope\n"
        "2026-05-01,tile imports,product,hs_code_volume_change,200000,TND,value,TN\n"
        "2026-06-01,tile imports,product,hs_code_volume_change,240000,TND,value,TN\n"
    )
    upload = await client.post(
        f"/api/v1/sources/{created.json()['id']}/upload-csv",
        headers=admin_headers,
        files={"file": ("tnd.csv", io.BytesIO(csv.encode()), "text/csv")},
    )
    assert upload.status_code == 200, upload.text
    await client.post(f"/api/v1/sources/{created.json()['id']}/run", headers=admin_headers)

    from app.models.models import SignalObservation

    rows = (await session.execute(__import__("sqlalchemy").select(SignalObservation))).scalars().all()
    assert rows
    assert {r.currency for r in rows} == {"TND"}
    assert {r.value for r in rows} == {200000.0, 240000.0}, "amounts are stored as reported"


async def test_bad_currency_code_is_refused(client, admin_headers):
    created = await client.post(
        "/api/v1/sources",
        headers=admin_headers,
        json={"slug": "badcur", "name": "Bad currency", "adapter_key": "csv_import", "config": {}},
    )
    csv = "observed_at,entity_name,signal_type,value,currency\n2026-05-01,thing,import_growth,10,DINARS\n"
    resp = await client.post(
        f"/api/v1/sources/{created.json()['id']}/upload-csv",
        headers=admin_headers,
        files={"file": ("bad.csv", io.BytesIO(csv.encode()), "text/csv")},
    )
    assert resp.status_code == 422
    assert "ISO-4217" in resp.json()["detail"]


# ------------------------------------------------------------- entity review
async def _make_candidate(session):
    from app.services.entities import resolve_entity

    await resolve_entity(session, "OpenAI", "company")
    created = await resolve_entity(session, "Open AI", "company")
    await session.commit()
    return created.candidate


async def test_review_queue_lists_pending_matches(client, admin_headers, session):
    await _make_candidate(session)
    resp = await client.get("/api/v1/entity-review", headers=admin_headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["observed_name"] == "Open AI"
    assert rows[0]["candidate_entity_name"] == "OpenAI"
    assert rows[0]["decision"] == "pending"
    assert rows[0]["confidence"] >= 0.9


async def test_a_decision_is_recorded_and_cannot_be_silently_overwritten(client, admin_headers, session):
    candidate = await _make_candidate(session)
    first = await client.post(
        f"/api/v1/entity-review/{candidate.id}",
        headers=admin_headers,
        json={"decision": "confirmed", "note": "same organisation"},
    )
    assert first.status_code == 200
    assert first.json()["decision"] == "confirmed"
    assert first.json()["decided_at"]

    second = await client.post(
        f"/api/v1/entity-review/{candidate.id}",
        headers=admin_headers,
        json={"decision": "rejected"},
    )
    assert second.status_code == 409
    assert "already decided" in second.json()["detail"]


async def test_only_an_admin_may_decide(client, viewer_headers, session):
    candidate = await _make_candidate(session)
    resp = await client.post(
        f"/api/v1/entity-review/{candidate.id}",
        headers=viewer_headers,
        json={"decision": "confirmed"},
    )
    assert resp.status_code == 403


async def test_topics_endpoint_lists_members(client, admin_headers):
    await _seed_scenarios(client, admin_headers, scenarios=("ai_coding_agents",))
    resp = await client.get("/api/v1/topics", headers=admin_headers)
    assert resp.status_code == 200
    topics = resp.json()
    assert topics
    assert topics[0]["entity_names"]
    assert topics[0]["label_is_ai_generated"] is False


async def test_explanation_is_dropped_when_no_model_is_configured(client, admin_headers):
    """The offline provider invents nothing, so no prose is stored - and no error."""
    await _seed_scenarios(client, admin_headers, scenarios=("ai_coding_agents",))
    listing = await client.get("/api/v1/trends?sort=score", headers=admin_headers)
    trend_id = listing.json()["items"][0]["id"]

    resp = await client.post(f"/api/v1/trends/{trend_id}/explain", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["explanation"] is None
    assert resp.json()["trend_score"] > 0, "the numbers stand without the prose"


async def test_trends_require_authentication(client):
    assert (await client.get("/api/v1/trends")).status_code == 401
    assert (await client.get("/api/v1/entity-review")).status_code == 401
