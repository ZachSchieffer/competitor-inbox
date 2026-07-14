"""Read-only source adapters."""

from .imap import (
    ImapConfig,
    ImapSource,
    ImapSourceError,
    KeychainCredentialStore,
    normalize_imap_app_password,
)
from .mbox import MboxSource

__all__ = [
    "ImapConfig",
    "ImapSource",
    "ImapSourceError",
    "KeychainCredentialStore",
    "normalize_imap_app_password",
    "MboxSource",
]
