from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .store import (
    UnsafeDataRootError,
    atomic_write_bytes,
    ensure_private_data_root as _ensure_private_data_root,
    read_bytes_no_follow,
)


KEYCHAIN_SERVICE = "competitor-inbox-imap"
DEFAULT_DATA_ROOT = Path.home() / "competitor-inbox-data"


@dataclass(slots=True)
class SourceConfig:
    mode: str = "imap"
    host: str = "imap.gmail.com"
    port: int = 993
    mailbox: str = "INBOX"
    account: str = ""
    label: str = ""
    domains: list[str] = field(default_factory=list)
    brand_aliases: dict[str, str] = field(default_factory=dict)
    mbox_path: str = ""


@dataclass(slots=True)
class AnalysisConfig:
    model: str = "claude-sonnet-4-6"
    ai_enabled: bool = True
    timezone: str = "America/Phoenix"


@dataclass(slots=True)
class AppConfig:
    data_root: Path = DEFAULT_DATA_ROOT
    source: SourceConfig = field(default_factory=SourceConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    retain_raw: bool = True


def ensure_private_data_root(path: Path) -> Path:
    return _ensure_private_data_root(path)


def load_config(path: Path | None = None, *, data_root: Path | None = None) -> AppConfig:
    root = (data_root or DEFAULT_DATA_ROOT).expanduser()
    config_path = path or (root / "config.toml")
    if config_path.is_symlink():
        raise UnsafeDataRootError("private configuration files cannot be symlinks")
    if not config_path.exists():
        return AppConfig(data_root=root)
    raw = tomllib.loads(read_bytes_no_follow(config_path).decode("utf-8"))
    resolved_root = Path(raw.get("data_root") or root).expanduser()
    # ``source``/``analysis`` were used by the first private prototype. Public
    # configuration uses the clearer ``inbox``/``filters``/``classification``
    # sections. Reading both keeps the private file upgrade-safe.
    source_raw: dict[str, Any] = raw.get("source") or raw.get("inbox") or {}
    filters_raw: dict[str, Any] = raw.get("filters") or {}
    analysis_raw: dict[str, Any] = raw.get("analysis") or raw.get("classification") or {}
    provider = str(source_raw.get("mode") or source_raw.get("provider") or "imap")
    if provider == "gmail_imap":
        provider = "imap"
    return AppConfig(
        data_root=resolved_root,
        source=SourceConfig(
            mode=provider,
            host=str(source_raw.get("host", "imap.gmail.com")),
            port=int(source_raw.get("port", 993)),
            mailbox=str(source_raw.get("mailbox", "INBOX")),
            account=str(source_raw.get("account") or source_raw.get("address") or ""),
            label=str(source_raw.get("label", "")),
            domains=[
                str(v).lower()
                for v in (source_raw.get("domains") or filters_raw.get("sender_domains") or [])
            ],
            brand_aliases={
                str(key).casefold(): str(value)
                for key, value in (
                    source_raw.get("brand_aliases") or filters_raw.get("brand_aliases") or {}
                ).items()
            },
            mbox_path=str(source_raw.get("mbox_path") or source_raw.get("path") or ""),
        ),
        analysis=AnalysisConfig(
            model=str(analysis_raw.get("model", "claude-sonnet-4-6")),
            ai_enabled=str(analysis_raw.get("mode", "")).casefold() != "deterministic_only"
            and bool(analysis_raw.get("ai_enabled", True)),
            timezone=str(
                analysis_raw.get("timezone")
                or raw.get("timezone")
                or "America/Phoenix"
            ),
        ),
        retain_raw=bool(raw.get("retain_raw", True)),
    )


def _toml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    root = ensure_private_data_root(config.data_root)
    requested_target = (path or (root / "config.toml")).expanduser()
    target = Path(os.path.abspath(os.fspath(requested_target)))
    if target.is_symlink():
        raise UnsafeDataRootError("private configuration files cannot be symlinks")
    resolved_candidate = target.parent.resolve(strict=False) / target.name
    if resolved_candidate != root and root not in resolved_candidate.parents:
        raise UnsafeDataRootError("private configuration must remain under the data root")
    domains = ", ".join(_toml_quote(v) for v in config.source.domains)
    aliases = ", ".join(
        f"{_toml_quote(key)} = {_toml_quote(value)}"
        for key, value in sorted(config.source.brand_aliases.items())
    )
    content = "\n".join(
        [
            f"data_root = {_toml_quote(str(root))}",
            f"retain_raw = {'true' if config.retain_raw else 'false'}",
            "",
            "[inbox]",
            f"provider = {_toml_quote('gmail_imap' if config.source.mode == 'imap' else config.source.mode)}",
            f"host = {_toml_quote(config.source.host)}",
            f"port = {config.source.port}",
            f"mailbox = {_toml_quote(config.source.mailbox)}",
            f"address = {_toml_quote(config.source.account)}",
            f"label = {_toml_quote(config.source.label)}",
            f"mbox_path = {_toml_quote(config.source.mbox_path)}",
            "",
            "[filters]",
            f"sender_domains = [{domains}]",
            f"brand_aliases = {{{aliases}}}",
            "",
            "[classification]",
            f"model = {_toml_quote(config.analysis.model)}",
            f"ai_enabled = {'true' if config.analysis.ai_enabled else 'false'}",
            f"timezone = {_toml_quote(config.analysis.timezone)}",
            "",
        ]
    )
    atomic_write_text(target, content, mode=0o600)
    return target


def atomic_write_text(path: Path, content: str, *, mode: int = 0o600) -> None:
    atomic_write_bytes(path, content.encode("utf-8"), mode=mode)


def is_private_mode(path: Path) -> bool:
    try:
        status = path.lstat()
    except FileNotFoundError:
        return False
    return not stat.S_ISLNK(status.st_mode) and stat.S_IMODE(status.st_mode) & 0o077 == 0
