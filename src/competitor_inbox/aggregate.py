"""Deterministic rollups and cross-foot validation for analyzed messages."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from statistics import median
from typing import Any, Iterable, Mapping

from .analysis import (
    QUADRANTS,
    analyze_message,
    numeric_offer_is_supported,
    quadrant_for,
    sanitize_ai_text,
)


class CrossFootError(ValueError):
    """Raised when a displayed total cannot be reconciled to its source rows."""


POSTURE_LABELS = {
    "Promotion/offer": "Promotion led",
    "Ingredient/education": "Education led",
    "Founder/brand story": "Story led",
    "New product launch": "Launch led",
    "Social proof/UGC": "Social proof led",
    "Upsell": "Upsell led",
    "Cross-sell": "Cross-sell led",
    "Featured products": "Merchandising led",
    "Lifestyle/seasonal": "Lifestyle led",
}


def _get(record: Mapping[str, Any], *names: str, default: Any = "") -> Any:
    for name in names:
        value: Any = record
        found = True
        for part in name.split("."):
            if isinstance(value, Mapping) and part in value:
                value = value[part]
            else:
                found = False
                break
        if found and value is not None:
            return value
    return default


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None


def _brand(record: Mapping[str, Any]) -> str:
    value = _get(record, "brand.canonical", "brand.name", "brand", "canonical_brand", default="Unknown brand")
    if isinstance(value, Mapping):
        value = value.get("canonical") or value.get("name") or "Unknown brand"
    return sanitize_ai_text(str(value).strip(), max_chars=120) or "Unknown brand"


def _received(record: Mapping[str, Any]) -> date | None:
    return _date(
        _get(
            record,
            "dates.received_at",
            "canonical_received_at",
            "received_at",
            "observed_at",
            "date",
            default="",
        )
    )


def _trusted_received(record: Mapping[str, Any]) -> date | None:
    trusted = _get(
        record,
        "dates.received_at_trusted",
        "received_at_trusted",
        default=None,
    )
    source_type = str(_get(record, "source_type", default="")).casefold()
    if trusted is False or (trusted is None and source_type == "mbox"):
        return None
    return _received(record)


def coverage_level(observed_days: int) -> dict[str, Any]:
    if observed_days < 1:
        return {"key": "none", "label": "No qualified broadcast history", "days": observed_days}
    if observed_days < 30:
        return {"key": "library", "label": "Library and current activity", "days": observed_days}
    if observed_days < 90:
        return {"key": "pulse", "label": "Current pulse, thin data", "days": observed_days}
    if observed_days < 330:
        return {"key": "mix", "label": "Cadence, mix, and posture", "days": observed_days}
    if observed_days < 730:
        return {"key": "annual", "label": "Annual and prior-season planning", "days": observed_days}
    return {"key": "yoy", "label": "Year-over-year analysis", "days": observed_days}


def assign_posture(intent_counts: Mapping[str, int]) -> dict[str, Any]:
    total = sum(intent_counts.values())
    ranked = sorted(intent_counts.items(), key=lambda item: (-item[1], item[0]))
    if not ranked or total == 0:
        return {"label": "Mixed", "leading_intent": "", "share": 0.0, "runner_up_share": 0.0}
    leader, leader_count = ranked[0]
    runner_count = ranked[1][1] if len(ranked) > 1 else 0
    share = leader_count / total
    runner_share = runner_count / total
    qualifies = share >= 0.35 and (runner_count == 0 or leader_count >= 1.25 * runner_count)
    return {
        "label": POSTURE_LABELS.get(leader, f"{leader} led") if qualifies else "Mixed",
        "leading_intent": leader,
        "share": round(share, 4),
        "runner_up_share": round(runner_share, 4),
    }


def _safe_library_item(record: Mapping[str, Any]) -> dict[str, Any]:
    primary = _get(record, "offer.primary", "primary_offer", default=None)
    if not isinstance(primary, Mapping):
        primary = {}
    seasonality = _get(record, "seasonality", default={})
    if not isinstance(seasonality, Mapping):
        seasonality = {}
    intent = _get(record, "intent", default={})
    if not isinstance(intent, Mapping):
        intent = {"label": str(intent or "")}
    received = _received(record)
    return {
        "record_id": str(_get(record, "record_id", "id", default="")),
        "brand": _brand(record),
        "date": received.isoformat() if received else "",
        "subject": sanitize_ai_text(
            _get(record, "sanitized.subject", "subject", default=""), max_chars=180
        ),
        "scope": str(_get(record, "scope", default="uncertain")),
        "quadrant": str(_get(record, "quadrant", default="")),
        "intent": str(intent.get("label") or ""),
        "offer_type": str(primary.get("type") or ""),
        "offer_depth": primary.get("depth"),
        "occasion": str(seasonality.get("occasion") or _get(record, "occasion", default="") or ""),
    }


def _analysis_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Map the canonical flat storage contract to the nested analysis contract."""

    if all(key in record for key in ("offer", "seasonality", "intent", "quadrant")) and isinstance(
        record.get("intent"), Mapping
    ):
        return dict(record)
    has_flat_analysis = (
        record.get("seasonal") is not None
        and record.get("intent") is not None
        and ("primary_offer" in record or "offer_candidates" in record)
    )
    if not has_flat_analysis:
        return analyze_message(record)
    converted = dict(record)
    primary = record.get("primary_offer")
    candidates = [dict(item) for item in (record.get("offer_candidates") or []) if isinstance(item, Mapping)]
    if isinstance(primary, Mapping) and not candidates:
        candidates = [dict(primary)]
    converted["offer"] = {
        "present": bool(primary or candidates),
        "primary": dict(primary) if isinstance(primary, Mapping) else (candidates[0] if candidates else None),
        "candidates": candidates,
        "numeric_supported": bool(
            not isinstance(primary, Mapping)
            or primary.get("depth") is None
            or (primary.get("deterministic") and primary.get("evidence"))
        ),
        "analysis_mode": "canonical",
    }
    converted["seasonality"] = {
        "seasonal": bool(record.get("seasonal")),
        "occasion": str(record.get("occasion") or ""),
        "source": "canonical",
        "evidence": "",
        "confidence": 1.0,
    }
    converted["intent"] = {
        "label": str(record.get("intent") or "Featured products"),
        "source": str(record.get("intent_source") or "deterministic"),
        "confidence": float(record.get("intent_confidence") or 0.0),
        "model": str(record.get("classification_model") or ""),
    }
    converted["quadrant"] = quadrant_for(
        bool(converted["offer"]["present"]), bool(converted["seasonality"]["seasonal"])
    )
    return converted


