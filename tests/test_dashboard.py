from __future__ import annotations

import json
import re
import struct
import zlib
from pathlib import Path

import pytest

from competitor_inbox.aggregate import aggregate_records
import competitor_inbox.dashboard as dashboard_module
from competitor_inbox.dashboard import (
    HeroRenderError,
    audit_hero_png,
    generate_dashboard,
    render_dashboard,
    render_hero_pngs,
    write_freeze_manifest,
    write_hero_candidates,
)
from competitor_inbox.demo import demo_summary


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", checksum)


def _hero_png(*, black_rows: int = 0, width: int = 1080, height: int = 1350) -> bytes:
    black = b"\x00\x00\x00" * width
    light = b"\xf2\xf5\xf9" * width
    pixels = b"".join(
        b"\x00" + (black if row < black_rows else light)
        for row in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(pixels, level=9))
        + _png_chunk(b"IEND", b"")
    )


def test_dashboard_is_static_local_and_has_restrictive_csp(tmp_path: Path) -> None:
    output = generate_dashboard(demo_summary(), tmp_path / "dashboard.html")
    document = output.read_text(encoding="utf-8")

    assert "<script" not in document.casefold()
    assert "javascript:" not in document.casefold()
    assert "http://" not in document.casefold()
    assert "https://" not in document.casefold()
    assert "<link" not in document.casefold()
    assert "<img" not in document.casefold()
    assert "default-src &#x27;none&#x27;" in document
    assert "script-src &#x27;none&#x27;" in document
    assert "connect-src &#x27;none&#x27;" in document
    assert "form-action &#x27;none&#x27;" in document
    assert "@media(max-width:900px)" in document


def test_demo_dashboard_marks_every_surface_and_matches_frozen_counts(tmp_path: Path) -> None:
    summary = demo_summary()
    output = generate_dashboard(summary, tmp_path / "demo.html")
    document = output.read_text(encoding="utf-8")

    assert document.count("ILLUSTRATIVE PROTOTYPE") >= 2
    assert "1,260 qualified broadcasts" in document
    assert "580 of 1,260" in document
    assert "491 / 1,260" in document
    assert "139 / 1,260" in document
    assert "50 / 1,260" in document
    assert "—" not in document
    assert "–" not in document


def test_untrusted_message_text_is_escaped() -> None:
    record = {
        "id": "unsafe-1",
        "brand": "Alder Row",
        "canonical_received_at": "2026-01-01T08:00:00Z",
        "subject": "</script><img src=x onerror=alert(1)>",
        "preheader": "Product details",
        "visible_text": "Product details",
        "scope": "broadcast",
        "intent": "Featured products",
        "intent_source": "deterministic",
        "intent_confidence": 1.0,
        "offer_candidates": [],
        "primary_offer": None,
        "seasonal": False,
        "occasion": None,
        "variant_count": 1,
    }
    document = render_dashboard(aggregate_records([record]))

    assert "</script><img" not in document
    assert "&lt;/script&gt;&lt;img" in document
    assert "<script" not in document.casefold()


def test_atomic_dashboard_retains_previous_success(tmp_path: Path) -> None:
    destination = tmp_path / "dashboard.html"
    destination.write_text("prior dashboard", encoding="utf-8")

    generate_dashboard(demo_summary(), destination)

    assert destination.read_text(encoding="utf-8").startswith("<!doctype html>")
    assert (tmp_path / "dashboard.previous.html").read_text(encoding="utf-8") == "prior dashboard"


def test_hero_candidates_are_1080_by_1350_screenshot_ready(tmp_path: Path) -> None:
    summary = demo_summary()
    candidates = write_hero_candidates(summary, tmp_path)

    assert [path.name for path in candidates] == ["hero-brand.html", "hero-portfolio.html"]
    for path in candidates:
        document = path.read_text(encoding="utf-8")
        assert "width:1080px" in document
        assert "min-height:1350px" in document
        assert "ILLUSTRATIVE PROTOTYPE" in document
        assert "<script" not in document.casefold()
    assert re.search(
        r"sent 126 broadcasts in 3\d{2} observed days",
        candidates[0].read_text(encoding="utf-8"),
    )
    assert re.search(
        r"1,260 emails from 10 brands, mapped into one strategy dashboard",
        candidates[1].read_text(encoding="utf-8"),
    )


