from __future__ import annotations

import hashlib
import mailbox
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import pytest

import competitor_inbox.pipeline as pipeline_module
from competitor_inbox.aggregate import aggregate_records
from competitor_inbox.cli import _bind_coverage_integrity, build_parser, main
from competitor_inbox.config import AppConfig, SourceConfig, load_config, save_config
from competitor_inbox.coverage import build_coverage_table, evaluate_early_data_gate
from competitor_inbox.dashboard import generate_dashboard
from competitor_inbox.dedupe import DedupeReport
from competitor_inbox.pipeline import (
    PipelineResult,
    SourceIngestionError,
    analyze_private_store,
    calendar_months_ago,
    dashboard_records,
    ingest,
)
from competitor_inbox.schedule import install as install_schedule
from competitor_inbox.schema import NormalizedMessage, SourceCompleteness, SourceEnvelope
from competitor_inbox.sources.imap import ImapSourceError
from competitor_inbox.store import MasterStore


NOW = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)


def test_build_parser_preserves_ordered_launch_hero_brand_universe() -> None:
    args = build_parser().parse_args(
        [
            "build",
            "--hero-priority-brand",
            "SKIMS",
            "--hero-priority-brand",
            "Olipop",
            "--hero-priority-brand",
            "Poppi",
        ]
    )

    assert args.hero_priority_brand == ["SKIMS", "Olipop", "Poppi"]


def _write_mbox(path: Path, count: int = 300) -> None:
    box = mailbox.mbox(path)
    try:
        for index in range(count):
            observed = NOW - timedelta(days=count - index - 1)
            token = hashlib.sha256(f"fixture-{index}".encode()).hexdigest()[:16]
            message = EmailMessage()
            message["From"] = "Northstar <news@northstar.example>"
            message["To"] = "Research <archive@inbox.example>"
            message["Date"] = observed.strftime("%a, %d %b %Y %H:%M:%S +0000")
            message["Subject"] = f"Editorial campaign {index} {token}"
            message["Message-ID"] = f"<{token}@northstar.example>"
            message["List-ID"] = "Northstar editorial <news.northstar.example>"
            message.set_content(f"Material guide {token} for campaign {index}.")
            mbox_message = mailbox.mboxMessage(message)
            mbox_message.set_from(observed.strftime("MAILER-DAEMON %a %b %d %H:%M:%S %Y"))
            box.add(mbox_message)
        box.flush()
    finally:
        box.close()


def test_config_round_trip_and_calendar_month_boundary(tmp_path: Path) -> None:
    root = tmp_path / "private"
    config = AppConfig(
        data_root=root,
        source=SourceConfig(
            mode="mbox",
            mbox_path=str(tmp_path / "mail.mbox"),
            domains=["northstar.example"],
            brand_aliases={"northstar.example": "Northstar"},
            fetch_batch_size=37,
        ),
    )
    path = save_config(config)
    loaded = load_config(path, data_root=root)

    assert loaded.source.mode == "mbox"
    assert loaded.source.domains == ["northstar.example"]
    assert loaded.source.brand_aliases == {"northstar.example": "Northstar"}
    assert loaded.source.fetch_batch_size == 37
    assert path.stat().st_mode & 0o077 == 0
    assert calendar_months_ago(
        datetime(2024, 3, 31, tzinfo=timezone.utc), 1, "UTC"
    ) == datetime(2024, 2, 29, tzinfo=timezone.utc)


