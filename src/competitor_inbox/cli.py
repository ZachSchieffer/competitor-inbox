"""Command-line interface for The Competitor Inbox."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import unquote, urlparse

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
from .coverage import coverage_markdown
from .dashboard import (
    _freeze_metrics,
    derive_dashboard_weekly_activity,
    generate_dashboard,
    hero_selection,
    render_hero,
    render_hero_pngs,
    write_freeze_manifest,
    write_hero_candidates,
)
from .demo import (
    DEMO_ACCOUNT,
    DEMO_QUADRANTS,
    DEMO_STAMP,
    DEMO_TOTAL,
    demo_summary,
    write_demo_dataset,
)
from .keychain import has_password, prompt_store
from .locking import AlreadyRunning, run_lock
from .pipeline import analyze_private_store, dashboard_records, ingest, pipeline_result_json
from .schedule import install as install_schedule
from .schedule import remove as remove_schedule
from .schedule import status as schedule_status
from .store import (
    PRIVATE_DIR_MODE,
    MasterStore,
    UnsafeDataRootError,
    atomic_write_bytes,
    atomic_write_json,
    read_bytes_no_follow,
)


EARLY_GATE_EXIT = 4

_OUTPUT_PACKAGE_FILES = (
    "coverage.json",
    "coverage.md",
    "dashboard.html",
    "dashboard.previous.html",
    "census.json",
    "freeze-manifest.json",
    "heroes/hero-brand.html",
    "heroes/hero-portfolio.html",
    "heroes/hero-brand.png",
    "heroes/hero-portfolio.png",
)


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


def _installed_source_directory() -> Path | None:
    """Return pip's local install source without assuming site-package layout."""

    try:
        raw = distribution("competitor-inbox").read_text("direct_url.json")
    except (PackageNotFoundError, OSError):
        return None
    if not raw:
        return None
    try:
        url = str(json.loads(raw).get("url") or "")
    except (TypeError, json.JSONDecodeError):
        return None
    parsed = urlparse(url)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        return None
    candidate = Path(unquote(parsed.path))
    return candidate if candidate.is_dir() else None


def _git_state() -> tuple[str, bool]:
    """Bind a freeze to the repository that supplied the installed package.

    ``pip install .`` copies modules into site-packages, so walking upward from
    ``__file__`` does not find the release clone. PEP 610's ``direct_url.json``
    retains that local source directory for both regular and editable installs.
    CWD and source parents remain safe fallbacks for direct source execution.
    """

    candidates = [
        _installed_source_directory(),
        Path.cwd(),
        *Path(__file__).resolve().parents,
    ]
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate is None:
            continue
        resolved = candidate.resolve(strict=False)
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=resolved,
            check=False,
            capture_output=True,
            text=True,
        )
        if root.returncode != 0 or not root.stdout.strip():
            continue
        repository = Path(root.stdout.strip())
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
        )
        if revision.returncode != 0:
            continue
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
        )
        return (
            revision.stdout.strip(),
            status.returncode != 0 or bool(status.stdout.strip()),
        )
    return "", True


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


def _remove_private_file(path: Path) -> None:
    if path.is_symlink():
        raise UnsafeDataRootError("private output files cannot be symlinks")
    if not path.exists():
        return
    if not path.is_file():
        raise UnsafeDataRootError("private output path is not a regular file")
    path.unlink()


def _render_real(
    config: AppConfig,
    result: Any,
    *,
    render_screenshots: bool = False,
    hero_priority_brands: Sequence[str] = (),
) -> dict[str, object]:
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
    if hero_priority_brands:
        summary["metadata"]["hero_priority_brands"] = list(hero_priority_brands)
    _bind_coverage_integrity(summary, result)
    verify_cross_foot(summary)
    hero_paths = write_hero_candidates(summary, store.root / "outputs" / "heroes")
    if render_screenshots:
        hero_screenshots = render_hero_pngs(hero_paths)
    else:
        hero_screenshots = []
        for hero in hero_paths:
            _remove_private_file(hero.with_suffix(".png"))
    dashboard_summary = dict(summary)
    dashboard_summary["_dashboard_weekly_activity"] = derive_dashboard_weekly_activity(
        records, summary
    )
    dashboard = generate_dashboard(
        dashboard_summary, store.root / "outputs" / "dashboard.html"
    )
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
        "hero_screenshot_mode": (
            "rendered" if render_screenshots else "not requested; dashboard build is browser-free"
        ),
        "freeze_manifest": str(freeze),
        "broadcasts": summary["broadcast_count"],
        "brands": summary["brand_count"],
        "cross_foot": summary["cross_foot"],
    }


