from __future__ import annotations

import hashlib
import json
import plistlib
import stat
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

import competitor_inbox.cli as cli_module
import competitor_inbox.schedule as schedule_module
from competitor_inbox.cli import build_parser, main
from competitor_inbox.config import AppConfig
from competitor_inbox.dashboard import generate_dashboard, write_freeze_manifest, write_hero_candidates
from competitor_inbox.demo import DEMO_QUADRANTS, DEMO_STAMP, DEMO_TOTAL, demo_summary
from competitor_inbox.locking import AlreadyRunning, run_lock
from competitor_inbox.schedule import LABEL, install, launch_agent_payload, status
from competitor_inbox.store import MasterStore, atomic_write_bytes, atomic_write_json, ensure_private_data_root
from scripts.fresh_install_test import find_local_user_paths, unconditional_requirements


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_demo_package_is_complete_private_and_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    assert main(["demo", "--data-root", str(first), "--json"]) == 0
    assert main(["build", "--data-root", str(second), "--json"]) == 0
    assert main(["verify", "--data-root", str(first), "--json"]) == 0
    assert main(["verify", "--data-root", str(second), "--json"]) == 0

    first_demo = first / "demo"
    second_demo = second / "demo"
    assert (first_demo / "northstar-apparel.json").read_bytes() == (
        second_demo / "northstar-apparel.json"
    ).read_bytes()
    assert (first_demo / "demo-summary.json").read_bytes() == (
        second_demo / "demo-summary.json"
    ).read_bytes()

    dataset = json.loads((first_demo / "northstar-apparel.json").read_text())
    summary = json.loads((first_demo / "demo-summary.json").read_text())
    freeze = json.loads((first_demo / "freeze-manifest.json").read_text())
    assert len(dataset["records"]) == DEMO_TOTAL
    assert {row["name"]: row["count"] for row in summary["quadrants"]} == DEMO_QUADRANTS
    assert summary["metadata"]["stamp"] == DEMO_STAMP
    assert freeze["stamp"] == DEMO_STAMP
    assert freeze["illustrative_prototype"] is True

    surfaces = [
        first_demo / "dashboard.html",
        first_demo / "heroes" / "hero-brand.html",
        first_demo / "heroes" / "hero-portfolio.html",
    ]
    assert all(DEMO_STAMP in path.read_text() for path in surfaces)
    assert 'class="freshness" role="status"' in surfaces[0].read_text()
    assert _mode(first) == 0o700
    assert _mode(first_demo) == 0o700
    assert _mode(first_demo / "heroes") == 0o700
    assert all(_mode(path) == 0o600 for path in first_demo.rglob("*") if path.is_file())