def test_brand_hero_uses_only_brand_specific_counts_and_dates(tmp_path: Path) -> None:
    summary = demo_summary()
    brand = summary["brands"][0]
    brand_document = write_hero_candidates(summary, tmp_path)[0].read_text(encoding="utf-8")

    assert 'data-census-scope="brand"' in brand_document
    assert f"{brand['quadrants']['Evergreen content']} of {brand['qualified_broadcasts']}" in brand_document
    for name, count in brand["quadrants"].items():
        assert f"<b>{count}</b><span>{name} |" in brand_document
    assert f"{brand['first_observed']} to {brand['last_observed']}" in brand_document
    assert f"{brand['observed_days']} observed days" in brand_document
    assert "580 of 1,260" not in brand_document
    assert "2026-07-14</strong>365 observed days" not in brand_document


def test_fallback_heroes_are_distinct_and_show_curated_export_limit(tmp_path: Path) -> None:
    summary = demo_summary()
    summary["metadata"]["source_completeness"] = "curated_export"
    for brand in summary["brands"]:
        brand["source_completeness"] = "curated_export"
        brand["hook_eligible"] = False

    dashboard_candidate, poster_candidate = write_hero_candidates(summary, tmp_path)
    dashboard_document = dashboard_candidate.read_text(encoding="utf-8")
    poster_document = poster_candidate.read_text(encoding="utf-8")

    assert dashboard_document != poster_document
    assert 'data-census-scope="portfolio-dashboard"' in dashboard_document
    assert '<div class="dashboard-product">' in dashboard_document
    assert "Competitor comparison" in dashboard_document
    assert "Evergreen + promo mix" in dashboard_document
    assert "Seasonal planner" in dashboard_document
    assert "Messaging library + action plan" in dashboard_document
    assert '<div class="dashboard-product">' not in poster_document
    assert "Curated export subset" in dashboard_document
    assert "Curated export subset" in poster_document
    assert "Single-brand comparisons are disabled" in dashboard_document


def test_fallback_hero_separates_source_census_from_broadcast_denominator(tmp_path: Path) -> None:
    summary = demo_summary()
    summary["brand_count"] = 11
    summary["pipeline"]["distinct_messages"] = 1271
    summary["scope_counts"] = {"broadcast": 1260, "lifecycle": 9, "uncertain": 2}
    for brand in summary["brands"]:
        brand["hook_eligible"] = False

    poster = write_hero_candidates(summary, tmp_path)[1].read_text(encoding="utf-8")

    assert "1,271 emails from 11 brands" in poster
    assert "1,260 qualified broadcasts after 9 lifecycle and 2 uncertain messages" in poster
    assert "580</b><span>Evergreen content | 46.0%" in poster


def test_curated_dashboard_does_not_promote_a_capped_volume_leader() -> None:
    summary = demo_summary()
    summary["metadata"]["source_completeness"] = "curated_export"

    document = render_dashboard(summary)

    assert "Highest inbox volume" not in document
    assert "Compare volume" not in document
    assert "volume leader" not in document
    assert "Compare the planned calendar with the 4-part census" in document
    assert (
        f"{summary['metadata']['coverage']['label']} | Curated export subset | "
        "n=1,260 broadcasts"
    ) in document
    assert "This is a curated export subset" in document


def test_local_renderer_writes_both_verified_pngs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heroes = write_hero_candidates(demo_summary(), tmp_path / "heroes")
    browser = tmp_path / "synthetic-browser"
    browser.write_text("synthetic executable", encoding="utf-8")
    browser.chmod(0o700)
    calls: list[list[str]] = []

    class FinishedProcess:
        pid = 999_999

        @staticmethod
        def poll() -> int:
            return 0

    def fake_popen(command: list[str], **_: object) -> FinishedProcess:
        calls.append(command)
        screenshot_argument = next(value for value in command if value.startswith("--screenshot="))
        Path(screenshot_argument.split("=", 1)[1]).write_bytes(_hero_png())
        return FinishedProcess()

    monkeypatch.setattr(dashboard_module.subprocess, "Popen", fake_popen)
    screenshots = render_hero_pngs(heroes, browser_path=browser)

    assert [path.name for path in screenshots] == ["hero-brand.png", "hero-portfolio.png"]
    assert all(path.read_bytes().startswith(b"\x89PNG") for path in screenshots)
    assert len(calls) == 2
    assert all("--window-size=1080,1350" in command for command in calls)
    assert all("--force-device-scale-factor=1" in command for command in calls)
    assert all(command[-1].startswith("file://") for command in calls)
    assert all(not any("http://" in value or "https://" in value for value in command) for command in calls)


def test_visual_audit_rejects_dominant_black_overlay(tmp_path: Path) -> None:
    clean = tmp_path / "clean.png"
    corrupted = tmp_path / "corrupted.png"
    clean.write_bytes(_hero_png())
    corrupted.write_bytes(_hero_png(black_rows=900))

    assert audit_hero_png(clean)["passed"] is True
    with pytest.raises(HeroRenderError, match="visual corruption"):
        audit_hero_png(corrupted)


