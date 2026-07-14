from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage

import pytest

from competitor_inbox.aggregate import aggregate_records
from competitor_inbox.analysis import build_ai_payload
from competitor_inbox.dashboard import render_dashboard
from competitor_inbox.parser import parse_envelope
from competitor_inbox.sanitize import (
    assert_recipient_safe,
    contains_direct_identifier,
    recipient_terms_from_headers,
    sanitize_brand,
    sanitize_text,
)
from competitor_inbox.schema import SourceEnvelope


def _address(local: str, domain: str = "inbox.test") -> str:
    return local + "@" + domain


def test_merge_tags_schemeless_links_query_tokens_and_greetings_are_removed() -> None:
    raw = "\n".join(
        (
            "Hi Zach,",
            "[[first_name]] [last_name] ${customer_name} %%FNAME%% *|LNAME|*",
            "Open click.brand.test/path?email_id=opaque-value&subscriber_id=private-value",
            "email_id=another-private-value",
        )
    )

    safe = sanitize_text(raw, recipient_terms=["Zach"])

    assert "Zach" not in safe
    assert "first_name" not in safe
    assert "last_name" not in safe
    assert "customer_name" not in safe
    assert "FNAME" not in safe
    assert "brand.test" not in safe
    assert "opaque-value" not in safe
    assert "another-private-value" not in safe
    assert contains_direct_identifier(safe, recipient_terms=["Zach"]) is False
    assert_recipient_safe(safe, recipient_terms=["Zach"])


def test_rendered_greeting_is_removed_without_a_configured_alias() -> None:
    safe = sanitize_text("Hey Priya!\nYour private edit is ready.")

    assert "Priya" not in safe
    assert "[personalization removed]" in safe
    assert contains_direct_identifier(safe) is False


def test_dotted_brand_names_are_not_mistaken_for_schemeless_links() -> None:
    assert sanitize_brand("J.Crew") == "J.Crew"


def test_recipient_header_names_and_mailbox_aliases_form_a_deny_list() -> None:
    headers = [
        f"Zach Schieffer <{_address('research.archive')}>",
        f"<{_address('secondary.reader')}>",
    ]

    terms = recipient_terms_from_headers(headers)

    assert "zach schieffer" in terms
    assert "zach" in terms
    assert "schieffer" in terms
    assert "research archive" in terms
    assert "secondary" in terms
    assert "reader" not in terms


def test_parser_enforces_header_derived_deny_list_before_record_creation() -> None:
    message = EmailMessage()
    message["From"] = f"Alder Row <{_address('news', 'alder-row.test')}>"
    message["To"] = f"Zach Schieffer <{_address('research.archive')}>"
    message["Date"] = "Tue, 14 Jul 2026 08:00:00 +0000"
    message["Subject"] = "Reserved for Zach"
    message["Message-ID"] = f"<{_address('privacy-hardening-1', 'alder-row.test')}>"
    message["List-ID"] = "Alder Row News <news.alder-row.test>"
    message["List-Unsubscribe"] = "<https://alder-row.test/u?email_id=private-value>"
    message.set_content(
        "Hi Zach,\n"
        "[[first_name]], your edit is ready.\n"
        "Open click.alder-row.test/campaign?email_id=private-value"
    )
    envelope = SourceEnvelope(
        raw_bytes=message.as_bytes(),
        source_type="imap",
        source_uid="privacy-hardening-1",
        uidvalidity="7",
        canonical_received_at=datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc),
    )

    record = parse_envelope(envelope)
    persisted = "\n".join((record.subject, record.preheader, record.visible_text))

    assert "Zach" not in persisted
    assert "first_name" not in persisted
    assert "private-value" not in persisted
    assert "alder-row.test/campaign" not in persisted
    assert contains_direct_identifier(persisted, recipient_terms=["Zach", "Schieffer"]) is False


def test_optional_ai_payload_reuses_the_canonical_sanitizer() -> None:
    payload = build_ai_payload(
        "Hi Zach!",
        "Open go.brand.test/click?email_id=private-value",
        "[[first_name]], your edit is ready. email_id=second-private-value",
    )
    serialized = "\n".join(payload.values())

    assert "Zach" not in serialized
    assert "brand.test" not in serialized
    assert "private-value" not in serialized
    assert "first_name" not in serialized
    assert_recipient_safe(serialized)


def test_dashboard_export_sanitizes_direct_record_input() -> None:
    record = {
        "id": "privacy-dashboard-1",
        "brand": "Alder Row",
        "canonical_received_at": "2026-07-14T08:00:00Z",
        "subject": "Hey Zach, open go.brand.test/click?email_id=private-value",
        "preheader": "[[first_name]], your edit is ready",
        "visible_text": "Product details",
        "scope": "broadcast",
        "intent": "Featured products",
        "intent_source": "deterministic",
        "intent_confidence": 1.0,
        "offer_candidates": [],
        "primary_offer": None,
        "seasonal": False,
        "occasion": None,
        "variant_count": 1,
    }

    document = render_dashboard(aggregate_records([record]))

    assert "Zach" not in document
    assert "brand.test" not in document
    assert "private-value" not in document
    assert "first_name" not in document


def test_unsafe_raw_value_fails_the_boundary_assertion() -> None:
    with pytest.raises(ValueError, match="survived sanitization"):
        assert_recipient_safe("Open click.brand.test/u?email_id=private-value")
