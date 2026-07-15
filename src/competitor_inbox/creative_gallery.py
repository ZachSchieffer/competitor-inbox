"""Private, build-time creative previews for the static messaging library.

Production manifests and image files remain below the private data root. Only
validated thumbnail bytes enter the renderer, as local data URIs. Absolute
paths, record IDs, message bodies, and source metadata never enter the view
model.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import struct
import zlib
from datetime import date as calendar_date
from pathlib import Path
from typing import Any, Mapping, Sequence

from .store import UnsafeDataRootError, read_bytes_no_follow


GALLERY_TARGET_MIN = 3
GALLERY_TARGET_MAX = 5
DEFAULT_MANIFEST_RELATIVE_PATH = Path(
    "creatives/manifests/full-archive-v7-manifest.json"
)
AUTHORITATIVE_PIPELINE_VERSION = "2026-07-15.8"
AUTHORITATIVE_MANIFEST_SHA256 = (
    "8d6b2ef31c7510ad7b1ae43a3062b5df55179ec14da1f6970b6828a6537871fe"
)
_MAX_THUMBNAIL_BYTES = 1_500_000
_MAX_GALLERY_BYTES = 32_000_000
_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_CATEGORY_LABELS = {
    "evergreen": "Evergreen content",
    "evergreen content": "Evergreen content",
    "everyday_promotion": "Everyday promotion",
    "everyday promotion": "Everyday promotion",
    "seasonal_content": "Seasonal content",
    "seasonal content": "Seasonal content",
    "seasonal_promotion": "Seasonal promotion",
    "seasonal promotion": "Seasonal promotion",
    "synthetic editorial preview": "Synthetic editorial preview",
    "synthetic offer preview": "Synthetic offer preview",
    "synthetic seasonal preview": "Synthetic seasonal preview",
}
_CATEGORY_FALLBACK = "Safe creative preview"
_SHA256_HEX_LENGTH = 64


def _brand_names(summary: Mapping[str, Any]) -> list[str]:
    names = {
        str(row.get("brand") or "").strip()
        for row in summary.get("brands", [])
        if isinstance(row, Mapping) and str(row.get("brand") or "").strip()
    }
    return sorted(names, key=str.casefold)


def _unavailable_gallery(
    brands: Sequence[str],
    *,
    reason: str,
    metadata_status: str,
) -> dict[str, Any]:
    return {
        "metadata_status": metadata_status,
        "target_min": GALLERY_TARGET_MIN,
        "target_max": GALLERY_TARGET_MAX,
        "loaded_safe_creatives": 0,
        "ready_brand_count": 0,
        "insufficient_brand_count": 0,
        "unavailable_brand_count": len(brands),
        "brands": [
            {
                "brand": brand,
                "status": "unavailable",
                "safe_count": 0,
                "items": [],
                "reason": reason,
            }
            for brand in brands
        ],
    }


def unavailable_creative_gallery(
    summary: Mapping[str, Any],
    reason: str = "Safe creative metadata was not supplied for this build.",
) -> dict[str, Any]:
    """Return explicit unavailable states for every brand in the census."""

    return _unavailable_gallery(
        _brand_names(summary),
        reason=reason,
        metadata_status="unavailable",
    )


def _contained_file(root: Path, relative_value: Any) -> Path | None:
    raw = str(relative_value or "").strip()
    relative = Path(raw)
    if (
        not raw
        or relative.is_absolute()
        or ".." in relative.parts
        or relative.suffix.casefold() not in _MIME_BY_SUFFIX
    ):
        return None
    candidate = root.joinpath(relative)
    try:
        resolved_root = root.resolve(strict=True)
        resolved_parent = candidate.parent.resolve(strict=True)
    except OSError:
        return None
    if resolved_parent != resolved_root and resolved_root not in resolved_parent.parents:
        return None
    target = resolved_parent / candidate.name
    if target.is_symlink() or not target.is_file():
        return None
    return target


def _image_mime(path: Path, payload: bytes) -> str | None:
    mime = _MIME_BY_SUFFIX.get(path.suffix.casefold())
    if mime == "image/png" and payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return mime
    if mime == "image/jpeg" and payload.startswith(b"\xff\xd8\xff"):
        return mime
    if (
        mime == "image/webp"
        and len(payload) >= 12
        and payload[:4] == b"RIFF"
        and payload[8:12] == b"WEBP"
    ):
        return mime
    return None


def _sha256_regular_file(path: Path) -> str | None:
    """Hash one regular file without following a final-component symlink."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _valid_sha256(value: Any) -> bool:
    raw = str(value or "")
    return len(raw) == _SHA256_HEX_LENGTH and all(
        character in "0123456789abcdef" for character in raw
    )