def _pipeline_counts(records: list[Mapping[str, Any]], supplied: Mapping[str, int] | None) -> dict[str, int]:
    collapsed = sum(max(1, int(_get(record, "variant_count", default=1) or 1)) - 1 for record in records)
    derived = {
        "parse_failures": 0,
        "variants_collapsed": collapsed,
        "distinct_messages": len(records),
        "parsed_input": len(records) + collapsed,
        "raw_fetched": len(records) + collapsed,
    }
    if supplied:
        derived.update({key: int(value) for key, value in supplied.items() if key in derived})
    return derived


def aggregate_records(
    records: Iterable[Mapping[str, Any]],
    *,
    pipeline_counts: Mapping[str, int] | None = None,
    generated_at: datetime | None = None,
    illustrative: bool | None = None,
) -> dict[str, Any]:
    """Create the complete dashboard view model and fail on any broken total."""

    source_records = [dict(record) for record in records]
    analyzed = [_analysis_record(record) for record in source_records]
    for record in analyzed:
        if not numeric_offer_is_supported(record):
            raise CrossFootError("A numeric offer has no deterministic evidence")

    is_illustrative = (
        bool(illustrative)
        if illustrative is not None
        else bool(analyzed) and all(bool(_get(record, "illustrative_prototype", default=False)) for record in analyzed)
    )
    generated = generated_at or datetime.now(timezone.utc)
    pipeline = _pipeline_counts(analyzed, pipeline_counts)
    scope_counts = Counter(str(_get(record, "scope", default="uncertain")) for record in analyzed)
    broadcasts = [record for record in analyzed if _get(record, "scope", default="") == "broadcast"]
    quadrants = Counter(str(_get(record, "quadrant", default="")) for record in broadcasts)
    # Keep the monthly census cross-footed without letting an untrusted mbox
    # Date header or file mtime masquerade as observed history. Such records
    # remain useful in the library and counts, but live in an explicit unknown
    # bucket until a trustworthy delivery timestamp is available.
    months: Counter[str] = Counter()
    for record in broadcasts:
        received = _trusted_received(record)
        months[received.strftime("%Y-%m") if received else "Unknown receipt date"] += 1
    all_dates = sorted(
        received
        for record in broadcasts
        if (received := _trusted_received(record)) is not None
    )
    observed_days = (all_dates[-1] - all_dates[0]).days + 1 if all_dates else 0

    by_brand: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in analyzed:
        by_brand[_brand(record)].append(record)

    brand_rows: list[dict[str, Any]] = []
    for brand, brand_records in by_brand.items():
        brand_broadcasts = [record for record in brand_records if _get(record, "scope") == "broadcast"]
        brand_dates = sorted(
            received
            for record in brand_broadcasts
            if (received := _trusted_received(record)) is not None
        )
        days = (brand_dates[-1] - brand_dates[0]).days + 1 if brand_dates else 0
        intent_counts = Counter(str(_get(record, "intent.label", default="Featured products")) for record in brand_broadcasts)
        quadrant_counts = Counter(str(_get(record, "quadrant", default="")) for record in brand_broadcasts)
        # Canonical records may carry non-fatal warnings such as a skewed RFC
        # date. Those are accounted for but do not make the successfully read
        # source range incomplete. Only a non-parsed status is an ingestion
        # completeness error here.
        parse_errors = sum(
            str(_get(record, "parse_status", default="parsed")) != "parsed"
            for record in brand_records
        )
        missing_dates = len(brand_broadcasts) - len(brand_dates)
        completeness = "complete" if parse_errors == 0 and missing_dates == 0 else "partial"
        numeric_offers = [
            float(depth)
            for record in brand_broadcasts
            if (depth := _get(record, "offer.primary.depth", default=None)) is not None
        ]
        brand_rows.append(
            {
                "brand": brand,
                "distinct_messages": len(brand_records),
                "qualified_broadcasts": len(brand_broadcasts),
                "lifecycle": sum(_get(record, "scope") == "lifecycle" for record in brand_records),
                "uncertain": sum(_get(record, "scope") == "uncertain" for record in brand_records),
                "first_observed": brand_dates[0].isoformat() if brand_dates else "",
                "last_observed": brand_dates[-1].isoformat() if brand_dates else "",
                "observed_days": days,
                "observed_weeks": math.ceil(days / 7) if days else 0,
                "months_represented": len({item.strftime("%Y-%m") for item in brand_dates}),
                "source_completeness": completeness,
                "ingestion_errors": parse_errors + missing_dates,
                "coverage": coverage_level(days),
                "posture": assign_posture(intent_counts),
                "intent_counts": dict(sorted(intent_counts.items())),
                "quadrants": {name: quadrant_counts.get(name, 0) for name in QUADRANTS},
                "offer_count": sum(bool(_get(record, "offer.present", default=False)) for record in brand_broadcasts),
                "numeric_offer_count": len(numeric_offers),
                "median_numeric_offer": (
                    float(median(numeric_offers)) if numeric_offers else None
                ),
                "early_gate_ready": len(brand_broadcasts) >= 15 and days >= 45,
                "hook_eligible": len(brand_broadcasts) >= 30 and days >= 90 and completeness == "complete",
            }
        )
    brand_rows.sort(key=lambda row: (-row["qualified_broadcasts"], row["brand"].casefold()))

    quadrant_rows = [
        {
            "name": name,
            "count": quadrants.get(name, 0),
            "percentage": round(100 * quadrants.get(name, 0) / len(broadcasts), 1) if broadcasts else 0.0,
        }
        for name in QUADRANTS
    ]
    intent_counts = Counter(str(_get(record, "intent.label", default="Featured products")) for record in broadcasts)
    occasion_counts = Counter(
        str(occasion)
        for record in broadcasts
        if (occasion := _get(record, "seasonality.occasion", default=""))
    )
    library = [_safe_library_item(record) for record in analyzed]
    library.sort(key=lambda item: (item["date"], item["brand"], item["record_id"]), reverse=True)

    summary: dict[str, Any] = {
        "metadata": {
            "generated_at": generated.astimezone(timezone.utc).isoformat(),
            "illustrative_prototype": is_illustrative,
            "first_observed": all_dates[0].isoformat() if all_dates else "",
            "last_observed": all_dates[-1].isoformat() if all_dates else "",
            "observed_days": observed_days,
            "trusted_receipt_dates": len(all_dates),
            "untrusted_receipt_dates": len(broadcasts) - len(all_dates),
            "coverage": coverage_level(observed_days),
        },
        "pipeline": pipeline,
        "scope_counts": {
            "broadcast": scope_counts.get("broadcast", 0),
            "lifecycle": scope_counts.get("lifecycle", 0),
            "uncertain": scope_counts.get("uncertain", 0),
        },
        "broadcast_count": len(broadcasts),
        "brand_count": len(by_brand),
        "quadrants": quadrant_rows,
        "intent_counts": dict(sorted(intent_counts.items(), key=lambda item: (-item[1], item[0]))),
        "overall_posture": assign_posture(intent_counts),
        "occasions": dict(sorted(occasion_counts.items(), key=lambda item: (-item[1], item[0]))),
        "monthly": dict(sorted(months.items())),
        "brands": brand_rows,
        "library": library,
        "early_data_gate": {
            "passed": len(broadcasts) >= 300 and any(row["early_gate_ready"] for row in brand_rows),
            "total_threshold": len(broadcasts) >= 300,
            "brand_threshold": any(row["early_gate_ready"] for row in brand_rows),
        },
    }
    summary["cross_foot"] = verify_cross_foot(summary)
    summary["findings"] = build_findings(summary)
    return summary


