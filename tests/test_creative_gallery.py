from __future__ import annotations

import base64
import json
import re
import struct
import zlib
from pathlib import Path

from competitor_inbox.creative_gallery import (
    load_private_creative_gallery,
    synthetic_creative_gallery,
)
from competitor_inbox.dashboard import render_dashboard
from competitor_inbox.demo import demo_summary
from competitor_inbox.store import ensure_private_data_root


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", checksum)


def _fixture_png(seed: int) -> bytes:
    width, height = 4, 5
    color = bytes(((seed * 31) % 255, (seed * 67) % 255, (seed * 97) % 255))
    rows = b"".join(b"\x00" + color * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(rows, level=9))
        + _png_chunk(b"IEND", b"")
    )


def _private_gallery_fixture(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    root = ensure_private_data_root(tmp_path / "private")
    creative_root = root / "creatives"
    thumbnails = creative_root / "thumbnails"
    manifests = creative_root / "manifests"
    thumbnails.mkdir(parents=True)
    manifests.mkdir(parents=True)

    items: list[dict[str, object]] = []
    for index in range(1, 8):
        filename = f"ready-{index}.png"
        (thumbnails / filename).write_bytes(_fixture_png(index))
        items.append(
            {
                "brand": "Ready Brand",
                "date": f"2026-07-{index:02d}",
                "status": "success",
                "record_id": f"private-record-{index}",
                "category": "evergreen",
                "thumbnail_path": f"thumbnails/{filename}",
            }
        )
    (thumbnails / "four-sigmatic.png").write_bytes(_fixture_png(20))
    items.append(
        {
            "brand": "Four Sigmatic",
            "date": "2026-99-99<script>",
            "status": "success",
            "record_id": "private-four-sigmatic-record",
            "category": "<img src=x onerror=alert(1)>",
            "subject": "Private subject must not enter the card",
            "visible_text": "Private body must not enter the card",
            "thumbnail_path": "thumbnails/four-sigmatic.png",
        }
    )
    items.append(
        {
            "brand": "Missing Brand",
            "date": "2026-07-11",
            "status": "success",
            "record_id": "unsafe-record",
            "thumbnail_path": "../outside.png",
        }
    )
    manifest = {
        "generated_at": "2026-07-14T00:00:00Z",
        "coverage": {
            "Ready Brand": {"available_records": 7, "rendered": 7},
            "Four Sigmatic": {"available_records": 2, "rendered": 1},
            "LMNT": {"available_records": 6, "rendered": 0},
        },
        "items": items,
    }
    manifest_path = manifests / "launch-sample-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    summary: dict[str, object] = {
        "brands": [
            {"brand": "Ready Brand"},
            {"brand": "Four Sigmatic"},
            {"brand": "LMNT"},
            {"brand": "Missing Brand"},
        ]
    }
    return root, summary


def test_private_manifest_caps_ready_brand_and_exposes_thin_states(tmp_path: Path) -> None:
    root, summary = _private_gallery_fixture(tmp_path)

    gallery = load_private_creative_gallery(root, summary)
    rows = {row["brand"]: row for row in gallery["brands"]}

    assert gallery["metadata_status"] == "available"
    assert gallery["loaded_safe_creatives"] == 6
    assert gallery["ready_brand_count"] == 1
    assert gallery["insufficient_brand_count"] == 1
    assert gallery["unavailable_brand_count"] == 2
    assert rows["Ready Brand"]["status"] == "ready"
    assert rows["Ready Brand"]["safe_count"] == 5
    assert rows["Ready Brand"]["items"][0]["category"] == "Evergreen content"
    assert [item["date"] for item in rows["Ready Brand"]["items"]] == [
        "2026-07-07",
        "2026-07-06",
        "2026-07-05",
        "2026-07-04",
        "2026-07-03",
    ]
    assert rows["Four Sigmatic"]["status"] == "insufficient"
    assert rows["Four Sigmatic"]["safe_count"] == 1
    assert rows["Four Sigmatic"]["items"][0]["date"] == ""
    assert rows["Four Sigmatic"]["items"][0]["category"] == "Safe creative preview"
    assert rows["LMNT"]["status"] == "unavailable"
    assert rows["LMNT"]["safe_count"] == 0
    assert rows["Missing Brand"]["status"] == "unavailable"
    assert all(
        set(item) == {"date", "category", "mime_type", "data_uri"}
        for row in rows.values()
        for item in row["items"]
    )
    assert all(
        item["data_uri"].startswith("data:image/png;base64,")
        for row in rows.values()
        for item in row["items"]
    )


