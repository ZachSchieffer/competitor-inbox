#!/usr/bin/env python3
"""Validate frozen Competitor Inbox distribution artifacts.

The validator consumes a JSON package manifest. Each text artifact declares its
kind, path, whether it must contain the launch keyword, and every number allowed
to appear after URLs are removed. Confirmed URLs and screenshot hashes are also
bound in the manifest, which makes stale counts and unwired links fail closed.
"""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import unquote, urlsplit, urlunsplit


EXPECTED_KEYWORD = "INBOX"
DEFAULT_REQUIRED_KINDS = {
    "linkedin_post",
    "pinned_comment",
    "notion",
    "bolu",
    "asana",
    "repository_docs",
    "screenshot_manifest",
}
TEXT_KINDS = DEFAULT_REQUIRED_KINDS - {"screenshot_manifest"}
NO_LINK_KINDS = {"linkedin_post", "pinned_comment"}
LINKEDIN_FORMAT_KINDS = {"linkedin_post", "pinned_comment"}
STRICT_QUANTITATIVE_KINDS = {"linkedin_post", "pinned_comment", "notion", "asana"}
FINDING_SEMANTIC_BINDINGS: dict[str, dict[str, str]] = {
    "finding_content_mix": {
        "numerator": "metrics.quadrants.Evergreen content.count",
        "denominator": "metrics.qualified_broadcasts",
    },
    "finding_offer_mix": {
        "numerator": "metrics.offer_count",
        "denominator": "metrics.qualified_broadcasts",
    },
    "finding_calendar_mix": {
        "numerator": "metrics.seasonal_count",
        "denominator": "metrics.qualified_broadcasts",
    },
    "finding_seasonal_promotion_mix": {
        "numerator": "metrics.seasonal_promotion_count",
        "denominator": "metrics.qualified_broadcasts",
    },
    "finding_cadence_coverage": {
        "numerator": "metrics.cadence_coverage_brand_count",
        "denominator": "metrics.broadcast_brand_count",
    },
}
FINDING_NAME_BY_LABEL = {
    "Content mix": "finding_content_mix",
    "Offer mix": "finding_offer_mix",
    "Calendar mix": "finding_calendar_mix",
    "Seasonal promotion mix": "finding_seasonal_promotion_mix",
    "Cadence coverage": "finding_cadence_coverage",
}
ALLOWED_DYNAMIC_TOKENS = {"name", "first name", "company", "brand"}
URL_STATUSES = {"CONFIRMED", "NEEDS-CONFIRMATION", "UNAVAILABLE"}

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
    (
        "false_negative",
        re.compile(
            r"\b(?:it|this|that)\s+(?:is not|isn['’]t|was not|wasn['’]t)\b"
            r".{0,140}\b(?:it|this|that)\s+(?:is|was)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    ("reversal_people", re.compile(r"\bmost people think\b", re.IGNORECASE)),
    ("reversal_truth", re.compile(r"\bthe truth is\b", re.IGNORECASE)),
    (
        "reversal_everyone",
        re.compile(
            r"\beveryone\s+(?:focuses|thinks|does|uses)\b.{0,180}"
            r"\b(?:the real|the truth|actually)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "asyndeton",
        re.compile(
            r"\bNo\s+[^.!?\n]{1,80}[.!?]\s+No\s+[^.!?\n]{1,80}[.!?]"
            r"\s+No\s+[^.!?\n]{1,80}[.!?]",
            re.IGNORECASE,
        ),
    ),
    ("rhetorical_setup", re.compile(r"(?mi)^\s*(?:the reality|here['’]s why|the strategy)\s*[?:]\s*$")),
    (
        "rhetorical_question",
        re.compile(
            r"(?mi)^\s*(?:why\s+(?:does|do|is|are|should|would|can)|"
            r"what\s+(?:does|do|is|are))\b[^?\n]{0,120}\?\s*$"
        ),
    ),
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
    (
        "unresolved_named_token",
        re.compile(r"\b(?:[A-Z][A-Z0-9]*_)+(?:URL|LINK|ID|GID|PATH|SHA|TOKEN|HERE)\b"),
    ),
)

DUMMY_HOSTS = {
    "example.com",
    "example.org",
    "example.net",
    "localhost",
    "test",
    "invalid",
}

_HEX_SHA256_RE = re.compile(r"[0-9a-f]{64}", re.IGNORECASE)
_HEX_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}", re.IGNORECASE)


@dataclass(frozen=True)
class Issue:
    artifact: str
    rule_id: str
    line: int | None = None
    detail: str | None = None


@dataclass(frozen=True)
class NumericOccurrence:
    token: str
    line: int
    start: int
    end: int


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
    raw = value.replace(",", "").strip()
    currency = raw.startswith("$")
    percent = raw.endswith("%")
    numeric = raw.lstrip("$").rstrip("%")
    if "." in numeric:
        numeric = numeric.rstrip("0").rstrip(".")
    if currency:
        return f"${numeric}"
    if percent:
        return f"{numeric}%"
    return numeric


def _identifier_spans(text: str) -> list[tuple[int, int]]:
    """Return non-quantitative identifier spans that can contain digits."""

    patterns = (
        URL_RE,
        re.compile(r"\b\d{1,2}:\d{2}\s*(?:AM|PM)\b", re.IGNORECASE),
        re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{40}(?![0-9A-Fa-f])"),
        re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{64}(?![0-9A-Fa-f])"),
        re.compile(r"\bv\d+(?:\.\d+){1,3}\b", re.IGNORECASE),
        re.compile(r"\bSHA-\d+\b", re.IGNORECASE),
    )
    return [match.span() for pattern in patterns for match in pattern.finditer(text)]


def _numeric_occurrences(text: str) -> list[NumericOccurrence]:
    identifiers = _identifier_spans(text)
    output: list[NumericOccurrence] = []
    for match in NUMBER_RE.finditer(text):
        start, end = match.span()
        if any(left <= start and end <= right for left, right in identifiers):
            continue
        output.append(
            NumericOccurrence(
                token=_number_token(match.group(0)),
                line=_line_number(text, start),
                start=start,
                end=end,
            )
        )
    return output


def _numbers_without_urls(text: str) -> list[tuple[str, int]]:
    return [(item.token, item.line) for item in _numeric_occurrences(text)]


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
        if (
            item.get("status") != "CONFIRMED"
            or item.get("verified") is not True
            or not str(item.get("verification") or "").strip()
        ):
            continue
        value = item.get("url")
        if isinstance(value, str) and not _url_contract_problem(value):
            confirmed.add(_normalize_url(value))
    return confirmed


def _url_contract_problem(value: str) -> str | None:
    decoded = unquote(value).strip()
    parsed = urlsplit(decoded)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return "invalid_url"
    host = parsed.hostname.lower()
    if host in DUMMY_HOSTS or any(host.endswith(f".{candidate}") for candidate in DUMMY_HOSTS):
        return "dummy_url"
    if any(pattern.search(decoded) for _, pattern in PLACEHOLDER_PATTERNS):
        return "dummy_url"
    if ANGLE_TOKEN_RE.search(decoded) or BRACE_TOKEN_RE.search(decoded) or SQUARE_TOKEN_RE.search(decoded):
        return "dummy_url"
    return None


def _is_kit_requirement(value: Any) -> bool:
    normalized = re.sub(r"[_-]+", " ", str(value or "")).casefold()
    return re.search(r"\bkit\b", normalized) is not None


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and _HEX_SHA256_RE.fullmatch(value) is not None


def _nested_value(value: Mapping[str, Any], dotted_path: str) -> Any:
    current: Any = value
    for part in dotted_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _numeric_tokens(value: Any, *, path: tuple[str, ...] = ()) -> set[str]:
    if isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)):
        token = _number_token(str(value))
        leaf = path[-1].casefold() if path else ""
        if leaf.endswith(("share", "percentage", "rate")):
            return {f"{token}%"}
        return {token}
    if isinstance(value, Mapping):
        output: set[str] = set()
        for key, nested in value.items():
            output.update(_numeric_tokens(nested, path=(*path, str(key))))
        return output
    if isinstance(value, list):
        output = set()
        for index, nested in enumerate(value):
            output.update(_numeric_tokens(nested, path=(*path, str(index))))
        return output
    if isinstance(value, str):
        return {number for number, _ in _numbers_without_urls(value)}
    return set()


