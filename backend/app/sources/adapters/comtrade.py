"""UN Comtrade: official import/export volumes by HS commodity code.

This is the backbone of the import-opportunity work: it is the only free, global,
official source that answers "is this product moving into this country, and is
the flow growing".

Honest limitations, stated here because they change how the data must be read:
  * The legacy open endpoint was retired. The current API needs a free
    subscription key, supplied as the `subscription_key` credential.
  * Reporting lags one to six months depending on the country, and some countries
    report annually only. Observations are dated to the period they describe, not
    the day they were published, so the trend engine sees the real timeline.
  * Tunisia and Libya report irregularly. Where a country does not report, the
    usual technique is to read its trade partners' mirror statistics instead -
    that is what `use_mirror` does, and mirrored rows are labelled in the payload.

Config: {"flows": [{"reporter": "788", "partner": "0", "cmd_code": "8481",
                    "flow": "M", "entity_name": "sanitary valves (Tunisia imports)",
                    "geo_scope": "TN", "use_mirror": false}],
         "years": 6, "frequency": "A"}
Credential: "subscription_key"
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.errors import PartialFetchError, SourceConfigError, SourceUnavailableError
from app.sources.base import BaseDataSource, RawSignal, SourceHealth
from app.sources.registry import register
from app.sources.series import build_series

API = "https://comtradeapi.un.org/data/v1/get"


@register("un_comtrade")
class UnComtradeSource(BaseDataSource):
    """UN Comtrade API: official trade flows by HS code, reporter and partner."""

    requires_network = True
    respect_robots = False  # documented JSON API
    requires_credentials = ("subscription_key",)
    default_rate_limit_per_minute = 10
    default_source_class = "official"
    documented_rate_limit = "free tier: 500 calls/day, 100 calls/hour (subject to UN terms)"

    async def fetch(self, since: datetime | None = None) -> list[RawSignal]:
        flows = self.cfg("flows", required=True)
        if isinstance(flows, dict):
            flows = [flows]
        years = int(self.cfg("years", 6))
        frequency = self.cfg("frequency", "A")
        current_year = datetime.now(UTC).year
        periods = ",".join(str(y) for y in range(current_year - years, current_year + 1))

        out: list[RawSignal] = []
        failures: list[str] = []
        for flow in flows:
            try:
                out.extend(await self._collect_flow(flow, periods, frequency))
            except SourceUnavailableError as exc:
                failures.append(f"{flow.get('entity_name') or flow.get('cmd_code')}: {exc}")

        if failures and not out:
            raise SourceUnavailableError("; ".join(failures))
        if failures:
            raise PartialFetchError("; ".join(failures), records=out)
        return out

    async def _collect_flow(self, flow: dict, periods: str, frequency: str) -> list[RawSignal]:
        cmd_code = flow.get("cmd_code")
        reporter = flow.get("reporter")
        if not cmd_code or not reporter:
            raise SourceConfigError(
                "Every entry in `flows` needs `cmd_code` (HS code) and `reporter` (M49 country code)."
            )
        use_mirror = bool(flow.get("use_mirror"))
        direction = flow.get("flow", "M")
        # Mirror statistics: ask the partners what they exported to this country,
        # which is how you see a market that does not publish its own imports.
        if use_mirror:
            reporter_param, partner_param = flow.get("partner", "0"), reporter
            direction = "X" if direction == "M" else "M"
        else:
            reporter_param, partner_param = reporter, flow.get("partner", "0")

        data = await self.fetcher.get_json(
            f"{API}/C/{frequency}/HS",
            params={
                "reporterCode": reporter_param,
                "partnerCode": partner_param,
                "cmdCode": cmd_code,
                "flowCode": direction,
                "period": periods,
                "subscription-key": self.credentials["subscription_key"],
            },
        )
        if data is None:
            return []
        rows = data.get("data")
        if rows is None:
            message = data.get("errorMessage") or data.get("message") or "no `data` field in response"
            raise SourceUnavailableError(f"Comtrade returned no data ({message}).")
        if not rows:
            return []

        metric = flow.get("metric", "netWgt")  # net weight; use "primaryValue" for value
        is_money = metric != "netWgt"
        unit = "kg" if not is_money else "value"
        # Comtrade reports value in USD. It is stored *as USD*, never converted, so
        # a later TND or CNY figure from another source cannot be silently mixed in.
        currency = "USD" if is_money else None
        points = []
        for row in rows:
            period = str(row.get("period") or "")
            value = row.get(metric)
            if value in (None, 0) or not period:
                continue
            try:
                observed = (
                    datetime(int(period), 12, 31, tzinfo=UTC)
                    if len(period) == 4
                    else datetime(int(period[:4]), int(period[4:6]), 28, tzinfo=UTC)
                )
            except ValueError:
                continue
            points.append((observed, float(value)))
        if not points:
            return []

        signal_type = flow.get("signal_type") or (
            "import_growth" if flow.get("flow", "M") == "M" else "export_growth"
        )
        return build_series(
            points=points,
            entity_name=flow.get("entity_name") or f"HS {cmd_code}",
            entity_type=flow.get("entity_type") or "product",
            signal_type=signal_type,
            source_prefix="comtrade",
            unit=unit,
            currency=currency,
            geo_scope=flow.get("geo_scope") or "global",
            confidence=0.9 if not use_mirror else 0.75,
            url="https://comtradeplus.un.org/",
            payload_extra={
                "cmd_code": cmd_code,
                "reporter": reporter,
                "mirrored": use_mirror,
                "metric": metric,
                "note": (
                    "Mirror statistics: partner-reported exports used as a stand-in for unreported imports."
                    if use_mirror
                    else "Directly reported by the country."
                ),
            },
        )

    async def health_check(self) -> SourceHealth:
        flows = self.config.get("flows") or []
        if not flows:
            return SourceHealth(healthy=False, detail="No flows configured.")
        year = datetime.now(UTC).year - 2
        data = await self.fetcher.get_json(
            f"{API}/C/A/HS",
            params={
                "reporterCode": flows[0].get("reporter"),
                "partnerCode": "0",
                "cmdCode": flows[0].get("cmd_code"),
                "flowCode": "M",
                "period": str(year),
                "subscription-key": self.credentials["subscription_key"],
            },
        )
        if data is None:
            return SourceHealth(healthy=True, detail="No change since last check.")
        rows = data.get("data")
        return SourceHealth(
            healthy=rows is not None,
            detail=(
                f"Comtrade key accepted; probe returned {len(rows)} rows for {year}."
                if rows is not None
                else f"Comtrade responded without data: {data.get('errorMessage', 'unknown error')}"
            ),
        )
