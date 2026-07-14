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


def _short_tracking_fragment(*, operator: str, separator: str) -> str:
    keys = ("k", "m", "r")
    values = ("opaque-campaign-value", "opaque-message-value", "opaque-recipient-value")
    return separator.join(
        key + operator + value for key, value in zip(keys, values, strict=True)
    )


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


def test_encoded_and_schemeless_short_tracking_fragments_are_removed() -> None:
    qp_equals = "=" + "3D"
    percent_equals = "%" + "3D"
    percent_ampersand = "%" + "26"
    entity_equals = "&#" + "x3D;"
    short_qp_fragment = "&".join(
        key + qp_equals + value
        for key, value in zip(("k", "m", "r"), ("0", "abc", "1"), strict=True)
    )
    encoded_fragments = (
        _short_tracking_fragment(operator=qp_equals, separator="&amp;"),
        _short_tracking_fragment(
            operator=percent_equals,
            separator=percent_ampersand,
        ),
        _short_tracking_fragment(operator=entity_equals, separator="&amp;"),
        _short_tracking_fragment(operator="=", separator="&"),
        short_qp_fragment,
        "?" + "r" + "=" + "opaque-recipient-value",
    )
    raw = "\n".join(encoded_fragments)

    assert contains_direct_identifier(raw) is True

    safe = sanitize_text(raw)

    assert "opaque-campaign-value" not in safe
    assert "opaque-message-value" not in safe
    assert "opaque-recipient-value" not in safe
    assert qp_equals.casefold() not in safe.casefold()
    assert contains_direct_identifier(safe) is False
    assert_recipient_safe(safe)


def test_plain_single_letter_equation_is_not_treated_as_a_tracking_fragment() -> None:
    raw = "Use r=rate when you review this formula."

    assert sanitize_text(raw) == raw
    assert contains_direct_identifier(raw) is False


def test_opaque_standalone_short_tracking_fragment_is_removed() -> None:
    raw = (
        "Receipt metadata "
        + "r"
        + "="
        + "opaque-recipient-value "
        + "k"
        + "="
        + "abcDEF1234567890"
    )

    assert contains_direct_identifier(raw) is True
    safe = sanitize_text(raw)

    assert "opaque-recipient-value" not in safe
    assert "abcDEF1234567890" not in safe
    assert contains_direct_identifier(safe) is False


def test_transfer_payload_link_tails_and_markup_remnants_are_removed() -> None:
    first_opaque = "AB12CD34EF56GH78IJ90KL12MN34"
    second_opaque = "qr12st34uv56wx78yz90ab12cd34"
    raw = "\n".join(
        (
            "[Shop the collection]([link removed]",
            f"/campaign/path?first={first_opaque}&second={second_opaque}",
            "=E2=80=94",
            "=   ",
            "/div>",
            "a/h2>",
        )
    )

    assert contains_direct_identifier(raw) is True
    safe = sanitize_text(raw)

    assert safe == "Shop the collection\n—"
    assert first_opaque not in safe
    assert second_opaque not in safe
    assert "[link removed]" not in safe
    assert "/div>" not in safe
    assert "a/h2>" not in safe
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


def test_quoted_printable_detection_does_not_decode_plain_equations() -> None:
    raw = "Use x=20, y=30, and z=40 in the model."

    assert sanitize_text(raw) == raw
    assert contains_direct_identifier(raw) is False


def test_quoted_printable_email_at_sign_is_fully_redacted() -> None:
    raw = "recipienttoken=40example.test"

    assert contains_direct_identifier(raw) is True
    with pytest.raises(ValueError, match="survived sanitization"):
        assert_recipient_safe(raw)

    safe = sanitize_text(raw)

    assert safe == "[address removed]"
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


def test_quoted_printable_short_key_keeps_encoded_tracking_evidence() -> None:
    raw = "r" + "=3D" + "abcdefghijklmnop"

    assert contains_direct_identifier(raw) is True
    with pytest.raises(ValueError, match="survived sanitization"):
        assert_recipient_safe(raw)

    safe = sanitize_text(raw)

    assert safe == "[tracking removed]"
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


def test_two_octet_quoted_printable_markup_is_decoded_and_stripped() -> None:
    raw = "Collection details\n=3Cdiv=3E\n=3D3Cscript=3D3E"

    assert contains_direct_identifier(raw) is True
    safe = sanitize_text(raw)

    assert safe == "Collection details"
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