def _manifest_provenance_is_valid(
    manifest: Mapping[str, Any],
    *,
    manifest_sha256: str,
    current_master_sha256: str | None,
    brands: Sequence[str],
    require_authoritative_manifest_hash: bool,
) -> bool:
    """Bind gallery inputs to an audited full-archive render.

    The compiled-in default is an immutable, hash-bound archive snapshot. It
    remains valid when the live inbox advances after that render. Explicit
    manifests are not trusted by hash, so they must still match the current
    private master exactly.
    """

    if (
        require_authoritative_manifest_hash
        and manifest_sha256 != AUTHORITATIVE_MANIFEST_SHA256
    ):
        return False
    manifest_master_sha256 = str(manifest.get("master_sha256") or "")
    if (
        manifest.get("pipeline_version") != AUTHORITATIVE_PIPELINE_VERSION
        or manifest.get("mode") != "full_archive"
        or manifest.get("state") != "complete"
        or manifest.get("outcome") not in {"complete", "complete_with_exclusions"}
        or not _valid_sha256(manifest_master_sha256)
        or (
            not require_authoritative_manifest_hash
            and current_master_sha256 != manifest_master_sha256
        )
    ):
        return False

    count_names = (
        "qualified_broadcasts",
        "requested",
        "processed",
        "resolved",
        "rendered",
        "skipped",
        "failed",
        "pending_total",
        "pending_unprocessed",
        "retryable_pending",
    )
    counts = {name: _nonnegative_int(manifest.get(name)) for name in count_names}
    if any(value is None for value in counts.values()):
        return False
    if not (
        counts["qualified_broadcasts"]
        == counts["requested"]
        == counts["processed"]
        == counts["resolved"]
    ):
        return False
    if counts["resolved"] != (
        counts["rendered"] + counts["skipped"] + counts["failed"]
    ):
        return False
    if any(
        counts[name] != 0
        for name in ("pending_total", "pending_unprocessed", "retryable_pending")
    ):
        return False

    coverage = manifest.get("coverage")
    items = manifest.get("items")
    if not isinstance(coverage, Mapping) or not isinstance(items, list):
        return False
    coverage_brands = {str(name) for name in coverage}
    census_brands = set(brands)
    if not coverage_brands or not coverage_brands.issubset(census_brands):
        return False
    coverage_totals = {
        name: 0 for name in ("requested", "rendered", "skipped", "failed")
    }
    for brand in sorted(coverage_brands, key=str.casefold):
        row = coverage.get(brand)
        if not isinstance(row, Mapping):
            return False
        row_counts = {
            name: _nonnegative_int(row.get(name))
            for name in (
                "requested",
                "processed",
                "resolved",
                "rendered",
                "skipped",
                "failed",
                "pending_total",
                "pending_unprocessed",
                "retryable_pending",
            )
        }
        if any(value is None for value in row_counts.values()):
            return False
        if not (
            row_counts["requested"]
            == row_counts["processed"]
            == row_counts["resolved"]
        ):
            return False
        if row_counts["resolved"] != (
            row_counts["rendered"] + row_counts["skipped"] + row_counts["failed"]
        ):
            return False
        if any(
            row_counts[name] != 0
            for name in ("pending_total", "pending_unprocessed", "retryable_pending")
        ):
            return False
        for name in coverage_totals:
            coverage_totals[name] += row_counts[name]
    if any(coverage_totals[name] != counts[name] for name in coverage_totals):
        return False
    if len(items) != counts["rendered"]:
        return False

    privacy_controls = manifest.get("privacy_controls")
    if not isinstance(privacy_controls, Mapping):
        return False
    required_controls = {
        "asset_cache_private": True,
        "cookies": False,
        "javascript": False,
        "recipient_terms_removed": True,
        "remote_runtime_requests": False,
        "sanitized_html_transient": True,
    }
    if any(
        privacy_controls.get(key) is not value
        for key, value in required_controls.items()
    ):
        return False

    rendered_by_brand = {brand: 0 for brand in coverage_brands}
    for item in items:
        if not isinstance(item, Mapping):
            return False
        brand = str(item.get("brand") or "")
        ocr_gate = item.get("ocr_privacy_gate")
        if (
            brand not in rendered_by_brand
            or item.get("status") != "success"
            or item.get("scope") != "broadcast"
            or item.get("pipeline_version") != AUTHORITATIVE_PIPELINE_VERSION
            or item.get("source_master_sha256") != manifest_master_sha256
            or not _valid_sha256(item.get("thumbnail_sha256"))
            or not isinstance(ocr_gate, Mapping)
            or ocr_gate.get("passed") is not True
            or ocr_gate.get("reason") != "clean"
        ):
            return False
        rendered_by_brand[brand] += 1
    return all(
        rendered_by_brand[brand] == int(coverage[brand]["rendered"])
        for brand in coverage_brands
    )


