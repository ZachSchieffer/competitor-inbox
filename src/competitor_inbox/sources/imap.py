"""Read-only TLS IMAP adapter with macOS Keychain credential storage."""

from __future__ import annotations

import imaplib
import re
import ssl
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.parser import BytesHeaderParser
from email import policy
from email.utils import parseaddr
from typing import Callable, Iterator, Sequence

from ..sanitize import sanitize_domain
from ..schema import SourceEnvelope


KEYCHAIN_SERVICE = "competitor-inbox-imap"
INTERNALDATE_RE = re.compile(rb'INTERNALDATE "([^"]+)"', re.I)
UIDVALIDITY_RE = re.compile(rb"UIDVALIDITY\s+(\d+)", re.I)
FETCH_UID_RE = re.compile(rb"(?:^|[ (])UID\s+(\d+)(?=[ )])", re.I)
ASCII_WHITESPACE_RE = re.compile(r"[ \t\r\n\f\v]+")
GOOGLE_APP_PASSWORD_RE = re.compile(r"[A-Za-z0-9]{16}\Z")
GMAIL_IMAP_HOST = "imap.gmail.com"
DEFAULT_FETCH_BATCH_SIZE = 200
MAX_FETCH_BATCH_SIZE = 500


class ImapSourceError(RuntimeError):
    """An IMAP failure represented by a fixed, non-sensitive stage code."""

    SAFE_CODES = frozenset(
        {
            "imap_auth_rejected",
            "imap_connection_lost",
            "imap_credential_missing",
            "imap_fetch_failed",
            "imap_mailbox_unavailable",
            "imap_search_failed",
            "imap_tls_failed",
            "imap_uidvalidity_unavailable",
        }
    )

    def __init__(self, safe_code: str) -> None:
        if safe_code not in self.SAFE_CODES:
            raise ValueError("invalid IMAP safe error code")
        self.safe_code = safe_code
        super().__init__(safe_code)


def normalize_imap_app_password(secret: str, *, host: str) -> str:
    """Remove Google UI separator whitespace from a valid app-password shape.

    The transformation is intentionally limited to Gmail's IMAP host and a
    16-character ASCII alphanumeric compact value. Other providers and values
    with an unexpected shape are returned byte-for-byte unchanged.
    """

    if host.casefold().rstrip(".") != GMAIL_IMAP_HOST:
        return secret
    compact = ASCII_WHITESPACE_RE.sub("", secret)
    if compact != secret and GOOGLE_APP_PASSWORD_RE.fullmatch(compact):
        return compact
    return secret


def _close_partial_connection(connection: imaplib.IMAP4_SSL) -> None:
    """Close a connection that may not have reached authenticated state."""

    shutdown = getattr(connection, "shutdown", None)
    if callable(shutdown):
        try:
            shutdown()
            return
        except (imaplib.IMAP4.error, OSError):
            pass
    try:
        connection.logout()
    except (imaplib.IMAP4.error, OSError):
        pass


@dataclass(slots=True)
class ImapConfig:
    username: str
    mailbox: str = "INBOX"
    host: str = "imap.gmail.com"
    port: int = 993
    sender_domains: tuple[str, ...] = ()
    timeout_seconds: int = 30
    max_retries: int = 2
    fetch_batch_size: int = DEFAULT_FETCH_BATCH_SIZE
    keychain_service: str = KEYCHAIN_SERVICE

    def __post_init__(self) -> None:
        self.sender_domains = tuple(
            domain for value in self.sender_domains if (domain := sanitize_domain(value))
        )
        mailbox_key = self.mailbox.casefold().replace(" ", "")
        if "spam" in mailbox_key or "trash" in mailbox_key:
            raise ValueError("Spam and Trash mailboxes are not valid ingestion sources")
        if (
            isinstance(self.fetch_batch_size, bool)
            or not isinstance(self.fetch_batch_size, int)
            or not 1 <= self.fetch_batch_size <= MAX_FETCH_BATCH_SIZE
        ):
            raise ValueError(
                f"fetch_batch_size must be between 1 and {MAX_FETCH_BATCH_SIZE}"
            )


class KeychainCredentialStore:
    """Retrieve or securely prompt for an IMAP app password on macOS."""

    def __init__(self, service: str = KEYCHAIN_SERVICE) -> None:
        self.service = service

    def get(self, account: str) -> str | None:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-a",
                account,
                "-s",
                self.service,
                "-w",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if result.returncode:
            return None
        secret = result.stdout.rstrip("\r\n")
        return secret or None

    def prompt_and_store(self, account: str) -> str:
        # A trailing -w makes the security CLI collect the secret without echoing
        # and, critically, without placing it in argv.
        subprocess.run(
            [
                "security",
                "add-generic-password",
                "-a",
                account,
                "-s",
                self.service,
                "-U",
                "-w",
            ],
            check=True,
        )
        secret = self.get(account)
        if not secret:
            raise RuntimeError("Keychain credential was not saved")
        return secret

    def require(self, account: str, *, prompt_if_missing: bool = False) -> str:
        secret = self.get(account)
        if secret:
            return secret
        if prompt_if_missing:
            return self.prompt_and_store(account)
        raise RuntimeError("IMAP credential is missing from macOS Keychain")