def _line_text(text: str, start: int) -> str:
    left = text.rfind("\n", 0, start) + 1
    right = text.find("\n", start)
    if right < 0:
        right = len(text)
    return text[left:right].strip()


_NOTION_OPERATIONAL_CONTEXTS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^#+\s+(?:Stage\s+\d+|\d+-quadrant census)\b",
        r"^\d+[.)]\s+(?:Start Here|Stage\s+\d+|The Worked Example|Benchmarks|Privacy|Work With|Sign in|Open Google|Turn on|Create an app password|Copy the generated)",
        r"\bPython\s+\d+(?:\.\d+)+\s+or newer\b",
        r"\bgit clone --branch v\d+(?:\.\d+)+ --depth \d+\b",
        r"\bbackfill (?:--months\s+)?\d+\s+calendar months\b|\bbackfill --months\s+\d+\b",
        r"^[-*]\s+At least \d+ qualified broadcasts across the allowlisted source universe$",
        r"^[-*]\s+At least \d+ brand with \d+ qualified broadcasts over at least \d+ observed days$",
        r"\bfailed gate exits with code\s+`?\d+`?\b",
        r"\bsingle-brand public hook\b.*\bat least \d+ qualified broadcasts\b.*\bat least \d+ observed days\b",
        r"^\|\s*(?:[^|,]+,\s*)?(?:Under \d+ days|\d+ to \d+ days|\d+ or more days)\s*\|",
        r"\bposture label requires\b.*\bat least \d+%.*\bat least \d+(?:\.\d+)? times\b",
        r"\bLaunchAgent runs at \d+(?::\d+)?\s*(?:AM|PM)\b.*\b\d+-day overlap\b",
        r"\bowner action plan in \d+ windows\b",
        r"^[-*]\s+(?:First|Following|Full)\s+\d+-day window\b|^[-*]\s+(?:First|Following|Full)\s+\d+ days\b",
        r"\b\d+-character app password\b|\b\d+-Step Verification\b",
    )
)

