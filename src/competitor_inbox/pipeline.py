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
from typing import Iterable, Mapping
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
from .sources.imap import ImapConfig, ImapSource, ImapSourceError, overlap_since
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


def _coverage_window(
    previous_metadata: dict[str, object],
    *,
    fetch_start: datetime,
    fetch_end: datetime,
    incremental: bool,
) -> tuple[dict[str, str], dict[str, str]]:
    """Keep the full observed source window separate from the latest fetch.

    Every ingest merges with the retained master, including a manual shorter
    backfill. Replacing the original window with the newest request would make
    retained history look younger than it is. The full window therefore keeps
    the union of successful requests while ``last_fetch_window`` records the
    exact newest request.
    """

    last_fetch = {"start": _iso(fetch_start), "end": _iso(fetch_end)}
    previous_window = previous_metadata.get("source_window")
    previous_start = ""
    previous_end = ""
    if isinstance(previous_window, dict):
        previous_start = str(previous_window.get("start") or "")
        previous_end = str(previous_window.get("end") or "")
    full_start = min(value for value in (previous_start, last_fetch["start"]) if value)
    full_end = max(value for value in (previous_end, last_fetch["end"]) if value)
    return {"start": full_start, "end": full_end}, last_fetch


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
                fetch_batch_size=config.source.fetch_batch_size,
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


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


DEDUPE_LEVELS = (
    "level_1_source",
    "level_2_message_id",
    "level_3_content",
    "level_4_campaign",
)


def _mapping_section(parent: Mapping[str, object], key: str) -> dict[str, object]:
    value = parent.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"defined_source.{key} must be an object")
    return dict(value)


def _validated_defined_source(
    metadata: Mapping[str, object],
) -> dict[str, object] | None:
    existing = metadata.get("defined_source")
    if existing is None:
        return None
    if not isinstance(existing, Mapping):
        raise ValueError("defined_source must be an object")
    defined = dict(existing)
    _mapping_section(defined, "included")
    _mapping_section(defined, "source_ingestion")
    post_alias = _mapping_section(defined, "post_alias_dedupe")
    _mapping_section(post_alias, "level_counts")
    source_window = defined.get("source_window")
    if source_window is not None and not isinstance(source_window, Mapping):
        raise ValueError("defined_source.source_window must be an object")
    return defined


def _capped_level_counts(
    values: Mapping[str, object],
    *,
    maximum: int,
) -> dict[str, int]:
    """Return the four-level schema without claiming more than ``maximum``."""

    remaining = _nonnegative_int(maximum)
    output = {level: 0 for level in DEDUPE_LEVELS}
    for level in DEDUPE_LEVELS:
        count = min(_nonnegative_int(values.get(level)), remaining)
        output[level] = count
        remaining -= count
    return output