def test_mbox_pipeline_passes_gate_analyzes_and_renders(tmp_path: Path) -> None:
    mbox_path = tmp_path / "research.mbox"
    _write_mbox(mbox_path)
    config = AppConfig(
        data_root=tmp_path / "private",
        source=SourceConfig(mode="mbox", mbox_path=str(mbox_path)),
    )

    phase_one = ingest(config, months=12, now=NOW)
    assert phase_one.early_gate.passed
    assert phase_one.coverage.total.qualified_broadcasts == 300
    assert phase_one.coverage.total.observed_days == 300

    analyzed = analyze_private_store(config)
    assert analyzed.coverage.total.evergreen_content == 300
    records = dashboard_records(MasterStore(config.data_root).load())
    summary = aggregate_records(
        records,
        pipeline_counts={
            "raw_fetched": 300,
            "parse_failures": 0,
            "parsed_input": 300,
            "variants_collapsed": 0,
            "distinct_messages": 300,
        },
    )
    dashboard = generate_dashboard(summary, config.data_root / "outputs" / "dashboard.html")
    text = dashboard.read_text(encoding="utf-8")
    assert summary["cross_foot"]["passed"] is True
    assert summary["broadcast_count"] == 300
    assert "Content-Security-Policy" in text
    assert "<script" not in text.casefold()
    assert "https://" not in text.casefold()


def test_source_failure_preserves_master_outputs_and_last_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "private"
    config = AppConfig(data_root=root, retain_raw=False)
    store = MasterStore(root)
    records = [
        NormalizedMessage(
            id=f"existing-{index}",
            source_type="imap",
            source_uid=str(index),
            uidvalidity="77",
            mailbox="INBOX",
            canonical_received_at=(NOW - timedelta(days=index % 100)).isoformat(),
            brand="Northstar",
            sender_name="Northstar",
            sender_domain="northstar.example",
            subject=f"Campaign {index}",
            preheader="",
            visible_text="Editorial guide.",
            content_hash=f"hash-{index}",
            scope="broadcast",
            scope_reason="fixture",
            scope_confidence=1.0,
            seasonal=False,
        )
        for index in range(300)
    ]
    store.save(records, metadata={"frozen": True})
    prior_success = "2026-07-13T16:00:00Z"
    store.write_state(
        "ingestion",
        {
            "status": "success",
            "last_attempt": prior_success,
            "last_success": prior_success,
            "ingestion_errors": 0,
        },
    )
    dashboard = root / "outputs" / "dashboard.html"
    dashboard.write_text("prior dashboard", encoding="utf-8")
    coverage = root / "outputs" / "coverage.json"
    coverage.write_text('{"prior": true}', encoding="utf-8")
    master_before = store.master_path.read_bytes()
    dashboard_before = dashboard.read_bytes()
    coverage_before = coverage.read_bytes()

    message = EmailMessage()
    message["From"] = "Northstar <news@northstar.example>"
    message["To"] = "Research <archive@inbox.example>"
    message["Date"] = "Tue, 14 Jul 2026 16:00:00 +0000"
    message["Subject"] = "New source message"
    message["Message-ID"] = "<new-source@northstar.example>"
    message["List-ID"] = "Northstar <news.northstar.example>"
    message.set_content("Editorial guide.")

    def broken_source(*args: object, **kwargs: object):
        yield SourceEnvelope(
            raw_bytes=message.as_bytes(),
            source_type="imap",
            source_uid="new-source",
            uidvalidity="77",
            mailbox="INBOX",
            canonical_received_at=NOW,
        )
        raise OSError("synthetic source failure")

    monkeypatch.setattr(pipeline_module, "_source", broken_source)
    with pytest.raises(SourceIngestionError, match="failed safely"):
        ingest(config, incremental=True, now=NOW)

    assert store.master_path.read_bytes() == master_before
    assert dashboard.read_bytes() == dashboard_before
    assert coverage.read_bytes() == coverage_before
    failed_state = store.read_state("ingestion")
    assert failed_state["status"] == "failed"
    assert failed_state["last_success"] == prior_success
    assert failed_state["ingestion_errors"] == 1
    assert failed_state["discarded_parsed_messages"] == 1


def test_imap_safe_error_code_is_written_without_server_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "private"
    config = AppConfig(data_root=root, retain_raw=False)

    def rejected_source(*args: object, **kwargs: object):
        raise ImapSourceError("imap_auth_rejected")

    monkeypatch.setattr(pipeline_module, "_source", rejected_source)
    with pytest.raises(SourceIngestionError, match=r"\(imap_auth_rejected\)"):
        ingest(config, now=NOW)

    failed_state = MasterStore(root).read_state("ingestion")
    assert failed_state["status"] == "failed"
    assert failed_state["ingestion_error_codes"] == ["imap_auth_rejected"]