_ASANA_OPERATIONAL_CONTEXTS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^(?:Immutable tag|Tag URL|Git SHA|Main image|Pinned image|Repo release):",
        r"^[-*]\s+(?:Hero attachment|Pinned-comment attachment|Census SHA|Dashboard SHA|Repo release):",
        r"^\d+[.)]\s+Launch:\s+Thursday July \d+,\s+\d{1,2}:\d{2}\s+(?:AM|PM)\s+Phoenix\.\s+Do not post before this time\.$",
        r"^\d+[.)]\s+Zach publishes the post with",
        r"^\d+[.)]\s+After the post is live, attach",
        r"^\d+[.)]\s+Bolu or Michelle covers the first \d+ hours.*\b\d+ hours\b",
        r"^\d+[.)]\s+The response target is under \d+ minutes.*under \d+ hour.*first \d+ hours\b",
        r"^\d+[.)]\s+Bolu delivers\b",
        r"^\d+[.)]\s+Keep all LinkedIn activity manual\b",
    )
)


def _static_context_is_operational(kind: str, line: str) -> bool:
    if kind in {"linkedin_post", "pinned_comment"}:
        return False
    if kind == "notion":
        return any(pattern.search(line) for pattern in _NOTION_OPERATIONAL_CONTEXTS)
    if kind == "asana":
        return any(pattern.search(line) for pattern in _ASANA_OPERATIONAL_CONTEXTS)
    return True


