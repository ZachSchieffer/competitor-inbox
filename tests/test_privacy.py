from __future__ import annotations

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