def test_invisible_padding_nested_markdown_and_broken_tags_are_removed() -> None:
    raw = "\n".join(
        (
            "\u00ad\u200b\u034fCollection notes",
            "[Read the [size guide]]([link removed]",
            "[Read the material guide]([address removed]",
            "[A truncated preheader]([addr",
            "A sentence.](",
            "( [link removed] )",
            "/em>\u200b",
            "<[link removed]>",
            "Broken entity&#12",
        )
    )

    assert contains_direct_identifier(raw) is True
    safe = sanitize_text(raw)

    assert safe == (
        "Collection notes\nRead the size guide\nRead the material guide\n"
        "A truncated preheader\nA sentence.\n\nBroken entity"
    )
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


def test_generic_recipient_and_company_placeholders_are_redacted() -> None:
    raw = "Hi [Recipient Full Name], welcome to [Company Name]."

    safe = sanitize_text(raw)

    assert "Recipient Full Name" not in safe
    assert "Company Name" not in safe
    assert safe.count("[personalization removed]") == 2
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


def test_nested_wrappers_reach_a_fixed_point_in_one_call() -> None:
    cases = (
        "[[Seasonal edit]([link removed]]",
        "[Open the guide](go.brand.test/private-path)",
        "[Hi Priya.]",
        "Campaign label]([link removed]",
        "[[link removed]",
    )

    for raw in cases:
        safe = sanitize_text(raw)
        assert sanitize_text(safe) == safe
        assert contains_direct_identifier(safe) is False
        assert "brand.test" not in safe


@pytest.mark.parametrize(
    "raw",
    (
        "https%3A%2F%2Fclick.brand.test%2Fgo%3Frecipient_id%3DAB12%26k%3Dprivate",
        "https%253A%252F%252Fclick.brand.test%252Fgo%253Frecipient_id%253DAB12",
        "https=3A=2F=2Fclick.brand.test=2Fgo=3Frecipient_id=3DAB12",
        (
            "&#61;68&#61;74&#61;74&#61;70&#61;73&#61;3A&#61;2F&#61;2F"
            "click.brand.test&#61;2Fgo&#61;3Frecipient_id&#61;3DAB12"
        ),
    ),
)
def test_nested_url_and_token_encodings_reach_a_safe_fixed_point(raw: str) -> None:
    assert contains_direct_identifier(raw) is True

    safe = sanitize_text(raw)

    assert safe == "[link removed]"
    assert "brand.test" not in safe
    assert "AB12" not in safe
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


def test_recursively_nested_html_entities_expose_then_remove_a_url() -> None:
    raw = (
        "&amp;#104;&amp;#116;&amp;#116;&amp;#112;&amp;#115;&amp;#58;"
        "&amp;#47;&amp;#47;click.brand.test/path?recipient_id=AB12"
    )

    safe = sanitize_text(raw)

    assert safe == "[link removed]"
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


def test_eight_html_entity_wrappers_expose_then_remove_an_email() -> None:
    raw = "recipienttoken&#64;example.test"
    for _ in range(8):
        raw = raw.replace("&", "&amp;")

    assert contains_direct_identifier(raw) is True
    with pytest.raises(ValueError, match="survived sanitization"):
        assert_recipient_safe(raw)

    safe = sanitize_text(raw)

    assert safe == "[address removed]"
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


@pytest.mark.parametrize(
    "raw",
    (
        "Hi,Priya!",
        "Hi Priya.",
        "Hello Jordan Lee:",
        "Dear Morgan",
        "Dear Dr. Priya Shah,",
        "Hello Ms. A. B. Smith!",
        "Good morning Prof. J. Chen:",
    ),
)
def test_rendered_greetings_with_additional_terminators_are_redacted(raw: str) -> None:
    safe = sanitize_text(raw)

    assert "[personalization removed]" in safe
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


@pytest.mark.parametrize("raw", ("Hi there.", "Hello friends:", "Hey everyone"))
def test_generic_vocatives_are_preserved(raw: str) -> None:
    assert sanitize_text(raw) == raw
    assert contains_direct_identifier(raw) is False


def test_multiline_recipient_address_blocks_are_redacted() -> None:
    raw = (
        "Shipping address\n\n"
        "Priya Shah\n"
        "1234 Market Street Apt 5\n"
        "Phoenix, AZ\n"
        "85001\n"
        "United States\n\n"
        "Priya Shah\n"
        "1234 Market Street Apt 5\n"
        "Phoenix, AZ\n"
        "85001\n"
        "United States\n\n"
        "Order summary"
    )

    assert contains_direct_identifier(raw) is True
    safe = sanitize_text(raw)

    assert "Priya" not in safe
    assert "1234" not in safe
    assert "85001" not in safe
    assert safe.count("[recipient address removed]") == 2
    assert "Order summary" in safe
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


