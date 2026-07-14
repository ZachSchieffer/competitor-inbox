"""Read-only mbox/Takeout adapter."""

from __future__ import annotations

import hashlib
import mailbox
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Iterator, Sequence

from ..sanitize import sanitize_domain
from ..schema import SourceEnvelope


def _inside_git_worktree(path: Path) -> bool:
    current = path.resolve(strict=False)
    for parent in (current, *current.parents):
        if (parent / ".git").exists():
            return True
    return False


@dataclass(frozen=True, slots=True)
class ReceiptTime:
    value: datetime
    source: str
    trusted: bool


def _parse_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if re.fullmatch(r"\d{9,16}(?:\.\d+)?", text):
            numeric = float(text)
            if numeric >= 10_000_000_000:
                numeric /= 1_000
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
        parsed = parsedate_to_datetime(text)
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _received_header_time(message: mailbox.mboxMessage) -> datetime | None:
    for header in ("Received", "X-Received"):
        for value in message.get_all(header, []):
            candidate = str(value).rsplit(";", 1)[-1].strip()
            parsed = _parse_timestamp(candidate)
            if parsed is not None:
                return parsed
    return None


def _separator_time(message: mailbox.mboxMessage) -> datetime | None:
    separator = str(message.get_from() or "").strip()
    parts = separator.split()
    for start in range(1, len(parts)):
        parsed = _parse_timestamp(" ".join(parts[start:]))
        if parsed is not None:
            return parsed
    return None


def _received_at(message: mailbox.mboxMessage, fallback: datetime) -> ReceiptTime:
    for header, source in (
        ("X-Delivery-Time", "x_delivery_time"),
        ("Delivery-Date", "delivery_date"),
    ):
        parsed = _parse_timestamp(message.get(header))
        if parsed is not None:
            return ReceiptTime(parsed, source, True)

    received = _received_header_time(message)
    if received is not None:
        return ReceiptTime(received, "received_header", True)

    separator = _separator_time(message)
    if separator is not None:
        return ReceiptTime(separator, "mbox_separator", True)

    header_date = _parse_timestamp(message.get("Date"))
    if header_date is not None:
        return ReceiptTime(header_date, "message_date_untrusted", False)

    return ReceiptTime(fallback.astimezone(timezone.utc), "file_mtime_untrusted", False)


def _sender_domain(message: mailbox.mboxMessage) -> str:
    _, address = parseaddr(str(message.get("From", "")))
    return sanitize_domain(address.rsplit("@", 1)[-1] if "@" in address else "")


def _domain_allowed(domain: str, allowed: Sequence[str]) -> bool:
    if not allowed:
        return True
    return any(domain == value or domain.endswith(f".{value}") for value in allowed)


class MboxSource:
    def __init__(
        self,
        path: str | Path,
        *,
        since: datetime | None = None,
        sender_domains: Sequence[str] = (),
        source_label: str = "mbox-import",
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        if _inside_git_worktree(self.path):
            raise ValueError("mbox source cannot live inside a Git worktree")
        self.since = since.astimezone(timezone.utc) if since and since.tzinfo else since
        if self.since and self.since.tzinfo is None:
            self.since = self.since.replace(tzinfo=timezone.utc)
        self.sender_domains = tuple(
            domain for value in sender_domains if (domain := sanitize_domain(value))
        )
        self.source_label = source_label

    def iter_messages(self) -> Iterator[SourceEnvelope]:
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        fallback = datetime.fromtimestamp(self.path.stat().st_mtime, tz=timezone.utc)
        source = mailbox.mbox(self.path, create=False)
        try:
            for index, message in enumerate(source):
                if not isinstance(message, mailbox.mboxMessage):
                    message = mailbox.mboxMessage(message)
                receipt_time = _received_at(message, fallback)
                if self.since and receipt_time.trusted and receipt_time.value < self.since:
                    continue
                domain = _sender_domain(message)
                if not _domain_allowed(domain, self.sender_domains):
                    continue
                raw = message.as_bytes(policy=policy.default)
                digest = hashlib.sha256(raw).hexdigest()[:24]
                yield SourceEnvelope(
                    raw_bytes=raw,
                    source_type="mbox",
                    source_uid=f"{index}:{digest}",
                    canonical_received_at=receipt_time.value,
                    received_at_source=receipt_time.source,
                    received_at_trusted=receipt_time.trusted,
                    mailbox=self.source_label,
                    uidvalidity=None,
                )
        finally:
            source.close()
