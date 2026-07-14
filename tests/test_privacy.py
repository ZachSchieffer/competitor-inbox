from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import privacy_audit  # noqa: E402
import validate_package  # noqa: E402


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def init_repo(path: Path) -> Path:
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.name", "Privacy Test")
    git(path, "config", "user.email", "privacy-test@localhost")
    return path


def test_safe_repository_passes_and_example_is_reported_separately(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / "README.md").write_text("Synthetic dashboard code only.\n", encoding="utf-8")
    synthetic_address = "demo" + "@" + "example.test"
    (repo / "config.example").write_text(
        f"synthetic_contact = '{synthetic_address}'\n", encoding="utf-8"
    )

    report = privacy_audit.scan_repository(repo)

    assert report["ok"] is True
    assert report["violation_count"] == 0
    assert report["synthetic_finding_count"] >= 1
    assert report["synthetic_example_finding_count"] >= 1
    assert all(
        row["path"] == "config.example" for row in report["synthetic_findings"]
    )


def test_audit_redacts_match_and_sensitive_path(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    private_value = "owner" + "@" + "private.corp"
    sensitive_path = repo / f"{private_value}.txt"
    sensitive_path.write_text(private_value, encoding="utf-8")

    report = privacy_audit.scan_repository(repo)
    rendered = privacy_audit.render_human(report)
    serialized = json.dumps(report)

    assert report["ok"] is False
    assert "email_address" in {row["rule_id"] for row in report["violations"]}
    assert private_value not in rendered
    assert private_value not in serialized
    assert any(
        str(row["path"]).startswith("<redacted-path:")
        for row in report["violations"]
    )


def test_audit_scans_staged_index_and_deleted_history(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    historic_token = "sk-ant-" + "A" * 24
    historic = repo / "historic.txt"
    historic.write_text(historic_token, encoding="utf-8")
    git(repo, "add", "historic.txt")
    git(repo, "commit", "-qm", "add historic fixture")
    git(repo, "rm", "-q", "historic.txt")
    git(repo, "commit", "-qm", "remove historic fixture")

    staged = repo / "staged.txt"
    staged_value = "this-is-" + "sensitive"
    staged_assignment = "pass" + f'word = "{staged_value}"\n'
    staged.write_text(staged_assignment, encoding="utf-8")
    git(repo, "add", "staged.txt")

    report = privacy_audit.scan_repository(repo)
    rows = report["violations"]

    assert any(
        row["scope"] == "history" and row["rule_id"] == "anthropic_key"
        for row in rows
    )
    assert any(
        row["scope"] == "staged" and row["rule_id"] == "password_assignment"
        for row in rows
    )
    assert historic_token not in json.dumps(report)


def test_audit_scans_unreachable_git_blobs(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    unreachable_token = "sk-ant-" + "U" * 24
    subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input=unreachable_token.encode(),
        check=True,
        capture_output=True,
    )

    report = privacy_audit.scan_repository(repo)

    assert report["scanned"]["unreachable"] >= 1
    assert any(
        row["scope"] == "unreachable" and row["rule_id"] == "anthropic_key"
        for row in report["violations"]
    )
    assert unreachable_token not in json.dumps(report)


def test_audit_inventories_reviewable_assets_without_exposing_content(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    payload = b"synthetic-image-bytes"
    (repo / "hero.png").write_bytes(payload)

    report = privacy_audit.scan_repository(repo)

    assert report["ok"] is True
    assert report["asset_count"] >= 1
    assert report["assets_for_manual_review"][0]["sha256"] == __import__(
        "hashlib"
    ).sha256(payload).hexdigest()


def test_package_validator_accepts_a_frozen_partial_artifact(tmp_path: Path) -> None:
    post = tmp_path / "post.txt"
    post.write_text(
        "I built a private competitor census from an inbox I control.\n\n"
        "Comment INBOX and connect with me. I'll send it.\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "package.json"
    manifest.write_text(
        json.dumps(
            {
                "package_root": ".",
                "keyword": "INBOX",
                "artifacts": [
                    {
                        "name": "post",
                        "kind": "linkedin_post",
                        "path": "post.txt",
                        "allowed_numbers": [],
                    }
                ],
                "urls": [],
                "images": [],
                "frozen_claims": [],
            }
        ),
        encoding="utf-8",
    )

    report = validate_package.validate_manifest(manifest, allow_partial=True)

    assert report["ok"] is True, report


def test_package_validator_allows_declared_tokens_only_in_repository_docs(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs.txt"
    docs.write_text("Set <DATA_ROOT> before using INBOX.\n", encoding="utf-8")
    post = tmp_path / "post.txt"
    post.write_text("Comment INBOX and use <DATA_ROOT>.\n", encoding="utf-8")
    manifest = tmp_path / "package.json"
    manifest.write_text(
        json.dumps(
            {
                "package_root": ".",
                "keyword": "INBOX",
                "artifacts": [
                    {
                        "name": "docs",
                        "kind": "repository_docs",
                        "path": "docs.txt",
                        "allowed_numbers": [],
                        "allowed_template_tokens": ["DATA_ROOT"],
                    },
                    {
                        "name": "post",
                        "kind": "linkedin_post",
                        "path": "post.txt",
                        "allowed_numbers": [],
                        "allowed_template_tokens": ["DATA_ROOT"],
                    },
                ],
                "urls": [],
                "images": [],
                "frozen_claims": [],
            }
        ),
        encoding="utf-8",
    )

    report = validate_package.validate_manifest(manifest, allow_partial=True)
    unresolved = [
        row
        for row in report["issues"]
        if row["rule_id"] == "unresolved_angle_token"
    ]

    assert len(unresolved) == 1
    assert unresolved[0]["artifact"] == "post"


def test_package_validator_fails_unresolved_copy_url_keyword_and_count(tmp_path: Path) -> None:
    post = tmp_path / "post.txt"
    post.write_text(
        "TBD — Most brands need this.\n"
        "Read https://example.com/resource for 255 emails.\n"
        "Comment ROI and connect with me.\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "package.json"
    manifest.write_text(
        json.dumps(
            {
                "package_root": ".",
                "keyword": "INBOX",
                "artifacts": [
                    {
                        "name": "post",
                        "kind": "linkedin_post",
                        "path": "post.txt",
                        "allowed_numbers": [300],
                    }
                ],
                "urls": [],
                "images": [],
                "frozen_claims": [],
            }
        ),
        encoding="utf-8",
    )

    report = validate_package.validate_manifest(manifest, allow_partial=True)
    rules = {row["rule_id"] for row in report["issues"]}

    assert report["ok"] is False
    assert {
        "tbd",
        "em_dash",
        "most_opener",
        "dummy_url",
        "unconfirmed_url",
        "external_link_not_allowed",
        "missing_keyword",
        "wrong_comment_keyword",
        "unfrozen_or_stale_number",
    } <= rules


def test_package_validator_checks_frozen_claim_and_rejects_kit(tmp_path: Path) -> None:
    notion = tmp_path / "notion.txt"
    notion.write_text("The census contains 301 qualified broadcasts. INBOX\n", encoding="utf-8")
    manifest = tmp_path / "package.json"
    manifest.write_text(
        json.dumps(
            {
                "package_root": ".",
                "keyword": "INBOX",
                "required_components": ["kit"],
                "artifacts": [
                    {
                        "name": "notion",
                        "kind": "notion",
                        "path": "notion.txt",
                        "allowed_numbers": [301],
                    }
                ],
                "urls": [],
                "images": [],
                "frozen_claims": [
                    {
                        "name": "qualified_broadcasts",
                        "expected": 300,
                        "pattern": "(?P<value>\\d+) qualified broadcasts",
                        "artifacts": ["notion"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = validate_package.validate_manifest(manifest, allow_partial=True)
    rules = {row["rule_id"] for row in report["issues"]}

    assert "kit_must_not_be_required" in rules
    assert "stale_frozen_claim" in rules


def _complete_package(tmp_path: Path) -> Path:
    public_url = "https://www.notion.so/help"
    copy = {
        "linkedin_post": (
            "A census covers 300 qualified broadcasts.\n\n"
            "Comment INBOX and connect with me. I'll send it.\n"
        ),
        "pinned_comment": "The second proof image uses the same census.\n",
        "notion": f"The INBOX guide covers 300 qualified broadcasts. {public_url}\n",
        "bolu": f"Deliver INBOX from {public_url}\n",
        "asana": f"INBOX launch task. Public guide: {public_url}\n",
        "repository_docs": "INBOX setup documentation.\n",
    }
    artifacts = []
    for kind, text in copy.items():
        path = tmp_path / f"{kind}.txt"
        path.write_text(text, encoding="utf-8")
        artifacts.append(
            {
                "name": kind,
                "kind": kind,
                "path": path.name,
                "allowed_numbers": [300] if kind in {"linkedin_post", "notion"} else [],
            }
        )

    image = tmp_path / "hero.png"
    image.write_bytes(b"synthetic final proof image")
    image_hash = hashlib.sha256(image.read_bytes()).hexdigest()
    freeze = tmp_path / "freeze.json"
    freeze.write_text(
        json.dumps(
            {
                "census_sha256": "a" * 64,
                "dashboard": {"sha256": "b" * 64},
                "screenshots": [{"path": "hero.png", "sha256": image_hash}],
                "qualified_broadcasts": 300,
                "metrics": {
                    "raw_messages": 300,
                    "qualified_broadcasts": 300,
                    "brand_count": 10,
                    "broadcast_brand_count": 10,
                    "observed_days": 365,
                    "offer_count": 150,
                    "offer_share": 50.0,
                    "seasonal_count": 75,
                    "seasonal_promotion_count": 75,
                    "cadence_coverage_brand_count": 10,
                    "seasonal_share": 25.0,
                    "seasonal_offer_share": 100.0,
                    "cadence_coverage_brand_share": 100.0,
                    "quadrants": {
                        "Evergreen content": {"count": 150, "percentage": 50.0},
                        "Everyday promotion": {"count": 75, "percentage": 25.0},
                        "Seasonal promotion": {"count": 75, "percentage": 25.0},
                        "Seasonal content": {"count": 0, "percentage": 0.0},
                    },
                },
                "git_sha": "a" * 40,
                "git_dirty": False,
            }
        ),
        encoding="utf-8",
    )
    artifacts.append(
        {
            "name": "screenshot_manifest",
            "kind": "screenshot_manifest",
            "path": freeze.name,
        }
    )
    manifest = tmp_path / "package.json"
    manifest.write_text(
        json.dumps(
            {
                "package_root": ".",
                "keyword": "INBOX",
                "required_components": [],
                "artifacts": artifacts,
                "urls": [
                    {
                        "name": "public_notion",
                        "role": "public_notion",
                        "url": public_url,
                        "status": "CONFIRMED",
                        "verified": True,
                        "verification": "logged_out",
                    }
                ],
                "images": [
                    {"name": "hero", "path": image.name, "sha256": image_hash}
                ],
                "frozen_claims": [
                    {
                        "name": "qualified_broadcasts",
                        "expected": 300,
                        "freeze_field": "metrics.qualified_broadcasts",
                        "pattern": "(?P<value>\\d+) qualified broadcasts",
                        "artifacts": ["linkedin_post", "notion"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_package_validator_accepts_every_final_artifact_type(tmp_path: Path) -> None:
    manifest = _complete_package(tmp_path)

    report = validate_package.validate_manifest(manifest)

    assert validate_package.DEFAULT_REQUIRED_KINDS == {
        "linkedin_post",
        "pinned_comment",
        "notion",
        "bolu",
        "asana",
        "repository_docs",
        "screenshot_manifest",
    }
    assert report["ok"] is True, report
    assert report["artifact_count"] == 7
    assert report["image_count"] == 1
    assert report["confirmed_url_count"] == 1


def test_package_validator_requires_frozen_screenshot_artifacts(tmp_path: Path) -> None:
    manifest = _complete_package(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["artifacts"] = [
        item for item in payload["artifacts"] if item["kind"] != "screenshot_manifest"
    ]
    payload["images"] = []
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    report = validate_package.validate_manifest(manifest)
    pairs = {(row["rule_id"], row.get("detail")) for row in report["issues"]}

    assert ("missing_required_artifact_kind", "screenshot_manifest") in pairs
    assert ("missing_required_image", None) in pairs
    assert ("missing_screenshot_manifest_binding", None) in pairs


def test_package_validator_rejects_unverified_and_dummy_confirmed_urls(
    tmp_path: Path,
) -> None:
    notion = tmp_path / "notion.txt"
    notion.write_text(
        "INBOX guide: https://www.notion.so/help and https://www.notion.so/TBD\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "package.json"
    manifest.write_text(
        json.dumps(
            {
                "package_root": ".",
                "keyword": "INBOX",
                "artifacts": [
                    {
                        "name": "notion",
                        "kind": "notion",
                        "path": notion.name,
                        "allowed_numbers": [],
                    }
                ],
                "urls": [
                    {
                        "name": "unverified",
                        "url": "https://www.notion.so/help",
                        "status": "CONFIRMED",
                    },
                    {
                        "name": "dummy_path",
                        "url": "https://www.notion.so/TBD",
                        "status": "CONFIRMED",
                        "verified": True,
                        "verification": "manual",
                    },
                ],
                "images": [],
                "frozen_claims": [],
            }
        ),
        encoding="utf-8",
    )

    report = validate_package.validate_manifest(manifest, allow_partial=True)
    rules = {row["rule_id"] for row in report["issues"]}

    assert "url_confirmed_without_verification" in rules
    assert "dummy_url" in rules
    assert "unconfirmed_url" in rules


def test_package_validator_allows_explicit_missing_link_statuses(tmp_path: Path) -> None:
    manifest = _complete_package(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["urls"].extend(
        [
            {
                "name": "booking_link",
                "status": "NEEDS-CONFIRMATION",
                "url": None,
                "reason": "No verified booking link was found in approved sources.",
            },
            {
                "name": "apply_link",
                "status": "UNAVAILABLE",
                "url": "",
                "reason": "No public apply link is available for this launch.",
            },
        ]
    )
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    report = validate_package.validate_manifest(manifest)

    assert report["ok"] is True, report


def test_package_validator_rejects_unverified_url_inside_missing_status(
    tmp_path: Path,
) -> None:
    manifest = _complete_package(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["urls"].append(
        {
            "name": "booking_link",
            "status": "NEEDS-CONFIRMATION",
            "url": "https://www.notion.so/help",
            "reason": "Still needs review.",
        }
    )
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    report = validate_package.validate_manifest(manifest)
    rules = {row["rule_id"] for row in report["issues"]}

    assert "unconfirmed_url_value_present" in rules


def test_package_validator_requires_logged_out_public_notion_contract(
    tmp_path: Path,
) -> None:
    manifest = _complete_package(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["urls"][0]["verification"] = "signed_in"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    signed_in_report = validate_package.validate_manifest(manifest)
    signed_in_rules = {row["rule_id"] for row in signed_in_report["issues"]}

    assert "public_notion_not_logged_out_verified" in signed_in_rules

    payload["urls"][0].pop("role")
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    missing_role_report = validate_package.validate_manifest(manifest)
    missing_role_rules = {row["rule_id"] for row in missing_role_report["issues"]}

    assert "missing_public_notion_url" in missing_role_rules


def test_package_validator_binds_counts_and_images_to_freeze_manifest(
    tmp_path: Path,
) -> None:
    manifest = _complete_package(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    for name in ("linkedin_post", "notion"):
        path = tmp_path / f"{name}.txt"
        path.write_text(
            path.read_text(encoding="utf-8").replace("300", "301"),
            encoding="utf-8",
        )
    for artifact in payload["artifacts"]:
        if artifact["kind"] in {"linkedin_post", "notion"}:
            artifact["allowed_numbers"] = [301]
    payload["frozen_claims"][0]["expected"] = 301
    freeze = json.loads((tmp_path / "freeze.json").read_text(encoding="utf-8"))
    freeze["screenshots"][0]["sha256"] = "f" * 64
    (tmp_path / "freeze.json").write_text(json.dumps(freeze), encoding="utf-8")
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    report = validate_package.validate_manifest(manifest)
    rules = {row["rule_id"] for row in report["issues"]}

    assert "frozen_claim_expected_mismatch" in rules
    assert "allowed_number_not_frozen_or_static" in rules
    assert "image_hash_not_frozen" in rules
    assert "frozen_screenshot_missing_from_package" in rules
    assert "unfrozen_or_stale_number" not in rules


def test_package_validator_requires_canonical_metrics_and_documented_static_numbers(
    tmp_path: Path,
) -> None:
    manifest = _complete_package(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    post = tmp_path / "linkedin_post.txt"
    post.write_text(
        post.read_text(encoding="utf-8").replace(
            "A census covers", "First-response coverage lasts 2 hours. A census covers"
        ),
        encoding="utf-8",
    )
    post_contract = next(
        item for item in payload["artifacts"] if item["kind"] == "linkedin_post"
    )
    post_contract["allowed_numbers"] = [2, 300]
    payload["static_numbers"] = [2]
    freeze = json.loads((tmp_path / "freeze.json").read_text(encoding="utf-8"))
    freeze.pop("metrics")
    (tmp_path / "freeze.json").write_text(json.dumps(freeze), encoding="utf-8")
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    failed = validate_package.validate_manifest(manifest)
    failed_rules = {row["rule_id"] for row in failed["issues"]}

    assert "missing_frozen_metrics" in failed_rules
    assert "invalid_static_number_contract" in failed_rules
    assert "allowed_number_not_frozen_or_static" in failed_rules

    freeze["metrics"] = {
        "raw_messages": 300,
        "qualified_broadcasts": 300,
        "brand_count": 10,
        "broadcast_brand_count": 10,
        "observed_days": 365,
        "offer_count": 150,
        "offer_share": 50.0,
        "seasonal_count": 75,
        "seasonal_promotion_count": 75,
        "cadence_coverage_brand_count": 10,
        "seasonal_share": 25.0,
        "seasonal_offer_share": 100.0,
        "cadence_coverage_brand_share": 100.0,
        "quadrants": {
            "Evergreen content": {"count": 150, "percentage": 50.0},
            "Everyday promotion": {"count": 75, "percentage": 25.0},
            "Seasonal promotion": {"count": 75, "percentage": 25.0},
            "Seasonal content": {"count": 0, "percentage": 0.0},
        },
    }
    (tmp_path / "freeze.json").write_text(json.dumps(freeze), encoding="utf-8")
    payload["static_numbers"] = [
        {
            "value": 2,
            "kind": "operational",
            "reason": "Launch-window coverage in hours.",
        }
    ]
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    passed = validate_package.validate_manifest(manifest)

    assert passed["ok"] is True, passed


def test_package_validator_requires_immutable_clean_git_freeze(tmp_path: Path) -> None:
    manifest = _complete_package(tmp_path)
    freeze_path = tmp_path / "freeze.json"
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    freeze["git_sha"] = "uncommitted"
    freeze["git_dirty"] = True
    freeze_path.write_text(json.dumps(freeze), encoding="utf-8")

    report = validate_package.validate_manifest(manifest)
    rules = {row["rule_id"] for row in report["issues"]}

    assert "invalid_frozen_git_sha" in rules
    assert "frozen_worktree_not_clean" in rules


def test_package_validator_catches_remaining_voice_tokens_and_kit_variants(
    tmp_path: Path,
) -> None:
    post = tmp_path / "post.txt"
    post.write_text(
        "No edits. No switches. No tweaks.\n"
        "Why does this matter?\n"
        "Everyone focuses on volume. The real answer is PUBLIC_NOTION_URL.\n"
        "This isn't a library. This is a strategy view.\n"
        "Comment INBOX and connect with me. I'll send it.\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "package.json"
    manifest.write_text(
        json.dumps(
            {
                "package_root": ".",
                "keyword": "INBOX",
                "required_components": ["Kit broadcast"],
                "artifacts": [
                    {
                        "name": "post",
                        "kind": "linkedin_post",
                        "path": post.name,
                        "allowed_numbers": [],
                    }
                ],
                "urls": [],
                "images": [],
                "frozen_claims": [],
            }
        ),
        encoding="utf-8",
    )

    report = validate_package.validate_manifest(manifest, allow_partial=True)
    rules = {row["rule_id"] for row in report["issues"]}

    assert {
        "asyndeton",
        "rhetorical_question",
        "reversal_everyone",
        "false_negative",
        "unresolved_named_token",
        "kit_must_not_be_required",
    } <= rules


def test_package_validator_requires_exact_keyword_token(tmp_path: Path) -> None:
    post = tmp_path / "post.txt"
    post.write_text(
        "Comment INBOXES and connect with me. I'll send it.\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "package.json"
    manifest.write_text(
        json.dumps(
            {
                "package_root": ".",
                "keyword": "INBOX",
                "artifacts": [
                    {
                        "name": "post",
                        "kind": "linkedin_post",
                        "path": post.name,
                        "allowed_numbers": [],
                    }
                ],
                "urls": [],
                "images": [],
                "frozen_claims": [],
            }
        ),
        encoding="utf-8",
    )

    report = validate_package.validate_manifest(manifest, allow_partial=True)
    rules = {row["rule_id"] for row in report["issues"]}

    assert "missing_keyword" in rules
    assert "wrong_comment_keyword" in rules