def test_context_address_without_postal_code_is_redacted() -> None:
    raw = (
        "Shipping address\n"
        "Priya Shah\n"
        "123 Main Street\n"
        "Phoenix, AZ\n"
        "United States"
    )

    assert contains_direct_identifier(raw) is True
    safe = sanitize_text(raw)

    assert safe == "[recipient address removed]"
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


def test_context_address_with_one_locality_line_is_redacted() -> None:
    raw = (
        "Shipping address\n"
        "Priya Shah\n"
        "123 Main Street\n"
        "Phoenix AZ"
    )

    assert contains_direct_identifier(raw) is True
    safe = sanitize_text(raw)

    assert safe == "[recipient address removed]"
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


@pytest.mark.parametrize(
    "raw",
    (
        "Shipping address: Priya Shah, 123 Main Street, Phoenix, AZ 85001",
        "Deliver to: Priya Shah, 123 Main St, Phoenix AZ",
    ),
)
def test_inline_context_address_is_redacted(raw: str) -> None:
    assert contains_direct_identifier(raw) is True
    safe = sanitize_text(raw)

    assert safe == "[recipient address removed]"
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


@pytest.mark.parametrize(
    "raw",
    (
        "Shipping address\nPriya Shah\nFlat 1\n10 King’s Road\nLondon SW3 4UD\nUnited Kingdom",
        "Shipping address\nPriya Shah\nOne Infinite Loop\nCupertino CA 95014",
        "Delivery details\nPriya Shah\n123 Broadway\nNew York NY 10001",
        "Billing address\nPriya Shah\nPO Box 123\nPhoenix AZ 85001",
        "Deliver to:\nPriya Shah\nFlat 4B\n12 Rue de Rivoli\n75001 Paris\nFrance",
    ),
)
def test_context_address_blocks_support_international_and_po_box_forms(raw: str) -> None:
    assert contains_direct_identifier(raw) is True
    safe = sanitize_text(raw)

    assert safe == "[recipient address removed]"
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


@pytest.mark.parametrize(
    "raw",
    (
        "Shipping address: Priya Shah, Oak Cottage, Little Wittering, United Kingdom",
        "Deliver to: Priya Shah, Rue de Rivoli, Paris, France",
        "Shipping address\nPriya Shah\nOak Cottage\nLittle Wittering\nUnited Kingdom",
        "Delivery details\nPriya Shah\nRue de Rivoli\nParis\nFrance",
        "Shipping details: Priya Shah, PO Box 7, Phoenix AZ 85001",
    ),
)
def test_digitless_and_ambiguous_address_forms_require_or_use_strong_context(
    raw: str,
) -> None:
    assert contains_direct_identifier(raw) is True

    safe = sanitize_text(raw)

    assert safe == "[recipient address removed]"
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


def test_generic_shipping_and_delivery_policy_is_not_erased_as_an_address() -> None:
    raw = (
        "Shipping details: Free shipping on orders over $50\n"
        "Delivery details\n"
        "Free delivery over $50\n"
        "Arrives in 3 days"
    )

    assert sanitize_text(raw) == raw
    assert contains_direct_identifier(raw) is False


def test_context_address_redaction_preserves_the_next_section() -> None:
    raw = (
        "Shipping address\n"
        "Priya Shah\n"
        "PO Box 123\n"
        "Phoenix AZ 85001\n"
        "Order summary\n"
        "2 products"
    )

    safe = sanitize_text(raw)

    assert safe == "[recipient address removed]\nOrder summary\n2 products"
    assert contains_direct_identifier(safe) is False


@pytest.mark.parametrize(
    "raw",
    (
        "Phone: (602) 555-1234",
        "Mobile number: +1 602 555 1234",
        "Customer phone 602-555-1234",
        "Phone: +44 20 7946 0958",
        "Contact number = +33 (0)1 42 68 53 00",
        "Telephone - +81-3-1234-5678",
        "Recipient phone\n+61 2 9374 4000",
    ),
)
def test_context_labeled_phone_numbers_are_redacted(raw: str) -> None:
    assert contains_direct_identifier(raw) is True
    safe = sanitize_text(raw)

    assert safe == "[recipient phone removed]"
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


@pytest.mark.parametrize(
    "raw",
    (
        "Order number: ABC123456",
        "Order #12345678",
        "Tracking number: 1Z999AA10123456784",
        "Customer number: 98765432",
        "Account ID: cust_8AHJ2910",
        "Order number: AB 12",
        "Tracking # Z9",
        "Customer ID = 42",
        "Account number\nA1",
        "Recipient ID - Q7",
    ),
)
def test_context_labeled_recipient_identifiers_are_redacted(raw: str) -> None:
    assert contains_direct_identifier(raw) is True
    safe = sanitize_text(raw)

    assert safe == "[recipient identifier removed]"
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


