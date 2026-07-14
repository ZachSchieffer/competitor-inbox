"""Deterministic synthetic dataset used by the credential-free demo command."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from .analysis import analyze_message
from .aggregate import aggregate_records


DEMO_STAMP = "ILLUSTRATIVE PROTOTYPE"
DEMO_ACCOUNT = "Northstar Apparel"
DEMO_START = date(2025, 7, 15)
DEMO_DAYS = 365
DEMO_QUADRANTS = {
    "Evergreen content": 580,
    "Everyday promotion": 491,
    "Seasonal promotion": 139,
    "Seasonal content": 50,
}

DEMO_BRANDS = (
    "Alder Row",
    "Calder Cloth",
    "Juniper Standard",
    "Harbor Loom",
    "Morrow Denim",
    "Cinder and Weft",
    "Fielding Goods",
    "Sable Street",
    "Kestrel Supply",
    "Ridge and Rail",
)

_EVERGREEN_SUBJECTS = (
    "How heavyweight cotton softens over time",
    "The denim care guide worth saving",
    "Inside our low-impact dye process",
    "3 ways to build a tighter travel wardrobe",
    "Why fabric weight changes the fit",
    "The everyday layers customers keep reaching for",
)
_EVERGREEN_BODIES = (
    "A practical material guide for choosing pieces that hold their shape.",
    "Customer notes on fit, fabric, and the details they notice after 30 wears.",
    "A closer look at construction, care, and how the garment develops over time.",
)
_PROMO_SUBJECTS = (
    "Save {depth}% on the everyday collection",
    "{depth}% off denim through tonight",
    "Take {depth}% off your next layer",
    "Save {depth}% on pieces built for repeat wear",
)
_SEASONAL_PROMOS = (
    ("Black Friday", "Black Friday: save {depth}% on the full collection"),
    ("Cyber Monday", "Cyber Monday: {depth}% off through tonight"),
    ("Holiday gifting", "Holiday gift orders get {depth}% off today"),
    ("Valentine's Day", "Valentine's Day: take {depth}% off selected pairs"),
    ("Mother's Day", "Mother's Day gift edit: save {depth}%"),
    ("Memorial Day", "Memorial Day: {depth}% off summer layers"),
    ("July 4", "July 4: save {depth}% on warm-weather staples"),
    ("Back to School", "Back-to-school edit: {depth}% off daily layers"),
    ("Labor Day", "Labor Day: take {depth}% off the weekend edit"),
    ("Halloween", "Halloween weekend: save {depth}% on dark denim"),
)
_SEASONAL_CONTENT = (
    ("Holiday gifting", "The holiday gift guide for repeat wear"),
    ("Black Friday", "Black Friday sizing guide"),
    ("Valentine's Day", "A Valentine's Day edit built around shared staples"),
    ("Mother's Day", "The Mother's Day gift guide"),
    ("Back to School", "The back-to-school uniform guide"),
    ("New Year", "New Year wardrobe planning guide"),
)


def _slug(value: str) -> str:
    return "-".join(value.casefold().replace("and", " ").split())


def _record_id(index: int, brand: str, subject: str, received_at: str) -> str:
    packed = f"northstar-demo|{index}|{brand}|{subject}|{received_at}".encode("utf-8")
    return "demo_" + hashlib.sha256(packed).hexdigest()[:24]


def _message_text(quadrant: str, index: int) -> tuple[str, str, str]:
    if quadrant == "Evergreen content":
        return (
            _EVERGREEN_SUBJECTS[index % len(_EVERGREEN_SUBJECTS)],
            "A practical note from the product team.",
            _EVERGREEN_BODIES[index % len(_EVERGREEN_BODIES)],
        )
    if quadrant == "Everyday promotion":
        depth = (10, 15, 20, 25, 30)[index % 5]
        return (
            _PROMO_SUBJECTS[index % len(_PROMO_SUBJECTS)].format(depth=depth),
            "The offer applies to selected full-price pieces.",
            f"Use the offer before it ends. Save {depth}% on eligible products.",
        )
    if quadrant == "Seasonal promotion":
        _, template = _SEASONAL_PROMOS[index % len(_SEASONAL_PROMOS)]
        depth = (15, 20, 25, 30, 35)[index % 5]
        return (
            template.format(depth=depth),
            "Seasonal ordering details are included below.",
            f"This calendar promotion includes a supported {depth}% discount.",
        )
    _, subject = _SEASONAL_CONTENT[index % len(_SEASONAL_CONTENT)]
    return (
        subject,
        "Planning notes for the next retail moment.",
        "A product and messaging guide with fit and fabric details.",
    )


def generate_demo_records() -> list[dict[str, Any]]:
    """Return 1,260 analyzed broadcasts with the frozen quadrant census."""

    quadrant_sequence: list[str] = []
    for quadrant, count in DEMO_QUADRANTS.items():
        quadrant_sequence.extend([quadrant] * count)

    records: list[dict[str, Any]] = []
    for index, quadrant in enumerate(quadrant_sequence):
        brand = DEMO_BRANDS[index % len(DEMO_BRANDS)]
        day_offset = (index * 37) % DEMO_DAYS
        received_date = DEMO_START + timedelta(days=day_offset)
        received = datetime.combine(
            received_date,
            time(hour=7 + (index % 12), minute=(index * 13) % 60),
            tzinfo=timezone.utc,
        ).isoformat()
        subject, preheader, visible_text = _message_text(quadrant, index)
        raw = {
            "schema_version": "1.0",
            "record_id": _record_id(index, brand, subject, received),
            "source_type": "synthetic_demo",
            "brand": {"canonical": brand},
            "received_at": received,
            "subject": subject,
            "preheader": preheader,
            "visible_text": visible_text,
            "headers": {"list_id": f"{_slug(brand)}.example"},
            "variant_count": 1,
            "illustrative_prototype": True,
            "data_classification": DEMO_STAMP,
            "demo_account": DEMO_ACCOUNT,
        }
        analyzed = analyze_message(raw)
        if analyzed["quadrant"] != quadrant:
            raise AssertionError(
                f"Synthetic fixture drifted: expected {quadrant}, got {analyzed['quadrant']}"
            )
        records.append(analyzed)
    return records


def demo_summary() -> dict[str, Any]:
    summary = aggregate_records(generate_demo_records(), illustrative=True)
    actual = {row["name"]: row["count"] for row in summary["quadrants"]}
    if actual != DEMO_QUADRANTS or summary["broadcast_count"] != 1260:
        raise AssertionError(f"Demo cross-foot failed: {actual}")
    return summary


def write_demo_dataset(output_path: str | Path) -> Path:
    """Write the public-safe fixture atomically and return its absolute path."""

    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    payload = {
        "stamp": DEMO_STAMP,
        "account": DEMO_ACCOUNT,
        "window": {
            "start": DEMO_START.isoformat(),
            "end": (DEMO_START + timedelta(days=DEMO_DAYS - 1)).isoformat(),
        },
        "records": generate_demo_records(),
    }
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, destination)
    return destination


__all__ = [
    "DEMO_ACCOUNT",
    "DEMO_BRANDS",
    "DEMO_DAYS",
    "DEMO_QUADRANTS",
    "DEMO_STAMP",
    "DEMO_START",
    "demo_summary",
    "generate_demo_records",
    "write_demo_dataset",
]
