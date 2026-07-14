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


LIFECYCLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("welcome", re.compile(r"\bwelcome(?:\s+to|\s+aboard|\s+series)?\b", re.I)),
    ("cart", re.compile(r"\b(?:abandon(?:ed)?|left).{0,30}\bcart\b|\bcart reminder\b", re.I)),
    ("checkout", re.compile(r"\b(?:complete|finish|resume).{0,25}\bcheckout\b", re.I)),
    ("browse", re.compile(r"\b(?:still thinking|caught your eye|viewed).{0,45}\b", re.I)),
    ("post-purchase", re.compile(r"\b(?:thanks for|thank you for).{0,20}\b(?:order|purchase)\b", re.I)),
    ("transactional", re.compile(r"\b(?:order|payment|refund).{0,20}\b(?:confirmed|confirmation|receipt|failed)\b", re.I)),
    ("shipping", re.compile(r"\b(?:shipped|shipping update|out for delivery|delivered|tracking number)\b", re.I)),
    ("account", re.compile(r"\b(?:verify|activate|reset).{0,20}\b(?:account|password|email)\b", re.I)),
    ("back-in-stock", re.compile(r"\b(?:back in stock|restocked|available again)\b", re.I)),
    ("replenishment", re.compile(r"\b(?:time to reorder|running low|replenish|refill reminder)\b", re.I)),
    ("winback", re.compile(r"\b(?:we miss you|come back|been a while)\b", re.I)),
    ("loyalty", re.compile(r"\b(?:loyalty|rewards?).{0,30}\b(?:points|balance|tier|earned)\b", re.I)),
    ("referral", re.compile(r"\b(?:refer a friend|referral reward|your referral)\b", re.I)),
)

BROADCAST_TEXT_RE = re.compile(
    r"\b(?:shop now|new collection|new arrival|sale|save \d|% off|free shipping|"
    r"limited time|ends (?:today|tonight|soon)|read more|discover|introducing)\b",
    re.I,
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


def _visible_text(message: Message, *, recipient_terms: tuple[str, ...] = ()) -> str:
    plain: list[str] = []
    html_parts: list[str] = []
    parts = message.walk() if message.is_multipart() else (message,)
    for part in parts:
        if part.is_multipart():
            continue
        disposition = (part.get_content_disposition() or "").lower()
        if disposition == "attachment":
            continue
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


def _embedded_message(message: Message) -> Message | None:
    for part in message.walk():
        if part.get_content_type().lower() != "message/rfc822":
            continue
        payload = part.get_payload()
        if isinstance(payload, list) and payload and isinstance(payload[0], Message):
            return payload[0]
        if isinstance(payload, Message):
            return payload
    return None


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


def _scope(message: Message, subject: str, visible_text: str) -> tuple[MessageScope, str, float]:
    sample = f"{subject}\n{visible_text[:1200]}"
    for label, pattern in LIFECYCLE_PATTERNS:
        if pattern.search(sample):
            return MessageScope.LIFECYCLE, f"lifecycle:{label}", 0.93

    precedence = (_decode_header(message.get("Precedence")) or "").lower()
    if message.get("List-Unsubscribe") or message.get("List-ID") or precedence in {"bulk", "list"}:
        return MessageScope.BROADCAST, "bulk_or_list_header", 0.96
    if BROADCAST_TEXT_RE.search(sample):
        return MessageScope.BROADCAST, "marketing_language", 0.78
    return MessageScope.UNCERTAIN, "insufficient_scope_evidence", 0.45


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

    embedded = _embedded_message(outer)
    message = embedded or outer
    parse_errors: list[str] = ["forward_unwrapped"] if embedded else []

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

    assert_recipient_safe(
        "\n".join((subject, preheader, visible_text)),
        recipient_terms=recipient_terms,
    )

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
    scope, scope_reason, scope_confidence = _scope(message, subject, visible_text)

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
