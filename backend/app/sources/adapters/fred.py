"""FRED (Federal Reserve Bank of St. Louis) macroeconomic series.

Macro series are context, not opportunities. They matter here because a rate
change, a producer-price move or a freight index turning is exactly the kind of
structural catalyst the product is supposed to notice - and because they are
official, revised, dated data, which makes them among the most reliable inputs
the system has.

Config: {"series": [{"id": "PCU3272103272101", "entity_name": "US cement prices",
                     "signal_type": "producer_price_index", "geo_scope": "US",
                     "entity_type": "indicator"}],
         "days": 1825}
Credential: "api_key" - free from https://fred.stlouisfed.org/docs/api/api_key.html
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.errors import PartialFetchError, SourceConfigError, SourceUnavailableError
from app.sources.base import BaseDataSource, RawSignal, SourceHealth
from app.sources.registry import register
from app.sources.series import build_gap, build_series

API = "https://api.stlouisfed.org/fred"


@register("fred")
class FredSource(BaseDataSource):
    """FRED API: official macro and price series with full revision history."""

    requires_network = True
    respect_robots = False  # documented JSON API
    requires_credentials = ("api_key",)
    default_rate_limit_per_minute = 60
    default_source_class = "official"
    documented_rate_limit = "120 requests/minute per API key"

    async def fetch(self, since: datetime | None = None) -> list[RawSignal]:
        series = self.cfg("series", required=True)
        if isinstance(series, dict):
            series = [series]
        days = int(self.cfg("days", 1825))
        start = datetime.now(UTC) - timedelta(days=days)
        if since is not None:
            start = max(start, since if since.tzinfo else since.replace(tzinfo=UTC))

        out: list[RawSignal] = []
        failures: list[str] = []
        for spec in series:
            try:
                out.extend(await self._collect_series(spec, start))
            except SourceUnavailableError as exc:
                failures.append(f"{spec.get('id')}: {exc}")

        if failures and not out:
            raise SourceUnavailableError("; ".join(failures))
        if failures:
            raise PartialFetchError("; ".join(failures), records=out)
        return out

    async def _collect_series(self, spec: dict, start: datetime) -> list[RawSignal]:
        series_id = spec.get("id")
        if not series_id:
            raise SourceConfigError("Every entry in `series` needs an `id` (the FRED series id).")
        data = await self.fetcher.get_json(
            f"{API}/series/observations",
            params={
                "series_id": series_id,
                "api_key": self.credentials["api_key"],
                "file_type": "json",
                "observation_start": start.strftime("%Y-%m-%d"),
            },
        )
        if data is None:
            return []
        if "error_message" in data:
            raise SourceUnavailableError(f"FRED rejected the request: {data['error_message']}")

        entity_name = spec.get("entity_name") or series_id
        entity_type = spec.get("entity_type") or "indicator"
        signal_type = spec.get("signal_type") or "macro_indicator"
        geo_scope = spec.get("geo_scope") or "US"

        points = []
        gaps: list[RawSignal] = []
        for row in data.get("observations", []):
            observed = datetime.strptime(row["date"], "%Y-%m-%d").replace(tzinfo=UTC)
            # FRED writes "." when a period has no figure. Recording that as a gap
            # keeps it visible to the confidence calculation; skipping it silently
            # would make a patchy series look complete.
            if row.get("value") in (None, "", "."):
                gaps.append(
                    build_gap(
                        observed_at=observed,
                        entity_name=entity_name,
                        entity_type=entity_type,
                        signal_type=signal_type,
                        source_prefix="fred",
                        geo_scope=geo_scope,
                        reason=f"FRED reports no value for {series_id} on {row['date']}.",
                    )
                )
                continue
            try:
                value = float(row["value"])
            except ValueError:
                continue
            points.append((observed, value))
        if not points:
            return gaps

        return gaps + build_series(
            points=points,
            entity_name=entity_name,
            entity_type=entity_type,
            signal_type=signal_type,
            source_prefix="fred",
            unit=spec.get("unit") or data.get("units"),
            geo_scope=geo_scope,
            currency=spec.get("currency"),
            confidence=0.95,
            url=f"https://fred.stlouisfed.org/series/{series_id}",
            payload_extra={"series_id": series_id, "fred_units": data.get("units")},
            is_proxy=False,
        )

    async def health_check(self) -> SourceHealth:
        data = await self.fetcher.get_json(
            f"{API}/series",
            params={"series_id": "GDP", "api_key": self.credentials["api_key"], "file_type": "json"},
        )
        if data is None:
            return SourceHealth(healthy=True, detail="No change since last check.")
        if "error_message" in data:
            return SourceHealth(healthy=False, detail=f"FRED error: {data['error_message']}")
        return SourceHealth(healthy=True, detail="FRED API key accepted.")