def _operational_number_contract(
    manifest: Mapping[str, Any],
    *,
    root: Path,
    artifacts: Mapping[str, Mapping[str, Any]],
    final_contract: bool,
) -> tuple[dict[str, set[str]], dict[str, list[tuple[int, int]]], list[Issue]]:
    """Validate context-scoped non-census numbers and return their coverage."""

    issues: list[Issue] = []
    raw_values = manifest.get("static_numbers", [])
    if not isinstance(raw_values, list):
        return {}, {}, [Issue("manifest", "invalid_static_numbers_contract")]
    tokens_by_artifact: dict[str, set[str]] = {}
    coverage: dict[str, list[tuple[int, int]]] = {}
    seen_tokens: set[str] = set()
    text_cache: dict[str, str] = {}

    for index, item in enumerate(raw_values):
        name = f"static_numbers[{index}]"
        if not isinstance(item, Mapping):
            issues.append(Issue(name, "invalid_static_number_contract"))
            continue
        if item.get("kind") != "operational":
            issues.append(Issue(name, "static_number_must_be_operational"))
        value = item.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            issues.append(Issue(name, "invalid_static_number_value"))
            continue
        unit = str(item.get("unit") or "count")
        if unit not in {"count", "percent", "currency"}:
            issues.append(Issue(name, "invalid_static_number_unit", detail=unit))
            continue
        if not str(item.get("reason") or "").strip():
            issues.append(Issue(name, "missing_static_number_reason"))
        token = _number_token(str(value))
        if unit == "percent":
            token = f"{token}%"
        elif unit == "currency":
            token = f"${token}"
        if token in seen_tokens:
            issues.append(Issue(name, "duplicate_static_number", detail=token))
        seen_tokens.add(token)

        broad_targets = item.get("artifacts", [])
        if not isinstance(broad_targets, list):
            issues.append(Issue(name, "invalid_static_number_artifacts"))
            broad_targets = []
        for target in broad_targets:
            target_name = str(target)
            artifact = artifacts.get(target_name)
            if artifact is None:
                issues.append(Issue(name, "static_number_target_missing", detail=target_name))
                continue
            kind = str(artifact.get("kind") or "")
            if final_contract and kind in STRICT_QUANTITATIVE_KINDS:
                issues.append(
                    Issue(name, "broad_static_number_forbidden", detail=target_name)
                )
                continue
            tokens_by_artifact.setdefault(target_name, set()).add(token)

        uses = item.get("uses", [])
        if not isinstance(uses, list):
            issues.append(Issue(name, "invalid_static_number_uses"))
            continue
        for use_index, use in enumerate(uses):
            use_name = f"{name}.uses[{use_index}]"
            if not isinstance(use, Mapping):
                issues.append(Issue(use_name, "invalid_static_number_use"))
                continue
            target_name = str(use.get("artifact") or "")
            artifact = artifacts.get(target_name)
            if artifact is None:
                issues.append(Issue(use_name, "static_number_target_missing", detail=target_name))
                continue
            pattern_value = use.get("pattern")
            if not isinstance(pattern_value, str) or not pattern_value:
                issues.append(Issue(use_name, "invalid_static_number_pattern"))
                continue
            try:
                pattern = re.compile(pattern_value, re.IGNORECASE | re.MULTILINE)
            except re.error:
                issues.append(Issue(use_name, "invalid_static_number_pattern"))
                continue
            if "value" not in pattern.groupindex:
                issues.append(Issue(use_name, "static_number_pattern_missing_value_group"))
                continue
            raw_path = artifact.get("path")
            if not isinstance(raw_path, str):
                continue
            if target_name not in text_cache:
                path = (root / raw_path).resolve()
                try:
                    path.relative_to(root)
                    text_cache[target_name] = path.read_text(encoding="utf-8")
                except (ValueError, OSError, UnicodeDecodeError):
                    continue
            text = text_cache[target_name]
            matches = list(pattern.finditer(text))
            if use.get("required", True) and not matches:
                issues.append(Issue(use_name, "required_static_number_use_missing"))
            for match in matches:
                observed = _number_token(match.group("value"))
                if observed != token:
                    issues.append(
                        Issue(
                            target_name,
                            "stale_static_number_use",
                            _line_number(text, match.start("value")),
                            token,
                        )
                    )
                    continue
                kind = str(artifact.get("kind") or "")
                line = _line_text(text, match.start("value"))
                if final_contract and kind in STRICT_QUANTITATIVE_KINDS and not _static_context_is_operational(kind, line):
                    issues.append(
                        Issue(
                            target_name,
                            "static_use_not_operational_context",
                            _line_number(text, match.start("value")),
                            token,
                        )
                    )
                    continue
                tokens_by_artifact.setdefault(target_name, set()).add(token)
                coverage.setdefault(target_name, []).append(match.span("value"))
    return tokens_by_artifact, coverage, issues


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
    if not text.strip():
        issues.append(Issue(name, "empty_artifact"))

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
    if requires_keyword and not re.search(rf"\b{re.escape(keyword)}\b", text):
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
        allowed_values = artifact.get("allowed_numbers")
        if not isinstance(allowed_values, list):
            issues.append(Issue(name, "invalid_allowed_numbers_contract"))
        else:
            allowed = {_number_token(str(value)) for value in allowed_values}
            for number, line in _numbers_without_urls(text):
                if number not in allowed:
                    issues.append(Issue(name, "unfrozen_or_stale_number", line, number))

    return issues