def test_real_render_executes_the_complete_output_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AppConfig(data_root=tmp_path / "private")
    total = SimpleNamespace(
        raw_fetched=0,
        parse_failures=0,
        parsed_input=0,
        variants_collapsed=0,
        distinct_messages=0,
    )
    result = SimpleNamespace(
        ai_mode="deterministic-only",
        coverage=SimpleNamespace(total=total, rows=[]),
    )
    summary = demo_summary()
    summary["metadata"]["illustrative_prototype"] = False

    monkeypatch.setattr(cli_module, "dashboard_records", lambda records: [])
    monkeypatch.setattr(cli_module, "aggregate_records", lambda *args, **kwargs: deepcopy(summary))
    monkeypatch.setattr(cli_module, "_bind_coverage_integrity", lambda *args: None)
    monkeypatch.setattr(cli_module, "verify_cross_foot", lambda value: None)
    monkeypatch.setattr(
        cli_module,
        "derive_dashboard_weekly_activity",
        lambda records, value: [],
    )

    def write_heroes(value: object, destination: Path) -> list[Path]:
        destination.mkdir(parents=True, mode=0o700)
        paths = [destination / "hero-brand.html", destination / "hero-portfolio.html"]
        for path in paths:
            path.write_text("hero")
        return paths

    def render_heroes(paths: list[Path]) -> list[Path]:
        rendered = [path.with_suffix(".png") for path in paths]
        for path in rendered:
            path.write_bytes(b"png")
        return rendered

    def write_dashboard(value: object, destination: Path) -> Path:
        destination.write_text("dashboard")
        return destination

    def write_freeze(*args: object, **kwargs: object) -> Path:
        destination = Path(args[3])
        destination.write_text("freeze")
        return destination

    monkeypatch.setattr(cli_module, "write_hero_candidates", write_heroes)
    monkeypatch.setattr(cli_module, "render_hero_pngs", render_heroes)
    monkeypatch.setattr(cli_module, "generate_dashboard", write_dashboard)
    monkeypatch.setattr(cli_module, "write_freeze_manifest", write_freeze)
    monkeypatch.setattr(cli_module, "_git_state", lambda: ("a" * 40, False))

    rendered = cli_module._render_real(config, result, render_screenshots=True)
    assert rendered["broadcasts"] == DEMO_TOTAL
    assert len(rendered["hero_candidates"]) == 2
    assert len(rendered["hero_screenshots"]) == 2
    assert Path(rendered["dashboard"]).is_file()
    assert Path(rendered["freeze_manifest"]).is_file()


def test_real_render_is_browser_free_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AppConfig(data_root=tmp_path / "private")
    total = SimpleNamespace(
        raw_fetched=0,
        parse_failures=0,
        parsed_input=0,
        variants_collapsed=0,
        distinct_messages=0,
    )
    result = SimpleNamespace(
        ai_mode="deterministic-only",
        coverage=SimpleNamespace(total=total, rows=[]),
    )
    summary = demo_summary()
    summary["metadata"]["illustrative_prototype"] = False
    monkeypatch.setattr(cli_module, "dashboard_records", lambda records: [])
    monkeypatch.setattr(cli_module, "aggregate_records", lambda *args, **kwargs: deepcopy(summary))
    monkeypatch.setattr(cli_module, "_bind_coverage_integrity", lambda *args: None)
    monkeypatch.setattr(cli_module, "verify_cross_foot", lambda value: None)
    monkeypatch.setattr(
        cli_module,
        "derive_dashboard_weekly_activity",
        lambda records, value: [],
    )

    def browser_must_not_run(paths: list[Path]) -> list[Path]:
        raise AssertionError("normal dashboard build invoked a browser")

    monkeypatch.setattr(cli_module, "render_hero_pngs", browser_must_not_run)
    rendered = cli_module._render_real(config, result)
    assert rendered["hero_screenshots"] == []
    assert rendered["hero_screenshot_mode"].endswith("browser-free")
    assert all(not Path(path).with_suffix(".png").exists() for path in rendered["hero_candidates"])


def test_complete_update_lock_skips_overlap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = ensure_private_data_root(tmp_path / "private")

    def unexpected_ingest(*args: object, **kwargs: object) -> object:
        raise AssertionError("overlapping update reached ingestion")

    monkeypatch.setattr(cli_module, "ingest", unexpected_ingest)
    with run_lock(root / "state"):
        assert main(["update", "--data-root", str(root), "--json"]) == 0

    assert _mode(root / "state" / "competitor-inbox.lock") == 0o600


