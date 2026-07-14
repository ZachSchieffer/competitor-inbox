from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import AppConfig, atomic_write_text, ensure_private_data_root


LABEL = "com.zhsecom.competitor-inbox"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install(config: AppConfig, *, dry_run: bool = False) -> Path:
    root = ensure_private_data_root(config.data_root)
    target = plist_path()
    payload: dict[str, Any] = {
        "Label": LABEL,
        "ProgramArguments": [
            sys.executable,
            "-m",
            "competitor_inbox",
            "update",
            "--data-root",
            str(root),
        ],
        "RunAtLoad": True,
        "StartCalendarInterval": {"Hour": 7, "Minute": 0},
        "ProcessType": "Background",
        "StandardOutPath": str(root / "logs" / "launchd.stdout.log"),
        "StandardErrorPath": str(root / "logs" / "launchd.stderr.log"),
    }
    if dry_run:
        return target
    for log_name in ("launchd.stdout.log", "launchd.stderr.log"):
        log_path = root / "logs" / log_name
        log_path.touch(exist_ok=True, mode=0o600)
        os.chmod(log_path, 0o600)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = plistlib.dumps(payload, fmt=plistlib.FMT_XML).decode("utf-8")
    atomic_write_text(target, data, mode=0o600)
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
    target = plist_path()
    query = subprocess.run(
        ["launchctl", "print", f"{_domain()}/{LABEL}"],
        check=False,
        capture_output=True,
        text=True,
    )
    state_path = config.data_root / "state" / "run.json"
    state: dict[str, Any] = {}
    if state_path.exists():
        import json

        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {"state_error": "unreadable"}
    return {
        "label": LABEL,
        "installed": target.exists(),
        "loaded": query.returncode == 0,
        "plist": str(target),
        "schedule": "07:00 local daily; RunAtLoad",
        "last_attempt": state.get("last_attempt"),
        "last_success": state.get("last_success"),
        "new_records": state.get("new_distinct_messages"),
        "failures": int(state.get("parse_failures") or 0)
        + int(state.get("ingestion_errors") or 0),
        "note": "Updates require this Mac to be on or to wake.",
    }
