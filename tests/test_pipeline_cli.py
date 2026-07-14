from __future__ import annotations

import hashlib
import json
import mailbox
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import pytest

import competitor_inbox.pipeline as pipeline_module
import competitor_inbox.cli as cli_module
from competitor_inbox.aggregate import aggregate_records
from competitor_inbox.cli import _bind_coverage_integrity, _git_state, build_parser, main
from competitor_inbox.config import AppConfig, SourceConfig, load_config, save_config
from competitor_inbox.coverage import build_coverage_table, evaluate_early_data_gate
from competitor_inbox.dashboard import generate_dashboard
from competitor_inbox.dedupe import DedupeReport, deduplicate_messages
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


def test_git_state_uses_pip_direct_url_for_regular_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = tmp_path / "release"
    release.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=release, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Release Test"], cwd=release, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "release-test@localhost"],
        cwd=release,
        check=True,
    )
    tracked = release / "release.txt"
    tracked.write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "release.txt"], cwd=release, check=True)
    subprocess.run(["git", "commit", "-qm", "release"], cwd=release, check=True)
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=release,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    class InstalledDistribution:
        @staticmethod
        def read_text(name: str) -> str | None:
            assert name == "direct_url.json"
            return json.dumps({"dir_info": {}, "url": release.as_uri()})

    monkeypatch.setattr(cli_module, "distribution", lambda _name: InstalledDistribution())
    monkeypatch.chdir(tmp_path)

    assert _git_state() == (expected, False)
    tracked.write_text("dirty\n", encoding="utf-8")
    assert _git_state() == (expected, True)


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


def test_incremental_ingest_preserves_full_backfill_window(tmp_path: Path) -> None:
    mbox_path = tmp_path / "research.mbox"
    _write_mbox(mbox_path, count=1)
    config = AppConfig(
        data_root=tmp_path / "private",
        source=SourceConfig(mode="mbox", mbox_path=str(mbox_path)),
    )

    ingest(config, months=12, now=NOW)
    store = MasterStore(config.data_root)
    initial = store.load_document()["metadata"]["source_window"]

    later = NOW + timedelta(days=1)
    ingest(config, incremental=True, now=later)
    metadata = store.load_document()["metadata"]
    state = store.read_state("ingestion")

    assert metadata["source_window"]["start"] == initial["start"]
    assert metadata["source_window"]["end"] == later.isoformat().replace("+00:00", "Z")
    assert metadata["last_fetch_window"]["start"] == (
        NOW - timedelta(days=14)
    ).isoformat().replace("+00:00", "Z")
    assert metadata["last_fetch_window"]["end"] == later.isoformat().replace(
        "+00:00", "Z"
    )
    assert state["source_window_start"] == initial["start"]
    assert state["source_window_end"] == later.isoformat().replace("+00:00", "Z")
    assert state["last_fetch_window_start"] == metadata["last_fetch_window"]["start"]
    assert state["last_fetch_window_end"] == metadata["last_fetch_window"]["end"]


def test_shorter_nonincremental_backfill_preserves_retained_history_window(
    tmp_path: Path,
) -> None:
    mbox_path = tmp_path / "research.mbox"
    _write_mbox(mbox_path, count=1)
    config = AppConfig(
        data_root=tmp_path / "private",
        source=SourceConfig(mode="mbox", mbox_path=str(mbox_path)),
    )

    ingest(config, months=12, now=NOW)
    store = MasterStore(config.data_root)
    initial = store.load_document()["metadata"]["source_window"]

    later = NOW + timedelta(days=1)
    ingest(config, months=1, incremental=False, now=later)
    metadata = store.load_document()["metadata"]

    assert metadata["source_window"]["start"] == initial["start"]
    assert metadata["source_window"]["end"] == later.isoformat().replace("+00:00", "Z")
    assert metadata["last_fetch_window"]["start"] == calendar_months_ago(
        later,
        1,
        config.analysis.timezone,
    ).isoformat().replace("+00:00", "Z")
    assert metadata["last_fetch_window"]["end"] == later.isoformat().replace(
        "+00:00",
        "Z",
    )