def normalize_creative_metadata(date_value: Any, category_value: Any) -> tuple[str, str]:
    """Return a real ISO date and one bounded creative-taxonomy label."""

    raw_date = str(date_value or "").strip()
    normalized_date = ""
    if len(raw_date) == 10:
        try:
            normalized_date = calendar_date.fromisoformat(raw_date).isoformat()
        except ValueError:
            pass
    raw_category = str(category_value or "").strip()
    normalized_category = (
        _CATEGORY_LABELS.get(raw_category.casefold(), _CATEGORY_FALLBACK)
        if len(raw_category) <= 64
        else _CATEGORY_FALLBACK
    )
    return normalized_date, normalized_category


def _load_thumbnail(creative_root: Path, item: Mapping[str, Any]) -> dict[str, str] | None:
    path = _contained_file(creative_root, item.get("thumbnail_path"))
    if path is None:
        return None
    try:
        payload = read_bytes_no_follow(path)
    except (OSError, UnsafeDataRootError):
        return None
    if not payload or len(payload) > _MAX_THUMBNAIL_BYTES:
        return None
    mime = _image_mime(path, payload)
    if mime is None:
        return None
    date, category = normalize_creative_metadata(
        item.get("date"), item.get("category")
    )
    sha256 = hashlib.sha256(payload).hexdigest()
    if sha256 != str(item.get("thumbnail_sha256") or ""):
        return None
    return {
        "date": date,
        "category": category,
        "mime_type": mime,
        "sha256": sha256,
        "data_uri": f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}",
    }