def _load_screenshot_manifest(
    root: Path,
    artifacts: Sequence[dict[str, Any]],
    *,
    final_contract: bool,
) -> tuple[dict[str, Any] | None, list[Issue]]:
    issues: list[Issue] = []
    candidates = [artifact for artifact in artifacts if artifact.get("kind") == "screenshot_manifest"]
    if not candidates:
        return None, issues
    if len(candidates) > 1:
        issues.append(Issue("manifest", "duplicate_screenshot_manifest"))
    artifact = candidates[0]
    name = str(artifact.get("name") or "screenshot_manifest")
    raw_path = artifact.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return None, [*issues, Issue(name, "missing_artifact_path")]
    path = (root / raw_path).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None, [*issues, Issue(name, "artifact_outside_package_root")]
    if not path.is_file():
        return None, [*issues, Issue(name, "missing_artifact_file")]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, [*issues, Issue(name, "invalid_screenshot_manifest_json")]
    if not isinstance(value, dict):
        return None, [*issues, Issue(name, "invalid_screenshot_manifest_contract")]

    if not _valid_sha256(value.get("census_sha256")):
        issues.append(Issue(name, "invalid_census_hash"))
    dashboard = value.get("dashboard")
    if not isinstance(dashboard, dict) or not _valid_sha256(dashboard.get("sha256")):
        issues.append(Issue(name, "invalid_dashboard_hash"))
    screenshots = value.get("screenshots")
    if not isinstance(screenshots, list) or (
        final_contract and not screenshots
    ):
        issues.append(Issue(name, "missing_frozen_screenshots"))
    elif any(
        not isinstance(item, dict) or not _valid_sha256(item.get("sha256"))
        for item in screenshots
    ):
        issues.append(Issue(name, "invalid_frozen_screenshot_hash"))
    metrics = value.get("metrics")
    if not isinstance(metrics, dict):
        issues.append(Issue(name, "missing_frozen_metrics"))
    else:
        for field in (
            "raw_messages",
            "qualified_broadcasts",
            "brand_count",
            "broadcast_brand_count",
            "observed_days",
            "offer_count",
            "seasonal_count",
            "seasonal_promotion_count",
            "cadence_coverage_brand_count",
        ):
            if not isinstance(metrics.get(field), int):
                issues.append(Issue(name, "invalid_frozen_metric", detail=field))
        for field in (
            "offer_share",
            "seasonal_share",
            "seasonal_offer_share",
            "cadence_coverage_brand_share",
        ):
            if isinstance(metrics.get(field), bool) or not isinstance(
                metrics.get(field), (int, float)
            ):
                issues.append(Issue(name, "invalid_frozen_metric", detail=field))
        quadrants = metrics.get("quadrants")
        if not isinstance(quadrants, dict) or any(
            not isinstance(quadrants.get(label), Mapping)
            for label in (
                "Evergreen content",
                "Everyday promotion",
                "Seasonal promotion",
                "Seasonal content",
            )
        ):
            issues.append(Issue(name, "invalid_frozen_quadrants"))
    if not isinstance(value.get("qualified_broadcasts"), int):
        issues.append(Issue(name, "invalid_frozen_broadcast_count"))
    elif isinstance(metrics, dict) and value.get("qualified_broadcasts") != metrics.get(
        "qualified_broadcasts"
    ):
        issues.append(Issue(name, "frozen_broadcast_count_mismatch"))
    if final_contract and not _HEX_GIT_SHA_RE.fullmatch(
        str(value.get("git_sha") or "")
    ):
        issues.append(Issue(name, "invalid_frozen_git_sha"))
    if final_contract and value.get("git_dirty") is not False:
        issues.append(Issue(name, "frozen_worktree_not_clean"))
    return value, issues


def _validate_url_contracts(manifest: dict[str, Any]) -> tuple[set[str], list[Issue]]:
    issues: list[Issue] = []
    raw_urls = manifest.get("urls", [])
    if not isinstance(raw_urls, list):
        return set(), [Issue("manifest", "invalid_urls_contract")]
    for item in raw_urls:
        if not isinstance(item, dict):
            issues.append(Issue("manifest", "invalid_url_contract"))
            continue
        name = str(item.get("name") or "url")
        status = str(item.get("status") or "")
        value = item.get("url")
        if status not in URL_STATUSES:
            issues.append(Issue(name, "invalid_url_status", detail=status))
        if status in {"NEEDS-CONFIRMATION", "UNAVAILABLE"}:
            if isinstance(value, str) and value.strip():
                issues.append(Issue(name, "unconfirmed_url_value_present"))
            if not str(item.get("reason") or "").strip():
                issues.append(Issue(name, "missing_url_status_reason"))
            continue
        if status != "CONFIRMED":
            continue
        if not isinstance(value, str) or not value.strip():
            issues.append(Issue(name, "missing_confirmed_url"))
            continue
        if problem := _url_contract_problem(value):
            issues.append(Issue(name, problem))
        if item.get("verified") is not True or not str(item.get("verification") or "").strip():
            issues.append(Issue(name, "url_confirmed_without_verification"))
    return _confirmed_urls(manifest), issues


def _binding_expected(
    frozen_value: Any,
    *,
    freeze_field: str,
    format_name: str,
) -> str | None:
    if format_name == "number":
        tokens = _numeric_tokens(
            frozen_value,
            path=tuple(freeze_field.split(".")),
        )
        return next(iter(tokens)) if len(tokens) == 1 else None
    if not isinstance(frozen_value, str):
        return None
    if format_name == "iso_date":
        try:
            date.fromisoformat(frozen_value)
        except ValueError:
            return None
        return frozen_value
    if format_name == "display_date":
        try:
            parsed = date.fromisoformat(frozen_value)
        except ValueError:
            return None
        return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}"
    if format_name == "text":
        return frozen_value
    return None


def _binding_observed(value: str, format_name: str) -> str:
    if format_name == "number":
        return _number_token(value)
    return value.strip()


