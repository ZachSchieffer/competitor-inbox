from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from competitor_inbox.aggregate import (
    CrossFootError,
    aggregate_records,
    assign_posture,
    verify_cross_foot,
)
from competitor_inbox.analysis import (
    AnthropicIntentClassifier,
    analyze_message,
    analyze_normalized_messages,
    classify_seasonality,
    extract_offers,
    numeric_offer_is_supported,
)
from competitor_inbox.demo import DEMO_QUADRANTS, demo_summary, generate_demo_records
from competitor_inbox.schema import NormalizedMessage, SourceCompleteness


def test_numeric_offer_requires_context_and_retains_exact_evidence() -> None:
    offer = extract_offers(
        "Save 25% on denim",
        "The offer ends tonight",
        "Fabric contains 30% recycled cotton and the jacket costs $49.00.",
    )

    assert offer["present"] is True
    assert offer["primary"] == {
        "type": "%off",
        "depth": 25.0,
        "unit": "percent",
        "source": "subject",
        "evidence": "Save 25%",
        "confidence": 1.0,
        "deterministic": True,
    }
    assert all(candidate.get("depth") != 30 for candidate in offer["candidates"])
    assert all(candidate.get("depth") != 49 for candidate in offer["candidates"])


def test_bare_product_numbers_never_become_discount_depths() -> None:
    offer = extract_offers(
        "The $48 tee is back",
        "Made with 80% organic cotton",
        "See the material guide.",
    )

    assert offer["present"] is False
    assert offer["primary"] is None


def test_seasonality_requires_language_and_uses_date_only_to_confirm() -> None:
    assert classify_seasonality("New arrivals", "", "", "2025-11-28")["seasonal"] is False
    assert classify_seasonality("Summer state of mind", "", "", "2025-07-10")["seasonal"] is False

    explicit = classify_seasonality("Black Friday sizing guide", "", "", "2025-02-10")
    assert explicit["seasonal"] is True
    assert explicit["occasion"] == "Black Friday"

    confirmed = classify_seasonality("Summer collection preview", "", "", "2025-07-10")
    assert confirmed["seasonal"] is True
    assert confirmed["occasion"] == "Summer"

    wrong_window = classify_seasonality("Summer collection preview", "", "", "2025-12-10")
    assert wrong_window["seasonal"] is False


def test_curated_export_preserves_nonnumeric_annotations_in_canonical_records() -> None:
    curated_offer = {
        "type": "bundle",
        "depth": None,
        "unit": "other",
        "source": "manual",
        "evidence": "curated column",
        "confidence": 0.9,
        "deterministic": False,
    }
    record = NormalizedMessage(
        id="curated-annotations",
        source_type="curated_export",
        source_uid="1",
        canonical_received_at="2026-02-01T08:00:00Z",
        brand="Alder Row",
        sender_name="Alder Row",
        sender_domain="alder-row.test",
        subject="The weekly edit",
        preheader="A few current favorites",
        visible_text="Browse the latest pieces.",
        content_hash="curated-annotations-hash",
        scope="broadcast",
        scope_reason="curated_export",
        scope_confidence=1.0,
        intent="Founder/brand story",
        intent_source="manual",
        intent_confidence=0.9,
        offer_candidates=[curated_offer],
        primary_offer=curated_offer,
        seasonal=True,
        occasion="Black Friday",
    )

    analyzed = analyze_normalized_messages([record])[0]

    assert record.source_completeness == SourceCompleteness.CURATED_EXPORT.value
    assert analyzed.primary_offer == {
        "type": "bundle",
        "depth": None,
        "unit": "other",
        "source": "curated_export",
        "evidence": "",
        "confidence": 0.9,
        "deterministic": False,
    }
    assert analyzed.offer_candidates == [analyzed.primary_offer]
    assert analyzed.seasonal is True
    assert analyzed.occasion == "Black Friday"
    assert analyzed.intent == "Promotion/offer"
    assert analyzed.intent_source == "curated_export"


def test_curated_intent_survives_only_without_an_offer() -> None:
    analyzed = analyze_message(
        {
            "source_type": "curated_export",
            "scope": "broadcast",
            "subject": "The weekly edit",
            "visible_text": "Browse the latest pieces.",
            "canonical_received_at": "2026-02-01T08:00:00Z",
            "intent": "Founder/brand story",
            "intent_source": "manual",
            "intent_confidence": 0.92,
            "seasonal": True,
            "occasion": "Unrecognized retail moment",
        }
    )

    assert analyzed["offer"]["present"] is False
    assert analyzed["seasonality"]["seasonal"] is False
    assert analyzed["intent"] == {
        "label": "Founder/brand story",
        "source": "curated_export",
        "confidence": 0.92,
    }


def test_deterministic_intent_overrides_curated_intent() -> None:
    analyzed = analyze_message(
        {
            "source_type": "curated_export",
            "scope": "broadcast",
            "subject": "Introducing our newest launch",
            "visible_text": "Meet the new collection.",
            "canonical_received_at": "2026-02-01T08:00:00Z",
            "intent": "Founder/brand story",
            "intent_source": "manual",
            "intent_confidence": 0.95,
        }
    )

    assert analyzed["intent"] == {
        "label": "New product launch",
        "source": "deterministic",
        "confidence": 0.9,
    }


