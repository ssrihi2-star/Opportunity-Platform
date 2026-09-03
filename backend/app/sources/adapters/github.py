"""GitHub repository activity.

Signals produced per configured repository:
  * `github_stars`, `github_forks`, `github_issue_velocity` - daily point-in-time
    snapshots from the repository endpoint. These are levels, not history: GitHub
    does not expose a star time series, so the series is built up one day at a
    time by repeated collection. That limitation is stated rather than papered over.
  * `github_commit_velocity` - a real 52-week history from `stats/commit_activity`,
    so this signal has depth from the first run.

Config:
    {"repos": ["owner/name", ...], "weeks": 52}
Credential (optional but strongly recommended):
    "token" - a fine-grained PAT with public read access. Without it GitHub allows
    60 requests/hour per IP, which is not enough for more than a couple of repos.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.errors import PartialFetchError, SourceConfigError, SourceUnavailableError
from app.sources.base import BaseDataSource, RawSignal, SourceHealth
from app.sources.registry import register
from app.sources.series import build_series

API = "https://api.github.com"


@register("github")
class GitHubSource(BaseDataSource):
    """GitHub REST API: stars, forks, issues and commit velocity per repository."""

    requires_network = True
    respect_robots = False  # documented JSON API with published terms of service
    default_rate_limit_per_minute = 60
    default_source_class = "primary_api"
    documented_rate_limit = "5,000 requests/hour authenticated; 60/hour anonymous"

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        token = self.credentials.get("token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def fetch(self, since: datetime | None = None) -> list[RawSignal]:
        repos = self.cfg("repos", required=True)
        if isinstance(repos, str):
            repos = [repos]
        weeks = int(self.cfg("weeks", 52))
        now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

        out: list[RawSignal] = []
        failures: list[str] = []

        for full_name in repos:
            if "/" not in full_name:
                raise SourceConfigError(f"repos entries must look like 'owner/name'; got {full_name!r}.")
            try:
                out.extend(await self._collect_repo(full_name, now, weeks, since))
            except SourceUnavailableError as exc:
                # One unreachable repo must not lose the others.
                failures.append(f"{full_name}: {exc}")

        if failures and not out:
            raise SourceUnavailableError("; ".join(failures))
        if failures:
            raise PartialFetchError(
                f"{len(failures)} of {len(repos)} repositories failed: " + "; ".join(failures),
                records=out,
            )
        return out

    async def _collect_repo(
        self, full_name: str, now: datetime, weeks: int, since: datetime | None
    ) -> list[RawSignal]:
        headers = self._headers()
        repo = await self.fetcher.get_json(f"{API}/repos/{full_name}", headers=headers)
        records: list[RawSignal] = []

        if repo is not None:  # None means 304 Not Modified - nothing changed today
            url = repo.get("html_url") or f"https://github.com/{full_name}"
            levels = {
                "github_stars": (repo.get("stargazers_count"), "stars"),
                "github_forks": (repo.get("forks_count"), "forks"),
                "github_issue_velocity": (repo.get("open_issues_count"), "open_issues"),
            }
            for signal_type, (value, unit) in levels.items():
                if value is None:
                    continue
                records.extend(
                    build_series(
                        points=[(now, float(value))],
                        entity_name=full_name,
                        entity_type="repository",
                        signal_type=signal_type,
                        source_prefix="github",
                        unit=unit,
                        confidence=0.85,
                        url=url,
                        payload_extra={"repo": full_name, "measurement": "point_in_time"},
                    )
                )

        activity = await self.fetcher.get_json(
            f"{API}/repos/{full_name}/stats/commit_activity", headers=headers
        )
        # GitHub answers 202 with an empty body while it computes statistics.
        if isinstance(activity, list) and activity:
            cutoff = now - timedelta(weeks=weeks)
            points = []
            for bucket in activity:
                week_start = datetime.fromtimestamp(int(bucket["week"]), tz=UTC)
                if week_start < cutoff:
                    continue
                if since is not None and week_start <= since - timedelta(days=7):
                    continue
                points.append((week_start, float(bucket.get("total", 0))))
            if points:
                records.extend(
                    build_series(
                        points=points,
                        entity_name=full_name,
                        entity_type="repository",
                        signal_type="github_commit_velocity",
                        source_prefix="github",
                        unit="commits_per_week",
                        confidence=0.85,
                        url=f"https://github.com/{full_name}",
                        payload_extra={"repo": full_name, "measurement": "weekly_total"},
                    )
                )
        return records

    async def health_check(self) -> SourceHealth:
        data = await self.fetcher.get_json(f"{API}/rate_limit", headers=self._headers())
        if not data:
            return SourceHealth(healthy=True, detail="Rate limit endpoint returned no change.")
        core = data.get("resources", {}).get("core", {})
        remaining = core.get("remaining")
        authenticated = bool(self.credentials.get("token"))
        return SourceHealth(
            healthy=bool(remaining),
            detail=(
                f"{'Authenticated' if authenticated else 'Anonymous'} access; "
                f"{remaining} of {core.get('limit')} core requests remaining."
            ),
            quota_remaining=remaining,
        )
