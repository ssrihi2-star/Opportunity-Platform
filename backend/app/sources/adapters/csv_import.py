"""Manual CSV import.

For data the user is entitled to use but that has no compliant API: customs
spreadsheets, marketplace exports, supplier price lists, field notes. Rows are
supplied either already parsed (`rows`) or as raw text (`csv_text`), the latter
being what the upload endpoint stores.

Expected columns:
    observed_at, entity_name, signal_type, value
Optional:
    entity_type, previous_value, unit, geo_scope, url, note, is_proxy, confidence,
    signal_class
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

from app.analytics.signal_types import class_of
from app.core.errors import SourceConfigError
from app.sources.base import BaseDataSource, RawSignal, SourceHealth
from app.sources.registry import register

REQUIRED_COLUMNS = {"observed_at", "entity_name", "signal_type", "value"}
KNOWN_COLUMNS = REQUIRED_COLUMNS | {
    "entity_type",
    "previous_value",
    "unit",
    "geo_scope",
    "url",
    "note",
    "is_proxy",
    "confidence",
    "signal_class",
    "currency",
    "status",
}
VALID_STATUSES = {"ok", "missing", "failed"}
MAX_ROWS = 50_000


def parse_rows(csv_text: str) -> list[dict[str, str]]:
    """Parse and validate the header. Raises with the exact problem, never guesses."""
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        raise ValueError("The CSV file has no header row.")
    header = {c.strip() for c in reader.fieldnames if c}
    missing = REQUIRED_COLUMNS - header
    if missing:
        raise ValueError(
            f"CSV is missing required column(s): {', '.join(sorted(missing))}. "
            f"Required columns are: {', '.join(sorted(REQUIRED_COLUMNS))}."
        )
    unknown = header - KNOWN_COLUMNS
    rows = [{k.strip(): (v or "").strip() for k, v in row.items() if k} for row in reader]
    if len(rows) > MAX_ROWS:
        raise ValueError(f"CSV has {len(rows)} rows; the limit is {MAX_ROWS}. Split the file.")
    _validate_rows(rows)
    if unknown:
        # Not fatal, but the operator should know a column is being ignored.
        for row in rows:
            row["_ignored_columns"] = ",".join(sorted(unknown))
    return rows


def _validate_rows(rows: list[dict[str, str]]) -> None:
    """Check every row at upload time, so a mistake surfaces now, not at 3am."""
    for index, row in enumerate(rows, start=2):  # header is line 1
        status = (row.get("status") or "ok").strip().lower()
        if status not in VALID_STATUSES:
            raise ValueError(
                f"Row {index}: 'status' must be one of {sorted(VALID_STATUSES)}, got {row.get('status')!r}."
            )
        currency = (row.get("currency") or "").strip()
        if currency and len(currency) != 3:
            raise ValueError(f"Row {index}: 'currency' must be a 3-letter ISO-4217 code, got {currency!r}.")
        if not (row.get("entity_name") and row.get("signal_type")):
            raise ValueError(f"Row {index}: 'entity_name' and 'signal_type' cannot be empty.")
        try:
            datetime.fromisoformat(row.get("observed_at", ""))
        except ValueError as exc:
            raise ValueError(
                f"Row {index}: 'observed_at' must be an ISO-8601 date, got {row.get('observed_at')!r}."
            ) from exc
        if status == "ok":
            try:
                float(row.get("value", ""))
            except ValueError as exc:
                raise ValueError(
                    f"Row {index}: 'value' must be numeric, got {row.get('value')!r}. "
                    "Use status=missing if the period has no figure."
                ) from exc


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


@register("csv_import")
class CsvImportSource(BaseDataSource):
    """Operator-supplied CSV, for data with no compliant API."""

    requires_network = False
    default_source_class = "manual_import"
    documented_rate_limit = "not applicable (local file)"

    async def fetch(self, since: datetime | None = None) -> list[RawSignal]:
        rows = self.config.get("rows")
        if rows is None:
            csv_text = self.config.get("csv_text", "")
            if not csv_text.strip():
                return []
            rows = parse_rows(csv_text)

        if since is not None and since.tzinfo is None:
            since = since.replace(tzinfo=UTC)

        out: list[RawSignal] = []
        for index, row in enumerate(rows, start=2):  # header is line 1
            try:
                observed_at = datetime.fromisoformat(row["observed_at"])
            except (KeyError, ValueError) as exc:
                raise SourceConfigError(
                    f"Row {index}: 'observed_at' must be an ISO-8601 date, got {row.get('observed_at')!r}."
                ) from exc
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=UTC)
            if since is not None and observed_at <= since:
                continue
            status = (row.get("status") or "ok").strip().lower()
            if status not in VALID_STATUSES:
                raise SourceConfigError(
                    f"Row {index}: 'status' must be one of {sorted(VALID_STATUSES)}, "
                    f"got {row.get('status')!r}."
                )
            value: float | None
            if status != "ok":
                # A row can say "this period was not reported" without inventing a
                # number for it.
                value = None
            else:
                try:
                    value = float(row["value"])
                except (KeyError, ValueError) as exc:
                    raise SourceConfigError(
                        f"Row {index}: 'value' must be numeric, got {row.get('value')!r}. "
                        "Use status=missing if the period has no figure."
                    ) from exc

            currency = (row.get("currency") or "").strip().upper() or None
            if currency and len(currency) != 3:
                raise SourceConfigError(
                    f"Row {index}: 'currency' must be a 3-letter ISO-4217 code, got {currency!r}."
                )

            previous = row.get("previous_value")
            entity = row["entity_name"]
            signal_type = row["signal_type"]
            if not entity or not signal_type:
                raise SourceConfigError(f"Row {index}: 'entity_name' and 'signal_type' cannot be empty.")
            try:
                confidence = float(row.get("confidence") or 0.7)
            except ValueError as exc:
                raise SourceConfigError(
                    f"Row {index}: 'confidence' must be a number between 0 and 1."
                ) from exc

            out.append(
                RawSignal(
                    external_id=f"csv|{entity}|{signal_type}|{observed_at.date().isoformat()}",
                    title=f"{entity} - {signal_type}",
                    content=row.get("note") or None,
                    url=row.get("url") or None,
                    published_at=observed_at,
                    metric_name=signal_type,
                    metric_value=value,
                    metric_unit=row.get("unit") or None,
                    metric_currency=currency,
                    status=status,
                    previous_value=float(previous) if previous else None,
                    entity_name=entity,
                    entity_type=row.get("entity_type") or "keyword",
                    signal_type=signal_type,
                    signal_class=row.get("signal_class") or class_of(signal_type),
                    is_proxy=_parse_bool(row.get("is_proxy", "")),
                    geo_scope=row.get("geo_scope") or "global",
                    confidence=min(max(confidence, 0.0), 1.0),
                    payload={"row": row, "line": index},
                )
            )
        return out

    async def health_check(self) -> SourceHealth:
        has_data = bool(self.config.get("rows") or self.config.get("csv_text"))
        return SourceHealth(
            healthy=True,
            detail="CSV payload present." if has_data else "No CSV payload uploaded yet.",
        )