def load_private_creative_gallery(
    data_root: str | Path,
    summary: Mapping[str, Any],
    *,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Load safe-render metadata without carrying private paths into output.

    Missing or malformed metadata does not block the dashboard. It produces an
    explicit unavailable state for every census brand. Individual unsafe or
    missing thumbnails are skipped and reduce that brand's safe count.
    """

    brands = _brand_names(summary)
    root = Path(data_root).expanduser().resolve(strict=True)
    creative_root = root / "creatives"
    using_authoritative_default = manifest_path is None
    selected_manifest = (
        Path(manifest_path).expanduser()
        if manifest_path is not None
        else root / DEFAULT_MANIFEST_RELATIVE_PATH
    )
    if not selected_manifest.is_absolute():
        selected_manifest = root / selected_manifest
    try:
        resolved_parent = selected_manifest.parent.resolve(strict=True)
    except OSError:
        return _unavailable_gallery(
            brands,
            reason="No safe creative manifest is available for this build.",
            metadata_status="unavailable",
        )
    resolved_manifest = resolved_parent / selected_manifest.name
    if root != resolved_manifest and root not in resolved_manifest.parents:
        return _unavailable_gallery(
            brands,
            reason="The safe creative manifest failed its private-path check.",
            metadata_status="invalid",
        )
    if resolved_manifest.is_symlink() or not resolved_manifest.is_file():
        return _unavailable_gallery(
            brands,
            reason="No safe creative manifest is available for this build.",
            metadata_status="unavailable",
        )
    try:
        manifest_bytes = read_bytes_no_follow(resolved_manifest)
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, UnsafeDataRootError):
        return _unavailable_gallery(
            brands,
            reason="The safe creative manifest could not be validated.",
            metadata_status="invalid",
        )
    if not isinstance(manifest, Mapping) or not isinstance(manifest.get("items"), list):
        return _unavailable_gallery(
            brands,
            reason="The safe creative manifest has an unsupported format.",
            metadata_status="invalid",
        )

    if not _manifest_provenance_is_valid(
        manifest,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        current_master_sha256=_sha256_regular_file(root / "master.json"),
        brands=brands,
        require_authoritative_manifest_hash=using_authoritative_default,
    ):
        return _unavailable_gallery(
            brands,
            reason="The safe creative manifest failed its provenance check.",
            metadata_status="invalid",
        )

    coverage = manifest.get("coverage")
    coverage = coverage if isinstance(coverage, Mapping) else {}
    item_groups: dict[str, list[Mapping[str, Any]]] = {}
    canonical_names = {name.casefold(): name for name in brands}
    for raw_item in manifest.get("items", []):
        if not isinstance(raw_item, Mapping) or str(raw_item.get("status") or "") != "success":
            continue
        canonical = canonical_names.get(str(raw_item.get("brand") or "").strip().casefold())
        if canonical:
            item_groups.setdefault(canonical, []).append(raw_item)

    total_bytes = 0
    loaded_total = 0
    rows: list[dict[str, Any]] = []
    for brand in brands:
        loaded: list[dict[str, str]] = []
        candidates = sorted(
            item_groups.get(brand, []),
            key=lambda item: (
                str(item.get("date") or ""),
                str(item.get("thumbnail_path") or ""),
            ),
            reverse=True,
        )
        seen_hashes: set[str] = set()
        for item in candidates:
            if len(loaded) >= GALLERY_TARGET_MAX:
                break
            thumbnail = _load_thumbnail(creative_root, item)
            if thumbnail is None or thumbnail["sha256"] in seen_hashes:
                continue
            encoded_bytes = len(thumbnail["data_uri"])
            if total_bytes + encoded_bytes > _MAX_GALLERY_BYTES:
                break
            total_bytes += encoded_bytes
            seen_hashes.add(thumbnail["sha256"])
            thumbnail.pop("sha256", None)
            loaded.append(thumbnail)

        safe_count = len(loaded)
        loaded_total += safe_count
        coverage_row = coverage.get(brand)
        coverage_row = coverage_row if isinstance(coverage_row, Mapping) else {}
        if safe_count >= GALLERY_TARGET_MIN:
            status = "ready"
            reason = (
                f"{safe_count} safe creative previews available. "
                f"Target: {GALLERY_TARGET_MIN}-{GALLERY_TARGET_MAX}."
            )
        elif safe_count:
            status = "insufficient"
            reason = (
                f"{safe_count} of {GALLERY_TARGET_MIN} minimum safe creative previews "
                "are available."
            )
        else:
            status = "unavailable"
            if int(coverage_row.get("available_records", 0) or 0) > 0:
                reason = "No creative preview passed the safe-render privacy gate."
            else:
                reason = "No safe creative preview is available for this brand."
        rows.append(
            {
                "brand": brand,
                "status": status,
                "safe_count": safe_count,
                "items": loaded,
                "reason": reason,
            }
        )

    ready = sum(row["status"] == "ready" for row in rows)
    insufficient = sum(row["status"] == "insufficient" for row in rows)
    unavailable = sum(row["status"] == "unavailable" for row in rows)
    return {
        "metadata_status": "available",
        "provenance_status": "verified",
        "manifest_generated_at": str(manifest.get("generated_at") or ""),
        "target_min": GALLERY_TARGET_MIN,
        "target_max": GALLERY_TARGET_MAX,
        "loaded_safe_creatives": loaded_total,
        "ready_brand_count": ready,
        "insufficient_brand_count": insufficient,
        "unavailable_brand_count": unavailable,
        "brands": rows,
    }


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", checksum)


def _synthetic_png(seed: str) -> bytes:
    """Generate a tiny deterministic RGB fixture with no external dependency."""

    width, height = 24, 32
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    background = tuple(48 + (value % 144) for value in digest[:3])
    accent = tuple(96 + (value % 144) for value in digest[3:6])
    rows = []
    for y in range(height):
        color = accent if 7 <= y < 12 or 21 <= y < 23 else background
        rows.append(b"\x00" + bytes(color) * width)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(b"".join(rows), level=9))
        + _png_chunk(b"IEND", b"")
    )


def synthetic_creative_gallery(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Return a fully synthetic, visibly prototyped gallery for the demo."""

    rows: list[dict[str, Any]] = []
    categories = ("Editorial", "Offer", "Seasonal")
    brand_dates = {
        str(row.get("brand") or ""): str(row.get("last_observed") or "")[:10]
        for row in summary.get("brands", [])
        if isinstance(row, Mapping)
    }
    for brand in _brand_names(summary):
        items = []
        for index, category in enumerate(categories, start=1):
            payload = _synthetic_png(f"{brand}|{index}")
            items.append(
                {
                    "date": brand_dates.get(brand, ""),
                    "category": f"Synthetic {category.casefold()} preview",
                    "mime_type": "image/png",
                    "data_uri": (
                        "data:image/png;base64,"
                        + base64.b64encode(payload).decode("ascii")
                    ),
                }
            )
        rows.append(
            {
                "brand": brand,
                "status": "ready",
                "safe_count": len(items),
                "items": items,
                "reason": (
                    f"{len(items)} synthetic creative previews. "
                    f"Target: {GALLERY_TARGET_MIN}-{GALLERY_TARGET_MAX}."
                ),
            }
        )
    return {
        "metadata_status": "synthetic_demo",
        "target_min": GALLERY_TARGET_MIN,
        "target_max": GALLERY_TARGET_MAX,
        "loaded_safe_creatives": len(rows) * len(categories),
        "ready_brand_count": len(rows),
        "insufficient_brand_count": 0,
        "unavailable_brand_count": 0,
        "brands": rows,
    }


__all__ = [
    "AUTHORITATIVE_MANIFEST_SHA256",
    "AUTHORITATIVE_PIPELINE_VERSION",
    "DEFAULT_MANIFEST_RELATIVE_PATH",
    "GALLERY_TARGET_MAX",
    "GALLERY_TARGET_MIN",
    "load_private_creative_gallery",
    "normalize_creative_metadata",
    "synthetic_creative_gallery",
    "unavailable_creative_gallery",
]
