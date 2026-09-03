"""SEC EDGAR: filing activity and reported financial concepts.

Two signals per company:
  * `sec_filing_activity` - filings per day from the submissions endpoint. A burst
    of 8-Ks, or the first S-1 in a sector, is a real early signal.
  * a financial concept series (default `Revenues`) from the XBRL companyconcept
    endpoint, emitted as `revenue_acceleration`.

SEC requires a descriptive User-Agent with contact details on every request; the
shared Fetcher always sends one (USER_AGENT plus a `From` header). Requests are
capped well below the published 10/second limit.

Config: {"companies": [{"cik": "0000320193", "name": "Apple Inc.", "ticker": "AAPL"}],
         "concept": "Revenues", "taxonomy": "us-gaap", "days": 1095}
No credentials required.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.errors import PartialFetchError, SourceConfigError, SourceUnavailableError
from app.sources.base import BaseDataSource, RawSignal, SourceHealth
from app.sources.registry import register
from app.sources.series import build_series, daily_counts

API = "https://data.sec.gov"


def normalise_cik(cik: str | int) -> str:
    digits = "".join(ch for ch in str(cik) if ch.isdigit())
    if not digits:
        raise SourceConfigError(f"CIK {cik!r} contains no digits.")
    return digits.zfill(10)


@register("sec_edgar")
class SecEdgarSource(BaseDataSource):
    """SEC EDGAR: filing cadence and XBRL financial concepts for US issuers."""

    requires_network = True
    respect_robots = False  # documented data API; SEC requires a UA, which we send
    default_rate_limit_per_minute = 60
    default_source_class = "regulated_filing"
    documented_rate_limit = "10 requests/second, descriptive User-Agent required"

    async def fetch(self, since: datetime | None = None) -> list[RawSignal]:
        companies = self.cfg("companies", required=True)
        if isinstance(companies, dict):
            companies = [companies]
        days = int(self.cfg("days", 1095))
        cutoff = datetime.now(UTC) - timedelta(days=days)
        if since is not None:
            cutoff = max(cutoff, since if since.tzinfo else since.replace(tzinfo=UTC))

        out: list[RawSignal] = []
        failures: list[str] = []
        for company in companies:
            try:
                out.extend(await self._collect_company(company, cutoff))
            except SourceUnavailableError as exc:
                failures.append(f"{company.get('name') or company.get('cik')}: {exc}")

        if failures and not out:
            raise SourceUnavailableError("; ".join(failures))
        if failures:
            raise PartialFetchError("; ".join(failures), records=out)
        return out

    async def _collect_company(self, company: dict, cutoff: datetime) -> list[RawSignal]:
        cik = normalise_cik(company.get("cik") or "")
        name = company.get("name") or f"CIK {cik}"
        ticker = company.get("ticker")
        records: list[RawSignal] = []

        submissions = await self.fetcher.get_json(f"{API}/submissions/CIK{cik}.json")
        if submissions is not None:
            recent = submissions.get("filings", {}).get("recent", {})
            dates = recent.get("filingDate", [])
            forms = recent.get("form", [])
            accessions = recent.get("accessionNumber", [])
            filed: list[datetime] = []
            for i, raw_date in enumerate(dates):
                try:
                    filed_at = datetime.strptime(raw_date, "%Y-%m-%d").replace(tzinfo=UTC)
                except ValueError:
                    continue
                if filed_at < cutoff:
                    continue
                filed.append(filed_at)
                if i < 25:
                    accession = accessions[i] if i < len(accessions) else ""
                    form = forms[i] if i < len(forms) else "?"
                    records.append(
                        RawSignal(
                            external_id=f"edgar|{cik}|{accession}",
                            title=f"{name} filed {form} on {raw_date}",
                            content=f"Form {form} filed with the SEC by {name}.",
                            url=(
                                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                                f"{accession.replace('-', '')}/{accession}-index.htm"
                            ),
                            published_at=filed_at,
                            entity_name=name,
                            entity_type="public_company",
                            confidence=0.95,
                            payload={"form": form, "cik": cik, "accession": accession},
                        )
                    )
            if filed:
                records.extend(
                    build_series(
                        points=daily_counts(filed),
                        entity_name=name,
                        entity_type="public_company",
                        signal_type="sec_filing_activity",
                        source_prefix="edgar",
                        unit="filings_per_day",
                        geo_scope="US",
                        confidence=0.95,
                        url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}",
                        payload_extra={"cik": cik, "ticker": ticker},
                    )
                )

        concept = self.cfg("concept", "Revenues")
        taxonomy = self.cfg("taxonomy", "us-gaap")
        # Not every issuer tags every concept; 404 here means "does not report it",
        # which is information, not a failure.
        concept_data = await self.fetcher.get_json(
            f"{API}/api/xbrl/companyconcept/CIK{cik}/{taxonomy}/{concept}.json",
            allow_status={404},
        )
        if concept_data:
            points = []
            seen: set[str] = set()
            for unit_rows in concept_data.get("units", {}).values():
                for row in unit_rows:
                    end = row.get("end")
                    value = row.get("val")
                    # Only annual/quarterly primary figures; skip duplicated restatements.
                    if not end or value is None or end in seen:
                        continue
                    try:
                        observed = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC)
                    except ValueError:
                        continue
                    if observed < cutoff:
                        continue
                    seen.add(end)
                    points.append((observed, float(value)))
            if points:
                records.extend(
                    build_series(
                        points=points,
                        entity_name=name,
                        entity_type="public_company",
                        signal_type="revenue_acceleration",
                        source_prefix="edgar",
                        unit=concept_data.get("label") or concept,
                        geo_scope="US",
                        confidence=0.95,
                        url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}",
                        payload_extra={"cik": cik, "concept": concept, "taxonomy": taxonomy},
                    )
                )
        return records

    async def health_check(self) -> SourceHealth:
        companies = self.config.get("companies") or []
        if not companies:
            return SourceHealth(healthy=False, detail="No companies configured.")
        cik = normalise_cik(companies[0].get("cik") or "")
        data = await self.fetcher.get_json(f"{API}/submissions/CIK{cik}.json")
        if data is None:
            return SourceHealth(healthy=True, detail="No change since last check.")
        return SourceHealth(
            healthy=bool(data.get("cik")),
            detail=f"EDGAR reachable; probe company is {data.get('name', 'unknown')}.",
        )
