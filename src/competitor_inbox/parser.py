"""MIME parsing into recipient-safe canonical records."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Mapping

from .analysis import classify_scope_evidence
from .sanitize import (
    assert_recipient_safe,
    canonical_brand,
    recipient_terms_from_headers,
    sanitize_domain,
    sanitize_identifier,
    sanitize_text,
)
from .schema import (
    MessageScope,
    NormalizedMessage,
    ParseFailure,
    SourceEnvelope,
    isoformat_utc,
    normalize_source_mailbox,
)


CAMPAIGN_HEADERS = (
    "X-Campaign-Id",
    "X-Klaviyo-Campaign-Id",
    "X-MC-Campaign",
    "X-Entity-Ref-ID",
    "X-SES-Outgoing",
)

RECIPIENT_HEADERS = (
    "To",
    "Cc",
    "Delivered-To",
    "X-Original-To",
    "Envelope-To",
)


class EmailParseError(ValueError):
    pass


class _VisibleHTMLParser(HTMLParser):
    BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "nav",
        "p",
        "section",
        "table",
        "td",
        "th",
        "tr",
    }
    SKIP_TAGS = {"head", "script", "style", "svg", "template", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif not self._skip_depth and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif not self._skip_depth and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeError, ValueError):
        return str(value)


def _part_text(part: Message) -> str:
    try:
        value = part.get_content()
        if isinstance(value, bytes):
            charset = part.get_content_charset() or "utf-8"
            return value.decode(charset, "replace")
        return str(value)
    except (LookupError, UnicodeError, ValueError, AttributeError):
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload
        return payload.decode(part.get_content_charset() or "utf-8", "replace")


def _content_leaf_parts(message: Message):
    """Yield visible leaves without descending into attachments or RFC822 parts."""

    disposition = (message.get_content_disposition() or "").lower()
    # A filename is attachment evidence even when a sender marks the part
    # ``inline``. Treating a named text file as message copy can persist order
    # exports or receipts that happen to use text/plain.
    if (
        disposition == "attachment"
        or bool(message.get_filename())
        or message.get_content_type().lower() == "message/rfc822"
    ):
        return
    if message.is_multipart():
        payload = message.get_payload()
        if isinstance(payload, list):
            for child in payload:
                if isinstance(child, Message):
                    yield from _content_leaf_parts(child)
        return
    yield message


def _visible_text(message: Message, *, recipient_terms: tuple[str, ...] = ()) -> str:
    plain: list[str] = []
    html_parts: list[str] = []
    for part in _content_leaf_parts(message):
        content_type = part.get_content_type().lower()
        if content_type == "text/plain":
            plain.append(_part_text(part))
        elif content_type == "text/html":
            parser = _VisibleHTMLParser()
            try:
                parser.feed(_part_text(part))
                html_parts.append(parser.text())
            except (ValueError, TypeError):
                continue
    chosen = "\n".join(plain).strip() or "\n".join(html_parts).strip()
    return sanitize_text(chosen, max_chars=100_000, recipient_terms=recipient_terms)


def _rfc822_payload(part: Message) -> Message | None:
    payload = part.get_payload()
    if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], Message):
        return payload[0]
    if isinstance(payload, Message):
        return payload
    return None


def _embedded_message(message: Message) -> tuple[Message | None, bool]:
    """Unwrap only a demonstrable forward; flag every other RFC822 container."""

    parts = [
        part
        for part in message.walk()
        if part is not message and part.get_content_type().lower() == "message/rfc822"
    ]
    if message.get_content_type().lower() == "message/rfc822":
        parts.insert(0, message)
    if not parts:
        return None, False

    outer_subject = _decode_header(message.get("Subject"))
    explicit_forward = bool(
        re.match(r"(?i)^\s*(?:fwd?|forwarded)\s*:", outer_subject)
    )
    outer_is_bulk = bool(
        message.get("List-ID")
        or message.get("List-Unsubscribe")
        or (_decode_header(message.get("Precedence")) or "").casefold()
        in {"bulk", "list"}
    )
    safe_wrapper = message.get_content_type().lower() == "message/rfc822"
    if len(parts) == 1 and (safe_wrapper or (explicit_forward and not outer_is_bulk)):
        embedded = _rfc822_payload(parts[0])
        if embedded is not None:
            return embedded, False
    return None, True


def _parsed_header_date(value: str | None) -> tuple[datetime | None, str | None]:
    if not value:
        return None, None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed is None:
            return None, "invalid_header_date"
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc), None
    except (TypeError, ValueError, OverflowError):
        return None, "invalid_header_date"


def _sender(message: Message) -> tuple[str, str]:
    name, address = parseaddr(_decode_header(message.get("From")))
    domain = sanitize_domain(address.rsplit("@", 1)[-1] if "@" in address else "")
    return sanitize_text(name, max_chars=120, strip_schemeless_urls=False), domain


def _scope(
    message: Message, subject: str, preheader: str, visible_text: str
) -> tuple[MessageScope, str, float]:
    precedence = (_decode_header(message.get("Precedence")) or "").lower()
    bulk_or_list = bool(
        message.get("List-Unsubscribe")
        or message.get("List-ID")
        or precedence in {"bulk", "list"}
    )
    scope, reason, confidence = classify_scope_evidence(
        subject, preheader, visible_text, bulk_or_list=bulk_or_list
    )
    return MessageScope(scope), reason, confidence


def _content_hash(subject: str, preheader: str, visible_text: str) -> str:
    normalized = "\n".join((subject, preheader, visible_text)).casefold()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode("utf-8", "replace")).hexdigest()


def _record_id(
    envelope: SourceEnvelope,
    message_id: str | None,
    content_hash: str,
    mailbox: str,
) -> str:
    if envelope.source_uid and envelope.uidvalidity:
        identity = (
            f"{envelope.source_type}\0{mailbox}\0"
            f"{envelope.uidvalidity}\0{envelope.source_uid}"
        )
    elif envelope.source_uid:
        identity = f"{envelope.source_type}\0{mailbox}\0{envelope.source_uid}"
    elif message_id:
        identity = message_id
    else:
        identity = content_hash
    return hashlib.sha256(f"record\0{identity}".encode()).hexdigest()


def parse_envelope(
    envelope: SourceEnvelope,
    *,
    brand_aliases: Mapping[str, str] | None = None,
) -> NormalizedMessage:
    """Parse a raw source envelope without retaining unsafe MIME content."""

    try:
        outer = BytesParser(policy=policy.default).parsebytes(envelope.raw_bytes)
    except Exception as exc:  # The email package exposes many codec subclasses.
        raise EmailParseError(f"mime_parse_failed:{type(exc).__name__}") from exc

    embedded, ambiguous_embedded = _embedded_message(outer)
    message = embedded or outer
    parse_errors: list[str] = []
    if embedded:
        parse_errors.append("forward_unwrapped")
    elif ambiguous_embedded:
        parse_errors.append("embedded_message_not_unwrapped")

    recipient_headers: list[str] = []
    for source_message in (outer, message):
        for header in RECIPIENT_HEADERS:
            recipient_headers.extend(str(value) for value in source_message.get_all(header, []))
    recipient_terms = recipient_terms_from_headers(recipient_headers)

    sender_name, sender_domain = _sender(message)
    brand = canonical_brand(sender_name, sender_domain, brand_aliases)
    subject = sanitize_text(
        _decode_header(message.get("Subject")),
        max_chars=500,
        recipient_terms=recipient_terms,
    )
    visible_text = _visible_text(message, recipient_terms=recipient_terms)
    explicit_preheader = _decode_header(message.get("X-Preheader"))
    preheader = sanitize_text(
        explicit_preheader,
        max_chars=300,
        recipient_terms=recipient_terms,
    )
    if not preheader:
        first_line = next((line for line in visible_text.splitlines() if line.strip()), "")
        preheader = sanitize_text(first_line, max_chars=180, recipient_terms=recipient_terms)

    # Validate each independently sanitized field. Joining fields before this
    # check can manufacture a quoted-printable soft break when one field ends
    # in a literal ``=`` and the next field starts after the separator newline.
    for safe_field in (subject, preheader, visible_text):
        assert_recipient_safe(safe_field, recipient_terms=recipient_terms)

    header_date_value, header_error = _parsed_header_date(message.get("Date"))
    if header_error:
        parse_errors.append(header_error)
    canonical_date = envelope.canonical_received_at
    if canonical_date.tzinfo is None:
        canonical_date = canonical_date.replace(tzinfo=timezone.utc)
    canonical_date = canonical_date.astimezone(timezone.utc)
    date_skew = None
    if header_date_value:
        date_skew = abs((canonical_date - header_date_value).total_seconds()) / 86_400
        if date_skew > 7:
            parse_errors.append("header_date_skew_over_7_days")

    raw_message_id = _decode_header(message.get("Message-ID"))
    message_id = sanitize_identifier(raw_message_id, namespace="message")
    list_id = sanitize_identifier(_decode_header(message.get("List-ID")), namespace="list")
    campaign_raw = next(
        (_decode_header(message.get(name)) for name in CAMPAIGN_HEADERS if message.get(name)),
        "",
    )
    campaign_id = sanitize_identifier(campaign_raw, namespace="campaign")
    content_hash = _content_hash(subject, preheader, visible_text)
    mailbox = normalize_source_mailbox(
        sanitize_text(envelope.mailbox, max_chars=120) or "INBOX"
    )
    record_id = _record_id(envelope, message_id, content_hash, mailbox)
    scope, scope_reason, scope_confidence = _scope(
        message, subject, preheader, visible_text
    )
    if ambiguous_embedded:
        scope = MessageScope.UNCERTAIN
        scope_reason = "embedded_message_ambiguous"
        scope_confidence = 0.99

    return NormalizedMessage(
        id=record_id,
        source_type=envelope.source_type,
        source_uid=str(envelope.source_uid),
        uidvalidity=str(envelope.uidvalidity) if envelope.uidvalidity is not None else None,
        mailbox=mailbox,
        message_id=message_id,
        list_id=list_id,
        campaign_id=campaign_id,
        canonical_received_at=isoformat_utc(canonical_date),
        received_at_source=envelope.received_at_source,
        received_at_trusted=envelope.received_at_trusted,
        source_completeness=envelope.source_completeness,
        header_date=isoformat_utc(header_date_value) if header_date_value else None,
        date_skew_days=round(date_skew, 3) if date_skew is not None else None,
        brand=brand,
        sender_name=sender_name or brand,
        sender_domain=sender_domain,
        subject=subject,
        preheader=preheader,
        visible_text=visible_text,
        content_hash=content_hash,
        scope=scope.value,
        scope_reason=scope_reason,
        scope_confidence=scope_confidence,
        variant_ids=[record_id],
        parse_errors=parse_errors,
    )


def try_parse_envelope(
    envelope: SourceEnvelope,
    *,
    brand_aliases: Mapping[str, str] | None = None,
) -> tuple[NormalizedMessage | None, ParseFailure | None]:
    try:
        return parse_envelope(envelope, brand_aliases=brand_aliases), None
    except Exception as exc:
        # Malformed MIME can surface through codec and policy-specific exception
        # classes. Convert every per-message failure into a content-free ledger
        # code so one corrupt email cannot abort the remaining backfill.
        error_code = str(exc) if isinstance(exc, EmailParseError) else f"parse_failed:{type(exc).__name__}"
        return None, ParseFailure(
            source_type=envelope.source_type,
            source_uid=str(envelope.source_uid),
            error_code=error_code,
        )
