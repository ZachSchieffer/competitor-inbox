"""Command-line interface for The Competitor Inbox."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import webbrowser
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .aggregate import aggregate_records, verify_cross_foot
from .config import (
    DEFAULT_DATA_ROOT,
    AppConfig,
    SourceConfig,
    ensure_private_data_root,
    is_private_mode,
    load_config,
    save_config,
)
from .coverage import assert_coverage_cross_foot, coverage_markdown
from .dashboard import (
    generate_dashboard,
    render_hero_pngs,
    write_freeze_manifest,
    write_hero_candidates,
)
from .demo import DEMO_QUADRANTS, demo_summary, generate_demo_records, write_demo_dataset
from .keychain import has_password, prompt_store
from .pipeline import analyze_private_store, dashboard_records, ingest, pipeline_result_json
from .schedule import install as install_schedule
from .schedule import remove as remove_schedule
from .schedule import status as schedule_status
from .store import MasterStore, atomic_write_json


EARLY_GATE_EXIT = 4


def _data_root(args: argparse.Namespace) -> Path:
    value = getattr(args, "data_root", None)
    return Path(value).expanduser() if value else DEFAULT_DATA_ROOT


def _config(args: argparse.Namespace) -> AppConfig:
    root = _data_root(args)
    config_path = getattr(args, "config", None)
    return load_config(Path(config_path).expanduser() if config_path else None, data_root=root)


def _print(value: object, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True, default=str))
    elif isinstance(value, str):
        print(value)
    else:
        print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _git_state() -> tuple[str, bool]:
    repository = Path(__file__).resolve().parents[2]
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    if revision.returncode != 0:
        return "", True
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    return revision.stdout.strip(), status.returncode != 0 or bool(status.stdout.strip())


def _pipeline_counts(result: Any) -> dict[str, int]:
    total = result.coverage.total
    return {
        "raw_fetched": total.raw_fetched,
        "parse_failures": total.parse_failures,
        "parsed_input": total.parsed_input,
        "variants_collapsed": total.variants_collapsed,
        "distinct_messages": total.distinct_messages,
    }


def _bind_coverage_integrity(summary: dict[str, Any], result: Any) -> None:
    """Make dashboard eligibility inherit the ingestion coverage ledger.

    Aggregation can prove that successfully parsed records have dates, but it
    cannot see source-level failures. A brand is therefore allowed to power a
    public hook only when both its row and the complete source range are clean.
    Unknown-brand parse failures conservatively disqualify every single-brand
    hook while preserving the multi-brand fallback and its limitation note.
    """

    total = result.coverage.total
    globally_complete = total.source_completeness == "complete"
    by_brand = {row.brand: row for row in result.coverage.rows}
    for brand in summary.get("brands", []):
        coverage = by_brand.get(str(brand.get("brand") or ""))
        if coverage is None:
            brand["source_completeness"] = "partial"
            brand["hook_eligible"] = False
            continue
        row_complete = coverage.source_completeness == "complete"
        brand["source_completeness"] = (
            "complete"
            if globally_complete and row_complete
            else (
                coverage.source_completeness
                if not row_complete
                else total.source_completeness
            )
        )
        brand["ingestion_errors"] = coverage.parse_failures + coverage.ingestion_errors
        brand["early_gate_ready"] = (
            coverage.qualified_broadcasts >= 15 and coverage.observed_days >= 45
        )
        brand["hook_eligible"] = (
            globally_complete
            and coverage.hook_gate_status == "eligible"
        )
    summary.setdefault("metadata", {})["source_completeness"] = total.source_completeness
    summary["metadata"]["source_error_count"] = (
        total.parse_failures + total.ingestion_errors
    )


def _render_real(config: AppConfig, result: Any) -> dict[str, object]:
    store = MasterStore(config.data_root)
    records = dashboard_records(store.load())
    summary = aggregate_records(records, pipeline_counts=_pipeline_counts(result), illustrative=False)
    summary["metadata"]["analysis_mode"] = result.ai_mode
    summary["metadata"]["analysis_model"] = (
        config.analysis.model if result.ai_mode == "ai+deterministic" else None
    )
    summary["metadata"]["filters"] = {
        "sender_domains": list(config.source.domains),
        "mailbox_or_label": config.source.label or config.source.mailbox,
        "qualified_scope": "broadcast",
    }
    _bind_coverage_integrity(summary, result)
    verify_cross_foot(summary)
    hero_paths = write_hero_candidates(summary, store.root / "outputs" / "heroes")
    hero_screenshots = render_hero_pngs(hero_paths)
    dashboard = generate_dashboard(summary, store.root / "outputs" / "dashboard.html")
    atomic_write_json(store.root / "outputs" / "census.json", summary)
    git_sha, git_dirty = _git_state()
    freeze = write_freeze_manifest(
        summary,
        dashboard,
        hero_paths,
        store.root / "outputs" / "freeze-manifest.json",
        screenshot_paths=hero_screenshots,
        git_sha=git_sha,
        git_dirty=git_dirty,
    )
    return {
        "dashboard": str(dashboard),
        "hero_candidates": [str(path) for path in hero_paths],
        "hero_screenshots": [str(path) for path in hero_screenshots],
        "freeze_manifest": str(freeze),
        "broadcasts": summary["broadcast_count"],
        "brands": summary["brand_count"],
        "cross_foot": summary["cross_foot"],
    }


def command_doctor(args: argparse.Namespace) -> int:
    config = _config(args)
    root = ensure_private_data_root(config.data_root)
    config_path = root / "config.toml"
    checks = {
        "version": __version__,
        "python": sys.version.split()[0],
        "python_supported": sys.version_info >= (3, 11),
        "macos_keychain_available": sys.platform == "darwin" and bool(subprocess.run(
            ["which", "security"], check=False, capture_output=True
        ).stdout),
        "data_root_outside_git": True,
        "data_root_private": is_private_mode(root),
        "config_exists": config_path.is_file(),
        "config_private": is_private_mode(config_path) if config_path.exists() else None,
        "source_mode": config.source.mode,
        "inbox_configured": bool(config.source.account) if config.source.mode == "imap" else bool(config.source.mbox_path),
        "keychain_credential": has_password(config.source.account) if config.source.mode == "imap" else None,
        "ai_mode": "optional when ANTHROPIC_API_KEY is set" if config.analysis.ai_enabled else "deterministic-only",
        "mac_on_dependency": "Scheduled updates require this Mac to be on or to wake.",
    }
    system_ready = (
        checks["python_supported"]
        and checks["data_root_private"]
        and checks["macos_keychain_available"]
    )
    production_ready = (
        system_ready
        and checks["inbox_configured"]
        and (checks["keychain_credential"] is not False)
    )
    checks["system_ready"] = bool(system_ready)
    checks["production_ready"] = bool(production_ready)
    checks["next_step"] = None if production_ready else "Run setup before a production backfill."
    _print(checks, as_json=args.json)
    return 0 if system_ready else 2


def command_setup(args: argparse.Namespace) -> int:
    root = ensure_private_data_root(_data_root(args))
    config_path = root / "config.toml"
    config = load_config(config_path, data_root=root)
    if not config_path.exists():
        mode = input("Source [imap/mbox] (imap): ").strip().casefold() or "imap"
        if mode not in {"imap", "mbox"}:
            raise ValueError("source must be imap or mbox")
        if mode == "imap":
            account = input("Dedicated inbox address: ").strip()
            if not account:
                raise ValueError("inbox address is required")
            config.source = SourceConfig(mode="imap", account=account)
        else:
            mbox_path = input("Absolute mbox path: ").strip()
            if not mbox_path:
                raise ValueError("mbox path is required")
            config.source = SourceConfig(mode="mbox", mbox_path=mbox_path)
        save_config(config, config_path)
    if config.source.mode == "imap" and not has_password(config.source.account):
        print("macOS Keychain will prompt for the IMAP app password with hidden input.")
        prompt_store(config.source.account)
    _print(
        {
            "configured": True,
            "source_mode": config.source.mode,
            "data_root": str(root),
            "credential_stored_in_keychain": config.source.mode != "imap" or has_password(config.source.account),
        },
        as_json=args.json,
    )
    return 0


def command_backfill(args: argparse.Namespace) -> int:
    config = _config(args)
    result = ingest(config, months=args.months, incremental=False)
    if args.json:
        _print(pipeline_result_json(result), as_json=True)
    else:
        print(coverage_markdown(result.coverage))
        print()
        print("Early Data Gate: " + ("PASS" if result.early_gate.passed else "STOP"))
        for reason in result.early_gate.reasons:
            print(f"- {reason}")
    return 0 if result.early_gate.passed else EARLY_GATE_EXIT


def command_update(args: argparse.Namespace) -> int:
    config = _config(args)
    try:
        result = ingest(config, months=12, incremental=True)
        if not result.early_gate.passed:
            _print(pipeline_result_json(result), as_json=args.json)
            return EARLY_GATE_EXIT
        analyzed = analyze_private_store(config)
        rendered = _render_real(config, analyzed)
        _print(rendered, as_json=args.json)
        return 0
    except Exception as exc:
        if sys.platform == "darwin":
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    'display notification "The prior dashboard was retained." with title "Competitor Inbox update failed"',
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        print(f"Update failed safely: {type(exc).__name__}", file=sys.stderr)
        return 2


def command_demo(args: argparse.Namespace) -> int:
    config = _config(args)
    root = ensure_private_data_root(config.data_root) / "demo"
    dataset = write_demo_dataset(root / "northstar-apparel.json")
    summary = demo_summary()
    summary_path = root / "demo-summary.json"
    atomic_write_json(summary_path, summary)
    dashboard = generate_dashboard(summary, root / "dashboard.html")
    heroes = write_hero_candidates(summary, root / "heroes")
    git_sha, git_dirty = _git_state()
    freeze = write_freeze_manifest(
        summary,
        dashboard,
        heroes,
        root / "freeze-manifest.json",
        git_sha=git_sha,
        git_dirty=git_dirty,
    )
    _print(
        {
            "stamp": "ILLUSTRATIVE PROTOTYPE",
            "dataset": str(dataset),
            "dashboard": str(dashboard),
            "hero_candidates": [str(path) for path in heroes],
            "freeze_manifest": str(freeze),
            "messages": summary["broadcast_count"],
            "quadrants": {row["name"]: row["count"] for row in summary["quadrants"]},
            "cross_foot": summary["cross_foot"],
        },
        as_json=args.json,
    )
    return 0


def command_build(args: argparse.Namespace) -> int:
    config = _config(args)
    store = MasterStore(config.data_root)
    if store.master_path.exists():
        analyzed = analyze_private_store(config)
        rendered = _render_real(config, analyzed)
    else:
        summary = demo_summary()
        demo_root = store.root / "demo"
        rendered = {
            "dashboard": str(generate_dashboard(summary, demo_root / "dashboard.html")),
            "mode": "ILLUSTRATIVE PROTOTYPE",
        }
    _print(rendered, as_json=args.json)
    return 0


def command_verify(args: argparse.Namespace) -> int:
    config = _config(args)
    store = MasterStore(config.data_root)
    checks: dict[str, Any] = {}
    if store.master_path.exists():
        result = analyze_private_store(config)
        assert_coverage_cross_foot(result.coverage, require_quadrants=True)
        checks["production_cross_foot"] = True
        checks["early_data_gate"] = result.early_gate.passed
        dashboard = store.root / "outputs" / "dashboard.html"
    else:
        summary = demo_summary()
        checks["demo_messages"] = summary["broadcast_count"] == 1260
        checks["demo_quadrants"] = {
            row["name"]: row["count"] for row in summary["quadrants"]
        } == DEMO_QUADRANTS
        checks["demo_cross_foot"] = bool(summary["cross_foot"]["passed"])
        dashboard = store.root / "demo" / "dashboard.html"
    if dashboard.exists():
        text = dashboard.read_text(encoding="utf-8")
        checks["dashboard_csp"] = "Content-Security-Policy" in text and "default-src &#x27;none&#x27;" in text
        checks["dashboard_no_script"] = "<script" not in text.casefold()
        checks["dashboard_no_remote_urls"] = "http://" not in text.casefold() and "https://" not in text.casefold()
    else:
        checks["dashboard_exists"] = False
    checks["passed"] = all(value is True for value in checks.values())
    _print(checks, as_json=args.json)
    return 0 if checks["passed"] else 2


def command_open(args: argparse.Namespace) -> int:
    config = _config(args)
    store = MasterStore(config.data_root)
    dashboard = store.root / "outputs" / "dashboard.html"
    if not dashboard.exists():
        dashboard = store.root / "demo" / "dashboard.html"
    if not dashboard.exists():
        raise FileNotFoundError("dashboard does not exist; run build first")
    webbrowser.open(dashboard.as_uri())
    print(str(dashboard))
    return 0


def command_privacy(args: argparse.Namespace) -> int:
    repo = Path.cwd().resolve()
    script = repo / "scripts" / "privacy_audit.py"
    if not script.is_file():
        print("Run privacy-check from the repository root.", file=sys.stderr)
        return 2
    command = [sys.executable, str(script), "--repo", str(repo)]
    if args.json:
        command.append("--json")
    return subprocess.run(command, check=False).returncode


def command_schedule(args: argparse.Namespace) -> int:
    config = _config(args)
    if args.schedule_command == "install":
        _print({"installed": str(install_schedule(config))}, as_json=args.json)
    elif args.schedule_command == "status":
        _print(schedule_status(config), as_json=args.json)
    elif args.schedule_command == "remove":
        _print({"removed": remove_schedule()}, as_json=args.json)
    else:
        raise ValueError("missing schedule action")
    return 0


def _common(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("--data-root", type=Path)
    subparser.add_argument("--config", type=Path)
    subparser.add_argument("--json", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="competitor-inbox", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    commands = {
        "doctor": command_doctor,
        "setup": command_setup,
        "backfill": command_backfill,
        "update": command_update,
        "demo": command_demo,
        "build": command_build,
        "verify": command_verify,
        "open": command_open,
        "privacy-check": command_privacy,
    }
    for name, handler in commands.items():
        child = subparsers.add_parser(name)
        _common(child)
        child.set_defaults(handler=handler)
        if name == "backfill":
            child.add_argument("--months", type=int, default=12)

    schedule = subparsers.add_parser("schedule")
    _common(schedule)
    schedule_sub = schedule.add_subparsers(dest="schedule_command", required=True)
    for action in ("install", "status", "remove"):
        action_parser = schedule_sub.add_parser(action)
        action_parser.set_defaults(handler=command_schedule)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
