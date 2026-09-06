"""Container health probes.

Two defects are covered here:

  * Docker polled `/api/v1/health` every 30s from the container gateway
    address. `RedisLimiter.allow()` runs INCR + EXPIRE(60) on *every* call, so
    a 30s interval inside a 60s window kept pushing the TTL out and the counter
    never reset: the bucket saturated after 240 probes (two hours of uptime)
    and the endpoint returned 429 from then on, permanently. A liveness probe
    must not be able to fail because it was polled.

  * `worker` and `beat` build from the same image as `api`, so the image-level
    HTTP HEALTHCHECK was inherited by two services that run no HTTP server.
    They are checked per-service in docker-compose.yml instead.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

from app.core.config import settings
from app.core.middleware import HEALTH_PATHS

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"
DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


# ------------------------------------------------------------------ endpoint
@pytest.mark.parametrize("path", ["/health", "/health/ready"])
async def test_probe_endpoints_are_never_rate_limited(client, monkeypatch, path):
    """Sustained polling well past the limit must never yield a 429."""
    monkeypatch.setattr(settings, "RATE_LIMIT_PER_MINUTE", 5)
    url = f"{settings.API_V1_PREFIX}{path}"

    statuses = {(await client.get(url)).status_code for _ in range(25)}

    assert 429 not in statuses, f"{url} was rate limited after repeated probes"


async def test_rate_limiting_still_applies_to_normal_routes(client, monkeypatch):
    """The exemption must not become a hole in the limiter."""
    monkeypatch.setattr(settings, "RATE_LIMIT_PER_MINUTE", 5)

    statuses = [(await client.get(f"{settings.API_V1_PREFIX}/trends")).status_code for _ in range(25)]

    assert 429 in statuses, "ordinary API routes must still be rate limited"


async def test_per_source_health_route_is_not_exempt(client, monkeypatch):
    """`/sources/{id}/health` is ordinary authenticated traffic, not a probe."""
    monkeypatch.setattr(settings, "RATE_LIMIT_PER_MINUTE", 5)
    url = f"{settings.API_V1_PREFIX}/sources/11111111-1111-1111-1111-111111111111/health"

    statuses = [(await client.get(url)).status_code for _ in range(25)]

    assert 429 in statuses, "only the exact liveness paths may bypass the limiter"


async def test_exempt_paths_are_exact():
    assert HEALTH_PATHS == {"/api/v1/health", "/api/v1/health/ready"}


# ------------------------------------------------------------------ packaging
async def test_image_declares_no_inherited_http_healthcheck():
    """worker and beat share this image and serve no HTTP."""
    text = DOCKERFILE.read_text()

    assert "HEALTHCHECK NONE" in text
    assert "curl -fsS http://localhost:8000" not in text


async def test_every_backend_service_defines_its_own_healthcheck(compose):
    for name in ("api", "worker", "beat"):
        assert compose["services"][name].get("healthcheck"), f"{name} has no healthcheck"


async def test_worker_and_beat_probes_are_not_http(compose):
    for name in ("worker", "beat"):
        probe = compose["services"][name]["healthcheck"]["test"][1]
        assert "curl" not in probe and "localhost:8000" not in probe, (
            f"{name} runs no HTTP server, so it must not be probed over HTTP"
        )


async def test_worker_probe_targets_this_containers_own_node(compose):
    """An unscoped ping passes whenever *any* worker answers the broker."""
    probe = compose["services"]["worker"]["healthcheck"]["test"][1]

    assert "inspect ping" in probe
    # $$(hostname), not $$HOSTNAME: CMD-SHELL is dash, which leaves HOSTNAME unset.
    assert "-d celery@$$(hostname)" in probe
    assert "$$HOSTNAME" not in probe


async def test_beat_probe_checks_the_pidfile_it_writes(compose):
    """Beat answers no pings and may not rewrite its schedule file for hours."""
    beat = compose["services"]["beat"]
    pidfile = "/tmp/celerybeat.pid"

    assert "--pidfile" in beat["command"]
    assert pidfile in beat["command"]
    assert pidfile in beat["healthcheck"]["test"][1]


async def test_api_probe_still_checks_http(compose):
    probe = compose["services"]["api"]["healthcheck"]["test"][1]

    assert "curl" in probe
    assert "/api/v1/health" in probe