def test_generic_order_quantity_and_promo_code_are_preserved() -> None:
    raw = "Order 2 products and apply promo code SAVE20."

    assert sanitize_text(raw) == raw
    assert contains_direct_identifier(raw) is False


@pytest.mark.parametrize(
    "raw",
    (
        "Name: Priya Shah",
        "Recipient name: Priya Shah",
        "First name: Priya",
        "Account holder: Priya Shah",
        "Customer: Priya Shah",
        "Name = Dr. Priya Shah",
        "Recipient: Ms. P. Shah",
        "Account holder - J. Smith",
        "Account holder Priya Shah",
        "Customer name Priya Shah",
        "Customer name\nPriya Shah",
        "Recipient\nDr. A. B. Smith",
    ),
)
def test_context_labeled_recipient_names_are_fully_redacted(raw: str) -> None:
    assert contains_direct_identifier(raw) is True
    safe = sanitize_text(raw)

    assert safe == "[recipient name removed]"
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


@pytest.mark.parametrize(
    "raw",
    (
        "Customer name fields are optional.",
        "Account holder benefits are available.",
        "Customer name Best Practices",
        "Account holder Policy Guidance",
    ),
)
def test_generic_unpunctuated_name_context_copy_is_preserved(raw: str) -> None:
    assert sanitize_text(raw) == raw
    assert contains_direct_identifier(raw) is False


def test_long_payload_and_css_lines_are_removed_without_dropping_copy() -> None:
    long_payload = ";1MDA," + (" template payload" * 350)
    css_payload = "font-family: Arial; color: #111111; width: 600px;"
    raw = f"Product details\n{long_payload}\n{css_payload}\nShop the edit"

    assert contains_direct_identifier(raw) is True
    safe = sanitize_text(raw)

    assert safe == "Product details\nShop the edit"
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


def test_personalized_account_balance_is_redacted_but_rewards_offer_remains() -> None:
    account_state = (
        "Your Rewards points balance: 125 points (worth $5).",
        "You have 1,250 points.",
        "Your available rewards: $5.",
        "Your wallet: $5.",
        "Your current tier: 2",
        "Your current tier: Gold",
        "VIP tier: Gold",
        "You currently have 875 points.",
        "Your points: 875",
        "Rewards balance: 875",
        "Account balance: $5.",
        "Current tier: Silver",
    )
    rewards_offers = (
        "Earn 100 Rewards points on your next order.",
        "Get $5 in rewards when you spend $50.",
        "Reach VIP tier status after 5 orders.",
    )
    raw = "\n".join((*account_state, *rewards_offers))

    assert contains_direct_identifier(raw) is True
    safe = sanitize_text(raw)

    assert all(value not in safe for value in account_state)
    assert safe.count("[recipient account value removed]") == len(account_state)
    assert all(value in safe for value in rewards_offers)
    assert sanitize_text(safe) == safe
    assert contains_direct_identifier(safe) is False


def test_parser_applies_payload_cleanup_before_record_persistence() -> None:
    message = EmailMessage()
    message["From"] = f"Alder Row <{_address('news', 'alder-row.test')}>"
    message["To"] = f"Research Archive <{_address('research.archive')}>"
    message["Date"] = "Tue, 14 Jul 2026 08:00:00 +0000"
    message["Subject"] = "Collection guide"
    message["Message-ID"] = f"<{_address('privacy-hardening-payload', 'alder-row.test')}>"
    message["List-ID"] = "Alder Row News <news.alder-row.test>"
    first_opaque = "AB12CD34EF56GH78IJ90KL12MN34"
    second_opaque = "qr12st34uv56wx78yz90ab12cd34"
    message.set_content(
        "[Shop the collection]([link removed]\n"
        f"/campaign/path?first={first_opaque}&second={second_opaque}\n"
        "=E2=80=94\n"
        "/div>\n"
        "a/h2>"
    )
    envelope = SourceEnvelope(
        raw_bytes=message.as_bytes(),
        source_type="imap",
        source_uid="privacy-hardening-payload",
        uidvalidity="7",
        canonical_received_at=datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc),
    )

    record = parse_envelope(envelope)
    persisted = "\n".join((record.subject, record.preheader, record.visible_text))

    assert first_opaque not in persisted
    assert second_opaque not in persisted
    assert "/div>" not in persisted
    assert "a/h2>" not in persisted
    assert contains_direct_identifier(persisted) is False


