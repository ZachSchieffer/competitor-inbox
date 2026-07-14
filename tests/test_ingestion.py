from __future__ import annotations

import hashlib
import mailbox
import imaplib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import pytest

from competitor_inbox.aggregate import aggregate_records
from competitor_inbox.config import (
    AppConfig,
    ensure_private_data_root as ensure_config_data_root,
    save_config,
)
from competitor_inbox.coverage import (
    assert_coverage_cross_foot,
    build_coverage_table,
    evaluate_early_data_gate,
)
from competitor_inbox.dedupe import deduplicate_messages
from competitor_inbox.dashboard import render_dashboard
from competitor_inbox.parser import parse_envelope
from competitor_inbox.pipeline import dashboard_records
from competitor_inbox.sanitize import contains_direct_identifier, sanitize_text
from competitor_inbox.schema import MessageScope, NormalizedMessage, SourceEnvelope, isoformat_utc
from competitor_inbox.sources.imap import ImapConfig, ImapSource, overlap_since
from competitor_inbox.sources.mbox import MboxSource
from competitor_inbox.store import (
    MasterStore,
    StoreLock,
    UnsafeDataRootError,
    ensure_private_data_root,
)


NOW = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)


def raw_message(
    *,
    subject: str = "New collection",
    body: str = "Shop now",
    sender: str = "Northstar <news@northstar.example>",
    recipient: str = "Reader <reader@recipient.example>",
    message_id: str = "<campaign-1@northstar.example>",
    list_headers: bool = True,
    campaign_id: str | None = None,
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Date"] = "Tue, 14 Jul 2026 08:00:00 +0000"
    message["Subject"] = subject
    message["Message-ID"] = message_id
    if list_headers:
        message["List-ID"] = "Northstar News <news.northstar.example>"
        message["List-Unsubscribe"] = "<https://northstar.example/u?token=private-value>"
    if campaign_id:
        message["X-Campaign-Id"] = campaign_id
    message.set_content(body)
    return message.as_bytes()


def envelope(
    raw: bytes,
    *,
    uid: str,
    received_at: datetime = NOW,
    mailbox_name: str = "INBOX",
) -> SourceEnvelope:
    return SourceEnvelope(
        raw_bytes=raw,
        source_type="imap",
        source_uid=uid,
        uidvalidity="77",
        mailbox=mailbox_name,
        canonical_received_at=received_at,
    )


def test_sanitizer_and_parser_remove_identifiers_and_classify_scope() -> None:
    raw = raw_message(
        body=(
            "Hello reader@recipient.example\n"
            "Shop now: https://northstar.example/products?subscriber=abc12345\n"
            "{{ first_name }}"
        )
    )
    record = parse_envelope(envelope(raw, uid="10"))

    assert record.scope == MessageScope.BROADCAST.value
    assert record.sender_domain == "northstar.example"
    assert not contains_direct_identifier(record.subject + record.preheader + record.visible_text)
    assert "abc12345" not in record.visible_text
    assert "reader@recipient.example" not in record.to_dict().__repr__()
    assert record.message_id and record.message_id.startswith("message:")

    lifecycle = parse_envelope(
        envelope(
            raw_message(subject="Your order confirmation", body="Your order is confirmed"),
            uid="11",
        )
    )
    assert lifecycle.scope == MessageScope.LIFECYCLE.value
    assert lifecycle.scope_reason == "lifecycle:transactional"
    assert "reader@recipient.example" not in sanitize_text("reader@recipient.example")


def test_four_level_dedupe_preserves_variants() -> None:
    base = parse_envelope(envelope(raw_message(), uid="1"))
    same_source = parse_envelope(
        envelope(raw_message(subject="Changed subject", message_id="<other@northstar.example>"), uid="1")
    )
    same_message_id = parse_envelope(
        envelope(raw_message(), uid="2", received_at=NOW + timedelta(hours=1))
    )
    same_content = parse_envelope(
        envelope(
            raw_message(message_id="<content-copy@northstar.example>"),
            uid="3",
            received_at=NOW + timedelta(hours=2),
        )
    )
    same_campaign = parse_envelope(
        envelope(
            raw_message(
                subject="A different creative",
                body="A different creative body",
                message_id="<campaign-copy@northstar.example>",
                campaign_id="campaign-alpha",
            ),
            uid="4",
            received_at=NOW + timedelta(days=2),
        )
    )
    campaign_anchor = parse_envelope(
        envelope(
            raw_message(
                subject="First campaign creative",
                body="First campaign body",
                message_id="<campaign-anchor@northstar.example>",
                campaign_id="campaign-alpha",
            ),
            uid="5",
            received_at=NOW + timedelta(days=1),
        )
    )
    distinct = parse_envelope(
        envelope(
            raw_message(
                subject="Education guide",
                body="A materially separate guide",
                message_id="<distinct@northstar.example>",
            ),
            uid="6",
            received_at=NOW + timedelta(days=30),
        )
    )

    report = deduplicate_messages(
        [base, same_source, same_message_id, same_content, campaign_anchor, same_campaign, distinct]
    )
    assert report.input_count == 7
    assert report.distinct_count == 3
    assert report.variants_collapsed == 4
    assert report.level_counts["level_1_source"] == 1
    assert report.level_counts["level_2_message_id"] == 1
    assert report.level_counts["level_3_content"] == 1
    assert report.level_counts["level_4_campaign"] == 1
    assert sorted(message.variant_count for message in report.messages) == [1, 2, 4]


def test_mailbox_namespaces_source_ids_dedupe_and_raw_files(tmp_path: Path) -> None:
    raw_a = raw_message(
        subject="Summer editorial journal",
        body="Material education and care notes.",
        message_id="<mailbox-a@northstar.example>",
    )
    raw_b = raw_message(
        subject="Clearance event deadline",
        body="Final sale inventory and discount terms.",
        message_id="<mailbox-b@northstar.example>",
    )
    envelope_a = envelope(raw_a, uid="44", mailbox_name="Label A")
    envelope_b = envelope(raw_b, uid="44", mailbox_name="Label B")
    record_a = parse_envelope(envelope_a)
    record_b = parse_envelope(envelope_b)

    assert record_a.id != record_b.id
    assert deduplicate_messages([record_a, record_b]).distinct_count == 2

    store = MasterStore(tmp_path / "private")
    legacy_identity = "\0".join(("imap", "77", "44"))
    legacy_path = store.root / "raw" / (
        hashlib.sha256(legacy_identity.encode()).hexdigest() + ".eml"
    )
    legacy_path.write_bytes(raw_a)
    legacy_path.chmod(0o600)

    assert store.save_raw(envelope_a) == legacy_path
    namespaced_b = store.save_raw(envelope_b)
    assert namespaced_b != legacy_path
    assert namespaced_b.read_bytes() == raw_b


def test_level_four_similarity_keeps_canonical_candidate_order() -> None:
    old = replace(
        synthetic_record(1),
        id="old",
        source_uid="old-source",
        canonical_received_at=isoformat_utc(NOW),
        subject="Old unrelated campaign",
        visible_text="Old unrelated body",
        content_hash="old-hash",
        variant_ids=["old"],
    )
    recent = replace(
        synthetic_record(2),
        id="recent",
        source_uid="recent-source",
        canonical_received_at=isoformat_utc(NOW + timedelta(days=9)),
        subject="Campaign beta guide",
        visible_text="Detailed beta guide for owners and operators.",
        content_hash="recent-hash",
        variant_ids=["recent"],
    )
    late_variant_of_old = replace(
        old,
        id="old-late-variant",
        canonical_received_at=isoformat_utc(NOW + timedelta(days=9, hours=12)),
        subject="Old campaign resend",
        visible_text="Old campaign resend body",
        content_hash="old-resend-hash",
        variant_ids=["old-late-variant"],
    )
    near_recent = replace(
        synthetic_record(3),
        id="near-recent",
        source_uid="near-recent-source",
        canonical_received_at=isoformat_utc(NOW + timedelta(days=9, hours=18)),
        subject="Campaign beta guide",
        visible_text="Detailed beta guide for owners & operators.",
        content_hash="near-recent-hash",
        variant_ids=["near-recent"],
    )

    report = deduplicate_messages([old, recent, late_variant_of_old, near_recent])
    assert report.distinct_count == 2
    assert sorted(message.variant_count for message in report.messages) == [2, 2]
    assert report.level_counts["level_1_source"] == 1
    assert report.level_counts["level_4_campaign"] == 1


def synthetic_record(index: int, *, brand: str = "Northstar") -> NormalizedMessage:
    received = NOW - timedelta(days=59) + timedelta(days=index % 60)
    return NormalizedMessage(
        id=f"record-{brand}-{index}",
        source_type="synthetic",
        source_uid=str(index),
        canonical_received_at=isoformat_utc(received),
        brand=brand,
        sender_name=brand,
        sender_domain="northstar.example",
        subject=f"Subject {index}",
        preheader="Preview",
        visible_text="Visible text",
        content_hash=f"hash-{brand}-{index}",
        scope="broadcast",
        scope_reason="fixture",
        scope_confidence=1,
        seasonal=False,
        variant_ids=[f"record-{brand}-{index}"],
    )


def test_coverage_cross_foot_and_early_gate() -> None:
    records = [synthetic_record(index) for index in range(300)]
    table = build_coverage_table(records)
    assert_coverage_cross_foot(table, require_quadrants=True)
    result = evaluate_early_data_gate(table)

    assert result.passed
    assert result.total_qualified_broadcasts == 300
    assert result.near_eligible_brands == ["Northstar"]
    assert table.total.evergreen_content == 300
    assert table.total.observed_days == 60

    failed = evaluate_early_data_gate(build_coverage_table(records[:299]))
    assert not failed.passed
    assert any("below 300" in reason for reason in failed.reasons)


def test_store_refuses_git_worktree_and_uses_private_modes(tmp_path: Path) -> None:
    worktree = tmp_path / "repo"
    (worktree / ".git").mkdir(parents=True)
    with pytest.raises(UnsafeDataRootError):
        ensure_private_data_root(worktree / "private")

    root = ensure_private_data_root(tmp_path / "outside")
    store = MasterStore(root)
    store.save([synthetic_record(1)])
    assert store.load()[0].id == "record-Northstar-1"
    assert root.stat().st_mode & 0o077 == 0
    assert store.master_path.stat().st_mode & 0o077 == 0


def test_store_and_config_reject_symlinked_roots_and_managed_children(
    tmp_path: Path,
) -> None:
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    root_link = tmp_path / "root-link"
    root_link.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(UnsafeDataRootError):
        ensure_private_data_root(root_link)
    with pytest.raises(UnsafeDataRootError):
        ensure_config_data_root(root_link)

    root = tmp_path / "private-root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "raw").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafeDataRootError):
        ensure_private_data_root(root)
    with pytest.raises(UnsafeDataRootError):
        ensure_config_data_root(root)


def test_sensitive_writes_do_not_follow_file_symlinks_or_escape_root(tmp_path: Path) -> None:
    root = ensure_private_data_root(tmp_path / "private")
    outside_master = tmp_path / "outside-master.json"
    outside_master.write_text("unchanged", encoding="utf-8")
    (root / "master.json").symlink_to(outside_master)

    store = MasterStore(root)
    with pytest.raises(UnsafeDataRootError):
        store.save([synthetic_record(1)])
    assert outside_master.read_text(encoding="utf-8") == "unchanged"

    outside_config = tmp_path / "outside-config.toml"
    outside_config.write_text("unchanged", encoding="utf-8")
    (root / "config.toml").symlink_to(outside_config)
    with pytest.raises(UnsafeDataRootError):
        save_config(AppConfig(data_root=root))
    assert outside_config.read_text(encoding="utf-8") == "unchanged"

    with pytest.raises(UnsafeDataRootError):
        save_config(AppConfig(data_root=root), path=tmp_path / "escaped.toml")

    outside_lock = tmp_path / "outside-lock"
    outside_lock.write_text("unchanged", encoding="utf-8")
    (root / "state" / "ingestion.lock").symlink_to(outside_lock)
    with pytest.raises(UnsafeDataRootError):
        with StoreLock(root):
            pass
    assert outside_lock.read_text(encoding="utf-8") == "unchanged"


def test_mbox_adapter_filters_domains_and_dates(tmp_path: Path) -> None:
    path = tmp_path / "takeout.mbox"
    box = mailbox.mbox(path)
    try:
        box.add(mailbox.mboxMessage(raw_message()))
        box.add(
            mailbox.mboxMessage(
                raw_message(
                    sender="Other <news@other.example>",
                    message_id="<other@other.example>",
                )
            )
        )
        box.flush()
    finally:
        box.close()

    messages = list(
        MboxSource(
            path,
            since=datetime(2026, 7, 1, tzinfo=timezone.utc),
            sender_domains=("northstar.example",),
        ).iter_messages()
    )
    assert len(messages) == 1
    assert messages[0].source_type == "mbox"
    assert messages[0].received_at_source == "mbox_separator"
    assert messages[0].received_at_trusted is True
    assert parse_envelope(messages[0]).sender_domain == "northstar.example"


def test_mbox_prefers_delivery_received_and_separator_timestamps(tmp_path: Path) -> None:
    path = tmp_path / "provenance.mbox"
    box = mailbox.mbox(path)
    try:
        delivery = mailbox.mboxMessage(raw_message(message_id="<delivery@northstar.example>"))
        delivery["X-Delivery-Time"] = "Tue, 07 Jul 2026 09:30:00 +0000"
        delivery["Received"] = "from relay.example; Mon, 06 Jul 2026 09:30:00 +0000"
        delivery.set_from("MAILER-DAEMON Sun Jul 05 09:30:00 2026")
        box.add(delivery)

        received = mailbox.mboxMessage(raw_message(message_id="<received@northstar.example>"))
        received["Received"] = "from relay.example; Wed, 08 Jul 2026 09:30:00 +0000"
        received.set_from("MAILER-DAEMON Tue Jul 07 09:30:00 2026")
        box.add(received)

        separator = mailbox.mboxMessage(raw_message(message_id="<separator@northstar.example>"))
        separator.set_from("MAILER-DAEMON Thu Jul 09 09:30:00 2026")
        box.add(separator)
        box.flush()
    finally:
        box.close()

    envelopes = list(MboxSource(path).iter_messages())
    records = [parse_envelope(value) for value in envelopes]

    assert [value.received_at_source for value in envelopes] == [
        "x_delivery_time",
        "received_header",
        "mbox_separator",
    ]
    assert [value.canonical_received_at for value in envelopes] == [
        datetime(2026, 7, 7, 9, 30, tzinfo=timezone.utc),
        datetime(2026, 7, 8, 9, 30, tzinfo=timezone.utc),
        datetime(2026, 7, 9, 9, 30, tzinfo=timezone.utc),
    ]
    assert all(value.received_at_trusted for value in envelopes)
    assert all(value.received_at_trusted for value in records)
    assert [value.received_at_source for value in records] == [
        "x_delivery_time",
        "received_header",
        "mbox_separator",
    ]


def test_untrusted_mbox_dates_cannot_satisfy_hook_day_gate(tmp_path: Path) -> None:
    path = tmp_path / "untrusted-dates.mbox"
    box = mailbox.mbox(path)
    try:
        for index in range(15):
            message = mailbox.mboxMessage(
                raw_message(message_id=f"<untrusted-{index}@northstar.example>")
            )
            del message["Date"]
            message["Date"] = (NOW - timedelta(days=index * 4)).strftime(
                "%a, %d %b %Y %H:%M:%S +0000"
            )
            message.set_from("MAILER-DAEMON")
            box.add(message)
        box.flush()
    finally:
        box.close()

    envelopes = list(MboxSource(path, since=NOW - timedelta(days=1)).iter_messages())
    records = [parse_envelope(value) for value in envelopes]
    table = build_coverage_table(records)
    result = evaluate_early_data_gate(table)
    summary = aggregate_records(dashboard_records(records))
    dashboard = render_dashboard(summary)

    assert len(envelopes) == 15
    assert {value.received_at_source for value in envelopes} == {"message_date_untrusted"}
    assert not any(value.received_at_trusted for value in envelopes)
    assert not any(value.received_at_trusted for value in records)
    assert table.total.qualified_broadcasts == 15
    assert table.total.observed_days == 0
    assert table.rows[0].hook_gate_status == "insufficient"
    assert result.near_eligible_brands == []
    assert summary["metadata"]["first_observed"] == ""
    assert summary["metadata"]["last_observed"] == ""
    assert summary["metadata"]["observed_days"] == 0
    assert summary["metadata"]["trusted_receipt_dates"] == 0
    assert summary["metadata"]["untrusted_receipt_dates"] == 15
    assert summary["monthly"] == {"Unknown receipt date": 15}
    assert summary["cross_foot"]["checks"]["monthly_broadcasts_equal_global"] is True
    assert summary["brands"][0]["observed_days"] == 0
    assert summary["brands"][0]["hook_eligible"] is False
    assert "Receipt window unavailable" in dashboard
    assert "Keep collecting history until annual planning coverage reaches 330 observed days." in dashboard
    assert "2026-07-14 to 2026-07-14" not in dashboard


def test_mbox_file_mtime_fallback_is_recorded_as_untrusted(tmp_path: Path) -> None:
    path = tmp_path / "mtime-fallback.mbox"
    message = mailbox.mboxMessage(raw_message(message_id="<mtime@northstar.example>"))
    del message["Date"]
    message.set_from("MAILER-DAEMON")
    box = mailbox.mbox(path)
    try:
        box.add(message)
        box.flush()
    finally:
        box.close()

    envelope_value = next(MboxSource(path).iter_messages())
    record = parse_envelope(envelope_value)

    assert envelope_value.received_at_source == "file_mtime_untrusted"
    assert envelope_value.received_at_trusted is False
    assert record.received_at_source == "file_mtime_untrusted"
    assert record.received_at_trusted is False


class FakeCredentialStore:
    def require(self, account: str, *, prompt_if_missing: bool = False) -> str:
        assert account.endswith(".example")
        assert not prompt_if_missing
        return "test-only-secret"


class FakeImapConnection:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.raw = raw_message()

    def login(self, username: str, password: str) -> tuple[str, list[bytes]]:
        self.calls.append(("login", username, password))
        return "OK", []

    def select(self, mailbox_name: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.calls.append(("select", mailbox_name, readonly))
        return "OK", [b"1"]

    def response(self, code: str) -> tuple[str, list[bytes]]:
        return code, [b"77"]

    def status(self, mailbox_name: str, query: str) -> tuple[str, list[bytes]]:
        return "OK", [b"INBOX (UIDVALIDITY 77)"]

    def uid(self, command: str, *args: object) -> tuple[str, list[object]]:
        self.calls.append(("uid", command, *args))
        if command == "search":
            return "OK", [b"101"]
        metadata = b'101 (UID 101 INTERNALDATE "14-Jul-2026 08:00:00 +0000" X-GM-LABELS ())'
        return "OK", [(metadata, self.raw), b")"]

    def logout(self) -> tuple[str, list[bytes]]:
        self.calls.append(("logout",))
        return "BYE", []


class AbortingImapConnection(FakeImapConnection):
    def uid(self, command: str, *args: object) -> tuple[str, list[object]]:
        if command == "fetch":
            raise imaplib.IMAP4.abort("session expired")
        return super().uid(command, *args)


def test_imap_adapter_is_read_only_uid_based_and_tracks_uidvalidity() -> None:
    connection = FakeImapConnection()
    source = ImapSource(
        ImapConfig(
            username="archive@inbox.example",
            sender_domains=("northstar.example",),
        ),
        credential_store=FakeCredentialStore(),  # type: ignore[arg-type]
        connection_factory=lambda *args, **kwargs: connection,  # type: ignore[arg-type]
    )
    messages = list(source.iter_messages(since=NOW - timedelta(days=1)))

    assert len(messages) == 1
    assert messages[0].uidvalidity == "77"
    assert ("select", "INBOX", True) in connection.calls
    fetch_call = next(call for call in connection.calls if call[:2] == ("uid", "fetch"))
    assert "BODY.PEEK[]" in str(fetch_call)
    assert source.uidvalidity == "77"
    assert overlap_since(NOW) == NOW - timedelta(days=14)


def test_imap_adapter_reconnects_after_expired_session() -> None:
    first = AbortingImapConnection()
    second = FakeImapConnection()
    connections = iter((first, second))
    source = ImapSource(
        ImapConfig(username="archive@inbox.example", max_retries=1),
        credential_store=FakeCredentialStore(),  # type: ignore[arg-type]
        connection_factory=lambda *args, **kwargs: next(connections),  # type: ignore[arg-type]
    )

    assert len(list(source.iter_messages(since=NOW - timedelta(days=1)))) == 1
    assert ("select", "INBOX", True) in second.calls
