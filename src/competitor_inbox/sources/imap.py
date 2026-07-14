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


@dataclass(slots=True)
class ImapConfig:
    username: str
    mailbox: str = "INBOX"
    host: str = "imap.gmail.com"
    port: int = 993
    sender_domains: tuple[str, ...] = ()
    timeout_seconds: int = 30
    max_retries: int = 2
    keychain_service: str = KEYCHAIN_SERVICE

    def __post_init__(self) -> None:
        self.sender_domains = tuple(
            domain for value in self.sender_domains if (domain := sanitize_domain(value))
        )
        mailbox_key = self.mailbox.casefold().replace(" ", "")
        if "spam" in mailbox_key or "trash" in mailbox_key:
            raise ValueError("Spam and Trash mailboxes are not valid ingestion sources")


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


def _extract_raw(response: Sequence[object]) -> tuple[bytes, bytes]:
    for item in response:
        if isinstance(item, tuple) and len(item) >= 2:
            metadata, raw = item[0], item[1]
            if isinstance(metadata, bytes) and isinstance(raw, bytes):
                return metadata, raw
    raise RuntimeError("IMAP fetch returned no RFC822 payload")


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
        connection = self.connection_factory(
            self.config.host,
            self.config.port,
            ssl_context=context,
            timeout=self.config.timeout_seconds,
        )
        connection.login(self.config.username, password)
        status, _ = connection.select(self.config.mailbox, readonly=True)  # readonly sends EXAMINE
        if status != "OK":
            connection.logout()
            raise RuntimeError("IMAP EXAMINE failed")
        self._connection = connection
        self.uidvalidity = self._read_uidvalidity(connection)

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
            pass
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
        password = self.credentials.require(
            self.config.username,
            prompt_if_missing=prompt_for_credential,
        )
        self._connect(password)
        assert self._connection is not None
        try:
            status, data = self._connection.uid(
                "search",
                None,
                "SINCE",
                since.strftime("%d-%b-%Y"),
            )
            if status != "OK":
                raise RuntimeError("IMAP UID SEARCH failed")
            uids = data[0].split() if data and data[0] else []
            initial_uidvalidity = self.uidvalidity
            for uid_bytes in uids:
                uid = uid_bytes.decode("ascii")
                for attempt in range(self.config.max_retries + 1):
                    try:
                        assert self._connection is not None
                        fetch_status, fetch_data = self._connection.uid(
                            "fetch",
                            uid,
                            "(UID INTERNALDATE X-GM-LABELS BODY.PEEK[])",
                        )
                        if fetch_status != "OK":
                            raise imaplib.IMAP4.abort("UID FETCH failed")
                        metadata, raw = _extract_raw(fetch_data)
                        break
                    except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError):
                        if attempt >= self.config.max_retries:
                            raise
                        self._reconnect(password)
                        if self.uidvalidity != initial_uidvalidity:
                            raise RuntimeError("UIDVALIDITY changed during ingestion; full rescan required")
                if b"\\Spam" in metadata or b"\\Trash" in metadata:
                    continue
                received_at = _internal_date(metadata)
                if received_at < since:
                    continue
                if not _allowed(_sender_domain(raw), self.config.sender_domains):
                    continue
                yield SourceEnvelope(
                    raw_bytes=raw,
                    source_type="imap",
                    source_uid=uid,
                    canonical_received_at=received_at,
                    received_at_source="imap_internaldate",
                    received_at_trusted=True,
                    mailbox=self.config.mailbox,
                    uidvalidity=initial_uidvalidity,
                )
        finally:
            self._disconnect()