def test_parser_does_not_create_transfer_payload_across_field_boundaries() -> None:
    message = EmailMessage()
    message["From"] = f"Alder Row <{_address('news', 'alder-row.test')}>"
    message["To"] = f"Research Archive <{_address('research.archive')}>"
    message["Date"] = "Tue, 14 Jul 2026 08:00:00 +0000"
    message["Subject"] = "Formula guide"
    message["X-Preheader"] = "Save this equation ="
    message["Message-ID"] = f"<{_address('privacy-hardening-boundary', 'alder-row.test')}>"
    message["List-ID"] = "Alder Row News <news.alder-row.test>"
    message.set_content("Use x=20, y=30, and z=40 in the model.")
    envelope = SourceEnvelope(
        raw_bytes=message.as_bytes(),
        source_type="imap",
        source_uid="privacy-hardening-boundary",
        uidvalidity="7",
        canonical_received_at=datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc),
    )

    record = parse_envelope(envelope)

    assert record.preheader == "Save this equation ="
    assert record.visible_text == "Use x=20, y=30, and z=40 in the model."
    assert contains_direct_identifier(record.preheader) is False
    assert contains_direct_identifier(record.visible_text) is False


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


def test_plus_address_tag_is_not_added_to_recipient_term_deny_list() -> None:
    headers = [f"Research Archive <{_address('em+nike')}>" ]

    terms = recipient_terms_from_headers(headers)
    safe = sanitize_text("Nike launches a new collection.", recipient_terms=terms)

    assert "nike" not in terms
    assert safe == "Nike launches a new collection."


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


def test_parser_strips_double_encoded_short_tracking_fragments_before_persistence() -> None:
    message = EmailMessage()
    message["From"] = f"Alder Row <{_address('news', 'alder-row.test')}>"
    message["To"] = f"Research Archive <{_address('research.archive')}>"
    message["Date"] = "Tue, 14 Jul 2026 08:00:00 +0000"
    message["Subject"] = "Material guide"
    message["Message-ID"] = f"<{_address('privacy-hardening-2', 'alder-row.test')}>"
    message["List-ID"] = "Alder Row News <news.alder-row.test>"
    qp_equals = "=" + "3D"
    message.set_content(
        "Material details\n"
        + _short_tracking_fragment(operator=qp_equals, separator="&amp;")
    )
    envelope = SourceEnvelope(
        raw_bytes=message.as_bytes(),
        source_type="imap",
        source_uid="privacy-hardening-2",
        uidvalidity="7",
        canonical_received_at=datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc),
    )

    record = parse_envelope(envelope)
    persisted = "\n".join((record.subject, record.preheader, record.visible_text))

    assert "opaque-campaign-value" not in persisted
    assert "opaque-message-value" not in persisted
    assert "opaque-recipient-value" not in persisted
    assert contains_direct_identifier(persisted) is False


def test_optional_ai_payload_reuses_the_canonical_sanitizer() -> None:
    qp_fragment = _short_tracking_fragment(operator="=" + "3D", separator="&amp;")
    payload = build_ai_payload(
        "Hi Zach!",
        "Open go.brand.test/click?email_id=private-value",
        "[[first_name]], your edit is ready. email_id=second-private-value "
        + qp_fragment,
    )
    serialized = "\n".join(payload.values())

    assert "Zach" not in serialized
    assert "brand.test" not in serialized
    assert "private-value" not in serialized
    assert "first_name" not in serialized
    assert "opaque-recipient-value" not in serialized
    assert_recipient_safe(serialized)


def test_dashboard_export_sanitizes_direct_record_input() -> None:
    qp_fragment = _short_tracking_fragment(operator="=" + "3D", separator="&amp;")
    record = {
        "id": "privacy-dashboard-1",
        "brand": "Alder Row",
        "canonical_received_at": "2026-07-14T08:00:00Z",
        "subject": "Hey Zach, open go.brand.test/click?email_id=private-value",
        "preheader": "[[first_name]], your edit is ready",
        "visible_text": "Product details " + qp_fragment,
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
    assert "opaque-recipient-value" not in document


def test_unsafe_raw_value_fails_the_boundary_assertion() -> None:
    with pytest.raises(ValueError, match="survived sanitization"):
        assert_recipient_safe("Open click.brand.test/u?email_id=private-value")


def test_encoded_short_tracking_value_fails_the_boundary_assertion() -> None:
    unsafe = _short_tracking_fragment(operator="=" + "3D", separator="&amp;")

    with pytest.raises(ValueError, match="survived sanitization"):
        assert_recipient_safe(unsafe)
