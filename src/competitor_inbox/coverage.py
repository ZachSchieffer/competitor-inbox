"""Complete coverage tables, quadrant counts, and the early-data gate."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Iterable, Mapping, Sequence

from .schema import MessageScope, NormalizedMessage


@dataclass(slots=True)
class CoverageRow:
    brand: str
    raw_fetched: int
    parse_failures: int
    parsed_input: int
    variants_collapsed: int
    distinct_messages: int
    lifecycle: int
    uncertain: int
    qualified_broadcasts: int
    evergreen_content: int
    everyday_promotion: int
    seasonal_promotion: int
    seasonal_content: int
    unclassified_broadcasts: int
    first_observed_date: str
    last_observed_date: str
    observed_days: int
    observed_weeks: int
    months_represented: int
    source_completeness: str
    ingestion_errors: int
    hook_gate_status: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class CoverageTable:
    rows: list[CoverageRow]
    total: CoverageRow

    def all_rows(self) -> list[CoverageRow]:
        return [*self.rows, self.total]


@dataclass(slots=True)
class EarlyGateResult:
    passed: bool
    total_qualified_broadcasts: int
    brand_count: int
    near_eligible_brands: list[str]
    closest_brand: str | None
    reasons: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _coerce(value: NormalizedMessage | Mapping[str, object]) -> NormalizedMessage:
    return value if isinstance(value, NormalizedMessage) else NormalizedMessage.from_dict(value)


def _quadrant(record: NormalizedMessage) -> str | None:
    if record.scope != MessageScope.BROADCAST.value or record.seasonal is None:
        return None
    has_offer = bool(record.primary_offer or record.offer_candidates)
    if record.seasonal and has_offer:
        return "seasonal_promotion"
    if record.seasonal:
        return "seasonal_content"
    if has_offer:
        return "everyday_promotion"
    return "evergreen_content"


def _date_range(records: Sequence[NormalizedMessage]) -> tuple[str, str, int, int, int]:
    trusted_records = [record for record in records if record.received_at_trusted]
    if not trusted_records:
        return "", "", 0, 0, 0
    days = sorted(record.received_datetime.date() for record in trusted_records)
    first, last = days[0], days[-1]
    observed_days = (last - first).days + 1
    observed_weeks = (observed_days + 6) // 7
    months = len({(day.year, day.month) for day in days})
    return first.isoformat(), last.isoformat(), observed_days, observed_weeks, months


def _build_row(
    brand: str,
    records: Sequence[NormalizedMessage],
    *,
    parse_failures: int,
    raw_fetched: int | None,
    ingestion_errors: int,
    is_total: bool = False,
) -> CoverageRow:
    parsed_input = sum(record.variant_count for record in records)
    variants_collapsed = max(0, parsed_input - len(records))
    lifecycle = sum(record.scope == MessageScope.LIFECYCLE.value for record in records)
    uncertain = sum(record.scope == MessageScope.UNCERTAIN.value for record in records)
    broadcasts = [record for record in records if record.scope == MessageScope.BROADCAST.value]
    date_source = broadcasts or list(records)
    first, last, observed_days, observed_weeks, months = _date_range(date_source)
    quadrant_counts = {
        "evergreen_content": 0,
        "everyday_promotion": 0,
        "seasonal_promotion": 0,
        "seasonal_content": 0,
    }
    unclassified = 0
    for record in broadcasts:
        quadrant = _quadrant(record)
        if quadrant:
            quadrant_counts[quadrant] += 1
        else:
            unclassified += 1

    raw_count = raw_fetched if raw_fetched is not None else parsed_input + parse_failures
    completeness = "complete" if parse_failures == 0 and ingestion_errors == 0 else "partial"
    if is_total:
        hook_status = "summary"
    elif len(broadcasts) >= 30 and observed_days >= 90 and completeness == "complete":
        hook_status = "eligible"
    elif len(broadcasts) >= 15 and observed_days >= 45:
        hook_status = "within_50_percent"
    else:
        hook_status = "insufficient"

    return CoverageRow(
        brand=brand,
        raw_fetched=raw_count,
        parse_failures=parse_failures,
        parsed_input=parsed_input,
        variants_collapsed=variants_collapsed,
        distinct_messages=len(records),
        lifecycle=lifecycle,
        uncertain=uncertain,
        qualified_broadcasts=len(broadcasts),
        evergreen_content=quadrant_counts["evergreen_content"],
        everyday_promotion=quadrant_counts["everyday_promotion"],
        seasonal_promotion=quadrant_counts["seasonal_promotion"],
        seasonal_content=quadrant_counts["seasonal_content"],
        unclassified_broadcasts=unclassified,
        first_observed_date=first,
        last_observed_date=last,
        observed_days=observed_days,
        observed_weeks=observed_weeks,
        months_represented=months,
        source_completeness=completeness,
        ingestion_errors=ingestion_errors,
        hook_gate_status=hook_status,
    )


def build_coverage_table(
    records: Iterable[NormalizedMessage | Mapping[str, object]],
    *,
    parse_failures_by_brand: Mapping[str, int] | None = None,
    raw_fetched_by_brand: Mapping[str, int] | None = None,
    ingestion_errors_by_brand: Mapping[str, int] | None = None,
) -> CoverageTable:
    parsed_records = [_coerce(value) for value in records]
    failures = dict(parse_failures_by_brand or {})
    raw_counts = dict(raw_fetched_by_brand or {})
    errors = dict(ingestion_errors_by_brand or {})
    grouped: dict[str, list[NormalizedMessage]] = {}
    for record in parsed_records:
        grouped.setdefault(record.brand or "Unknown Brand", []).append(record)

    brands = set(grouped) | set(failures) | set(raw_counts) | set(errors)
    rows = [
        _build_row(
            brand,
            grouped.get(brand, []),
            parse_failures=failures.get(brand, 0),
            raw_fetched=raw_counts.get(brand),
            ingestion_errors=errors.get(brand, 0),
        )
        for brand in sorted(brands, key=str.casefold)
    ]
    total_failures = sum(row.parse_failures for row in rows)
    total_errors = sum(row.ingestion_errors for row in rows)
    supplied_raw = sum(row.raw_fetched for row in rows) if rows else None
    total = _build_row(
        "TOTAL",
        parsed_records,
        parse_failures=total_failures,
        raw_fetched=supplied_raw,
        ingestion_errors=total_errors,
        is_total=True,
    )
    return CoverageTable(rows=rows, total=total)


def evaluate_early_data_gate(table: CoverageTable) -> EarlyGateResult:
    total = table.total.qualified_broadcasts
    near = [
        row.brand
        for row in table.rows
        if row.qualified_broadcasts >= 15 and row.observed_days >= 45
    ]
    reasons: list[str] = []
    if total < 300:
        reasons.append(f"total qualified broadcasts {total} is below 300")
    if not near:
        reasons.append("no brand has at least 15 qualified broadcasts across at least 45 observed days")

    closest: str | None = None
    if table.rows:
        closest = max(
            table.rows,
            key=lambda row: min(
                row.qualified_broadcasts / 15,
                row.observed_days / 45,
            ),
        ).brand
    return EarlyGateResult(
        passed=not reasons,
        total_qualified_broadcasts=total,
        brand_count=len([row for row in table.rows if row.distinct_messages]),
        near_eligible_brands=near,
        closest_brand=closest,
        reasons=reasons,
    )


def assert_coverage_cross_foot(table: CoverageTable, *, require_quadrants: bool = False) -> None:
    for row in table.all_rows():
        if row.raw_fetched != row.parse_failures + row.parsed_input:
            raise AssertionError(f"{row.brand}: raw does not equal failures plus parsed input")
        if row.parsed_input != row.variants_collapsed + row.distinct_messages:
            raise AssertionError(f"{row.brand}: parsed input does not equal variants plus distinct")
        if row.distinct_messages != row.qualified_broadcasts + row.lifecycle + row.uncertain:
            raise AssertionError(f"{row.brand}: distinct messages do not cross-foot by scope")
        quadrant_total = (
            row.evergreen_content
            + row.everyday_promotion
            + row.seasonal_promotion
            + row.seasonal_content
        )
        if require_quadrants and row.unclassified_broadcasts:
            raise AssertionError(f"{row.brand}: broadcasts remain unclassified")
        if quadrant_total + row.unclassified_broadcasts != row.qualified_broadcasts:
            raise AssertionError(f"{row.brand}: broadcast quadrants do not cross-foot")


def coverage_markdown(table: CoverageTable) -> str:
    headers = (
        "Brand",
        "Raw",
        "Failures",
        "Parsed",
        "Collapsed",
        "Distinct",
        "Lifecycle",
        "Uncertain",
        "Broadcasts",
        "Evergreen",
        "Everyday promo",
        "Seasonal promo",
        "Seasonal content",
        "First",
        "Last",
        "Days",
        "Weeks",
        "Months",
        "Completeness",
        "Errors",
        "Hook gate",
    )
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in table.all_rows():
        values = (
            row.brand,
            row.raw_fetched,
            row.parse_failures,
            row.parsed_input,
            row.variants_collapsed,
            row.distinct_messages,
            row.lifecycle,
            row.uncertain,
            row.qualified_broadcasts,
            row.evergreen_content,
            row.everyday_promotion,
            row.seasonal_promotion,
            row.seasonal_content,
            row.first_observed_date,
            row.last_observed_date,
            row.observed_days,
            row.observed_weeks,
            row.months_represented,
            row.source_completeness,
            row.ingestion_errors,
            row.hook_gate_status,
        )
        lines.append("| " + " | ".join(str(value).replace("|", "\\|") for value in values) + " |")
    return "\n".join(lines)


def global_quadrant_table(table: CoverageTable) -> list[dict[str, object]]:
    total = table.total.qualified_broadcasts
    rows = (
        ("Evergreen content", table.total.evergreen_content),
        ("Everyday promotion", table.total.everyday_promotion),
        ("Seasonal promotion", table.total.seasonal_promotion),
        ("Seasonal content", table.total.seasonal_content),
    )
    date_range = " to ".join(
        value for value in (table.total.first_observed_date, table.total.last_observed_date) if value
    )
    output = [
        {
            "quadrant": label,
            "count": count,
            "percentage": round(count / total * 100, 1) if total else 0.0,
            "total_denominator": total,
            "date_range": date_range,
        }
        for label, count in rows
    ]
    output.append(
        {
            "quadrant": "Total",
            "count": total,
            "percentage": 100.0 if total else 0.0,
            "total_denominator": total,
            "date_range": date_range,
        }
    )
    return output
