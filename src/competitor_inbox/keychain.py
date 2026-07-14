from __future__ import annotations

import subprocess

from .config import KEYCHAIN_SERVICE


class KeychainError(RuntimeError):
    pass


def has_password(account: str) -> bool:
    if not account:
        return False
    result = subprocess.run(
        ["security", "find-generic-password", "-a", account, "-s", KEYCHAIN_SERVICE],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def get_password(account: str) -> str:
    if not account:
        raise KeychainError("Inbox account is not configured")
    result = subprocess.run(
        ["security", "find-generic-password", "-a", account, "-s", KEYCHAIN_SERVICE, "-w"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise KeychainError("No app password found in macOS Keychain")
    secret = result.stdout.rstrip("\r\n")
    if not secret:
        raise KeychainError("The macOS Keychain app password is empty")
    return secret


def prompt_store(account: str) -> None:
    if not account:
        raise KeychainError("Inbox account is required before storing a password")
    command = [
        "security",
        "add-generic-password",
        "-a",
        account,
        "-s",
        KEYCHAIN_SERVICE,
        "-l",
        "Competitor Inbox IMAP",
        "-U",
        "-w",
    ]
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise KeychainError("macOS Keychain did not store the app password")