def test_incremental_ingest_refreshes_existing_defined_source_ledger(
    tmp_path: Path,
) -> None:
    mbox_path = tmp_path / "research.mbox"
    _write_mbox(mbox_path, count=1)
    config = AppConfig(
        data_root=tmp_path / "private",
        source=SourceConfig(mode="mbox", mbox_path=str(mbox_path)),
    )

    ingest(config, months=12, now=NOW)
    store = MasterStore(config.data_root)
    document = store.load_document()
    metadata = dict(document["metadata"])
    metadata["defined_source"] = {
        "type": "post_fetch_sender_domain_allowlist",
        "mapping_sha256": "0" * 64,
        "contributing_brand_count": 1,
        "source_window": dict(metadata["source_window"]),
        "source_ingestion": {
            "status": "success",
            "parse_failures_preserved": 0,
            "ingestion_errors": 0,
            "ingestion_error_codes": [],
            "source_completeness": "complete",
        },
        "included": {
            "distinct_messages": 1,
            "delivery_variants": 1,
            "broadcast": 1,
            "lifecycle": 0,
            "uncertain": 0,
        },
        "excluded": {"distinct_messages": 7},
        "post_alias_dedupe": {
            "input_distinct_messages": 1,
            "output_distinct_messages": 1,
            "variants_collapsed": 0,
            "delivery_variants_preserved": 1,
            "level_counts": {
                "level_1_source": 0,
                "level_2_message_id": 0,
                "level_3_content": 0,
                "level_4_campaign": 0,
            },
        },
    }
    store.save(store.load(), metadata=metadata)

    # Append one delivery duplicate and one new campaign. The reviewed ledger
    # must advance once during ingest, then remain stable through analysis.
    _write_mbox(mbox_path, count=2)
    later = NOW + timedelta(days=1)
    phase_one = ingest(config, incremental=True, now=later)
    assert phase_one.new_distinct_messages == 2
    assert phase_one.coverage.total.distinct_messages == 2
    assert phase_one.coverage.total.parsed_input == 3

    ingested_metadata = store.load_document()["metadata"]
    defined = ingested_metadata["defined_source"]
    assert defined["source_window"] == ingested_metadata["source_window"]
    assert defined["mapping_sha256"] == "0" * 64
    assert defined["excluded"] == {"distinct_messages": 7}
    assert defined["included"] == {
        "distinct_messages": 2,
        "delivery_variants": 3,
        "broadcast": 2,
        "lifecycle": 0,
        "uncertain": 0,
    }
    assert defined["post_alias_dedupe"] == {
        "input_distinct_messages": 3,
        "output_distinct_messages": 2,
        "variants_collapsed": 1,
        "delivery_variants_preserved": 3,
        "level_counts": {
            "level_1_source": 0,
            "level_2_message_id": 1,
            "level_3_content": 0,
            "level_4_campaign": 0,
        },
    }

    analyze_private_store(config)
    analyzed_defined = store.load_document()["metadata"]["defined_source"]
    assert analyzed_defined["included"] == defined["included"]
    assert analyzed_defined["post_alias_dedupe"] == defined["post_alias_dedupe"]
    assert analyzed_defined["source_ingestion"] == {
        "status": "success",
        "parse_failures_preserved": 0,
        "ingestion_errors": 0,
        "ingestion_error_codes": [],
        "source_completeness": "complete",
    }

    # Analysis is idempotent and does not add another round of inputs.
    analyze_private_store(config)
    assert store.load_document()["metadata"]["defined_source"] == analyzed_defined

    # The unchanged overlap contains a source identity that was collapsed into
    # variant_ids on the prior run. It must not be counted a second time.
    unchanged = ingest(config, incremental=True, now=later + timedelta(days=1))
    unchanged_defined = store.load_document()["metadata"]["defined_source"]
    assert unchanged.new_distinct_messages == 0
    assert unchanged.coverage.total.parsed_input == 3
    assert unchanged_defined["post_alias_dedupe"] == defined["post_alias_dedupe"]

    # A genuinely new delivery of the same message has a new stable ID and is
    # still retained as a new variant exactly once.
    _write_mbox(mbox_path, count=1)
    new_variant = ingest(config, incremental=True, now=later + timedelta(days=2))
    new_variant_defined = store.load_document()["metadata"]["defined_source"]
    assert new_variant.new_distinct_messages == 1
    assert new_variant.coverage.total.parsed_input == 4
    assert new_variant_defined["post_alias_dedupe"] == {
        "input_distinct_messages": 4,
        "output_distinct_messages": 2,
        "variants_collapsed": 2,
        "delivery_variants_preserved": 4,
        "level_counts": {
            "level_1_source": 0,
            "level_2_message_id": 2,
            "level_3_content": 0,
            "level_4_campaign": 0,
        },
    }

    # Missing state must not let analysis fabricate a successful ingestion.
    (store.root / "state" / "ingestion.json").unlink()
    analyze_private_store(config)
    assert (
        store.load_document()["metadata"]["defined_source"]["source_ingestion"][
            "status"
        ]
        == "unknown"
    )