def overlap_since(last_success: datetime, *, overlap_days: int = 14) -> datetime:
    if last_success.tzinfo is None:
        last_success = last_success.replace(tzinfo=timezone.utc)
    return last_success.astimezone(timezone.utc) - timedelta(days=overlap_days)


def _parse_search_uids(response: Sequence[object]) -> list[str]:
    """Return a stable, duplicate-free UID order from an IMAP SEARCH response."""

    values: set[int] = set()
    for item in response:
        if item is None:
            continue
        if not isinstance(item, bytes):
            raise RuntimeError("IMAP search returned malformed data")
        for value in item.split():
            if not value.isdigit():
                raise RuntimeError("IMAP search returned a malformed UID")
            parsed = int(value)
            if parsed <= 0:
                raise RuntimeError("IMAP search returned a malformed UID")
            values.add(parsed)
    return [str(value) for value in sorted(values)]


def _uid_batches(uids: Sequence[str], size: int) -> Iterator[list[str]]:
    for offset in range(0, len(uids), size):
        yield list(uids[offset : offset + size])


def _extract_batch(
    response: Sequence[object],
    expected_uids: Sequence[str],
) -> dict[str, tuple[bytes, bytes]]:
    """Validate a complete UID FETCH response before any message can be yielded."""

    expected = set(expected_uids)
    extracted: dict[str, tuple[bytes, bytes]] = {}
    for item in response:
        if item is None:
            continue
        if not isinstance(item, tuple):
            # imaplib emits a separate closing parenthesis after each literal.
            if isinstance(item, bytes) and item.strip() in {b"", b")"}:
                continue
            raise RuntimeError("IMAP fetch returned malformed data")
        if len(item) < 2:
            raise RuntimeError("IMAP fetch returned malformed data")
        metadata, raw = item[0], item[1]
        if not isinstance(metadata, bytes) or not isinstance(raw, bytes):
            raise RuntimeError("IMAP fetch returned malformed data")
        match = FETCH_UID_RE.search(metadata)
        if not match:
            raise RuntimeError("IMAP fetch omitted UID")
        uid = str(int(match.group(1)))
        if uid not in expected or uid in extracted:
            raise RuntimeError("IMAP fetch returned an unexpected or duplicate UID")
        extracted[uid] = (metadata, raw)
    if set(extracted) != expected:
        raise RuntimeError("IMAP fetch returned an incomplete batch")
    return extracted


def _internal_date(metadata: bytes) -> datetime:
    match = INTERNALDATE_RE.search(metadata)
    if not match:
        raise RuntimeError("IMAP fetch omitted INTERNALDATE")
    value = match.group(1).decode("ascii", "strict")
    return datetime.strptime(value, "%d-%b-%Y %H:%M:%S %z").astimezone(timezone.utc)


def _sender_domain(raw: bytes) -> str:
    header = BytesHeaderParser(policy=policy.default).parsebytes(raw, headersonly=True)
    _, address = parseaddr(str(header.get("From", "")))
    return sanitize_domain(address.rsplit("@", 1)[-1] if "@" in address else "")


def _allowed(domain: str, filters: Sequence[str]) -> bool:
    return not filters or any(domain == value or domain.endswith(f".{value}") for value in filters)