def test_missing_manifest_marks_every_census_brand_unavailable(tmp_path: Path) -> None:
    root = ensure_private_data_root(tmp_path / "private")
    summary = {"brands": [{"brand": "LMNT"}, {"brand": "Four Sigmatic"}]}

    gallery = load_private_creative_gallery(root, summary)

    assert gallery["metadata_status"] == "unavailable"
    assert gallery["unavailable_brand_count"] == 2
    assert [row["status"] for row in gallery["brands"]] == [
        "unavailable",
        "unavailable",
    ]


def test_gallery_renderer_uses_data_images_and_keeps_states_visible(tmp_path: Path) -> None:
    root, gallery_summary = _private_gallery_fixture(tmp_path)
    summary = demo_summary()
    summary["_creative_gallery"] = load_private_creative_gallery(root, gallery_summary)

    document = render_dashboard(summary)
    image_sources = re.findall(r'<img[^>]+src="([^"]+)"', document)

    assert len(image_sources) == 6
    assert all(source.startswith("data:image/png;base64,") for source in image_sources)
    assert "Four Sigmatic" in document
    assert "Insufficient: 1" in document
    assert "LMNT" in document
    assert "Unavailable: 0" in document
    assert "3-5 privacy-reviewed previews per brand" in document
    assert "private-record" not in document
    assert "Private subject" not in document
    assert "Private body" not in document
    assert "thumbnail_path" not in document
    assert str(root) not in document
    assert "http://" not in document.casefold()
    assert "https://" not in document.casefold()
    assert "<script" not in document.casefold()


def test_renderer_recomputes_forged_status_and_normalizes_card_metadata() -> None:
    summary = demo_summary()
    valid_item = synthetic_creative_gallery(summary)["brands"][0]["items"][0]
    summary["_creative_gallery"] = {
        "target_min": 1,
        "target_max": 99,
        "loaded_safe_creatives": 999,
        "ready_brand_count": 2,
        "insufficient_brand_count": 0,
        "unavailable_brand_count": 0,
        "brands": [
            {
                "brand": "Forged Ready",
                "status": "ready",
                "safe_count": 99,
                "reason": "Forged ready claim",
                "items": [
                    {
                        "data_uri": "data:image/png;base64,not-valid-base64!",
                        "date": "2026-07-14",
                        "category": "evergreen",
                    }
                ],
            },
            {
                "brand": "Hostile Metadata",
                "status": "ready",
                "safe_count": 5,
                "reason": "Another forged claim",
                "items": [
                    {
                        "data_uri": valid_item["data_uri"],
                        "date": "2026-99-99<script>",
                        "category": "<img src=x onerror=alert(1)>",
                    }
                ],
            },
        ],
    }

    document = render_dashboard(summary)

    assert "Ready: 0" not in document
    assert "Unavailable: 0" in document
    assert "Insufficient: 1" in document
    assert "Forged ready claim" not in document
    assert "Another forged claim" not in document
    assert "2026-99-99" not in document
    assert "onerror" not in document
    assert "alert(1)" not in document
    assert "Safe creative preview" in document
    assert "<b>1</b><span>Validated local creative previews" in document
    assert "<b>0 / 2</b><span>Brands at the 3-5 preview target" in document
    assert "http://" not in document.casefold()
    assert "https://" not in document.casefold()


def test_demo_gallery_is_synthetic_and_complete() -> None:
    summary = demo_summary()
    gallery = synthetic_creative_gallery(summary)

    assert gallery["metadata_status"] == "synthetic_demo"
    assert gallery["ready_brand_count"] == summary["brand_count"]
    assert gallery["loaded_safe_creatives"] == summary["brand_count"] * 3
    assert all(row["status"] == "ready" for row in gallery["brands"])
    for row in gallery["brands"]:
        assert len(row["items"]) == 3
        for item in row["items"]:
            prefix, encoded = item["data_uri"].split(",", 1)
            assert prefix == "data:image/png;base64"
            assert base64.b64decode(encoded).startswith(b"\x89PNG\r\n\x1a\n")