def test_v102_update_repairs_inflated_variant_count_from_unique_ids(
    tmp_path: Path,
) -> None:
    mbox_path = tmp_path / "research.mbox"
    _write_mbox(mbox_path, count=1)
    config = AppConfig(
        data_root=tmp_path / "private",
        source=SourceConfig(mode="mbox", mbox_path=str(mbox_path)),
    )
    ingest(config, months=12, now=NOW)
    store = MasterStore(config.data_root)

    stale = store.load_document()
    stale["records"][0]["variant_count"] = 4
    stale["records"][0]["variant_ids"] = [
        stale["records"][0]["id"],
        stale["records"][0]["id"],
    ]
    store.master_path.write_text(
        json.dumps(stale, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert store.load()[0].variant_count == 1
    analyze_private_store(config)
    repaired = store.load_document()["records"][0]
    assert repaired["variant_count"] == 1
    assert repaired["variant_count"] == len(set(repaired["variant_ids"]))


def test_defined_source_totals_heal_from_retained_records(
    tmp_path: Path,
) -> None:
    mbox_path = tmp_path / "research.mbox"
    _write_mbox(mbox_path, count=2)
    config = AppConfig(
        data_root=tmp_path / "private",
        source=SourceConfig(mode="mbox", mbox_path=str(mbox_path)),
    )
    ingest(config, months=12, now=NOW)
    records = MasterStore(config.data_root).load()
    records[0] = replace(
        records[0],
        variant_count=9,
        variant_ids=[records[0].id, "historic-variant"],
    )
    table = build_coverage_table(records)
    metadata: dict[str, object] = {
        "source_window": {"start": "earlier", "end": "later"},
        "defined_source": {
            "included": {"distinct_messages": 1, "delivery_variants": 1},
            "source_ingestion": {},
            # Missing post_alias_dedupe is an older, incomplete ledger.
        },
    }

    pipeline_module._refresh_defined_source(
        metadata,
        records,
        table,
        ingestion_status="success",
    )

    defined = metadata["defined_source"]
    assert defined["included"]["distinct_messages"] == 2
    assert defined["included"]["delivery_variants"] == 3
    assert defined["post_alias_dedupe"] == {
        "input_distinct_messages": 3,
        "output_distinct_messages": 2,
        "variants_collapsed": 1,
        "delivery_variants_preserved": 3,
        "level_counts": {
            "level_1_source": 0,
            "level_2_message_id": 0,
            "level_3_content": 0,
            "level_4_campaign": 0,
        },
    }


def test_defined_source_caps_earlier_duplicate_level_delta(
    tmp_path: Path,
) -> None:
    mbox_path = tmp_path / "research.mbox"
    _write_mbox(mbox_path, count=1)
    config = AppConfig(
        data_root=tmp_path / "private",
        source=SourceConfig(mode="mbox", mbox_path=str(mbox_path)),
    )
    ingest(config, months=12, now=NOW)
    base = MasterStore(config.data_root).load()[0]
    previous = replace(
        base,
        variant_count=3,
        variant_ids=[base.id, "historic-a", "historic-b"],
    )
    earlier_new = replace(
        previous,
        id="earlier-new",
        source_uid="earlier-new",
        canonical_received_at=(NOW - timedelta(days=1)).isoformat(),
        variant_count=1,
        variant_ids=["earlier-new"],
    )
    dedupe = deduplicate_messages([previous, earlier_new])
    assert dedupe.level_counts["level_2_message_id"] == 3
    table = build_coverage_table(dedupe.messages)
    metadata: dict[str, object] = {
        "defined_source": {
            "included": {},
            "source_ingestion": {},
            "post_alias_dedupe": {
                "input_distinct_messages": 3,
                "output_distinct_messages": 1,
                "variants_collapsed": 2,
                "delivery_variants_preserved": 3,
                "level_counts": {"level_2_message_id": 2},
            },
        }
    }

    pipeline_module._refresh_defined_source(
        metadata,
        dedupe.messages,
        table,
        previous_collapsed_variants=2,
        dedupe=dedupe,
        ingestion_status="success",
    )

    post_alias = metadata["defined_source"]["post_alias_dedupe"]
    assert post_alias["variants_collapsed"] == 3
    assert post_alias["level_counts"]["level_2_message_id"] == 3
    assert sum(post_alias["level_counts"].values()) <= 3


def test_malformed_defined_source_fails_before_fetch_and_preserves_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mbox_path = tmp_path / "research.mbox"
    _write_mbox(mbox_path, count=1)
    config = AppConfig(
        data_root=tmp_path / "private",
        source=SourceConfig(mode="mbox", mbox_path=str(mbox_path)),
    )
    ingest(config, months=12, now=NOW)
    store = MasterStore(config.data_root)
    document = store.load_document()
    metadata = dict(document["metadata"])
    metadata["defined_source"] = {"included": "not-an-object"}
    store.save(store.load(), metadata=metadata)
    master_before = store.master_path.read_bytes()
    state_before = store.read_state("ingestion")

    def should_not_fetch(*args: object, **kwargs: object):
        pytest.fail("malformed reviewed metadata must fail before source fetch")

    monkeypatch.setattr(pipeline_module, "_source", should_not_fetch)
    with pytest.raises(ValueError, match="defined_source.included must be an object"):
        ingest(config, incremental=True, now=NOW + timedelta(days=1))

    assert store.master_path.read_bytes() == master_before
    assert store.read_state("ingestion") == state_before


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
    store.save(
        records,
        metadata={
            "frozen": True,
            "source_window": {
                "start": (NOW - timedelta(days=99)).isoformat(),
                "end": NOW.isoformat(),
            },
            "defined_source": {
                "included": {},
                "source_ingestion": {
                    "status": "success",
                    "ingestion_errors": 0,
                    "ingestion_error_codes": [],
                },
                "post_alias_dedupe": {
                    "input_distinct_messages": 300,
                    "output_distinct_messages": 300,
                    "variants_collapsed": 0,
                    "delivery_variants_preserved": 300,
                    "level_counts": {},
                },
            },
        },
    )
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

    analyze_private_store(config)
    source_ingestion = store.load_document()["metadata"]["defined_source"][
        "source_ingestion"
    ]
    assert source_ingestion == {
        "status": "failed",
        "parse_failures_preserved": 0,
        "ingestion_errors": 1,
        "ingestion_error_codes": ["OSError"],
        "source_completeness": "partial",
    }


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
                "scope_reason": "bulk_or_list_header",
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