def test_curated_numeric_annotations_never_create_numeric_claims() -> None:
    curated_numeric = {
        "type": "%off",
        "depth": 40.0,
        "unit": "percent",
        "source": "subject",
        "evidence": "Save 40%",
        "confidence": 1.0,
        "deterministic": True,
    }
    record = {
        "source_type": "curated_export",
        "scope": "broadcast",
        "subject": "The weekly edit",
        "visible_text": "Browse the latest pieces.",
        "canonical_received_at": "2026-02-01T08:00:00Z",
        "intent": "Ingredient/education",
        "primary_offer": curated_numeric,
        "offer_candidates": [curated_numeric],
    }

    analyzed = analyze_message(record)
    deterministic_override = analyze_message({**record, "subject": "Save 25% today"})

    assert analyzed["offer"]["present"] is False
    assert analyzed["offer"]["primary"] is None
    assert analyzed["offer"]["candidates"] == []
    assert "40.0" not in json.dumps(analyzed, sort_keys=True)
    assert analyzed["intent"]["label"] == "Ingredient/education"
    assert deterministic_override["offer"]["primary"]["depth"] == 25.0
    assert deterministic_override["offer"]["primary"]["source"] == "subject"
    assert all(
        candidate.get("depth") != 40.0
        for candidate in deterministic_override["offer"]["candidates"]
    )
    assert numeric_offer_is_supported(
        {
            "offer": {
                "primary": {"type": "bundle", "depth": None},
                "candidates": [curated_numeric],
            }
        }
    ) is False


def test_deterministic_offer_and_seasonality_override_curated_annotations() -> None:
    analyzed = analyze_message(
        {
            "source_type": "curated_export",
            "scope": "broadcast",
            "subject": "Cyber Monday: Save 20% today",
            "visible_text": "The offer ends tonight.",
            "canonical_received_at": "2026-11-30T08:00:00Z",
            "intent": "Founder/brand story",
            "offer_candidates": [
                {
                    "type": "bundle",
                    "depth": None,
                    "source": "manual",
                }
            ],
            "seasonal": True,
            "occasion": "Black Friday",
        }
    )

    assert analyzed["offer"]["primary"]["depth"] == 20.0
    assert analyzed["offer"]["primary"]["source"] == "subject"
    assert analyzed["offer"]["analysis_mode"] == "deterministic"
    assert analyzed["seasonality"]["occasion"] == "Cyber Monday"
    assert analyzed["seasonality"]["source"] == "subject"
    assert analyzed["intent"]["label"] == "Promotion/offer"
    assert analyzed["intent"]["source"] == "deterministic"


def test_imap_records_do_not_inherit_curated_annotations() -> None:
    analyzed = analyze_message(
        {
            "source_type": "imap",
            "scope": "broadcast",
            "subject": "The weekly edit",
            "visible_text": "Browse the latest pieces.",
            "canonical_received_at": "2026-02-01T08:00:00Z",
            "intent": "Founder/brand story",
            "offer_candidates": [
                {
                    "type": "bundle",
                    "depth": None,
                    "source": "manual",
                }
            ],
            "seasonal": True,
            "occasion": "Black Friday",
        }
    )

    assert analyzed["offer"]["present"] is False
    assert analyzed["seasonality"]["seasonal"] is False
    assert analyzed["intent"]["label"] == "Featured products"
    assert analyzed["intent"]["source"] == "deterministic"

    deterministic_offer = analyze_message(
        {
            "source_type": "imap",
            "scope": "broadcast",
            "subject": "Save 25% today",
            "visible_text": "The offer ends tonight.",
            "canonical_received_at": "2026-02-01T08:00:00Z",
        }
    )
    assert deterministic_offer["offer"]["primary"]["depth"] == 25.0
    assert deterministic_offer["offer"]["primary"]["source"] == "subject"
    assert deterministic_offer["intent"]["label"] == "Promotion/offer"


def test_scope_keeps_lifecycle_out_of_broadcast_metrics() -> None:
    lifecycle = analyze_message(
        {
            "brand": "Alder Row",
            "subject": "Your order has shipped",
            "visible_text": "Track your delivery.",
            "canonical_received_at": "2026-01-02T08:00:00Z",
        }
    )
    broadcast = analyze_message(
        {
            "brand": "Alder Row",
            "subject": "How linen softens over time",
            "visible_text": "A material guide.",
            "canonical_received_at": "2026-01-03T08:00:00Z",
        }
    )
    summary = aggregate_records([lifecycle, broadcast])

    assert summary["broadcast_count"] == 1
    assert summary["scope_counts"] == {"broadcast": 1, "lifecycle": 1, "uncertain": 0}


class _FakeMessages:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "intent": "Featured products",
                            "uniqueness": 3,
                            "benefit_theme": "daily layers",
                            "offer_type": "bundle",
                        }
                    ),
                }
            ]
        }