def _refresh_defined_source(
    metadata: dict[str, object],
    records: list[NormalizedMessage],
    table: CoverageTable,
    *,
    source_window: Mapping[str, str] | None = None,
    previous_collapsed_variants: int | None = None,
    dedupe: DedupeReport | None = None,
    ingestion_status: str = "unknown",
    ingestion_error_codes: Iterable[str] = (),
) -> None:
    """Keep an existing reviewed-source ledger tied to the retained corpus.

    ``defined_source`` is created only by the explicit source-universe review
    workflow. Normal installs do not gain that assertion here. Once present,
    however, every successful ingest must advance its nested window and counts
    alongside the top-level metadata. Analysis refreshes the same ledger from
    final scope classifications without adding another round of inputs.
    """

    defined = _validated_defined_source(metadata)
    if defined is None:
        return

    active_window_value: object = source_window or metadata.get("source_window")
    if active_window_value is not None and not isinstance(active_window_value, Mapping):
        raise ValueError("source_window must be an object")
    active_window = dict(active_window_value or {})
    if active_window:
        defined["source_window"] = active_window

    total = table.total
    included = _mapping_section(defined, "included")
    included.update(
        {
            "distinct_messages": total.distinct_messages,
            "delivery_variants": total.parsed_input,
            "broadcast": total.qualified_broadcasts,
            "lifecycle": total.lifecycle,
            "uncertain": total.uncertain,
        }
    )
    defined["included"] = included
    defined["contributing_brand_count"] = len(
        {record.brand for record in records if record.brand}
    )

    source_ingestion = _mapping_section(defined, "source_ingestion")
    source_ingestion.update(
        {
            "status": str(ingestion_status or "unknown"),
            "parse_failures_preserved": total.parse_failures,
            "ingestion_errors": total.ingestion_errors,
            "ingestion_error_codes": list(ingestion_error_codes),
            "source_completeness": total.source_completeness,
        }
    )
    defined["source_ingestion"] = source_ingestion

    post_alias = _mapping_section(defined, "post_alias_dedupe")
    stored_levels = _mapping_section(post_alias, "level_counts")
    collapsed_variants = max(0, total.parsed_input - total.distinct_messages)
    prior_collapsed = (
        collapsed_variants
        if previous_collapsed_variants is None
        else min(collapsed_variants, _nonnegative_int(previous_collapsed_variants))
    )
    level_counts = _capped_level_counts(stored_levels, maximum=prior_collapsed)
    if dedupe is not None:
        newly_collapsed = max(0, collapsed_variants - prior_collapsed)
        increments = _capped_level_counts(
            dedupe.level_counts,
            maximum=newly_collapsed,
        )
        for level in DEDUPE_LEVELS:
            level_counts[level] += increments[level]
    post_alias.update(
        {
            "input_distinct_messages": total.parsed_input,
            "output_distinct_messages": total.distinct_messages,
            "variants_collapsed": collapsed_variants,
            "delivery_variants_preserved": total.parsed_input,
            "level_counts": level_counts,
        }
    )
    defined["post_alias_dedupe"] = post_alias
    metadata["defined_source"] = defined


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
        previous_document = store.load_document()
        previous_metadata = dict(previous_document.get("metadata") or {})
        _validated_defined_source(previous_metadata)
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
        existing_variant_ids = {
            variant_id
            for record in previous
            for variant_id in record.variant_ids
        }
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
                    # A collapsed delivery keeps its stable record ID in the
                    # canonical cluster even though its source identity is no
                    # longer the canonical record's identity. Treat overlap
                    # replays of that ID as already retained, while allowing a
                    # genuinely new delivery ID to become a new variant.
                    if record.id in existing_variant_ids:
                        existing_sources.add(source_key)
                        continue
                    parsed_new.append(record)
                    existing_sources.add(source_key)
                    existing_variant_ids.update(record.variant_ids)
                elif failure is not None:
                    failures_new.append(failure)
        except Exception as exc:
            # A connection, source-file, or private-write error means the source
            # window is incomplete. Discard parsed records from this attempt and
            # leave master.json, parse failures, coverage, and the dashboard
            # untouched. Only failure status is updated, preserving last_success.
            error_code = (
                exc.safe_code if isinstance(exc, ImapSourceError) else type(exc).__name__
            )
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

        source_window, last_fetch_window = _coverage_window(
            previous_metadata,
            fetch_start=since,
            fetch_end=now,
            incremental=incremental,
        )
        metadata = {
            **previous_metadata,
            "source_window": source_window,
            "last_fetch_window": last_fetch_window,
            "timezone": config.analysis.timezone,
            "source_mode": config.source.mode,
            "ingestion_error_codes": ingestion_error_codes,
            "dedupe": dedupe.to_dict(),
            "early_gate": gate.to_dict(),
        }
        _refresh_defined_source(
            metadata,
            records,
            table,
            source_window=source_window,
            previous_collapsed_variants=max(
                0,
                sum(record.variant_count for record in previous) - len(previous),
            ),
            dedupe=dedupe,
            ingestion_status="success",
            ingestion_error_codes=ingestion_error_codes,
        )
        store.save(records, failures=failures, metadata=metadata)
        _write_coverage(store, table, gate)
        state = {
            "status": "success",
            "last_attempt": _iso(now),
            "last_success": _iso(now),
            # These fields describe the complete retained corpus. The latest
            # overlap request is recorded separately so coverage gates never
            # mistake a 14-day incremental fetch for the full history window.
            "source_window_start": source_window["start"],
            "source_window_end": source_window["end"],
            "last_fetch_window_start": last_fetch_window["start"],
            "last_fetch_window_end": last_fetch_window["end"],
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
        prior_document = store.load_document()
        metadata = dict(prior_document.get("metadata") or {})
        _validated_defined_source(metadata)
        classifier = None
        if config.analysis.ai_enabled:
            classifier = build_optional_classifier(
                store.root / "ai-cache",
                model=config.analysis.model,
            )
        analyzed = analyze_normalized_messages(records, classifier=classifier)
        metadata["analysis"] = {
            "mode": "ai+deterministic" if classifier else "deterministic-only",
            "model": config.analysis.model if classifier else None,
            "analyzed_at": _iso(_utc_now()),
        }
        ingestion_state = store.read_state("ingestion")
        ingestion_errors = int(ingestion_state.get("ingestion_errors") or 0)
        table = _coverage(analyzed, failures, ingestion_errors=ingestion_errors)
        assert_coverage_cross_foot(table, require_quadrants=True)
        gate = evaluate_early_data_gate(table)
        _refresh_defined_source(
            metadata,
            analyzed,
            table,
            ingestion_status=str(ingestion_state.get("status") or "unknown"),
            ingestion_error_codes=ingestion_state.get("ingestion_error_codes") or (),
        )
        store.save(analyzed, failures=failures, metadata=metadata)
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