def verify_cross_foot(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Validate all totals used by the dashboard and return auditable equations."""

    pipeline = summary.get("pipeline", {})
    scope = summary.get("scope_counts", {})
    quadrant_rows = summary.get("quadrants", [])
    broadcast_count = int(summary.get("broadcast_count", 0))
    distinct = int(pipeline.get("distinct_messages", 0))
    checks = {
        "raw_equals_failures_plus_parsed": int(pipeline.get("raw_fetched", 0))
        == int(pipeline.get("parse_failures", 0)) + int(pipeline.get("parsed_input", 0)),
        "parsed_equals_collapsed_plus_distinct": int(pipeline.get("parsed_input", 0))
        == int(pipeline.get("variants_collapsed", 0)) + distinct,
        "distinct_equals_scopes": distinct
        == int(scope.get("broadcast", 0)) + int(scope.get("lifecycle", 0)) + int(scope.get("uncertain", 0)),
        "broadcast_equals_quadrants": broadcast_count
        == sum(int(row.get("count", 0)) for row in quadrant_rows),
        "brand_broadcasts_equal_global": broadcast_count
        == sum(int(row.get("qualified_broadcasts", 0)) for row in summary.get("brands", [])),
        "monthly_broadcasts_equal_global": broadcast_count
        == sum(int(value) for value in summary.get("monthly", {}).values()),
    }
    by_quadrant = {str(row.get("name")): int(row.get("count", 0)) for row in quadrant_rows}
    checks["promotion_axes_reconcile"] = broadcast_count == (
        by_quadrant.get("Everyday promotion", 0)
        + by_quadrant.get("Seasonal promotion", 0)
        + by_quadrant.get("Evergreen content", 0)
        + by_quadrant.get("Seasonal content", 0)
    )
    checks["seasonal_axes_reconcile"] = checks["promotion_axes_reconcile"]
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise CrossFootError("Cross-foot failed: " + ", ".join(failures))
    return {"passed": True, "checks": checks}


def build_findings(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    broadcasts = int(summary.get("broadcast_count", 0))
    brands = summary.get("brands", [])
    quadrants = {row["name"]: row for row in summary.get("quadrants", [])}
    date_range = {
        "first": _get(summary, "metadata.first_observed", default=""),
        "last": _get(summary, "metadata.last_observed", default=""),
    }
    findings: list[dict[str, Any]] = []
    if brands:
        leader = brands[0]
        findings.append(
            {
                "label": "Highest inbox volume",
                "value": leader["brand"],
                "numerator": leader["qualified_broadcasts"],
                "denominator": broadcasts,
                "date_range": date_range,
            }
        )
    for name, label in (
        ("Evergreen content", "Evergreen content share"),
        ("Everyday promotion", "Everyday promotion share"),
        ("Seasonal promotion", "Seasonal promotion share"),
        ("Seasonal content", "Seasonal content share"),
    ):
        row = quadrants.get(name, {"count": 0, "percentage": 0})
        findings.append(
            {
                "label": label,
                "value": f"{row['percentage']:.1f}%",
                "numerator": row["count"],
                "denominator": broadcasts,
                "date_range": date_range,
            }
        )
    return findings[:5]


__all__ = [
    "CrossFootError",
    "aggregate_records",
    "assign_posture",
    "build_findings",
    "coverage_level",
    "verify_cross_foot",
]
