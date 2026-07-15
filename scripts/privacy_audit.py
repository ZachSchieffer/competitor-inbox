#!/usr/bin/env python3
"""Redacted privacy audit for the public Competitor Inbox repository.

The audit scans the live worktree, Git-untracked files, the staged index, and
every blob in the Git object database, including unreachable objects. Findings
never include matched values. Example filenames receive no trust exemption;
only addresses in IANA-reserved test domains are treated as fixtures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from typing import Iterable, Sequence


EMAIL_RE = re.compile(
    rb"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63}\b", re.IGNORECASE
)
HOME_PATH_RE = re.compile(rb"/(?:Users|home)/[A-Za-z0-9._-]+/")
SHORT_TRACKING_FRAGMENT_RE = re.compile(
    rb"(?i)(?:"
    rb"(?<![A-Z0-9_])(?:r|k|m)\s*"
    rb"(?:=3d|%(?:25)*3d|&#(?:0*61|x0*3d);)\s*[^\s&#<>'\"]+"
    rb"|(?:\?|&(?:amp;)?|;|%(?:25)*26)\s*(?:r|k|m)\s*=\s*"
    rb"[^\s&#<>'\"]{4,}"
    rb"|(?<![A-Z0-9_])(?:r|k|m)\s*=\s*[^\s&#<>'\"]+"
    rb"(?:\s*(?:&(?:amp;)?|;|%(?:25)*26)\s*(?:r|k|m)\s*=\s*"
    rb"[^\s&#<>'\"]+)+"
    rb")"
)
STANDALONE_SHORT_TRACKING_RE = re.compile(
    rb"(?<![A-Z0-9_?&;])(?:r|k|m)\s*=\s*"
    rb"(?P<value>[A-Z0-9._~-]{12,})(?![A-Z0-9_.-])",
    re.IGNORECASE,
)

CONTENT_RULES: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("email_address", EMAIL_RE),
    ("absolute_home_path", HOME_PATH_RE),
    ("private_key", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("openai_key", re.compile(rb"\bsk-(?:proj|svcacct)-[A-Za-z0-9_-]{16,}\b")),
    ("anthropic_key", re.compile(rb"\bsk-ant-[A-Za-z0-9_-]{16,}\b")),
    ("github_token", re.compile(rb"\bgh[opusr]_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{16,}\b")),
    ("aws_access_key", re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google_api_key", re.compile(rb"\bAIza[A-Za-z0-9_-]{30,}\b")),
    (
        "password_assignment",
        re.compile(
            rb"(?i)\b(?:app[_-]?password|password|client[_-]?secret)\s*[:=]\s*"
            rb"['\"][^'\"\r\n]{8,}['\"]"
        ),
    ),
    (
        "authorization_header",
        re.compile(rb"(?i)\bauthorization\s*:\s*(?:bearer|basic)\s+[A-Za-z0-9._~+/-]{12,}"),
    ),
    (
        "cookie_header",
        re.compile(rb"(?i)(?:^|\n)\s*(?:cookie|set-cookie)\s*:\s*[^\r\n]{12,}"),
    ),
    ("recipient_tracking_fragment", SHORT_TRACKING_FRAGMENT_RE),
    (
        "git_lfs_pointer",
        re.compile(rb"(?im)^version https://git-lfs\.github\.com/spec/v1\s*$"),
    ),
)

SENSITIVE_EXTENSIONS = {
    ".eml": "raw_email_file",
    ".mbox": "mailbox_export",
    ".mbx": "mailbox_export",
    ".pst": "mailbox_export",
    ".ost": "mailbox_export",
    ".sqlite": "database_file",
    ".sqlite3": "database_file",
    ".db": "database_file",
}

REVIEWABLE_ASSET_EXTENSIONS = {
    ".gif",
    ".heic",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}

@dataclass(frozen=True)
class Finding:
    scope: str
    path: str
    rule_id: str
    match_hash: str
    blob_oid: str | None = None


@dataclass(frozen=True)
class Asset:
    scope: str
    path: str
    sha256: str
    size: int
    synthetic_example: bool
    blob_oid: str | None = None


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()[:16]


def _safe_path(path: str) -> str:
    encoded = path.encode("utf-8", "surrogateescape")
    if EMAIL_RE.search(encoded) or HOME_PATH_RE.search(encoded):
        return f"<redacted-path:{_digest(encoded)}>"
    return path


def _is_synthetic_example(path: str) -> bool:
    return PurePosixPath(path).name.endswith(".example")


def _is_reserved_test_email(value: bytes) -> bool:
    _, _, domain_bytes = value.lower().rpartition(b"@")
    domain = domain_bytes.decode("ascii", "ignore")
    return (
        domain == "localhost"
        or domain in {"example.com", "example.org", "example.net"}
        or domain.endswith((".example", ".invalid", ".test"))
    )


def _finding_synthetic_category(path: str, finding: Finding) -> str | None:
    if finding.rule_id == "synthetic_email_address":
        return "reserved_fixture"
    return None


def _git(repo: Path, args: Sequence[str], *, check: bool = True) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"git command failed ({' '.join(args)}): {message}")
    return result.stdout


def _git_object_inventory(repo: Path) -> tuple[set[str], set[str]]:
    """Return reachable and complete local object IDs without batch-all-objects.

    Some macOS Git builds stall while sorting loose objects for
    ``--batch-all-objects``. Reachable IDs come from rev-list, while loose and
    packed inventories are read independently so unreachable objects remain in
    scope.
    """

    reachable = {
        line.decode("ascii", "replace")
        for line in _git(
            repo, ["rev-list", "--objects", "--no-object-names", "--all"]
        ).splitlines()
        if line
    }
    inventory = set(reachable)
    git_dir_value = _git(repo, ["rev-parse", "--git-dir"]).decode().strip()
    git_dir = Path(git_dir_value)
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    objects = git_dir.resolve() / "objects"
    for prefix in objects.iterdir():
        if not prefix.is_dir() or not re.fullmatch(r"[0-9a-f]{2}", prefix.name):
            continue
        for value in prefix.iterdir():
            if value.is_file() and re.fullmatch(r"[0-9a-f]{38}", value.name):
                inventory.add(prefix.name + value.name)
    for index in sorted((objects / "pack").glob("*.idx")):
        for line in _git(repo, ["verify-pack", "-v", str(index)]).splitlines():
            oid = line.partition(b" ")[0].decode("ascii", "replace")
            if re.fullmatch(r"[0-9a-f]{40}", oid):
                inventory.add(oid)
    return reachable, inventory


def _git_batch_objects(
    repo: Path, object_ids: Iterable[str]
) -> Iterable[tuple[str, str, bytes]]:
    requested = sorted(set(object_ids))
    if not requested:
        return
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output:
        source.write(("\n".join(requested) + "\n").encode("ascii"))
        source.seek(0)
        result = subprocess.run(
            ["git", "cat-file", "--batch"],
            cwd=repo,
            stdin=source,
            stdout=output,
            stderr=subprocess.PIPE,
            check=False,
        )
        output.seek(0)
        payload = output.read()
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"git cat-file batch failed: {message}")
    offset = 0
    while offset < len(payload):
        line_end = payload.find(b"\n", offset)
        if line_end < 0:
            raise RuntimeError("git cat-file batch returned a truncated header")
        header = payload[offset:line_end].split()
        offset = line_end + 1
        if len(header) != 3:
            raise RuntimeError("git cat-file batch returned an invalid header")
        oid = header[0].decode("ascii", "replace")
        object_type = header[1].decode("ascii", "replace")
        size = int(header[2])
        data = payload[offset : offset + size]
        offset += size
        if payload[offset : offset + 1] != b"\n":
            raise RuntimeError("git cat-file batch returned a truncated payload")
        offset += 1
        yield oid, object_type, data


def _load_deny_rules(path: Path | None) -> tuple[tuple[str, re.Pattern[bytes]], ...]:
    if path is None:
        return ()
    rules: list[tuple[str, re.Pattern[bytes]]] = []
    for index, raw_line in enumerate(path.read_bytes().splitlines(), start=1):
        value = raw_line.strip()
        if not value or value.startswith(b"#"):
            continue
        rules.append((f"private_deny_term_{index}", re.compile(re.escape(value), re.IGNORECASE)))
    return tuple(rules)


def _looks_like_raw_mail(data: bytes) -> bool:
    lowered = data.lower()
    required = (b"mime-version:", b"content-type:")
    has_address_header = any(
        header in lowered
        for header in (b"\nfrom:", b"\nto:", b"\ndelivered-to:", b"\nreturn-path:")
    )
    return all(value in lowered for value in required) and has_address_header


def _opaque_short_tracking_value(value: bytes) -> bool:
    return (
        len(value) >= 20
        or any(48 <= character <= 57 or 65 <= character <= 90 for character in value)
        or sum(value.count(separator) for separator in (b"-", b"_", b".", b"~")) >= 2
    )


def _scan_payload(
    *,
    data: bytes,
    scope: str,
    path: str,
    blob_oid: str | None,
    deny_rules: Iterable[tuple[str, re.Pattern[bytes]]],
) -> tuple[list[Finding], list[Asset]]:
    findings: list[Finding] = []
    assets: list[Asset] = []
    safe_path = _safe_path(path)
    suffix = PurePosixPath(path).suffix.lower()

    encoded_path = path.encode("utf-8", "surrogateescape")
    for rule_id, pattern in (
        ("email_address_in_path", EMAIL_RE),
        ("absolute_home_path_in_path", HOME_PATH_RE),
    ):
        for match in pattern.finditer(encoded_path):
            effective_rule = rule_id
            if rule_id == "email_address_in_path" and _is_reserved_test_email(
                match.group(0)
            ):
                effective_rule = "synthetic_email_address"
            findings.append(
                Finding(
                    scope=scope,
                    path=safe_path,
                    rule_id=effective_rule,
                    match_hash=_digest(match.group(0)),
                    blob_oid=blob_oid,
                )
            )

    if PurePosixPath(path).name == ".gitmodules":
        findings.append(
            Finding(
                scope=scope,
                path=safe_path,
                rule_id="submodule_manifest",
                match_hash=_digest(data),
                blob_oid=blob_oid,
            )
        )

    if suffix in SENSITIVE_EXTENSIONS:
        findings.append(
            Finding(
                scope=scope,
                path=safe_path,
                rule_id=SENSITIVE_EXTENSIONS[suffix],
                match_hash=_digest(data),
                blob_oid=blob_oid,
            )
        )

    if suffix in REVIEWABLE_ASSET_EXTENSIONS:
        assets.append(
            Asset(
                scope=scope,
                path=safe_path,
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
                synthetic_example=_is_synthetic_example(path),
                blob_oid=blob_oid,
            )
        )

    for rule_id, pattern in (*CONTENT_RULES, *deny_rules):
        for match in pattern.finditer(data):
            effective_rule = rule_id
            if rule_id == "email_address" and _is_reserved_test_email(match.group(0)):
                effective_rule = "synthetic_email_address"
            findings.append(
                Finding(
                    scope=scope,
                    path=safe_path,
                    rule_id=effective_rule,
                    match_hash=_digest(match.group(0)),
                    blob_oid=blob_oid,
                )
            )

    for match in STANDALONE_SHORT_TRACKING_RE.finditer(data):
        if not _opaque_short_tracking_value(match.group("value")):
            continue
        findings.append(
            Finding(
                scope=scope,
                path=safe_path,
                rule_id="recipient_tracking_fragment",
                match_hash=_digest(match.group(0)),
                blob_oid=blob_oid,
            )
        )

    if _looks_like_raw_mail(data):
        findings.append(
            Finding(
                scope=scope,
                path=safe_path,
                rule_id="raw_mail_payload",
                match_hash=_digest(data),
                blob_oid=blob_oid,
            )
        )

    return findings, assets


def _walk_worktree(repo: Path) -> Iterable[tuple[str, Path]]:
    for root, directories, filenames in os.walk(repo, followlinks=False):
        descend: list[str] = []
        for name in sorted(directories):
            if name == ".git":
                continue
            absolute = Path(root) / name
            if absolute.is_symlink():
                yield absolute.relative_to(repo).as_posix(), absolute
            else:
                descend.append(name)
        directories[:] = descend
        for name in sorted(filenames):
            absolute = Path(root) / name
            relative = absolute.relative_to(repo).as_posix()
            yield relative, absolute


def _nul_paths(payload: bytes) -> list[str]:
    return [
        item.decode("utf-8", "surrogateescape")
        for item in payload.split(b"\0")
        if item
    ]


def _all_git_blobs(repo: Path) -> Iterable[tuple[str, str, str, bytes]]:
    """Yield every reachable and unreachable blob with a redacted-safe label."""

    reachable_paths: dict[str, str] = {}
    commits = _git(repo, ["rev-list", "--all"]).splitlines()
    for commit in commits:
        tree = _git(repo, ["ls-tree", "-r", "-z", commit.decode("ascii")])
        for entry in tree.split(b"\0"):
            if not entry or b"\t" not in entry:
                continue
            metadata, path_bytes = entry.split(b"\t", 1)
            fields = metadata.split()
            if len(fields) != 3 or fields[1] != b"blob":
                continue
            oid = fields[2].decode("ascii", "replace")
            path = path_bytes.decode("utf-8", "surrogateescape")
            reachable_paths.setdefault(oid, path)

    reachable, inventory = _git_object_inventory(repo)
    for oid, object_type, data in _git_batch_objects(repo, inventory):
        if object_type != "blob":
            continue
        reachable = oid in reachable_paths
        scope = "history" if reachable else "unreachable"
        path = reachable_paths.get(oid, f"<unreachable-blob:{oid[:12]}>")
        yield scope, oid, path, data


def _index_gitlinks(repo: Path) -> Iterable[tuple[str, str]]:
    payload = _git(repo, ["ls-files", "--stage", "-z"], check=False)
    for record in payload.split(b"\0"):
        if not record or b"\t" not in record:
            continue
        metadata, path_bytes = record.split(b"\t", 1)
        fields = metadata.split()
        if len(fields) != 3 or fields[0] != b"160000":
            continue
        yield (
            fields[1].decode("ascii", "replace"),
            path_bytes.decode("utf-8", "surrogateescape"),
        )


def _all_gitlinks(repo: Path) -> Iterable[tuple[str, str, str, str]]:
    """Yield gitlinks from every reachable and unreachable tree object."""

    reachable, inventory = _git_object_inventory(repo)
    for tree_oid, object_type, _data in _git_batch_objects(repo, inventory):
        if object_type != "tree":
            continue
        scope = "history" if tree_oid in reachable else "unreachable"
        for record in _git(repo, ["ls-tree", "-z", tree_oid]).split(b"\0"):
            if not record or b"\t" not in record:
                continue
            metadata, path_bytes = record.split(b"\t", 1)
            fields = metadata.split()
            if len(fields) != 3 or fields[0] != b"160000":
                continue
            path = path_bytes.decode("utf-8", "surrogateescape")
            target_oid = fields[2].decode("ascii", "replace")
            yield scope, tree_oid, path, target_oid


def scan_repository(repo: Path, deny_pattern_file: Path | None = None) -> dict[str, object]:
    repo = repo.resolve()
    if _git(repo, ["rev-parse", "--is-inside-work-tree"], check=False).strip() != b"true":
        raise RuntimeError(f"not a Git worktree: {repo}")

    deny_rules = _load_deny_rules(deny_pattern_file)
    all_findings: list[tuple[Finding, str | None]] = []
    assets: list[Asset] = []
    scanned_counts = {
        "worktree": 0,
        "untracked": 0,
        "staged": 0,
        "history": 0,
        "unreachable": 0,
        "gitlinks": 0,
    }

    for relative, absolute in _walk_worktree(repo):
        scanned_counts["worktree"] += 1
        if absolute.is_symlink():
            finding = Finding(
                scope="worktree",
                path=_safe_path(relative),
                rule_id="symlink_not_allowed",
                match_hash=_digest(os.readlink(absolute).encode("utf-8", "surrogateescape")),
            )
            all_findings.append((finding, None))
            continue
        try:
            data = absolute.read_bytes()
        except OSError as error:
            finding = Finding(
                scope="worktree",
                path=_safe_path(relative),
                rule_id="unreadable_file",
                match_hash=_digest(type(error).__name__.encode()),
            )
            all_findings.append((finding, None))
            continue
        found, found_assets = _scan_payload(
            data=data,
            scope="worktree",
            path=relative,
            blob_oid=None,
            deny_rules=deny_rules,
        )
        all_findings.extend(
            (item, _finding_synthetic_category(relative, item)) for item in found
        )
        assets.extend(found_assets)

    untracked = _nul_paths(
        _git(repo, ["ls-files", "--others", "--exclude-standard", "-z"], check=False)
    )
    for relative in untracked:
        absolute = repo / relative
        if not absolute.is_file() or absolute.is_symlink():
            continue
        scanned_counts["untracked"] += 1
        data = absolute.read_bytes()
        found, found_assets = _scan_payload(
            data=data,
            scope="untracked",
            path=relative,
            blob_oid=None,
            deny_rules=deny_rules,
        )
        all_findings.extend(
            (item, _finding_synthetic_category(relative, item)) for item in found
        )
        assets.extend(found_assets)

    staged = _nul_paths(
        _git(
            repo,
            ["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
            check=False,
        )
    )
    for relative in staged:
        data = _git(repo, ["show", f":{relative}"], check=False)
        scanned_counts["staged"] += 1
        found, found_assets = _scan_payload(
            data=data,
            scope="staged",
            path=relative,
            blob_oid=None,
            deny_rules=deny_rules,
        )
        all_findings.extend(
            (item, _finding_synthetic_category(relative, item)) for item in found
        )
        assets.extend(found_assets)

    for target_oid, relative in _index_gitlinks(repo):
        scanned_counts["gitlinks"] += 1
        finding = Finding(
            scope="index",
            path=_safe_path(relative),
            rule_id="submodule_gitlink",
            match_hash=_digest(f"{relative}\0{target_oid}".encode("utf-8", "surrogateescape")),
            blob_oid=None,
        )
        all_findings.append((finding, None))

    for scope, oid, relative, data in _all_git_blobs(repo):
        scanned_counts[scope] += 1
        found, found_assets = _scan_payload(
            data=data,
            scope=scope,
            path=relative,
            blob_oid=oid,
            deny_rules=deny_rules,
        )
        all_findings.extend(
            (item, _finding_synthetic_category(relative, item)) for item in found
        )
        assets.extend(found_assets)

    for scope, tree_oid, relative, target_oid in _all_gitlinks(repo):
        scanned_counts["gitlinks"] += 1
        finding = Finding(
            scope=scope,
            path=_safe_path(f"<tree:{tree_oid[:12]}>/{relative}"),
            rule_id="submodule_gitlink",
            match_hash=_digest(f"{relative}\0{target_oid}".encode("utf-8", "surrogateescape")),
            blob_oid=tree_oid,
        )
        all_findings.append((finding, None))

    violations = [asdict(item) for item, category in all_findings if category is None]
    synthetic_example_findings = [
        asdict(item) for item, category in all_findings if category == "example_template"
    ]
    reserved_fixture_findings = [
        asdict(item) for item, category in all_findings if category == "reserved_fixture"
    ]
    synthetic_findings = [*synthetic_example_findings, *reserved_fixture_findings]
    asset_rows = [asdict(item) for item in assets]
    return {
        "ok": not violations,
        "repository": _safe_path(str(repo)),
        "scanned": scanned_counts,
        "violation_count": len(violations),
        "synthetic_finding_count": len(synthetic_findings),
        "synthetic_example_finding_count": len(synthetic_example_findings),
        "reserved_fixture_finding_count": len(reserved_fixture_findings),
        "asset_count": len(asset_rows),
        "violations": violations,
        "synthetic_findings": synthetic_findings,
        "synthetic_example_findings": synthetic_example_findings,
        "reserved_fixture_findings": reserved_fixture_findings,
        "assets_for_manual_review": asset_rows,
        "generated_directories_scanned": True,
    }


def render_human(report: dict[str, object]) -> str:
    status = "PASS" if report["ok"] else "FAIL"
    lines = [f"Privacy audit: {status}"]
    scanned = report["scanned"]
    assert isinstance(scanned, dict)
    lines.append(
        "Scanned: "
        + ", ".join(f"{scope}={count}" for scope, count in scanned.items())
    )
    lines.append(f"Violations: {report['violation_count']}")
    for row in report["violations"]:
        assert isinstance(row, dict)
        blob = f" blob={str(row['blob_oid'])[:12]}" if row.get("blob_oid") else ""
        lines.append(
            f"- scope={row['scope']} path={row['path']} rule={row['rule_id']} "
            f"match={row['match_hash']}{blob}"
        )
    lines.append(
        f"Synthetic .example findings: {report['synthetic_example_finding_count']}"
    )
    for row in report["synthetic_example_findings"]:
        assert isinstance(row, dict)
        lines.append(
            f"- scope={row['scope']} path={row['path']} rule={row['rule_id']} "
            f"match={row['match_hash']}"
        )
    lines.append(
        f"Reserved-domain fixture findings: {report['reserved_fixture_finding_count']}"
    )
    for row in report["reserved_fixture_findings"]:
        assert isinstance(row, dict)
        lines.append(
            f"- scope={row['scope']} path={row['path']} rule={row['rule_id']} "
            f"match={row['match_hash']}"
        )
    lines.append(f"Assets requiring manual review: {report['asset_count']}")
    for row in report["assets_for_manual_review"]:
        assert isinstance(row, dict)
        lines.append(
            f"- scope={row['scope']} path={row['path']} sha256={row['sha256']} "
            f"size={row['size']} synthetic_example={row['synthetic_example']}"
        )
    lines.append("Generated directories and caches scanned: yes")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--deny-pattern-file",
        type=Path,
        help="Private newline-delimited deny terms. Values are never printed.",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = scan_repository(args.repo, args.deny_pattern_file)
    except (OSError, RuntimeError) as error:
        print(f"Privacy audit could not run: {error}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_human(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
