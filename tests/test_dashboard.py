from __future__ import annotations

import json
import re
from pathlib import Path

from competitor_inbox.aggregate import aggregate_records
from competitor_inbox.dashboard import (
    generate_dashboard,
    render_dashboard,
    write_freeze_manifest,
    write_hero_candidates,
)
from competitor_inbox.demo import demo_summary


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
    candidates = write_hero_candidates(demo_summary(), tmp_path)

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
        r"1,260 competitor broadcasts mapped across 10 brands",
        candidates[1].read_text(encoding="utf-8"),
    )


def test_freeze_manifest_hashes_dashboard_heroes_and_finished_screenshots(tmp_path: Path) -> None:
    summary = demo_summary()
    dashboard = generate_dashboard(summary, tmp_path / "dashboard.html")
    heroes = write_hero_candidates(summary, tmp_path / "heroes")
    screenshot = tmp_path / "hero.png"
    screenshot.write_bytes(b"synthetic screenshot bytes")
    output = write_freeze_manifest(
        summary,
        dashboard,
        heroes,
        tmp_path / "freeze.json",
        screenshot_paths=[screenshot],
        git_sha="abc123",
    )
    manifest = json.loads(output.read_text(encoding="utf-8"))

    assert len(manifest["census_sha256"]) == 64
    assert len(manifest["dashboard"]["sha256"]) == 64
    assert len(manifest["hero_html"]) == 2
    assert len(manifest["screenshots"]) == 1
    assert manifest["qualified_broadcasts"] == 1260
    assert manifest["git_sha"] == "abc123"
