"""Read-only source adapters."""

from .imap import ImapConfig, ImapSource, KeychainCredentialStore
from .mbox import MboxSource

__all__ = [
    "ImapConfig",
    "ImapSource",
    "KeychainCredentialStore",
    "MboxSource",
]

