#!/usr/bin/env python3
"""Validate frozen Competitor Inbox distribution artifacts.

The validator consumes a JSON package manifest. Each text artifact declares its
kind, path, whether it must contain the launch keyword, and every number allowed
to appear after URLs are removed. Confirmed URLs and screenshot hashes are also
bound in the manifest, which makes stale counts and unwired links fail closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Sequence
from urllib.parse import urlsplit, urlunsplit


EXPECTED_KEYWORD = "INBOX"
DEFAULT_REQUIRED_KINDS = {
    "linkedin_post",
    "pinned_comment",
    "notion",
    "bolu",
    "asana",
    "repository_docs",
}
TEXT_KINDS = DEFAULT_REQUIRED_KINDS
NO_LINK_KINDS = {"linkedin_post", "pinned_comment"}
LINKEDIN_FORMAT_KINDS = {"linkedin_post", "pinned_comment"}
ALLOWED_DYNAMIC_TOKENS = {"name", "first name", "company", "brand"}

URL_RE = re.compile(r"https?://[^\s<>()\[\]{}]+", re.IGNORECASE)
ANGLE_TOKEN_RE = re.compile(r"<[^>\r\n]+>")
BRACE_TOKEN_RE = re.compile(r"\{[^}\r\n]+\}")
SQUARE_TOKEN_RE = re.compile(r"\[([^\]\r\n]+)\](?!\s*\()")
NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])(?:\$)?\d[\d,]*(?:\.\d+)?%?(?![A-Za-z0-9])")

BANNED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("em_dash", re.compile(r"—|&mdash;|&#(?:8212|x2014);", re.IGNORECASE)),
    (
        "most_opener",
        re.compile(r"(?mi)(?:^|[.!?—]\s+|\n\s*)(?:[-*>]\s*)?Most\b"),
    ),
    ("shift_word", re.compile(r"\bshift(?:s|ed|ing)?\b", re.IGNORECASE)),
    ("heres_the_thing", re.compile(r"\bhere['’]s the thing\b", re.IGNORECASE)),
    ("basically", re.compile(r"\bbasically\b", re.IGNORECASE)),
    ("essentially", re.compile(r"\bessentially\b", re.IGNORECASE)),
    ("notably", re.compile(r"\bnotably\b", re.IGNORECASE)),
    ("furthermore", re.compile(r"\bfurthermore\b", re.IGNORECASE)),
    ("moreover", re.compile(r"\bmoreover\b", re.IGNORECASE)),
    ("additionally", re.compile(r"\badditionally\b", re.IGNORECASE)),
    ("leverage", re.compile(r"\bleverag(?:e|es|ed|ing)\b", re.IGNORECASE)),
    ("utilize", re.compile(r"\butiliz(?:e|es|ed|ing|ation)\b", re.IGNORECASE)),
    ("seamless", re.compile(r"\bseamless(?:ly)?\b", re.IGNORECASE)),
    ("robust", re.compile(r"\brobust\b", re.IGNORECASE)),
    ("unlock", re.compile(r"\bunlock(?:s|ed|ing)?\b", re.IGNORECASE)),
    ("game_changer", re.compile(r"\bgame[- ]changer\b", re.IGNORECASE)),
    ("dive_deep", re.compile(r"\bdive deep\b", re.IGNORECASE)),
    ("revolutionary", re.compile(r"\brevolutionary\b", re.IGNORECASE)),
    ("false_negative", re.compile(r"\bit['’]?s not\b.{0,120}\bit['’]?s\b", re.IGNORECASE | re.DOTALL)),
    ("reversal_people", re.compile(r"\bmost people think\b", re.IGNORECASE)),
    ("reversal_truth", re.compile(r"\bthe truth is\b", re.IGNORECASE)),
    ("rhetorical_setup", re.compile(r"(?mi)^\s*(?:the reality|here['’]s why|the strategy)\s*[?:]\s*$")),
    ("mechanism_contrast", re.compile(r"\bin ways\b.{0,100}\b(?:can['’]?t|cannot)\b", re.IGNORECASE)),
    ("vague_authority", re.compile(r"\bi(?:'ve| have) seen this play out\b|\bi see this constantly\b", re.IGNORECASE)),
    ("clever_comparative", re.compile(r"\bbad\b.{0,120}\bgreat\b", re.IGNORECASE | re.DOTALL)),
    ("hashtag", re.compile(r"(?<![\w/])#[A-Za-z][A-Za-z0-9_]*\b")),
)

PLACEHOLDER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("tbd", re.compile(r"\bTBD\b", re.IGNORECASE)),
    ("todo", re.compile(r"\bTODO\b", re.IGNORECASE)),
    ("fixme", re.compile(r"\bFIXME\b", re.IGNORECASE)),
    ("placeholder_word", re.compile(r"\bPLACEHOLDER\b", re.IGNORECASE)),
    ("dummy_word", re.compile(r"\bDUMMY(?: URL)?\b", re.IGNORECASE)),
)

DUMMY_HOSTS = {
    "example.com",
    "example.org",
    "example.net",
    "localhost",
    "test",
    "invalid",
}


@dataclass(frozen=True)
class Issue:
    artifact: str
    rule_id: str
    line: int | None = None
    detail: str | None = None


def _line_number(text: str, start: int) -> int:
    return text.count("\n", 0, start) + 1


def _normalize_url(url: str) -> str:
    url = url.rstrip(".,;:!?'\"")
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))


def _urls(text: str) -> list[str]:
    return [_normalize_url(match.group(0)) for match in URL_RE.finditer(text)]


def _number_token(value: str) -> str:
    return value.replace(",", "").lstrip("$").rstrip("%")


def _numbers_without_urls(text: str) -> list[tuple[str, int]]:
    scrubbed = URL_RE.sub(" ", text)
    return [(_number_token(match.group(0)), _line_number(scrubbed, match.start())) for match in NUMBER_RE.finditer(scrubbed)]


def _first_words(line: str) -> str | None:
    cleaned = re.sub(r"^\s*(?:[-*+>]|\d+[.)])\s*", "", line)
    words = re.findall(r"[A-Za-z][A-Za-z'’]*", cleaned.lower())
    if len(words) < 2:
        return None
    return " ".join(words[:2])


def _anaphora_lines(text: str) -> Iterable[int]:
    sequence: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        prefix = _first_words(line)
        if prefix is None:
            continue
        sequence.append((number, prefix))
        if len(sequence) > 3:
            sequence.pop(0)
        if len(sequence) == 3 and len({item[1] for item in sequence}) == 1:
            yield sequence[0][0]


def _confirmed_urls(manifest: dict[str, Any]) -> set[str]:
    confirmed: set[str] = set()
    for item in manifest.get("urls", []):
        if not isinstance(item, dict):
            continue
        if item.get("status") != "CONFIRMED":
            continue
        value = item.get("url")
        if isinstance(value, str):
            confirmed.add(_normalize_url(value))
    return confirmed


def _validate_text_artifact(
    *,
    root: Path,
    artifact: dict[str, Any],
    keyword: str,
    confirmed_urls: set[str],
) -> list[Issue]:
    issues: list[Issue] = []
    name = str(artifact.get("name") or artifact.get("kind") or "unnamed")
    kind = str(artifact.get("kind") or "")
    raw_path = artifact.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return [Issue(name, "missing_artifact_path")]
    path = (root / raw_path).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return [Issue(name, "artifact_outside_package_root")]
    if not path.is_file():
        return [Issue(name, "missing_artifact_file")]

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [Issue(name, "artifact_not_utf8_text")]

    for rule_id, pattern in PLACEHOLDER_PATTERNS:
        for match in pattern.finditer(text):
            issues.append(Issue(name, rule_id, _line_number(text, match.start())))

    allowed_template_tokens = {
        str(value).strip().strip("<>")
        for value in artifact.get("allowed_template_tokens", [])
    }
    for match in ANGLE_TOKEN_RE.finditer(text):
        token = match.group(0).strip().strip("<>")
        if kind == "repository_docs" and token in allowed_template_tokens:
            continue
        issues.append(Issue(name, "unresolved_angle_token", _line_number(text, match.start())))
    for match in BRACE_TOKEN_RE.finditer(text):
        issues.append(Issue(name, "unresolved_brace_token", _line_number(text, match.start())))
    for match in SQUARE_TOKEN_RE.finditer(text):
        if match.group(1).strip().lower() not in ALLOWED_DYNAMIC_TOKENS:
            issues.append(Issue(name, "unresolved_square_token", _line_number(text, match.start())))

    extracted_urls = _urls(text)
    for url in extracted_urls:
        host = (urlsplit(url).hostname or "").lower()
        if host in DUMMY_HOSTS or any(host.endswith(f".{value}") for value in DUMMY_HOSTS):
            issues.append(Issue(name, "dummy_url"))
        if url not in confirmed_urls:
            issues.append(Issue(name, "unconfirmed_url", detail=hashlib.sha256(url.encode()).hexdigest()[:16]))
    if kind in NO_LINK_KINDS and extracted_urls:
        issues.append(Issue(name, "external_link_not_allowed"))

    requires_keyword = artifact.get("requires_keyword", kind not in {"pinned_comment", "repository_docs"})
    if requires_keyword and keyword not in text:
        issues.append(Issue(name, "missing_keyword"))
    for match in re.finditer(r"(?i)\bcomment\s+[\"'“”‘’]?([A-Z]{2,12})\b", text):
        if match.group(1).upper() != keyword:
            issues.append(Issue(name, "wrong_comment_keyword", _line_number(text, match.start())))

    for rule_id, pattern in BANNED_PATTERNS:
        for match in pattern.finditer(text):
            issues.append(Issue(name, rule_id, _line_number(text, match.start())))
    for line in _anaphora_lines(text):
        issues.append(Issue(name, "anaphora", line))

    if kind in LINKEDIN_FORMAT_KINDS:
        if re.search(r"\*\*|__", text):
            issues.append(Issue(name, "bold_formatting"))
        if re.search(r"(?<!\*)\*(?![\s*])|(?<!_)_(?![\s_])", text):
            issues.append(Issue(name, "italic_formatting"))

    if "allowed_numbers" not in artifact:
        issues.append(Issue(name, "missing_allowed_numbers_contract"))
    else:
        allowed = {_number_token(str(value)) for value in artifact.get("allowed_numbers", [])}
        for number, line in _numbers_without_urls(text):
            if number not in allowed:
                issues.append(Issue(name, "unfrozen_or_stale_number", line, number))

    return issues


def _validate_claims(root: Path, manifest: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    artifacts = {str(item.get("name")): item for item in manifest.get("artifacts", []) if isinstance(item, dict)}
    for claim in manifest.get("frozen_claims", []):
        if not isinstance(claim, dict):
            continue
        claim_name = str(claim.get("name") or "unnamed_claim")
        expected = _number_token(str(claim.get("expected", "")))
        pattern_value = claim.get("pattern")
        targets = claim.get("artifacts", [])
        if not expected or not isinstance(pattern_value, str) or not isinstance(targets, list):
            issues.append(Issue(claim_name, "invalid_frozen_claim_contract"))
            continue
        try:
            pattern = re.compile(pattern_value, re.IGNORECASE)
        except re.error:
            issues.append(Issue(claim_name, "invalid_frozen_claim_regex"))
            continue
        for target in targets:
            artifact = artifacts.get(str(target))
            if artifact is None:
                issues.append(Issue(claim_name, "claim_target_missing", detail=str(target)))
                continue
            path_value = artifact.get("path")
            if not isinstance(path_value, str):
                continue
            path = (root / path_value).resolve()
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            matches = list(pattern.finditer(text))
            if claim.get("required", True) and not matches:
                issues.append(Issue(str(target), "required_frozen_claim_missing", detail=claim_name))
            for match in matches:
                raw_value = match.groupdict().get("value") if match.groupdict() else match.group(0)
                if _number_token(raw_value) != expected:
                    issues.append(
                        Issue(
                            str(target),
                            "stale_frozen_claim",
                            _line_number(text, match.start()),
                            claim_name,
                        )
                    )
    return issues


def validate_manifest(manifest_path: Path, *, allow_partial: bool = False) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root_value = manifest.get("package_root", ".")
    if not isinstance(root_value, str):
        raise ValueError("package_root must be a string")
    root = (manifest_path.parent / root_value).resolve()
    issues: list[Issue] = []

    keyword = manifest.get("keyword")
    if keyword != EXPECTED_KEYWORD:
        issues.append(Issue("manifest", "wrong_manifest_keyword", detail=str(keyword)))
        keyword = EXPECTED_KEYWORD

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
        issues.append(Issue("manifest", "missing_artifacts"))
    kinds = {str(item.get("kind")) for item in artifacts if isinstance(item, dict)}
    if "kit" in kinds or any("kit" == str(item.get("name", "")).lower() for item in artifacts if isinstance(item, dict)):
        issues.append(Issue("manifest", "kit_must_not_be_required"))
    required_components = {str(item).lower() for item in manifest.get("required_components", [])}
    if "kit" in required_components or "kit_broadcast" in required_components:
        issues.append(Issue("manifest", "kit_must_not_be_required"))
    if not allow_partial:
        for missing in sorted(DEFAULT_REQUIRED_KINDS - kinds):
            issues.append(Issue("manifest", "missing_required_artifact_kind", detail=missing))

    confirmed_urls = _confirmed_urls(manifest)
    for item in manifest.get("urls", []):
        if not isinstance(item, dict):
            issues.append(Issue("manifest", "invalid_url_contract"))
            continue
        if item.get("status") != "CONFIRMED":
            issues.append(Issue(str(item.get("name") or "url"), "url_not_confirmed"))

    for artifact in artifacts:
        if not isinstance(artifact, dict):
            issues.append(Issue("manifest", "invalid_artifact_contract"))
            continue
        kind = str(artifact.get("kind") or "")
        if kind in TEXT_KINDS:
            issues.extend(
                _validate_text_artifact(
                    root=root,
                    artifact=artifact,
                    keyword=str(keyword),
                    confirmed_urls=confirmed_urls,
                )
            )

    for image in manifest.get("images", []):
        if not isinstance(image, dict):
            issues.append(Issue("manifest", "invalid_image_contract"))
            continue
        name = str(image.get("name") or "image")
        raw_path = image.get("path")
        expected_hash = image.get("sha256")
        if not isinstance(raw_path, str) or not isinstance(expected_hash, str):
            issues.append(Issue(name, "invalid_image_contract"))
            continue
        path = (root / raw_path).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            issues.append(Issue(name, "image_outside_package_root"))
            continue
        if not path.is_file():
            issues.append(Issue(name, "missing_image"))
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected_hash:
            issues.append(Issue(name, "stale_image_hash"))

    issues.extend(_validate_claims(root, manifest))
    return {
        "ok": not issues,
        "keyword": keyword,
        "artifact_count": len(artifacts),
        "confirmed_url_count": len(confirmed_urls),
        "image_count": len(manifest.get("images", [])),
        "issue_count": len(issues),
        "issues": [asdict(issue) for issue in issues],
    }


def render_human(report: dict[str, Any]) -> str:
    lines = [f"Package validation: {'PASS' if report['ok'] else 'FAIL'}"]
    lines.append(
        f"Artifacts: {report['artifact_count']}; confirmed URLs: "
        f"{report['confirmed_url_count']}; images: {report['image_count']}"
    )
    lines.append(f"Issues: {report['issue_count']}")
    for issue in report["issues"]:
        position = f" line={issue['line']}" if issue.get("line") else ""
        detail = f" detail={issue['detail']}" if issue.get("detail") else ""
        lines.append(
            f"- artifact={issue['artifact']} rule={issue['rule_id']}{position}{detail}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = validate_manifest(args.manifest, allow_partial=args.allow_partial)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Package validation could not run: {error}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_human(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
