"""Canonical records shared by ingestion, analysis, and dashboard code.

The production store contains sanitized metadata and visible text only. Raw RFC822
bytes are represented by :class:`SourceEnvelope` while in transit and are never
serialized through :class:`NormalizedMessage`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


SCHEMA_VERSION = "1.0.0"


class MessageScope(str, Enum):
    BROADCAST = "broadcast"
    LIFECYCLE = "lifecycle"
    UNCERTAIN = "uncertain"


class SourceCompleteness(str, Enum):
    """Whether a record came from a complete source window or an approved subset."""

    COMPLETE = "complete"
    CURATED_EXPORT = "curated_export"


def normalize_source_completeness(value: str | SourceCompleteness) -> str:
    try:
        return SourceCompleteness(value).value
    except (TypeError, ValueError):
        raise ValueError(
            "source_completeness must be complete or curated_export"
        ) from None


def normalize_source_mailbox(value: str | None) -> str:
    """Return the stable mailbox namespace used by every source identity key."""

    normalized = " ".join(str(value or "INBOX").split()).strip() or "INBOX"
    return "INBOX" if normalized.casefold() == "inbox" else normalized


def source_identity_key(
    source_type: str,
    mailbox: str | None,
    uidvalidity: str | None,
    source_uid: str,
) -> tuple[str, str, str | None, str]:
    """Namespace a provider UID by mailbox so labels cannot collide."""

    return (
        str(source_type),
        normalize_source_mailbox(mailbox),
        str(uidvalidity) if uidvalidity is not None else None,
        str(source_uid),
    )


@dataclass(slots=True)
class SourceEnvelope:
    """One source message before parsing.

    ``raw_bytes`` must stay outside JSON logs and public artifacts. ``mailbox`` is
    a logical source label, never a local filesystem path.
    """

    raw_bytes: bytes = field(repr=False)
    source_type: str
    source_uid: str
    canonical_received_at: datetime
    received_at_source: str = "source_provided"
    received_at_trusted: bool = True
    source_completeness: str = SourceCompleteness.COMPLETE.value
    mailbox: str = "INBOX"
    uidvalidity: str | None = None

    def __post_init__(self) -> None:
        normalized_completeness = normalize_source_completeness(
            self.source_completeness
        )
        self.source_completeness = (
            SourceCompleteness.CURATED_EXPORT.value
            if str(self.source_type).strip().casefold() == "curated_export"
            else normalized_completeness
        )

    @property
    def identity_key(self) -> tuple[str, str, str | None, str]:
        return source_identity_key(
            self.source_type,
            self.mailbox,
            self.uidvalidity,
            self.source_uid,
        )


@dataclass(slots=True)
class ParseFailure:
    source_type: str
    source_uid: str
    error_code: str
    brand: str = "Unassigned"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class NormalizedMessage:
    """A recipient-safe normalized email record.

    Values in this object may be persisted to ``master.json``. Consequently it
    intentionally has no raw bytes, raw HTML, addresses, or personalized URLs.
    """

    id: str
    source_type: str
    source_uid: str
    canonical_received_at: str
    brand: str
    sender_name: str
    sender_domain: str
    subject: str
    preheader: str
    visible_text: str
    content_hash: str
    scope: str
    scope_reason: str
    scope_confidence: float
    schema_version: str = SCHEMA_VERSION
    mailbox: str = "INBOX"
    uidvalidity: str | None = None
    message_id: str | None = None
    list_id: str | None = None
    campaign_id: str | None = None
    header_date: str | None = None
    date_skew_days: float | None = None
    received_at_source: str = "source_provided"
    received_at_trusted: bool = True
    source_completeness: str = SourceCompleteness.COMPLETE.value
    variant_count: int = 1
    variant_ids: list[str] = field(default_factory=list)
    parse_status: str = "parsed"
    parse_errors: list[str] = field(default_factory=list)
    redaction_status: str = "sanitized"
    intent: str | None = None
    intent_source: str | None = None
    intent_confidence: float | None = None
    offer_candidates: list[dict[str, Any]] = field(default_factory=list)
    primary_offer: dict[str, Any] | None = None
    seasonal: bool | None = None
    occasion: str | None = None
    classification_model: str | None = None

    def __post_init__(self) -> None:
        normalized_completeness = normalize_source_completeness(
            self.source_completeness
        )
        self.source_completeness = (
            SourceCompleteness.CURATED_EXPORT.value
            if str(self.source_type).strip().casefold() == "curated_export"
            else normalized_completeness
        )
        if self.variant_count < 1:
            raise ValueError("variant_count must be at least 1")
        if not 0 <= self.scope_confidence <= 1:
            raise ValueError("scope_confidence must be between 0 and 1")
        # Ensure the canonical ID is represented exactly once.
        ids = [self.id, *self.variant_ids]
        self.variant_ids = list(dict.fromkeys(value for value in ids if value))
        self.variant_count = max(self.variant_count, len(self.variant_ids))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NormalizedMessage":
        allowed = cls.__dataclass_fields__.keys()
        payload = {key: value[key] for key in allowed if key in value}
        if "received_at_trusted" not in payload and value.get("source_type") == "mbox":
            payload["received_at_trusted"] = False
            payload["received_at_source"] = "legacy_mbox_unknown"
        return cls(**payload)

    @property
    def received_datetime(self) -> datetime:
        return parse_iso_datetime(self.canonical_received_at)

    @property
    def source_identity_key(self) -> tuple[str, str, str | None, str]:
        return source_identity_key(
            self.source_type,
            self.mailbox,
            self.uidvalidity,
            self.source_uid,
        )


def parse_iso_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def isoformat_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