def test_demo_cli_and_schedule_dry_run_need_no_credentials(tmp_path: Path) -> None:
    root = tmp_path / "demo-root"
    assert main(["demo", "--data-root", str(root), "--json"]) == 0
    assert main(["verify", "--data-root", str(root), "--json"]) == 0
    config = AppConfig(data_root=root)
    target = install_schedule(config, dry_run=True)
    assert target.name == "com.zhsecom.competitor-inbox.plist"
    assert not target.exists()


def test_unknown_parse_failure_disqualifies_single_brand_hook() -> None:
    records = []
    for index in range(30):
        observed = NOW - timedelta(days=index * 4)
        records.append(
            {
                "id": f"record-{index}",
                "source_type": "mbox",
                "source_uid": str(index),
                "received_at_source": "mbox_separator",
                "received_at_trusted": True,
                "brand": "Northstar",
                "canonical_received_at": observed.isoformat(),
                "sender_name": "Northstar",
                "sender_domain": "northstar.example",
                "scope": "broadcast",
                "scope_reason": "bulk-list-evidence",
                "scope_confidence": 1.0,
                "subject": f"Campaign {index}",
                "preheader": "",
                "visible_text": "Editorial guide.",
                "content_hash": f"hash-{index}",
                "variant_count": 1,
                "seasonal": False,
            }
        )
    summary = aggregate_records(records)
    assert summary["brands"][0]["hook_eligible"] is True

    table = build_coverage_table(
        records,
        parse_failures_by_brand={"Unassigned": 1},
    )
    result = PipelineResult(
        coverage=table,
        early_gate=evaluate_early_data_gate(table),
        dedupe=DedupeReport(
            messages=[],
            input_count=0,
            distinct_count=0,
            variants_collapsed=0,
        ),
        new_envelopes=0,
        new_distinct_messages=0,
        parse_failures=1,
        ingestion_errors=0,
        since="",
        through="",
    )

    _bind_coverage_integrity(summary, result)

    assert summary["metadata"]["source_completeness"] == "partial"
    assert summary["brands"][0]["hook_eligible"] is False


def test_curated_export_provenance_survives_coverage_binding() -> None:
    records = [
        NormalizedMessage(
            id=f"curated-{index}",
            source_type="mbox",
            source_uid=str(index),
            canonical_received_at=(NOW - timedelta(days=index % 120)).isoformat(),
            received_at_source="mbox_separator",
            received_at_trusted=True,
            source_completeness=SourceCompleteness.CURATED_EXPORT.value,
            brand="Northstar",
            sender_name="Northstar",
            sender_domain="northstar.example",
            subject=f"Campaign {index}",
            preheader="",
            visible_text="Editorial guide.",
            content_hash=f"curated-hash-{index}",
            scope="broadcast",
            scope_reason="fixture",
            scope_confidence=1.0,
            seasonal=False,
        )
        for index in range(300)
    ]
    table = build_coverage_table(records)
    gate = evaluate_early_data_gate(table)
    result = PipelineResult(
        coverage=table,
        early_gate=gate,
        dedupe=DedupeReport(
            messages=records,
            input_count=300,
            distinct_count=300,
            variants_collapsed=0,
        ),
        new_envelopes=300,
        new_distinct_messages=300,
        parse_failures=0,
        ingestion_errors=0,
        since="",
        through="",
    )
    summary = aggregate_records(dashboard_records(records))

    _bind_coverage_integrity(summary, result)

    assert gate.passed is True
    assert summary["metadata"]["source_completeness"] == "curated_export"
    assert summary["metadata"]["source_error_count"] == 0
    assert summary["brands"][0]["source_completeness"] == "curated_export"
    assert summary["brands"][0]["hook_eligible"] is False
