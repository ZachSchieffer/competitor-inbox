from __future__ import annotations

import json
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
    classify_seasonality,
    extract_offers,
)
from competitor_inbox.demo import DEMO_QUADRANTS, demo_summary, generate_demo_records


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