def _private_child_directory(root: Path, name: str) -> Path:
    """Create one private directory immediately below a hardened data root."""

    if not name or Path(name).name != name:
        raise ValueError("private child directory must be one path component")
    destination = root / name
    if destination.is_symlink():
        raise UnsafeDataRootError("private output directories cannot be symlinks")
    destination.mkdir(exist_ok=True, mode=PRIVATE_DIR_MODE)
    if destination.is_symlink() or not destination.is_dir():
        raise UnsafeDataRootError("private output path is not a directory")
    resolved = destination.resolve(strict=True)
    if resolved.parent != root.resolve(strict=True):
        raise UnsafeDataRootError("private output directory escaped the data root")
    os.chmod(resolved, PRIVATE_DIR_MODE)
    return resolved


def _render_demo(config: AppConfig) -> dict[str, object]:
    """Build the complete credential-free demo package."""

    store = MasterStore(config.data_root)
    root = _private_child_directory(store.root, "demo")
    hero_root = _private_child_directory(root, "heroes")
    dataset = write_demo_dataset(root / "northstar-apparel.json")
    summary = demo_summary()
    summary_path = root / "demo-summary.json"
    atomic_write_json(summary_path, summary)
    dashboard = generate_dashboard(summary, root / "dashboard.html")
    heroes = write_hero_candidates(summary, hero_root)
    git_sha, git_dirty = _git_state()
    freeze = write_freeze_manifest(
        summary,
        dashboard,
        heroes,
        root / "freeze-manifest.json",
        git_sha=git_sha,
        git_dirty=git_dirty,
    )
    return {
        "stamp": DEMO_STAMP,
        "account": DEMO_ACCOUNT,
        "dataset": str(dataset),
        "summary": str(summary_path),
        "dashboard": str(dashboard),
        "hero_candidates": [str(path) for path in heroes],
        "freeze_manifest": str(freeze),
        "messages": summary["broadcast_count"],
        "quadrants": {row["name"]: row["count"] for row in summary["quadrants"]},
        "cross_foot": summary["cross_foot"],
    }


def _snapshot_output_package(root: Path) -> dict[str, bytes | None]:
    """Capture every managed output that must move as one evidence generation."""

    output_root = root / "outputs"
    snapshot: dict[str, bytes | None] = {}
    for relative in _OUTPUT_PACKAGE_FILES:
        path = output_root / relative
        if path.is_symlink():
            raise UnsafeDataRootError("managed output files cannot be symlinks")
        snapshot[relative] = read_bytes_no_follow(path) if path.is_file() else None
    return snapshot


def _restore_output_package(root: Path, snapshot: dict[str, bytes | None]) -> None:
    """Restore a complete prior evidence generation after any failed update."""

    output_root = root / "outputs"
    for relative, prior in snapshot.items():
        path = output_root / relative
        if prior is None:
            _remove_private_file(path)
        else:
            atomic_write_bytes(path, prior)


def _record_update_failure(
    root: Path,
    prior_run: dict[str, Any],
    exc: Exception,
) -> None:
    store = MasterStore(root)
    store.write_state(
        "run",
        {
            "status": "failed",
            "last_attempt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "last_success": prior_run.get("last_success"),
            "error_type": type(exc).__name__,
            "update_errors": 1,
            "dashboard_replaced": False,
        },
    )


