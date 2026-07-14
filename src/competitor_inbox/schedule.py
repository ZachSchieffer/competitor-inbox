from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import AppConfig, atomic_write_text, ensure_private_data_root
from .store import PRIVATE_FILE_MODE, read_bytes_no_follow


LABEL = "com.zhsecom.competitor-inbox"
SCHEDULE_HOUR_LOCAL = 7
SCHEDULE_MINUTE_LOCAL = 0
INCREMENTAL_OVERLAP_DAYS = 14
MAC_ON_DEPENDENCY = "Updates require this Mac to be on or to wake."


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def launch_agent_payload(
    config: AppConfig,
    *,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Return the secret-free LaunchAgent contract for a daily local update."""

    root = ensure_private_data_root(config.data_root)
    arguments = [
        sys.executable,
        "-m",
        "competitor_inbox",
        "update",
        "--data-root",
        str(root),
    ]
    if config_path is not None:
        requested = Path(config_path).expanduser()
        if requested.is_symlink() or not requested.is_file():
            raise ValueError("scheduled custom config must be an existing regular file")
        arguments.extend(["--config", str(requested.resolve(strict=True))])
    payload: dict[str, Any] = {
        "Label": LABEL,
        "ProgramArguments": arguments,
        "RunAtLoad": True,
        "StartCalendarInterval": {
            "Hour": SCHEDULE_HOUR_LOCAL,
            "Minute": SCHEDULE_MINUTE_LOCAL,
        },
        "ProcessType": "Background",
        "StandardOutPath": str(root / "logs" / "launchd.stdout.log"),
        "StandardErrorPath": str(root / "logs" / "launchd.stderr.log"),
    }
    serialized = plistlib.dumps(payload, fmt=plistlib.FMT_XML).decode("utf-8").casefold()
    forbidden = ("anthropic_api_key", "app_password", "oauth", "cookie")
    if any(token in serialized for token in forbidden):
        raise ValueError("LaunchAgent payload contains a forbidden secret field")
    return payload


def install(
    config: AppConfig,
    *,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> Path:
    root = ensure_private_data_root(config.data_root)
    target = plist_path()
    payload = launch_agent_payload(config, config_path=config_path)
    if dry_run:
        return target
    for log_name in ("launchd.stdout.log", "launchd.stderr.log"):
        log_path = root / "logs" / log_name
        log_path.touch(exist_ok=True, mode=PRIVATE_FILE_MODE)
        os.chmod(log_path, PRIVATE_FILE_MODE)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = plistlib.dumps(payload, fmt=plistlib.FMT_XML).decode("utf-8")
    atomic_write_text(target, data, mode=PRIVATE_FILE_MODE)
    subprocess.run(["launchctl", "bootout", _domain(), str(target)], check=False, capture_output=True)
    subprocess.run(["launchctl", "bootstrap", _domain(), str(target)], check=True, capture_output=True)
    return target


def remove() -> bool:
    target = plist_path()
    subprocess.run(["launchctl", "bootout", _domain(), str(target)], check=False, capture_output=True)
    existed = target.exists()
    target.unlink(missing_ok=True)
    return existed


def status(config: AppConfig) -> dict[str, Any]:
    root = ensure_private_data_root(config.data_root)
    target = plist_path()
    query = subprocess.run(
        ["launchctl", "print", f"{_domain()}/{LABEL}"],
        check=False,
        capture_output=True,
        text=True,
    )
    state_path = root / "state" / "run.json"
    state: dict[str, Any] = {}
    if state_path.exists():
        import json

        try:
            state = json.loads(read_bytes_no_follow(state_path))
        except Exception:
            state = {"state_error": "unreadable"}
    return {
        "label": LABEL,
        "installed": target.exists(),
        "loaded": query.returncode == 0,
        "plist": str(target),
        "schedule": (
            f"{SCHEDULE_HOUR_LOCAL:02d}:{SCHEDULE_MINUTE_LOCAL:02d} "
            "local daily; RunAtLoad"
        ),
        "last_attempt": state.get("last_attempt"),
        "last_success": state.get("last_success"),
        "new_records": state.get("new_distinct_messages"),
        "failures": int(state.get("parse_failures") or 0)
        + int(state.get("ingestion_errors") or 0)
        + int(state.get("update_errors") or 0),
        "incremental_overlap_days": INCREMENTAL_OVERLAP_DAYS,
        "process_lock": "one complete update at a time",
        "failure_behavior": (
            "files replace individually; caught failures restore the prior output package; "
            "prior dashboard retained"
        ),
        "note": MAC_ON_DEPENDENCY,
    }


__all__ = [
    "INCREMENTAL_OVERLAP_DAYS",
    "LABEL",
    "MAC_ON_DEPENDENCY",
    "SCHEDULE_HOUR_LOCAL",
    "SCHEDULE_MINUTE_LOCAL",
    "install",
    "launch_agent_payload",
    "plist_path",
    "remove",
    "status",
]