def test_update_early_gate_restores_prior_output_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = ensure_private_data_root(tmp_path / "private")
    coverage = root / "outputs" / "coverage.json"
    atomic_write_bytes(coverage, b'{"generation":"old"}\n')
    result = SimpleNamespace(early_gate=SimpleNamespace(passed=False))

    def failed_gate(*args: object, **kwargs: object) -> object:
        atomic_write_bytes(coverage, b'{"generation":"new"}\n')
        return result

    monkeypatch.setattr(cli_module, "ingest", failed_gate)
    monkeypatch.setattr(cli_module, "pipeline_result_json", lambda value: {"gate": "failed"})
    monkeypatch.setattr(cli_module, "_notify_update_failure", lambda: None)

    assert main(["update", "--data-root", str(root), "--json"]) == cli_module.EARLY_GATE_EXIT
    assert coverage.read_bytes() == b'{"generation":"old"}\n'
    run_state = json.loads((root / "state" / "run.json").read_text())
    assert run_state["status"] == "failed"
    assert run_state["update_errors"] == 1
    assert run_state["dashboard_replaced"] is False


def test_update_failure_restores_exact_prior_output_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = ensure_private_data_root(tmp_path / "private")
    outputs = root / "outputs"
    previous = {
        "coverage.json": b'{"generation":"old"}\n',
        "coverage.md": b"old coverage\n",
        "dashboard.html": b"old dashboard\n",
        "census.json": b'{"generation":"old"}\n',
        "freeze-manifest.json": b'{"generation":"old"}\n',
        "heroes/hero-brand.html": b"old brand hero\n",
        "heroes/hero-portfolio.html": b"old portfolio hero\n",
        "heroes/hero-brand.png": b"old brand png\n",
        "heroes/hero-portfolio.png": b"old portfolio png\n",
    }
    for relative, payload in previous.items():
        atomic_write_bytes(outputs / relative, payload)
    result = SimpleNamespace(early_gate=SimpleNamespace(passed=True))

    monkeypatch.setattr(cli_module, "ingest", lambda *args, **kwargs: result)
    monkeypatch.setattr(cli_module, "analyze_private_store", lambda config: result)

    def broken_render(config: object, analyzed: object) -> object:
        atomic_write_bytes(outputs / "dashboard.html", b"new dashboard\n")
        atomic_write_bytes(outputs / "census.json", b"new census\n")
        atomic_write_bytes(outputs / "heroes" / "hero-brand.html", b"new hero\n")
        atomic_write_bytes(outputs / "dashboard.previous.html", b"new previous\n")
        raise RuntimeError("synthetic render failure")

    monkeypatch.setattr(cli_module, "_render_real", broken_render)
    monkeypatch.setattr(cli_module, "_notify_update_failure", lambda: None)

    assert main(["update", "--data-root", str(root)]) == 2
    for relative, payload in previous.items():
        assert (outputs / relative).read_bytes() == payload
        assert _mode(outputs / relative) == 0o600
    assert not (outputs / "dashboard.previous.html").exists()
    run_state = json.loads((root / "state" / "run.json").read_text())
    assert run_state["status"] == "failed"
    assert run_state["update_errors"] == 1
    assert run_state["dashboard_replaced"] is False


