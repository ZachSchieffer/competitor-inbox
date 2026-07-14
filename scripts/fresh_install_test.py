#!/usr/bin/env python3
"""Run the public credential-free install path from an immutable Git ref."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


DEFAULT_SOURCE = "https://github.com/ZachSchieffer/competitor-inbox.git"
DEFAULT_REF = "v1.0.2"
LOCAL_USER_PATH_RE = re.compile(
    rb"(?:/Users/[A-Za-z0-9._-]+/|/home/[A-Za-z0-9._-]+/|[A-Za-z]:\\Users\\[^\\\s]+\\)"
)


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        stdin=subprocess.DEVNULL,
    )


def _capture(command: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    return result.stdout


def find_local_user_paths(roots: list[Path]) -> list[str]:
    """Return files containing machine-specific user-home paths."""

    findings: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or ".git" in path.parts:
                continue
            try:
                payload = path.read_bytes()
            except OSError:
                continue
            if LOCAL_USER_PATH_RE.search(payload):
                findings.append(str(path.relative_to(root)))
    return sorted(set(findings))


def unconditional_requirements(installed_python: Path) -> list[str]:
    probe = (
        "import importlib.metadata as m, json; "
        "print(json.dumps(m.requires('competitor-inbox') or []))"
    )
    requirements = json.loads(_capture([str(installed_python), "-c", probe]))
    return sorted(
        str(requirement)
        for requirement in requirements
        if "extra ==" not in str(requirement).casefold()
    )


def run_fresh_install(source: str, ref: str, *, python: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="competitor-inbox-fresh-install-") as temporary:
        root = Path(temporary)
        checkout = root / "checkout"
        environment = root / "venv"
        data_root = root / "private-data"

        _run(["git", "clone", "--no-hardlinks", source, str(checkout)])
        _run(["git", "checkout", "--detach", ref], cwd=checkout)
        expected_git_sha = _capture(["git", "rev-parse", "HEAD"], cwd=checkout).strip()
        _run([python, "-m", "venv", str(environment)])
        installed_python = environment / "bin" / "python"
        _run([str(installed_python), "-m", "pip", "install", "."], cwd=checkout)
        _run([str(installed_python), "-m", "pip", "check"], cwd=checkout)

        runtime_requirements = unconditional_requirements(installed_python)
        if runtime_requirements:
            raise RuntimeError(
                "fresh install has undeclared mandatory runtime dependencies: "
                + ", ".join(runtime_requirements)
            )

        base = [str(installed_python), "-m", "competitor_inbox"]
        command_env = dict(os.environ)
        for name in list(command_env):
            if any(token in name.casefold() for token in ("anthropic", "api_key", "token")):
                command_env.pop(name, None)
        for command in ("doctor", "demo", "build", "verify"):
            _run(
                [*base, command, "--data-root", str(data_root), "--json"],
                cwd=root,
                env=command_env,
            )

        freeze_path = data_root / "demo" / "freeze-manifest.json"
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        if freeze.get("git_sha") != expected_git_sha or freeze.get("git_dirty") is not False:
            raise RuntimeError(
                "fresh install freeze is not bound to the clean immutable checkout"
            )

        forbidden = [
            data_root / "master.json",
            data_root / "config.toml",
        ]
        if any(path.exists() for path in forbidden):
            raise RuntimeError("fresh demo install created a production data or config file")
        raw_files = [path for path in (data_root / "raw").rglob("*") if path.is_file()]
        if raw_files:
            raise RuntimeError("fresh demo install created raw mail")
        credential_files = [
            path
            for path in data_root.rglob("*")
            if path.is_file()
            and (
                path.name in {".env", "config.toml"}
                or path.suffix.casefold() in {".token", ".key", ".pem"}
            )
        ]
        if credential_files:
            raise RuntimeError("fresh demo install created a credential-bearing file")
        local_paths = find_local_user_paths([checkout, data_root])
        if local_paths:
            raise RuntimeError(
                "fresh install contains machine-specific user paths: "
                + ", ".join(local_paths)
            )

        return {
            "passed": True,
            "source": source,
            "ref": ref,
            "git_sha": expected_git_sha,
            "freeze_git_sha": freeze.get("git_sha"),
            "freeze_git_clean": freeze.get("git_dirty") is False,
            "commands": ["doctor", "demo", "build", "verify"],
            "production_data_created": False,
            "credentials_requested": False,
            "stdin_closed_for_commands": True,
            "pip_check": "passed",
            "unconditional_requirements": runtime_requirements,
            "machine_specific_user_paths": local_paths,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--ref", default=DEFAULT_REF)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    result = run_fresh_install(args.source, args.ref, python=args.python)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