class ImapSource:
    def __init__(
        self,
        config: ImapConfig,
        *,
        credential_store: KeychainCredentialStore | None = None,
        connection_factory: Callable[..., imaplib.IMAP4_SSL] = imaplib.IMAP4_SSL,
    ) -> None:
        self.config = config
        self.credentials = credential_store or KeychainCredentialStore(config.keychain_service)
        self.connection_factory = connection_factory
        self._connection: imaplib.IMAP4_SSL | None = None
        self.uidvalidity: str | None = None

    def _connect(self, password: str) -> None:
        context = ssl.create_default_context()
        try:
            connection = self.connection_factory(
                self.config.host,
                self.config.port,
                ssl_context=context,
                timeout=self.config.timeout_seconds,
            )
        except (imaplib.IMAP4.error, OSError, ssl.SSLError):
            raise ImapSourceError("imap_tls_failed") from None
        try:
            connection.login(self.config.username, password)
        except imaplib.IMAP4.error:
            _close_partial_connection(connection)
            raise ImapSourceError("imap_auth_rejected") from None
        except (OSError, ssl.SSLError):
            _close_partial_connection(connection)
            raise ImapSourceError("imap_connection_lost") from None
        try:
            # readonly=True sends EXAMINE rather than SELECT.
            status, _ = connection.select(self.config.mailbox, readonly=True)
        except (imaplib.IMAP4.error, OSError):
            _close_partial_connection(connection)
            raise ImapSourceError("imap_mailbox_unavailable") from None
        if status != "OK":
            _close_partial_connection(connection)
            raise ImapSourceError("imap_mailbox_unavailable")
        try:
            uidvalidity = self._read_uidvalidity(connection)
        except (imaplib.IMAP4.error, OSError, RuntimeError):
            _close_partial_connection(connection)
            raise ImapSourceError("imap_uidvalidity_unavailable") from None
        self._connection = connection
        self.uidvalidity = uidvalidity

    def _read_uidvalidity(self, connection: imaplib.IMAP4_SSL) -> str:
        response = connection.response("UIDVALIDITY")
        if response and len(response) > 1 and response[1]:
            value = response[1][0]
            if isinstance(value, bytes):
                match = re.search(rb"\d+", value)
                if match:
                    return match.group().decode("ascii")
        status, data = connection.status(self.config.mailbox, "(UIDVALIDITY)")
        if status == "OK" and data and isinstance(data[0], bytes):
            match = UIDVALIDITY_RE.search(data[0])
            if match:
                return match.group(1).decode("ascii")
        raise RuntimeError("IMAP server did not report UIDVALIDITY")

    def _disconnect(self) -> None:
        if self._connection is None:
            return
        try:
            self._connection.logout()
        except (imaplib.IMAP4.error, OSError):
            _close_partial_connection(self._connection)
        self._connection = None

    def _reconnect(self, password: str) -> None:
        self._disconnect()
        self._connect(password)

    def iter_messages(
        self,
        *,
        since: datetime,
        prompt_for_credential: bool = False,
    ) -> Iterator[SourceEnvelope]:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        since = since.astimezone(timezone.utc)
        try:
            password = self.credentials.require(
                self.config.username,
                prompt_if_missing=prompt_for_credential,
            )
        except RuntimeError:
            raise ImapSourceError("imap_credential_missing") from None
        password = normalize_imap_app_password(password, host=self.config.host)
        self._connect(password)
        assert self._connection is not None
        try:
            try:
                status, data = self._connection.uid(
                    "search",
                    None,
                    "SINCE",
                    since.strftime("%d-%b-%Y"),
                )
            except (imaplib.IMAP4.error, OSError):
                raise ImapSourceError("imap_search_failed") from None
            if status != "OK":
                raise ImapSourceError("imap_search_failed")
            try:
                uids = _parse_search_uids(data or [])
            except RuntimeError:
                raise ImapSourceError("imap_search_failed") from None
            initial_uidvalidity = self.uidvalidity
            for batch in _uid_batches(uids, self.config.fetch_batch_size):
                envelopes: list[SourceEnvelope] = []
                for attempt in range(self.config.max_retries + 1):
                    try:
                        assert self._connection is not None
                        fetch_status, fetch_data = self._connection.uid(
                            "fetch",
                            ",".join(batch),
                            "(UID INTERNALDATE X-GM-LABELS BODY.PEEK[])",
                        )
                        if fetch_status != "OK":
                            raise imaplib.IMAP4.abort("UID FETCH failed")
                        fetched = _extract_batch(fetch_data or [], batch)
                        pending: list[SourceEnvelope] = []
                        for uid in batch:
                            metadata, raw = fetched[uid]
                            if b"\\Spam" in metadata or b"\\Trash" in metadata:
                                continue
                            received_at = _internal_date(metadata)
                            if received_at < since:
                                continue
                            if not _allowed(_sender_domain(raw), self.config.sender_domains):
                                continue
                            pending.append(
                                SourceEnvelope(
                                    raw_bytes=raw,
                                    source_type="imap",
                                    source_uid=uid,
                                    canonical_received_at=received_at,
                                    received_at_source="imap_internaldate",
                                    received_at_trusted=True,
                                    mailbox=self.config.mailbox,
                                    uidvalidity=initial_uidvalidity,
                                )
                            )
                        envelopes = pending
                        break
                    except (
                        imaplib.IMAP4.abort,
                        imaplib.IMAP4.error,
                        OSError,
                        RuntimeError,
                        UnicodeError,
                        ValueError,
                    ):
                        if attempt >= self.config.max_retries:
                            raise ImapSourceError("imap_fetch_failed") from None
                        self._reconnect(password)
                        if self.uidvalidity != initial_uidvalidity:
                            raise ImapSourceError("imap_uidvalidity_unavailable")
                yield from envelopes
        finally:
            self._disconnect()