def test_production_verify_binds_complete_output_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MasterStore(tmp_path / "private")
    summary = demo_summary()
    summary["metadata"]["illustrative_prototype"] = False
    monkeypatch.setattr(cli_module, "_git_state", lambda: ("a" * 40, False))
    dashboard = generate_dashboard(summary, store.root / "outputs" / "dashboard.html")
    heroes = write_hero_candidates(summary, store.root / "outputs" / "heroes")
    atomic_write_json(store.root / "outputs" / "census.json", summary)
    quadrants = {
        row["name"]: row["count"] for row in summary["quadrants"]
    }
    atomic_write_json(
        store.root / "outputs" / "coverage.json",
        {
            "total": {
                **summary["pipeline"],
                "qualified_broadcasts": summary["scope_counts"]["broadcast"],
                "lifecycle": summary["scope_counts"]["lifecycle"],
                "uncertain": summary["scope_counts"]["uncertain"],
                "first_observed_date": summary["metadata"]["first_observed"],
                "last_observed_date": summary["metadata"]["last_observed"],
                "observed_days": summary["metadata"]["observed_days"],
                "evergreen_content": quadrants["Evergreen content"],
                "everyday_promotion": quadrants["Everyday promotion"],
                "seasonal_promotion": quadrants["Seasonal promotion"],
                "seasonal_content": quadrants["Seasonal content"],
            },
            "quadrants": [
                {
                    "quadrant": name,
                    "count": count,
                    "total_denominator": summary["broadcast_count"],
                }
                for name, count in quadrants.items()
            ],
            "early_gate": {
                "passed": True,
                "total_qualified_broadcasts": summary["broadcast_count"],
                "brand_count": summary["brand_count"],
            },
        },
    )
    write_freeze_manifest(
        summary,
        dashboard,
        heroes,
        store.root / "outputs" / "freeze-manifest.json",
        git_sha="a" * 40,
        git_dirty=False,
    )

    checks = cli_module._verify_production_package(store)
    assert checks
    assert all(value is True for value in checks.values())

    freeze_path = store.root / "outputs" / "freeze-manifest.json"
    freeze = json.loads(freeze_path.read_text())
    original_freeze = deepcopy(freeze)
    freeze["hero_selection"]["numerator"] += 1
    atomic_write_json(freeze_path, freeze)
    tampered_checks = cli_module._verify_production_package(store)
    assert tampered_checks["freeze_hero_selection"] is False
    atomic_write_json(freeze_path, original_freeze)

    dirty_freeze = deepcopy(original_freeze)
    dirty_freeze["git_sha"] = ""
    dirty_freeze["git_dirty"] = True
    atomic_write_json(freeze_path, dirty_freeze)
    dirty_checks = cli_module._verify_production_package(store)
    assert dirty_checks["freeze_git_sha"] is False
    assert dirty_checks["freeze_git_clean"] is False
    assert dirty_checks["freeze_git_matches_source"] is False
    atomic_write_json(freeze_path, original_freeze)

    coverage_path = store.root / "outputs" / "coverage.json"
    original_coverage = json.loads(coverage_path.read_text())
    stale_coverage = deepcopy(original_coverage)
    stale_coverage["total"]["qualified_broadcasts"] -= 1
    atomic_write_json(coverage_path, stale_coverage)
    stale_checks = cli_module._verify_production_package(store)
    assert stale_checks["early_data_gate"] is True
    assert stale_checks["coverage_census_binding"] is False
    atomic_write_json(coverage_path, original_coverage)

    monkeypatch.setattr(cli_module, "_git_state", lambda: ("b" * 40, False))
    source_mismatch = cli_module._verify_production_package(store)
    assert source_mismatch["freeze_git_matches_source"] is False
    monkeypatch.setattr(cli_module, "_git_state", lambda: ("a" * 40, False))

    stale_metrics = deepcopy(original_freeze)
    stale_metrics["metrics"]["offer_share"] += 0.1
    atomic_write_json(freeze_path, stale_metrics)
    metric_checks = cli_module._verify_production_package(store)
    assert metric_checks["freeze_quadrants"] is True
    assert metric_checks["freeze_metrics_binding"] is False
    atomic_write_json(freeze_path, original_freeze)

    hero_path = store.root / "outputs" / "heroes" / "hero-brand.html"
    original_hero = hero_path.read_bytes()
    atomic_write_bytes(hero_path, original_hero + b"\n")
    paired_tamper = deepcopy(original_freeze)
    for row in paired_tamper["hero_html"]:
        if Path(row["path"]).name == hero_path.name:
            row["sha256"] = hashlib.sha256(hero_path.read_bytes()).hexdigest()
    atomic_write_json(freeze_path, paired_tamper)
    paired_checks = cli_module._verify_production_package(store)
    assert paired_checks["freeze_hero_hashes"] is True
    assert paired_checks["freeze_hero_census_binding"] is False
    atomic_write_bytes(hero_path, original_hero)
    atomic_write_json(freeze_path, original_freeze)

    changed = deepcopy(summary)
    changed["broadcast_count"] += 1
    atomic_write_json(store.root / "outputs" / "census.json", changed)
    changed_checks = cli_module._verify_production_package(store)
    assert changed_checks["production_cross_foot"] is False
    assert changed_checks["freeze_census_hash"] is False