def _notify_update_failure() -> None:
    if sys.platform != "darwin":
        return
    subprocess.run(
        [
            "osascript",
            "-e",
            'display notification "The dashboard was not replaced." with title "Competitor Inbox update failed"',
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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
    configured_path = getattr(args, "config", None)
    config_path = Path(configured_path).expanduser() if configured_path else root / "config.toml"
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
    root = ensure_private_data_root(config.data_root)
    snapshot: dict[str, bytes | None] | None = None
    prior_run: dict[str, Any] = {}
    try:
        with run_lock(root / "state"):
            store = MasterStore(root)
            snapshot = _snapshot_output_package(root)
            prior_run = store.read_state("run")
            result = ingest(config, months=12, incremental=True)
            if not result.early_gate.passed:
                _restore_output_package(root, snapshot)
                gate_error = RuntimeError("early data gate failed")
                _record_update_failure(root, prior_run, gate_error)
                _notify_update_failure()
                _print(pipeline_result_json(result), as_json=args.json)
                return EARLY_GATE_EXIT
            analyzed = analyze_private_store(config)
            rendered = _render_real(config, analyzed)
            _print(rendered, as_json=args.json)
            return 0
    except AlreadyRunning:
        _print(
            {
                "status": "skipped",
                "reason": "another Competitor Inbox update is already running",
            },
            as_json=args.json,
        )
        return 0
    except Exception as exc:
        rollback_error: Exception | None = None
        if snapshot is not None:
            try:
                _restore_output_package(root, snapshot)
            except Exception as rollback_exc:
                rollback_error = rollback_exc
        try:
            _record_update_failure(root, prior_run, rollback_error or exc)
        except Exception:
            pass
        _notify_update_failure()
        print(f"Update failed safely: {type(exc).__name__}", file=sys.stderr)
        if rollback_error is not None:
            print(
                f"Output rollback also failed safely: {type(rollback_error).__name__}",
                file=sys.stderr,
            )
        return 2


def command_demo(args: argparse.Namespace) -> int:
    config = _config(args)
    _print(_render_demo(config), as_json=args.json)
    return 0


def command_build(args: argparse.Namespace) -> int:
    config = _config(args)
    store = MasterStore(config.data_root)
    if store.master_path.exists():
        with run_lock(store.root / "state"):
            snapshot = _snapshot_output_package(store.root)
            try:
                analyzed = analyze_private_store(config)
                rendered = _render_real(
                    config,
                    analyzed,
                    render_screenshots=bool(getattr(args, "render_heroes", False)),
                    hero_priority_brands=tuple(
                        getattr(args, "hero_priority_brand", ()) or ()
                    ),
                )
            except Exception:
                _restore_output_package(store.root, snapshot)
                raise
    else:
        rendered = _render_demo(config)
    _print(rendered, as_json=args.json)
    return 0


def _sha256(path: Path) -> str:
    return hashlib.sha256(read_bytes_no_follow(path)).hexdigest()


def _static_html_checks(
    checks: dict[str, Any],
    path: Path,
    *,
    prefix: str,
    require_stamp: bool,
    require_freshness: bool = False,
) -> None:
    checks[f"{prefix}_exists"] = path.is_file() and not path.is_symlink()
    if not checks[f"{prefix}_exists"]:
        return
    text = read_bytes_no_follow(path).decode("utf-8")
    folded = text.casefold()
    checks[f"{prefix}_private"] = is_private_mode(path)
    checks[f"{prefix}_csp"] = (
        "Content-Security-Policy" in text
        and "default-src &#x27;none&#x27;" in text
    )
    checks[f"{prefix}_no_script"] = "<script" not in folded
    checks[f"{prefix}_no_remote_urls"] = (
        "http://" not in folded and "https://" not in folded
    )
    if require_stamp:
        checks[f"{prefix}_prototype_stamp"] = DEMO_STAMP in text
    if require_freshness:
        checks[f"{prefix}_freshness_badge"] = 'class="freshness"' in text


def _heroes_match_census(heroes: Sequence[Path], summary: dict[str, Any]) -> bool:
    expected = {
        "hero-brand.html": render_hero(summary, "brand"),
        "hero-portfolio.html": render_hero(summary, "portfolio"),
    }
    if {path.name for path in heroes} != set(expected):
        return False
    try:
        return all(
            read_bytes_no_follow(path).decode("utf-8") == expected[path.name]
            for path in heroes
        )
    except (OSError, UnicodeError, UnsafeDataRootError):
        return False


def _verify_demo_package(store: MasterStore) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    root = store.root / "demo"
    dataset_path = root / "northstar-apparel.json"
    summary_path = root / "demo-summary.json"
    dashboard_path = root / "dashboard.html"
    freeze_path = root / "freeze-manifest.json"
    heroes = [
        root / "heroes" / "hero-brand.html",
        root / "heroes" / "hero-portfolio.html",
    ]

    checks["demo_directory_private"] = root.is_dir() and is_private_mode(root)
    required = [dataset_path, summary_path, dashboard_path, freeze_path, *heroes]
    checks["demo_files_present"] = all(path.is_file() and not path.is_symlink() for path in required)
    checks["demo_files_private"] = checks["demo_files_present"] and all(
        is_private_mode(path) for path in required
    )
    if not checks["demo_files_present"]:
        return checks

    dataset = json.loads(read_bytes_no_follow(dataset_path))
    records = list(dataset.get("records", []))
    checks["demo_dataset_stamp"] = dataset.get("stamp") == DEMO_STAMP
    checks["demo_account"] = dataset.get("account") == DEMO_ACCOUNT
    checks["demo_messages"] = len(records) == DEMO_TOTAL
    checks["demo_record_ids_unique"] = len({row.get("record_id") for row in records}) == DEMO_TOTAL
    checks["demo_records_fully_stamped"] = all(
        row.get("illustrative_prototype") is True
        and row.get("data_classification") == DEMO_STAMP
        and row.get("demo_account") == DEMO_ACCOUNT
        for row in records
    )

    expected_summary = demo_summary()
    summary = json.loads(read_bytes_no_follow(summary_path))
    checks["demo_summary_deterministic"] = summary == expected_summary
    checks["demo_quadrants"] = {
        row["name"]: row["count"] for row in summary.get("quadrants", [])
    } == DEMO_QUADRANTS
    checks["demo_cross_foot"] = bool(summary.get("cross_foot", {}).get("passed"))
    checks["demo_summary_stamp"] = (
        summary.get("metadata", {}).get("illustrative_prototype") is True
        and summary.get("metadata", {}).get("stamp") == DEMO_STAMP
    )

    _static_html_checks(
        checks,
        dashboard_path,
        prefix="dashboard",
        require_stamp=True,
        require_freshness=True,
    )
    for index, path in enumerate(heroes, start=1):
        _static_html_checks(
            checks,
            path,
            prefix=f"hero_{index}",
            require_stamp=True,
        )
    checks["freeze_hero_census_binding"] = _heroes_match_census(heroes, summary)

    freeze = json.loads(read_bytes_no_follow(freeze_path))
    census_bytes = json.dumps(
        summary,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    checks["freeze_stamp"] = (
        freeze.get("illustrative_prototype") is True
        and freeze.get("stamp") == DEMO_STAMP
    )
    checks["freeze_message_count"] = freeze.get("qualified_broadcasts") == DEMO_TOTAL
    checks["freeze_hero_selection"] = (
        freeze.get("hero_selection") == hero_selection(summary)
    )
    checks["freeze_census_hash"] = freeze.get("census_sha256") == hashlib.sha256(census_bytes).hexdigest()
    checks["freeze_dashboard_hash"] = freeze.get("dashboard", {}).get("sha256") == _sha256(dashboard_path)
    frozen_heroes = list(freeze.get("hero_html", []))
    checks["freeze_hero_hashes"] = len(frozen_heroes) == 2 and {
        row.get("sha256") for row in frozen_heroes
    } == {_sha256(path) for path in heroes}
    return checks


def _coverage_matches_census(
    coverage: dict[str, Any],
    census: dict[str, Any],
) -> bool:
    """Bind the source ledger to the same aggregate generation as the census."""

    try:
        total = coverage["total"]
        pipeline = census["pipeline"]
        scopes = census["scope_counts"]
        metadata = census["metadata"]
        early_gate = coverage["early_gate"]
        census_gate = census["early_data_gate"]
        if not all(
            isinstance(value, dict)
            for value in (total, pipeline, scopes, metadata, early_gate, census_gate)
        ):
            return False

        expected = {
            "raw_fetched": int(pipeline["raw_fetched"]),
            "parse_failures": int(pipeline["parse_failures"]),
            "parsed_input": int(pipeline["parsed_input"]),
            "variants_collapsed": int(pipeline["variants_collapsed"]),
            "distinct_messages": int(pipeline["distinct_messages"]),
            "qualified_broadcasts": int(scopes["broadcast"]),
            "lifecycle": int(scopes["lifecycle"]),
            "uncertain": int(scopes["uncertain"]),
        }
        if any(int(total[key]) != value for key, value in expected.items()):
            return False

        if int(census["broadcast_count"]) != expected["qualified_broadcasts"]:
            return False
        if str(total["first_observed_date"]) != str(metadata["first_observed"]):
            return False
        if str(total["last_observed_date"]) != str(metadata["last_observed"]):
            return False
        if int(total["observed_days"]) != int(metadata["observed_days"]):
            return False

        census_quadrants = {
            str(row["name"]): int(row["count"])
            for row in census["quadrants"]
            if isinstance(row, dict)
        }
        total_quadrants = {
            "Evergreen content": int(total["evergreen_content"]),
            "Everyday promotion": int(total["everyday_promotion"]),
            "Seasonal promotion": int(total["seasonal_promotion"]),
            "Seasonal content": int(total["seasonal_content"]),
        }
        if total_quadrants != census_quadrants:
            return False

        coverage_quadrants = {
            str(row["quadrant"]): (
                int(row["count"]),
                int(row["total_denominator"]),
            )
            for row in coverage["quadrants"]
            if isinstance(row, dict) and row.get("quadrant") != "Total"
        }
        expected_quadrants = {
            name: (count, expected["qualified_broadcasts"])
            for name, count in census_quadrants.items()
        }
        if coverage_quadrants != expected_quadrants:
            return False

        return (
            early_gate.get("passed") is True
            and census_gate.get("passed") is True
            and int(early_gate["total_qualified_broadcasts"])
            == expected["qualified_broadcasts"]
            and int(early_gate["brand_count"]) == int(census["brand_count"])
        )
    except (KeyError, TypeError, ValueError):
        return False


def _verify_production_package(store: MasterStore) -> dict[str, Any]:
    """Verify one frozen production output generation without mutating source data."""

    checks: dict[str, Any] = {}
    output_root = store.root / "outputs"
    dashboard_path = output_root / "dashboard.html"
    census_path = output_root / "census.json"
    coverage_path = output_root / "coverage.json"
    freeze_path = output_root / "freeze-manifest.json"
    heroes = [
        output_root / "heroes" / "hero-brand.html",
        output_root / "heroes" / "hero-portfolio.html",
    ]
    screenshots = [path.with_suffix(".png") for path in heroes]
    required = [dashboard_path, census_path, coverage_path, freeze_path, *heroes]
    checks["production_files_present"] = all(
        path.is_file() and not path.is_symlink() for path in required
    )
    checks["production_files_private"] = checks["production_files_present"] and all(
        is_private_mode(path) for path in required
    )
    if not checks["production_files_present"]:
        return checks

    census = json.loads(read_bytes_no_follow(census_path))
    coverage = json.loads(read_bytes_no_follow(coverage_path))
    freeze = json.loads(read_bytes_no_follow(freeze_path))
    try:
        verify_cross_foot(census)
    except Exception:
        checks["production_cross_foot"] = False
    else:
        checks["production_cross_foot"] = True
    checks["early_data_gate"] = coverage.get("early_gate", {}).get("passed") is True
    checks["coverage_census_binding"] = _coverage_matches_census(coverage, census)

    _static_html_checks(
        checks,
        dashboard_path,
        prefix="dashboard",
        require_stamp=False,
        require_freshness=True,
    )
    checks["freeze_hero_census_binding"] = _heroes_match_census(heroes, census)

    census_bytes = json.dumps(
        census,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    checks["freeze_census_hash"] = (
        freeze.get("census_sha256") == hashlib.sha256(census_bytes).hexdigest()
    )
    checks["freeze_dashboard_hash"] = (
        freeze.get("dashboard", {}).get("sha256") == _sha256(dashboard_path)
    )
    frozen_heroes = list(freeze.get("hero_html", []))
    checks["freeze_hero_hashes"] = len(frozen_heroes) == len(heroes) and {
        row.get("sha256") for row in frozen_heroes if isinstance(row, dict)
    } == {_sha256(path) for path in heroes}

    screenshot_entries = list(freeze.get("screenshots", []))
    if screenshot_entries:
        checks["freeze_screenshot_files"] = len(screenshot_entries) == len(screenshots) and all(
            path.is_file() and not path.is_symlink() and is_private_mode(path)
            for path in screenshots
        )
        checks["freeze_screenshot_hashes"] = checks["freeze_screenshot_files"] and {
            row.get("sha256")
            for row in screenshot_entries
            if isinstance(row, dict)
        } == {_sha256(path) for path in screenshots}
        checks["freeze_screenshot_dimensions"] = all(
            isinstance(row, dict)
            and row.get("width") == 1080
            and row.get("height") == 1350
            for row in screenshot_entries
        )
    else:
        checks["freeze_screenshot_files"] = all(not path.exists() for path in screenshots)
        checks["freeze_screenshot_hashes"] = True
        checks["freeze_screenshot_dimensions"] = True

    quadrants = {
        str(row.get("name")): int(row.get("count", 0))
        for row in census.get("quadrants", [])
        if isinstance(row, dict)
    }
    frozen_quadrants = {
        str(name): int(value.get("count", 0))
        for name, value in freeze.get("metrics", {}).get("quadrants", {}).items()
        if isinstance(value, dict)
    }
    checks["freeze_message_count"] = (
        freeze.get("qualified_broadcasts") == int(census.get("broadcast_count", 0))
        and freeze.get("metrics", {}).get("qualified_broadcasts")
        == int(census.get("broadcast_count", 0))
    )
    checks["freeze_quadrants"] = frozen_quadrants == quadrants
    checks["freeze_metrics_binding"] = freeze.get("metrics") == _freeze_metrics(census)
    checks["freeze_window"] = freeze.get("window") == {
        "first": census.get("metadata", {}).get("first_observed", ""),
        "last": census.get("metadata", {}).get("last_observed", ""),
    }
    checks["freeze_hero_selection"] = (
        freeze.get("hero_selection") == hero_selection(census)
    )
    checks["freeze_git_sha"] = bool(
        re.fullmatch(
            r"[0-9a-f]{40}",
            str(freeze.get("git_sha") or ""),
            re.IGNORECASE,
        )
    )
    checks["freeze_git_clean"] = freeze.get("git_dirty") is False
    current_git_sha, current_git_dirty = _git_state()
    checks["freeze_git_matches_source"] = (
        current_git_dirty is False
        and bool(current_git_sha)
        and freeze.get("git_sha") == current_git_sha
    )
    return checks


def command_verify(args: argparse.Namespace) -> int:
    config = _config(args)
    store = MasterStore(config.data_root)
    checks: dict[str, Any] = {}
    if store.master_path.exists():
        checks.update(_verify_production_package(store))
    else:
        checks.update(_verify_demo_package(store))
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
    candidates = [Path.cwd().resolve(), Path(__file__).resolve().parents[2]]
    repo = next(
        (
            candidate
            for candidate in candidates
            if (candidate / ".git").exists()
            and (candidate / "scripts" / "privacy_audit.py").is_file()
        ),
        None,
    )
    if repo is None:
        print("Run privacy-check from the repository root.", file=sys.stderr)
        return 2
    script = repo / "scripts" / "privacy_audit.py"
    command = [sys.executable, str(script), "--repo", str(repo)]
    if args.json:
        command.append("--json")
    return subprocess.run(command, check=False).returncode


def command_schedule(args: argparse.Namespace) -> int:
    config = _config(args)
    if args.schedule_command == "install":
        config_path = (
            Path(args.config).expanduser().resolve(strict=True)
            if getattr(args, "config", None)
            else None
        )
        _print(
            {"installed": str(install_schedule(config, config_path=config_path))},
            as_json=args.json,
        )
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


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


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
            child.add_argument("--months", type=_positive_int, default=12)
        elif name == "build":
            child.add_argument(
                "--render-heroes",
                action="store_true",
                help="also render 1080x1350 PNGs; requires a local Chromium-family browser",
            )
            child.add_argument(
                "--hero-priority-brand",
                action="append",
                default=[],
                metavar="BRAND",
                help=(
                    "restrict the single-brand launch hero to this brand; repeat in "
                    "the approved tie-break order"
                ),
            )

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