class _FakeClient:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


def test_optional_ai_receives_only_sanitized_text_and_cannot_invent_depth(tmp_path: Path) -> None:
    client = _FakeClient()
    classifier = AnthropicIntentClassifier(tmp_path / "external-cache", client=client)
    address = "buyer" + "@" + "private.test"
    link = "https" + "://store.test/click?recipient=private-token"

    analyzed = analyze_message(
        {
            "scope": "broadcast",
            "subject": f"A new set for {address}",
            "preheader": link,
            "visible_text": "A matching set is now available.",
            "canonical_received_at": "2026-05-02T08:00:00Z",
        },
        classifier=classifier,
    )
    sent = client.messages.kwargs["messages"][0]["content"]

    assert address not in sent
    assert link not in sent
    assert "[redacted email]" in sent
    assert "[redacted url]" in sent
    assert analyzed["offer"]["primary"]["source"] == "ai"
    assert analyzed["offer"]["primary"]["depth"] is None
    assert analyzed["offer"]["numeric_supported"] is False
    assert classifier.cache_path.is_file()


def test_flat_canonical_records_aggregate_without_losing_analysis() -> None:
    record = {
        "id": "flat-1",
        "brand": "Calder Cloth",
        "canonical_received_at": "2026-01-01T08:00:00Z",
        "subject": "Save 20% on denim",
        "preheader": "Offer details",
        "visible_text": "Save 20% today.",
        "scope": "broadcast",
        "intent": "Promotion/offer",
        "intent_source": "deterministic",
        "intent_confidence": 1.0,
        "offer_candidates": [
            {
                "type": "%off",
                "depth": 20.0,
                "unit": "percent",
                "source": "subject",
                "evidence": "Save 20%",
                "confidence": 1.0,
                "deterministic": True,
            }
        ],
        "primary_offer": {
            "type": "%off",
            "depth": 20.0,
            "unit": "percent",
            "source": "subject",
            "evidence": "Save 20%",
            "confidence": 1.0,
            "deterministic": True,
        },
        "seasonal": False,
        "occasion": None,
        "variant_count": 1,
    }

    summary = aggregate_records([record])

    assert summary["broadcast_count"] == 1
    assert summary["quadrants"][1]["count"] == 1
    assert summary["brands"][0]["intent_counts"] == {"Promotion/offer": 1}


def test_posture_requires_share_and_runner_up_margin() -> None:
    assert assign_posture({"Featured products": 34, "Promotion/offer": 33, "Ingredient/education": 33})["label"] == "Mixed"
    qualified = assign_posture({"Ingredient/education": 50, "Promotion/offer": 30, "Featured products": 20})
    assert qualified["label"] == "Education led"


def test_posture_is_withheld_until_message_and_history_gates_both_pass() -> None:
    def promotion_records(count: int, step_days: int) -> list[dict[str, object]]:
        start = datetime(2026, 1, 1, 8, tzinfo=timezone.utc)
        return [
            {
                "id": f"promotion-{count}-{step_days}-{index}",
                "brand": "Alder Row",
                "canonical_received_at": (
                    start + timedelta(days=index * step_days)
                ).isoformat(),
                "subject": "Save 20% today",
                "preheader": "The offer ends tonight.",
                "visible_text": "Save 20% on eligible products.",
                "scope": "broadcast",
                "variant_count": 1,
            }
            for index in range(count)
        ]

    thin_history = aggregate_records(promotion_records(30, 2))["brands"][0]
    thin_count = aggregate_records(promotion_records(29, 4))["brands"][0]
    eligible = aggregate_records(promotion_records(30, 4))["brands"][0]

    assert thin_history["qualified_broadcasts"] == 30
    assert thin_history["observed_days"] < 90
    assert thin_history["posture"]["label"] == "Insufficient history"
    assert thin_history["posture"]["eligible"] is False

    assert thin_count["qualified_broadcasts"] < 30
    assert thin_count["observed_days"] >= 90
    assert thin_count["posture"]["label"] == "Insufficient history"
    assert thin_count["posture"]["eligible"] is False

    assert eligible["qualified_broadcasts"] == 30
    assert eligible["observed_days"] >= 90
    assert eligible["posture"]["label"] == "Promotion led"
    assert eligible["posture"]["eligible"] is True


def test_cross_foot_fails_loudly() -> None:
    summary = demo_summary()
    summary["broadcast_count"] += 1

    with pytest.raises(CrossFootError, match="Cross-foot failed"):
        verify_cross_foot(summary)


def test_demo_is_exact_and_fully_stamped() -> None:
    records = generate_demo_records()
    summary = demo_summary()

    assert len(records) == 1260
    assert summary["broadcast_count"] == 1260
    assert {row["name"]: row["count"] for row in summary["quadrants"]} == DEMO_QUADRANTS
    assert summary["metadata"]["observed_days"] == 365
    assert summary["cross_foot"]["passed"] is True
    assert all(record["data_classification"] == "ILLUSTRATIVE PROTOTYPE" for record in records)