def test_launch_agent_contract_is_secret_free_and_daily(tmp_path: Path) -> None:
    config = AppConfig(data_root=tmp_path / "private")
    config.source.account = "should-never-enter-plist"
    payload = launch_agent_payload(config)
    serialized = plistlib.dumps(payload).decode("utf-8")

    assert payload["Label"] == LABEL
    assert payload["RunAtLoad"] is True
    assert payload["StartCalendarInterval"] == {"Hour": 7, "Minute": 0}
    assert payload["ProgramArguments"][1:4] == ["-m", "competitor_inbox", "update"]
    assert "should-never-enter-plist" not in serialized
    assert "EnvironmentVariables" not in payload
    assert all(token not in serialized.casefold() for token in ("password", "token", "secret"))


def test_launch_agent_persists_custom_config_path(tmp_path: Path) -> None:
    config = AppConfig(data_root=tmp_path / "private")
    custom = ensure_private_data_root(config.data_root) / "custom.toml"
    atomic_write_bytes(custom, b"[classification]\nai_enabled = false\n")
    payload = launch_agent_payload(config, config_path=custom)
    arguments = payload["ProgramArguments"]
    assert arguments[-2:] == ["--config", str(custom.resolve())]


def test_launch_agent_install_and_status_are_private(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AppConfig(data_root=tmp_path / "private")
    target = tmp_path / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    monkeypatch.setattr(schedule_module, "plist_path", lambda: target)
    monkeypatch.setattr(
        schedule_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    assert install(config) == target
    payload = plistlib.loads(target.read_bytes())
    assert payload["RunAtLoad"] is True
    assert _mode(target) == 0o600
    assert _mode(config.data_root / "logs" / "launchd.stdout.log") == 0o600
    assert _mode(config.data_root / "logs" / "launchd.stderr.log") == 0o600

    current = status(config)
    assert current["incremental_overlap_days"] == 14
    assert current["process_lock"] == "one complete update at a time"
    assert "prior dashboard retained" in current["failure_behavior"]
    assert "Mac" in current["note"]


def test_all_approved_commands_parse() -> None:
    parser = build_parser()
    commands = [
        ["doctor"],
        ["demo"],
        ["setup"],
        ["backfill", "--months", "12"],
        ["update"],
        ["build"],
        ["open"],
        ["verify"],
        ["privacy-check"],
        ["schedule", "install"],
        ["schedule", "status"],
        ["schedule", "remove"],
    ]
    for argv in commands:
        assert parser.parse_args(argv).handler is not None

    with pytest.raises(SystemExit):
        parser.parse_args(["backfill", "--months", "0"])

    assert parser.parse_args(["build", "--render-heroes"]).render_heroes is True


def test_run_lock_refuses_second_holder_and_hardens_modes(tmp_path: Path) -> None:
    state = tmp_path / "state"
    with run_lock(state):
        with pytest.raises(AlreadyRunning):
            with run_lock(state):
                pass
    assert _mode(state) == 0o700
    assert _mode(state / "competitor-inbox.lock") == 0o600


def test_fresh_install_checks_dependencies_and_local_user_paths(tmp_path: Path) -> None:
    safe = tmp_path / "safe"
    safe.mkdir()
    (safe / "doc.txt").write_text("https://github.com/ZachSchieffer/competitor-inbox\n")
    assert find_local_user_paths([safe]) == []

    local_home = "/" + "Users" + "/demo-user/private/config.toml\n"
    (safe / "bad.txt").write_text(local_home)
    assert find_local_user_paths([safe]) == ["bad.txt"]
    assert unconditional_requirements(Path(sys.executable)) == []