def _validate_claims(
    root: Path,
    manifest: dict[str, Any],
    *,
    screenshot_manifest: Mapping[str, Any] | None = None,
    require_freeze_binding: bool = False,
) -> tuple[list[Issue], dict[str, list[tuple[int, int]]], dict[str, set[str]]]:
    issues: list[Issue] = []
    artifacts = {str(item.get("name")): item for item in manifest.get("artifacts", []) if isinstance(item, dict)}
    claims = manifest.get("frozen_claims", [])
    if not isinstance(claims, list):
        return [Issue("manifest", "invalid_frozen_claims_contract")], {}, {}
    if require_freeze_binding and not claims:
        issues.append(Issue("manifest", "missing_frozen_claims"))
    coverage: dict[str, list[tuple[int, int]]] = {}
    target_claims: dict[str, set[str]] = {}
    seen_names: set[str] = set()
    for claim in claims:
        if not isinstance(claim, dict):
            issues.append(Issue("manifest", "invalid_frozen_claim_contract"))
            continue
        claim_name = str(claim.get("name") or "unnamed_claim")
        if claim_name in seen_names:
            issues.append(Issue(claim_name, "duplicate_frozen_claim_name"))
        seen_names.add(claim_name)
        pattern_value = claim.get("pattern")
        targets = claim.get("artifacts", [])
        if not isinstance(pattern_value, str) or not isinstance(targets, list):
            issues.append(Issue(claim_name, "invalid_frozen_claim_contract"))
            continue
        try:
            pattern = re.compile(pattern_value, re.IGNORECASE)
        except re.error:
            issues.append(Issue(claim_name, "invalid_frozen_claim_regex"))
            continue
        raw_bindings = claim.get("bindings")
        if not isinstance(raw_bindings, Mapping) or not raw_bindings:
            if require_freeze_binding:
                issues.append(Issue(claim_name, "missing_claim_bindings"))
                continue
            freeze_field = claim.get("freeze_field")
            expected = claim.get("expected")
            if expected is None:
                issues.append(Issue(claim_name, "invalid_frozen_claim_contract"))
                continue
            if not isinstance(freeze_field, str):
                bindings = {
                    "value": (_number_token(str(expected)), "number")
                }
                raw_bindings = {}
            else:
                bindings = {}
                raw_bindings = {
                    "value": {
                        "freeze_field": freeze_field,
                        "expected": expected,
                        "format": "number",
                    }
                }
        else:
            bindings = {}
        binding_paths: dict[str, str] = {}
        for group_name, raw_binding in raw_bindings.items():
            binding_name = str(group_name)
            if binding_name not in pattern.groupindex:
                issues.append(
                    Issue(claim_name, "claim_binding_group_missing", detail=binding_name)
                )
                continue
            if not isinstance(raw_binding, Mapping):
                issues.append(
                    Issue(claim_name, "invalid_claim_binding", detail=binding_name)
                )
                continue
            freeze_field = raw_binding.get("freeze_field")
            format_name = str(raw_binding.get("format") or "number")
            if not isinstance(freeze_field, str) or not freeze_field:
                issues.append(Issue(claim_name, "missing_freeze_field", detail=binding_name))
                continue
            if not freeze_field.startswith("metrics."):
                issues.append(
                    Issue(claim_name, "freeze_field_outside_metrics", detail=freeze_field)
                )
                continue
            binding_paths[binding_name] = freeze_field
            if "expected" not in raw_binding:
                issues.append(
                    Issue(claim_name, "missing_claim_binding_expected", detail=binding_name)
                )
                continue
            if screenshot_manifest is None:
                if require_freeze_binding:
                    issues.append(Issue(claim_name, "missing_screenshot_manifest_binding"))
                    continue
                declared = _binding_observed(str(raw_binding["expected"]), format_name)
                bindings[binding_name] = (declared, format_name)
                continue
            frozen_value = _nested_value(screenshot_manifest, freeze_field)
            if frozen_value is None:
                issues.append(Issue(claim_name, "unknown_freeze_field", detail=freeze_field))
                continue
            derived = _binding_expected(
                frozen_value,
                freeze_field=freeze_field,
                format_name=format_name,
            )
            if derived is None:
                issues.append(
                    Issue(claim_name, "invalid_claim_binding_format", detail=format_name)
                )
                continue
            declared = _binding_observed(str(raw_binding["expected"]), format_name)
            if declared.casefold() != derived.casefold():
                issues.append(
                    Issue(claim_name, "frozen_claim_expected_mismatch", detail=freeze_field)
                )
                continue
            bindings[binding_name] = (declared, format_name)

        if claim_name.startswith("finding_"):
            required_paths = FINDING_SEMANTIC_BINDINGS.get(claim_name)
            if required_paths is None:
                issues.append(Issue(claim_name, "unknown_finding_claim"))
            else:
                for group_name, required_path in required_paths.items():
                    if binding_paths.get(group_name) != required_path:
                        issues.append(
                            Issue(
                                claim_name,
                                "semantic_claim_binding_mismatch",
                                detail=f"{group_name}:{required_path}",
                            )
                        )

        for target in targets:
            target_name = str(target)
            target_claims.setdefault(target_name, set()).add(claim_name)
            artifact = artifacts.get(target_name)
            if artifact is None:
                issues.append(Issue(claim_name, "claim_target_missing", detail=str(target)))
                continue
            path_value = artifact.get("path")
            if not isinstance(path_value, str):
                continue
            path = (root / path_value).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                continue
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            matches = list(pattern.finditer(text))
            if claim.get("required", True) and not matches:
                issues.append(Issue(str(target), "required_frozen_claim_missing", detail=claim_name))
            for match in matches:
                for binding_name, (expected, format_name) in bindings.items():
                    observed_raw = match.group(binding_name)
                    if observed_raw is None:
                        issues.append(
                            Issue(target_name, "claim_binding_not_matched", detail=claim_name)
                        )
                        continue
                    observed = _binding_observed(observed_raw, format_name)
                    if observed.casefold() != expected.casefold():
                        issues.append(
                            Issue(
                                target_name,
                                "stale_frozen_claim",
                                _line_number(text, match.start(binding_name)),
                                f"{claim_name}.{binding_name}",
                            )
                        )
                        continue
                    coverage.setdefault(target_name, []).append(match.span(binding_name))

    if require_freeze_binding:
        for artifact_name, artifact in artifacts.items():
            kind = str(artifact.get("kind") or "")
            if kind not in STRICT_QUANTITATIVE_KINDS:
                continue
            declared = artifact.get("claim_bindings")
            if not isinstance(declared, list) or any(
                not isinstance(value, str) for value in declared
            ):
                issues.append(Issue(artifact_name, "missing_artifact_claim_bindings"))
                continue
            declared_set = set(declared)
            expected_set = target_claims.get(artifact_name, set())
            for missing in sorted(expected_set - declared_set):
                issues.append(
                    Issue(artifact_name, "artifact_claim_binding_missing", detail=missing)
                )
            for extra in sorted(declared_set - expected_set):
                issues.append(
                    Issue(artifact_name, "artifact_claim_binding_unknown", detail=extra)
                )
        linkedin = artifacts.get("linkedin_post")
        if linkedin is not None and isinstance(linkedin.get("path"), str):
            path = (root / str(linkedin["path"])).resolve()
            try:
                path.relative_to(root)
                linkedin_text = path.read_text(encoding="utf-8")
            except (ValueError, OSError, UnicodeDecodeError):
                linkedin_text = ""
            for match in re.finditer(
                r"(?m)^- (?P<label>[^:\n]+): [\d,]+ of [\d,]+ qualified broadcasts\b",
                linkedin_text,
            ):
                label = match.group("label").strip()
                expected_claim = FINDING_NAME_BY_LABEL.get(label)
                if expected_claim is None:
                    issues.append(
                        Issue("linkedin_post", "unrecognized_finding_context", detail=label)
                    )
                elif expected_claim not in target_claims.get("linkedin_post", set()):
                    issues.append(
                        Issue(
                            "linkedin_post",
                            "finding_claim_binding_missing",
                            detail=expected_claim,
                        )
                    )
    return issues, coverage, target_claims


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
    artifact_contracts = [item for item in artifacts if isinstance(item, dict)]
    artifact_by_name: dict[str, Mapping[str, Any]] = {}
    for item in artifact_contracts:
        artifact_name = str(item.get("name") or "")
        if not artifact_name:
            issues.append(Issue("manifest", "artifact_name_missing"))
            continue
        if artifact_name in artifact_by_name:
            issues.append(Issue(artifact_name, "duplicate_artifact_name"))
        artifact_by_name[artifact_name] = item
    kinds = {str(item.get("kind")) for item in artifact_contracts}
    if any(
        _is_kit_requirement(item.get("kind")) or _is_kit_requirement(item.get("name"))
        for item in artifact_contracts
    ):
        issues.append(Issue("manifest", "kit_must_not_be_required"))
    required_components = manifest.get("required_components", [])
    if not isinstance(required_components, list):
        issues.append(Issue("manifest", "invalid_required_components"))
        required_components = []
    if any(_is_kit_requirement(item) for item in required_components):
        issues.append(Issue("manifest", "kit_must_not_be_required"))
    if not allow_partial:
        for missing in sorted(DEFAULT_REQUIRED_KINDS - kinds):
            issues.append(Issue("manifest", "missing_required_artifact_kind", detail=missing))

    confirmed_urls, url_issues = _validate_url_contracts(manifest)
    issues.extend(url_issues)
    if not allow_partial and not confirmed_urls:
        issues.append(Issue("manifest", "missing_confirmed_url"))
    if not allow_partial:
        raw_urls = manifest.get("urls", [])
        public_notion_urls = [
            item
            for item in raw_urls
            if isinstance(item, dict) and item.get("role") == "public_notion"
        ]
        if not public_notion_urls:
            issues.append(Issue("manifest", "missing_public_notion_url"))
        elif not any(
            item.get("status") == "CONFIRMED"
            and item.get("verified") is True
            and item.get("verification") == "logged_out"
            and isinstance(item.get("url"), str)
            and _url_contract_problem(str(item.get("url"))) is None
            for item in public_notion_urls
        ):
            issues.append(Issue("public_notion", "public_notion_not_logged_out_verified"))

    screenshot_manifest, screenshot_issues = _load_screenshot_manifest(
        root,
        artifact_contracts,
        final_contract=not allow_partial,
    )
    issues.extend(screenshot_issues)

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

    images = manifest.get("images", [])
    if not isinstance(images, list):
        issues.append(Issue("manifest", "invalid_images_contract"))
        images = []
    if not allow_partial and not images:
        issues.append(Issue("manifest", "missing_required_image"))
    frozen_hashes = {
        str(item.get("sha256"))
        for collection in ("screenshots", "section_captures")
        for item in (screenshot_manifest or {}).get(collection, [])
        if isinstance(item, dict) and _valid_sha256(item.get("sha256"))
    }
    package_hashes: set[str] = set()
    for image in images:
        if not isinstance(image, dict):
            issues.append(Issue("manifest", "invalid_image_contract"))
            continue
        name = str(image.get("name") or "image")
        raw_path = image.get("path")
        expected_hash = image.get("sha256")
        if not isinstance(raw_path, str) or not _valid_sha256(expected_hash):
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
        package_hashes.add(actual)
        if actual != expected_hash:
            issues.append(Issue(name, "stale_image_hash"))
        if screenshot_manifest is not None and actual not in frozen_hashes:
            issues.append(Issue(name, "image_hash_not_frozen"))

    if screenshot_manifest is not None:
        for missing_hash in sorted(frozen_hashes - package_hashes):
            issues.append(
                Issue(
                    "screenshot_manifest",
                    "frozen_screenshot_missing_from_package",
                    detail=missing_hash[:16],
                )
            )

    claim_issues, claim_coverage, _target_claims = _validate_claims(
        root,
        manifest,
        screenshot_manifest=screenshot_manifest,
        require_freeze_binding=not allow_partial,
    )
    issues.extend(claim_issues)
    static_tokens, static_coverage, static_issues = _operational_number_contract(
        manifest,
        root=root,
        artifacts=artifact_by_name,
        final_contract=not allow_partial,
    )
    issues.extend(static_issues)

    if not allow_partial:
        frozen_metrics = (
            screenshot_manifest.get("metrics", {})
            if isinstance(screenshot_manifest, Mapping)
            else {}
        )
        frozen_numbers = _numeric_tokens(frozen_metrics)
        for artifact in artifact_contracts:
            if artifact.get("kind") not in TEXT_KINDS:
                continue
            artifact_name = str(artifact.get("name") or artifact.get("kind") or "artifact")
            allowed_numbers = artifact.get("allowed_numbers")
            if not isinstance(allowed_numbers, list):
                continue
            for value in allowed_numbers:
                token = _number_token(str(value))
                if token not in frozen_numbers and token not in static_tokens.get(
                    artifact_name, set()
                ):
                    issues.append(
                        Issue(
                            artifact_name,
                            "allowed_number_not_frozen_or_static",
                            detail=token,
                        )
                    )

            if str(artifact.get("kind") or "") not in STRICT_QUANTITATIVE_KINDS:
                continue
            raw_path = artifact.get("path")
            if not isinstance(raw_path, str):
                continue
            path = (root / raw_path).resolve()
            try:
                path.relative_to(root)
                text = path.read_text(encoding="utf-8")
            except (ValueError, OSError, UnicodeDecodeError):
                continue
            covered = claim_coverage.get(artifact_name, []) + static_coverage.get(
                artifact_name, []
            )
            for occurrence in _numeric_occurrences(text):
                if any(
                    left <= occurrence.start and occurrence.end <= right
                    for left, right in covered
                ):
                    continue
                issues.append(
                    Issue(
                        artifact_name,
                        "unbound_quantitative_claim",
                        occurrence.line,
                        occurrence.token,
                    )
                )
    return {
        "ok": not issues,
        "keyword": keyword,
        "artifact_count": len(artifacts),
        "confirmed_url_count": len(confirmed_urls),
        "image_count": len(images),
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