def test_checkpoint_findings_include_brand_set_limitation_and_coverage_gate() -> None:
    summary = demo_summary()
    expected_brands = sorted(
        (row["brand"] for row in summary["brands"] if row["qualified_broadcasts"]),
        key=str.casefold,
    )

    assert len(summary["findings"]) == 5
    for finding in summary["findings"][:4]:
        assert finding["brand_set"] == expected_brands
        assert "behavior" in finding["limitation"]
        assert finding["coverage_gate"] == {
            "name": "cadence_mix_posture",
            "minimum_observed_days": 90,
            "observed_days": 365,
            "passed": True,
            "coverage": summary["metadata"]["coverage"],
        }
    coverage_finding = summary["findings"][4]
    assert coverage_finding["label"] == "Brands with cadence and mix coverage"
    assert coverage_finding["numerator"] == 10
    assert coverage_finding["denominator"] == 10
    assert coverage_finding["brand_set"] == expected_brands
    assert coverage_finding["coverage_gate"] == {
        "name": "brand_cadence_mix",
        "minimum_qualified_broadcasts": 30,
        "minimum_observed_days": 90,
        "qualifying_brands": 10,
        "total_brands": 10,
        "passed": True,
    }


def test_executive_findings_use_strategy_ratios_not_capped_volume_rank() -> None:
    summary = demo_summary()
    findings = {row["label"]: row for row in summary["findings"]}

    assert "Highest inbox volume" not in findings
    assert findings["Evergreen content share"]["numerator"] == 580
    assert findings["Evergreen content share"]["denominator"] == 1260
    assert findings["Promotion share"]["numerator"] == 630
    assert findings["Promotion share"]["denominator"] == 1260
    assert findings["Seasonal share"]["numerator"] == 189
    assert findings["Seasonal share"]["denominator"] == 1260
    assert findings["Seasonal messages carrying an offer"]["numerator"] == 139
    assert findings["Seasonal messages carrying an offer"]["denominator"] == 189


def test_freeze_manifest_hashes_dashboard_heroes_and_finished_screenshots(tmp_path: Path) -> None:
    summary = demo_summary()
    dashboard = generate_dashboard(summary, tmp_path / "dashboard.html")
    heroes = write_hero_candidates(summary, tmp_path / "heroes")
    screenshot = tmp_path / "hero.png"
    screenshot.write_bytes(_hero_png())
    output = write_freeze_manifest(
        summary,
        dashboard,
        heroes,
        tmp_path / "freeze.json",
        screenshot_paths=[screenshot],
        git_sha="a" * 40,
        git_dirty=False,
    )
    manifest = json.loads(output.read_text(encoding="utf-8"))

    assert len(manifest["census_sha256"]) == 64
    assert len(manifest["dashboard"]["sha256"]) == 64
    assert len(manifest["hero_html"]) == 2
    assert len(manifest["screenshots"]) == 1
    assert manifest["screenshots"][0]["width"] == 1080
    assert manifest["screenshots"][0]["height"] == 1350
    assert manifest["screenshots"][0]["visual_audit"]["passed"] is True
    assert manifest["qualified_broadcasts"] == 1260
    assert manifest["git_sha"] == "a" * 40
    assert manifest["git_dirty"] is False
    assert manifest["metrics"]["qualified_broadcasts"] == 1260
    assert manifest["metrics"]["brand_count"] == 10
    assert manifest["metrics"]["broadcast_brand_count"] == 10
    assert manifest["metrics"]["offer_share"] == 50.0
    assert manifest["metrics"]["seasonal_share"] == 15.0
    assert manifest["metrics"]["seasonal_offer_share"] == 73.5
    assert manifest["metrics"]["cadence_coverage_brand_share"] == 100.0
    assert manifest["metrics"]["quadrants"]["Evergreen content"] == {
        "count": 580,
        "percentage": 46.0,
    }


def test_dashboard_hero_and_manifest_files_are_private(tmp_path: Path) -> None:
    summary = demo_summary()
    dashboard = generate_dashboard(summary, tmp_path / "dashboard.html")
    heroes = write_hero_candidates(summary, tmp_path / "heroes")
    screenshot = tmp_path / "hero.png"
    screenshot.write_bytes(_hero_png())
    manifest = write_freeze_manifest(
        summary,
        dashboard,
        heroes,
        tmp_path / "freeze.json",
        screenshot_paths=[screenshot],
    )

    for path in (dashboard, *heroes, manifest):
        assert path.stat().st_mode & 0o777 == 0o600
