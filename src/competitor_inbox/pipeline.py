"""Launch-critical ingestion and analysis orchestration.

This module is intentionally local-first. Every production artifact it writes
lives under the configured private data root, never beside the source tree.
"""

from __future__ import annotations

import calendar
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from .analysis import analyze_normalized_messages, build_optional_classifier
from .config import AppConfig
from .coverage import (
    CoverageTable,
    EarlyGateResult,
    assert_coverage_cross_foot,
    build_coverage_table,
    coverage_markdown,
    evaluate_early_data_gate,
    global_quadrant_table,
)
from .dedupe import DedupeReport, deduplicate_messages
from .parser import try_parse_envelope
from .schema import NormalizedMessage, ParseFailure, SourceEnvelope
from .sources.imap import ImapConfig, ImapSource, overlap_since
from .sources.mbox import MboxSource
from .store import MasterStore, StoreLock, atomic_write_bytes, atomic_write_json


@dataclass(slots=True)
class PipelineResult:
    coverage: CoverageTable
    early_gate: EarlyGateResult
    dedupe: DedupeReport
    new_envelopes: int
    new_distinct_messages: int
    parse_failures: int
    ingestion_errors: int
    since: str
    through: str
    ai_mode: str = "not-run"


class SourceIngestionError(RuntimeError):
    """A source-level failure that discarded the attempted normalized update."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def calendar_months_ago(now: datetime, months: int, timezone_name: str) -> datetime:
    """Return the same local wall date ``months`` earlier, clamped by month."""

    if months < 1:
        raise ValueError("months must be at least 1")
    zone = ZoneInfo(timezone_name)
    local = now.astimezone(zone)
    month_index = local.year * 12 + local.month - 1 - months
    year, zero_month = divmod(month_index, 12)
    month = zero_month + 1
    day = min(local.day, calendar.monthrange(year, month)[1])
    return local.replace(year=year, month=month, day=day).astimezone(timezone.utc)


def _load_failures(store: MasterStore) -> list[ParseFailure]:
    if not store.failure_path.exists():
        return []
    try:
        document = json.loads(store.failure_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    output: list[ParseFailure] = []
    for value in document.get("failures", []):
        try:
            output.append(ParseFailure(**value))
        except (TypeError, ValueError):
            continue
    return output


def _unique_failures(values: Iterable[ParseFailure]) -> list[ParseFailure]:
    output: list[ParseFailure] = []
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        key = (value.source_type, value.source_uid, value.error_code)
        if key not in seen:
            seen.add(key)
            output.append(value)
    return output


def _source(config: AppConfig, *, since: datetime) -> Iterable[SourceEnvelope]:
    if config.source.mode == "imap":
        adapter = ImapSource(
            ImapConfig(
                username=config.source.account,
                mailbox=config.source.label or config.source.mailbox,
                host=config.source.host,
                port=config.source.port,
                sender_domains=tuple(config.source.domains),
            )
        )
        return adapter.iter_messages(since=since, prompt_for_credential=False)
    if config.source.mode == "mbox":
        if not config.source.mbox_path:
            raise ValueError("mbox mode requires inbox.mbox_path")
        return MboxSource(
            config.source.mbox_path,
            since=since,
            sender_domains=config.source.domains,
        ).iter_messages()
    raise ValueError(f"unsupported source mode: {config.source.mode}")


def _coverage(
    records: list[NormalizedMessage],
    failures: list[ParseFailure],
    *,
    ingestion_errors: int,
) -> CoverageTable:
    failures_by_brand: dict[str, int] = {}
    for failure in failures:
        failures_by_brand[failure.brand or "Unassigned"] = (
            failures_by_brand.get(failure.brand or "Unassigned", 0) + 1
        )
    errors_by_brand = {"Unassigned": ingestion_errors} if ingestion_errors else {}
    return build_coverage_table(
        records,
        parse_failures_by_brand=failures_by_brand,
        ingestion_errors_by_brand=errors_by_brand,
    )


def _write_coverage(store: MasterStore, table: CoverageTable, gate: EarlyGateResult) -> None:
    payload = {
        "rows": [row.to_dict() for row in table.rows],
        "total": table.total.to_dict(),
        "early_gate": gate.to_dict(),
        "quadrants": global_quadrant_table(table),
    }
    atomic_write_json(store.root / "outputs" / "coverage.json", payload)
    atomic_write_bytes(
        store.root / "outputs" / "coverage.md",
        (coverage_markdown(table) + "\n").encode("utf-8"),
    )


def ingest(
    config: AppConfig,
    *,
    months: int = 12,
    incremental: bool = False,
    now: datetime | None = None,
) -> PipelineResult:
    """Read, redact, deduplicate, persist, and evaluate the Early Data Gate."""

    now = now or _utc_now()
    store = MasterStore(config.data_root)
    with StoreLock(store.root):
        previous = store.load()
        prior_failures = _load_failures(store)
        prior_state = store.read_state("ingestion")
        if incremental and prior_state.get("last_success"):
            previous_success = datetime.fromisoformat(
                str(prior_state["last_success"]).replace("Z", "+00:00")
            )
            since = overlap_since(previous_success, overlap_days=14)
        else:
            since = calendar_months_ago(now, months, config.analysis.timezone)

        existing_sources = {record.source_identity_key for record in previous}
        parsed_new: list[NormalizedMessage] = []
        failures_new: list[ParseFailure] = []
        fetched = 0
        ingestion_errors = 0
        ingestion_error_codes: list[str] = []

        try:
            for envelope in _source(config, since=since):
                fetched += 1
                if config.retain_raw:
                    store.save_raw(envelope)
                source_key = envelope.identity_key
                # The 14-day overlap deliberately sees old UIDs. Do not inflate
                # delivery counts when the exact same source record reappears.
                if source_key in existing_sources:
                    continue
                record, failure = try_parse_envelope(
                    envelope,
                    brand_aliases=config.source.brand_aliases,
                )
                if record is not None:
                    parsed_new.append(record)
                    existing_sources.add(source_key)
                elif failure is not None:
                    failures_new.append(failure)
        except Exception as exc:
            # A connection, source-file, or private-write error means the source
            # window is incomplete. Discard parsed records from this attempt and
            # leave master.json, parse failures, coverage, and the dashboard
            # untouched. Only failure status is updated, preserving last_success.
            error_code = type(exc).__name__
            failure_state = {
                "status": "failed",
                "last_attempt": _iso(now),
                "last_success": prior_state.get("last_success"),
                "source_window_start": _iso(since),
                "source_window_end": _iso(now),
                "new_envelopes": fetched,
                "new_distinct_messages": 0,
                "discarded_parsed_messages": len(parsed_new),
                "parse_failures": len(prior_failures),
                "ingestion_errors": 1,
                "ingestion_error_codes": [error_code],
            }
            store.write_state("ingestion", failure_state)
            store.write_state("run", failure_state)
            raise SourceIngestionError(
                f"source iteration failed safely ({error_code})"
            ) from exc

        failures = _unique_failures([*prior_failures, *failures_new])
        dedupe = deduplicate_messages([*previous, *parsed_new])
        records = dedupe.messages
        table = _coverage(records, failures, ingestion_errors=ingestion_errors)
        assert_coverage_cross_foot(table, require_quadrants=False)
        gate = evaluate_early_data_gate(table)

        metadata = {
            "source_window": {"start": _iso(since), "end": _iso(now)},
            "timezone": config.analysis.timezone,
            "source_mode": config.source.mode,
            "ingestion_error_codes": ingestion_error_codes,
            "dedupe": dedupe.to_dict(),
            "early_gate": gate.to_dict(),
        }
        store.save(records, failures=failures, metadata=metadata)
        _write_coverage(store, table, gate)
        state = {
            "status": "success",
            "last_attempt": _iso(now),
            "last_success": _iso(now),
            "source_window_start": _iso(since),
            "source_window_end": _iso(now),
            "new_envelopes": fetched,
            "new_distinct_messages": len(parsed_new),
            "parse_failures": len(failures),
            "ingestion_errors": ingestion_errors,
            "ingestion_error_codes": ingestion_error_codes,
            "early_gate": gate.to_dict(),
        }
        store.write_state("ingestion", state)
        store.write_state("run", state)

    return PipelineResult(
        coverage=table,
        early_gate=gate,
        dedupe=dedupe,
        new_envelopes=fetched,
        new_distinct_messages=len(parsed_new),
        parse_failures=len(failures),
        ingestion_errors=ingestion_errors,
        since=_iso(since),
        through=_iso(now),
    )


def analyze_private_store(config: AppConfig) -> PipelineResult:
    """Run deterministic analysis, optional AI, and strict quadrant cross-foot."""

    store = MasterStore(config.data_root)
    with StoreLock(store.root):
        records = store.load()
        if not records:
            raise RuntimeError("no messages are available; run backfill first")
        failures = _load_failures(store)
        classifier = None
        if config.analysis.ai_enabled:
            classifier = build_optional_classifier(
                store.root / "ai-cache",
                model=config.analysis.model,
            )
        analyzed = analyze_normalized_messages(records, classifier=classifier)
        prior_document = store.load_document()
        metadata = dict(prior_document.get("metadata") or {})
        metadata["analysis"] = {
            "mode": "ai+deterministic" if classifier else "deterministic-only",
            "model": config.analysis.model if classifier else None,
            "analyzed_at": _iso(_utc_now()),
        }
        store.save(analyzed, failures=failures, metadata=metadata)
        ingestion_state = store.read_state("ingestion")
        ingestion_errors = int(ingestion_state.get("ingestion_errors") or 0)
        table = _coverage(analyzed, failures, ingestion_errors=ingestion_errors)
        assert_coverage_cross_foot(table, require_quadrants=True)
        gate = evaluate_early_data_gate(table)
        _write_coverage(store, table, gate)
        dedupe = deduplicate_messages(analyzed)
        state = {
            "mode": metadata["analysis"]["mode"],
            "model": metadata["analysis"]["model"],
            "analyzed_at": metadata["analysis"]["analyzed_at"],
            "records": len(analyzed),
        }
        store.write_state("analysis", state)

    source_window = metadata.get("source_window") or {}
    return PipelineResult(
        coverage=table,
        early_gate=gate,
        dedupe=dedupe,
        new_envelopes=0,
        new_distinct_messages=0,
        parse_failures=len(failures),
        ingestion_errors=ingestion_errors,
        since=str(source_window.get("start") or ""),
        through=str(source_window.get("end") or ""),
        ai_mode=str(metadata["analysis"]["mode"]),
    )


def pipeline_result_json(result: PipelineResult) -> dict[str, object]:
    return {
        "new_envelopes": result.new_envelopes,
        "new_distinct_messages": result.new_distinct_messages,
        "parse_failures": result.parse_failures,
        "ingestion_errors": result.ingestion_errors,
        "since": result.since,
        "through": result.through,
        "ai_mode": result.ai_mode,
        "early_gate": result.early_gate.to_dict(),
        "dedupe": result.dedupe.to_dict(),
        "coverage": {
            "rows": [asdict(row) for row in result.coverage.rows],
            "total": asdict(result.coverage.total),
        },
    }


def dashboard_records(records: Iterable[NormalizedMessage]) -> list[dict[str, object]]:
    """Map canonical storage records to the dashboard aggregation contract."""

    output: list[dict[str, object]] = []
    for record in records:
        value = record.to_dict()
        has_offer = bool(record.primary_offer or record.offer_candidates)
        seasonal = bool(record.seasonal)
        value["received_at"] = record.canonical_received_at
        value["offer"] = {
            "present": has_offer,
            "primary": dict(record.primary_offer) if record.primary_offer else None,
            "candidates": [dict(item) for item in record.offer_candidates],
        }
        value["seasonality"] = {
            "seasonal": seasonal,
            "occasion": record.occasion or "",
        }
        value["intent"] = {
            "label": record.intent or "Featured products",
            "source": record.intent_source or "deterministic",
            "confidence": record.intent_confidence or 0.0,
            "model": record.classification_model,
        }
        if has_offer and seasonal:
            quadrant = "Seasonal promotion"
        elif has_offer:
            quadrant = "Everyday promotion"
        elif seasonal:
            quadrant = "Seasonal content"
        else:
            quadrant = "Evergreen content"
        value["quadrant"] = quadrant
        output.append(value)
    return output
