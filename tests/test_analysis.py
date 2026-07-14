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
    classify_scope_evidence,
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


def test_numeric_offer_support_rejects_depth_that_disagrees_with_evidence() -> None:
    record = {
        "subject": "Save 10% today",
        "offer": {
            "primary": {
                "type": "%off",
                "depth": 20.0,
                "source": "subject",
                "evidence": "Save 10%",
                "deterministic": True,
            },
            "candidates": [],
        },
    }

    assert numeric_offer_is_supported(record) is False


@pytest.mark.parametrize(
    "subject",
    (
        "Get 20% more protein",
        "Enjoy 30% more hydration",
        "Take 10% less time",
        "Score 15% higher on recovery",
        "Claim 20% market share",
        "Save 20% water",
    ),
)
def test_percent_metrics_do_not_become_offers(subject: str) -> None:
    offer = extract_offers(subject, "A product update", "Read the details.")

    assert offer["present"] is False
    assert offer["primary"] is None


@pytest.mark.parametrize("subject", ("Save 20%", "Get 20% off today"))
def test_percent_offer_verbs_preserve_real_discount_claims(subject: str) -> None:
    offer = extract_offers(subject, "Offer details", "The event ends tonight.")

    assert offer["present"] is True
    assert offer["primary"]["type"] == "%off"
    assert offer["primary"]["depth"] == 20.0
    assert all(candidate.get("depth") != 49 for candidate in offer["candidates"])


def test_bare_product_numbers_never_become_discount_depths() -> None:
    offer = extract_offers(
        "The $48 tee is back",
        "Made with 80% organic cotton",
        "See the material guide.",
    )

    assert offer["present"] is False
    assert offer["primary"] is None


def test_campaign_offer_rules_ignore_standing_policies_and_named_sets() -> None:
    standing_policy = extract_offers(
        "The weekly edit",
        "Three current favorites",
        "A product story. " + "x" * 600 + " Free shipping on all orders. Terms apply.",
    )
    named_set = extract_offers(
        "Meet the Citrus Set",
        "A new product format",
        "The Citrus Bundle contains three full-size products.",
    )

    assert standing_policy["present"] is False
    assert named_set["present"] is False


def test_specific_offer_evidence_outranks_generic_sale_language() -> None:
    numeric = extract_offers(
        "The annual sale starts now",
        "Save 35% through tonight",
        "Sale details and terms.",
    )
    gift = extract_offers(
        "The spring sale",
        "Up to 3 free gifts",
        "Choose a complimentary gift with a qualifying purchase.",
    )
    shipping = extract_offers(
        "Weekend event",
        "Free shipping today",
        "The sale applies to select products.",
    )

    assert numeric["primary"]["type"] == "%off"
    assert numeric["primary"]["depth"] == 35.0
    assert gift["primary"]["type"] == "gift"
    assert shipping["primary"]["type"] == "free_shipping"


def test_bogo_structure_outranks_its_embedded_percentage() -> None:
    structured = extract_offers(
        "BOGO weekend",
        "Buy one, get one 40% off",
        "The paired-item offer ends tonight.",
    )
    quantity = extract_offers(
        "A paired-item event",
        "Buy 2, get 1 free",
        "The benefit applies at checkout.",
    )

    assert structured["primary"]["type"] == "bogo"
    assert any(
        candidate["type"] == "%off" and candidate["depth"] == 40.0
        for candidate in structured["candidates"]
    )
    assert quantity["primary"]["type"] == "bogo"


def test_qualitative_offers_require_campaign_lead_evidence() -> None:
    gift = extract_offers(
        "A reward for your next order",
        "Free travel case with an order over the qualifying amount",
        "Choose your color at checkout.",
    )
    trial = extract_offers(
        "Try it at home",
        "A risk-free trial",
        "Return it within the stated window if it is not a fit.",
    )
    qualitative_save = extract_offers(
        "Save on the summer edit",
        "A limited campaign",
        "Shop the current assortment.",
    )

    assert gift["primary"]["type"] == "gift"
    assert trial["primary"]["type"] == "other"
    assert qualitative_save["primary"]["type"] == "other"


def test_gift_and_risk_free_rules_require_active_benefit_language() -> None:
    urgent_gift = extract_offers(
        "Ends tonight: Free travel pouch",
        "A limited campaign",
        "Browse the current assortment.",
    )
    threshold_gift = extract_offers(
        "A thank-you event",
        "A complimentary mini on orders $75+",
        "Choose a qualifying item.",
    )
    guarantee = extract_offers(
        "Try it at home",
        "A 30-day money-back guarantee",
        "The return window begins on delivery.",
    )
    unavailable = extract_offers(
        "A policy update",
        "The risk-free trial is not available in every region",
        "Read the eligibility rules.",
    )

    assert urgent_gift["primary"]["type"] == "gift"
    assert threshold_gift["primary"]["type"] == "gift"
    assert guarantee["primary"]["type"] == "other"
    assert unavailable["present"] is False


def test_gift_merchandising_does_not_become_a_gift_with_purchase() -> None:
    holiday_merchandising = extract_offers(
        "Holiday gifts on sale",
        "Shop the gift edit",
        "Choose a gift for someone on your list.",
    )
    spring_merchandising = extract_offers(
        "Spring sale",
        "Choose a gift for Mom",
        "Browse the current collection.",
    )
    editorial_ideas = extract_offers(
        "Free gift ideas",
        "A guide for the season",
        "Browse the editorial edit.",
    )

    assert holiday_merchandising["primary"]["type"] == "other"
    assert spring_merchandising["primary"]["type"] == "other"
    assert editorial_ideas["present"] is False
    assert all(
        candidate["type"] != "gift"
        for offer in (holiday_merchandising, spring_merchandising)
        for candidate in offer["candidates"]
    )


def test_offer_vocabulary_requires_an_active_campaign_benefit() -> None:
    informational_deals = extract_offers(
        "Help us tailor your updates",
        "A short preference survey",
        "Answer a few questions so we can send relevant deals and information.",
    )
    charitable_sales = extract_offers(
        "The community set returns",
        "All sales support a local nonprofit",
        "Shop the returning set.",
    )
    standing_shipping = extract_offers(
        "The weekly journal",
        "A new editorial dispatch",
        "Free shipping plus a starter sample on orders $80+ or a deluxe sample on orders $120+.",
    )
    limited_offer = extract_offers(
        "A two-step routine",
        "Softer skin in two steps",
        "Limited time offer. Browse the current duo.",
    )

    assert informational_deals["present"] is False
    assert charitable_sales["present"] is False
    assert standing_shipping["present"] is False
    assert limited_offer["primary"]["type"] == "other"


def test_footer_disclaimers_and_standing_perks_are_not_campaign_offers() -> None:
    final_sale_policy = extract_offers(
        "The weekly edit",
        "Three current favorites",
        "Select items final sale.",
    )
    combination_policy = extract_offers(
        "A product update",
        "Read the details",
        "Discounts cannot be combined with other benefits.",
    )
    standalone_perk = extract_offers(
        "An advisor's picks",
        "A curated assortment",
        "Watch the edit\nFree Shipping\nEarn rewards",
    )
    membership_perk = extract_offers(
        "Membership news",
        "Program details",
        "Membership perks include free shipping and returns.",
    )
    active_terms = extract_offers(
        "Save 20% today",
        "Terms apply",
        "The campaign ends tonight.",
    )

    assert final_sale_policy["present"] is False
    assert combination_policy["present"] is False
    assert standalone_perk["present"] is False
    assert membership_perk["present"] is False
    assert active_terms["primary"]["type"] == "%off"


def test_quoted_printable_spacing_preserves_numeric_offer_evidence() -> None:
    visible_text = "Members enjoy=C2=A020% OFF the current assortment."
    offer = extract_offers("A member update", "", visible_text)

    assert offer["primary"]["type"] == "%off"
    assert offer["primary"]["depth"] == 20.0
    assert offer["primary"]["evidence"] == "=C2=A020% OFF"
    assert numeric_offer_is_supported(
        {
            "visible_text": visible_text,
            "primary_offer": offer["primary"],
            "offer_candidates": offer["candidates"],
        }
    )


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


def test_retail_calendar_language_maps_to_the_specific_occasion() -> None:
    new_year = classify_seasonality(
        "The best way to start the year", "A January plan", "", "2026-01-02"
    )
    holiday = classify_seasonality(
        "Super Saturday starts now", "Free shipping today", "", "2025-12-20"
    )
    mothers_day = classify_seasonality(
        "A note for Mother’s Day", "Shop the gift edit", "", "2026-05-01"
    )

    assert new_year["occasion"] == "New Year"
    assert holiday["occasion"] == "Holiday gifting"
    assert mothers_day["occasion"] == "Mother's Day"


def test_generic_gift_language_is_calendar_gated() -> None:
    outside_window = classify_seasonality(
        "Shop gifts", "A gift guide", "", "2026-05-01"
    )
    holiday_window = classify_seasonality(
        "Shop gifts", "A gift guide", "", "2026-11-15"
    )

    assert outside_window["seasonal"] is False
    assert holiday_window["occasion"] == "Holiday gifting"


def test_holiday_gifts_is_an_explicit_date_independent_occasion() -> None:
    result = classify_seasonality(
        "Holiday gifts have arrived", "Shop the collection", "", "2026-03-15"
    )

    assert result["seasonal"] is True
    assert result["occasion"] == "Holiday gifting"
    assert result["source"] == "subject"


@pytest.mark.parametrize("observed_at", ("2026-01-15", "2026-12-15"))
def test_generic_mom_gift_guide_is_not_seasonal_outside_april_or_may(
    observed_at: str,
) -> None:
    result = classify_seasonality(
        "Mom gift guide", "Gifts for Mom", "Browse the edit.", observed_at
    )

    assert result["seasonal"] is False


@pytest.mark.parametrize("observed_at", ("2026-04-15", "2026-05-15"))
def test_generic_mom_gift_guide_maps_to_mothers_day_in_window(
    observed_at: str,
) -> None:
    result = classify_seasonality(
        "Mom gift guide", "Gifts for Mom", "Browse the edit.", observed_at
    )

    assert result["seasonal"] is True
    assert result["occasion"] == "Mother's Day"


def test_explicit_holiday_language_overrides_generic_mom_gift_guard() -> None:
    result = classify_seasonality(
        "Holiday gifts for Mom", "Mom gift guide", "", "2026-03-15"
    )

    assert result["seasonal"] is True
    assert result["occasion"] == "Holiday gifting"


def test_intent_weights_campaign_lead_over_footer_boilerplate() -> None:
    education = analyze_message(
        {
            "scope": "broadcast",
            "subject": "How non-toxic materials are tested",
            "preheader": "The science behind the product",
            "visible_text": "A testing guide. " + "x" * 500 + " A note from our founder.",
        }
    )
    endorsement = analyze_message(
        {
            "scope": "broadcast",
            "subject": "The style advisor's picks",
            "preheader": "A community edit",
            "visible_text": "Three products selected by an independent advisor.",
        }
    )
    seasonal_edit = analyze_message(
        {
            "scope": "broadcast",
            "subject": "In case you forgot",
            "preheader": "Shop Mother’s Day gifts",
            "visible_text": "A gift edit for the occasion.",
            "canonical_received_at": "2026-05-01T08:00:00Z",
        }
    )

    assert education["intent"]["label"] == "Ingredient/education"
    assert endorsement["intent"]["label"] == "Social proof/UGC"
    assert seasonal_edit["intent"]["label"] == "Lifestyle/seasonal"


def test_launch_synonyms_do_not_require_the_word_launch() -> None:
    coming_soon = analyze_message(
        {
            "scope": "broadcast",
            "subject": "Coming soon: a new retail partner",
            "visible_text": "Mark the release date.",
        }
    )
    reimagined = analyze_message(
        {
            "scope": "broadcast",
            "subject": "A familiar ritual",
            "preheader": "The daily format, reimagined",
            "visible_text": "Meet the updated product.",
        }
    )

    assert coming_soon["intent"]["label"] == "New product launch"
    assert reimagined["intent"]["label"] == "New product launch"


def test_high_signal_launch_and_social_proof_phrases_beat_the_fallback() -> None:
    arrived = analyze_message(
        {
            "campaign_id": "synthetic-arrival",
            "subject": "New daily capsule is here",
            "visible_text": "Explore the current colors.",
        }
    )
    new_option = analyze_message(
        {
            "campaign_id": "synthetic-new-option",
            "subject": "Choose your format",
            "visible_text": "You can now choose a compact size for travel.",
        }
    )
    endorsement = analyze_message(
        {
            "campaign_id": "synthetic-endorsement",
            "subject": "The essentials our editor swears by",
            "visible_text": "Browse the selected pieces.",
        }
    )
    community_proof = analyze_message(
        {
            "campaign_id": "synthetic-community-proof",
            "subject": "A customer favorite returns",
            "visible_text": "Read the community notes.",
        }
    )
    merchandising = analyze_message(
        {
            "campaign_id": "synthetic-routine",
            "subject": "Replace four products with one routine",
            "visible_text": "Shop the system.",
        }
    )

    assert arrived["intent"]["label"] == "New product launch"
    assert new_option["intent"]["label"] == "New product launch"
    assert endorsement["intent"]["label"] == "Social proof/UGC"
    assert community_proof["intent"]["label"] == "Social proof/UGC"
    assert merchandising["intent"]["label"] == "Featured products"


def test_season_drop_subject_is_classified_as_a_product_launch() -> None:
    analyzed = analyze_message(
        {
            "campaign_id": "synthetic-season-drop",
            "subject": "Our boldest drop of the season",
            "visible_text": "Explore the newly released flavors.",
        }
    )

    assert analyzed["intent"]["label"] == "New product launch"


def test_strong_content_intent_is_not_overridden_by_a_secondary_body_offer() -> None:
    lifecycle_lesson = analyze_message(
        {
            "subject": "Welcome: want to learn with us?",
            "visible_text": (
                "Use code WELCOME15 for 15% off your first order. "
                "The science guide explains why daily fiber matters."
            ),
        }
    )
    broadcast_lesson = analyze_message(
        {
            "campaign_id": "synthetic-broadcast-lesson",
            "subject": "What are adaptogens?",
            "visible_text": (
                "A practical ingredient guide. Subscribe and save 15% on recurring orders."
            ),
        }
    )

    assert lifecycle_lesson["scope"] == "lifecycle"
    assert lifecycle_lesson["intent"]["label"] == "Ingredient/education"
    assert lifecycle_lesson["offer"]["primary"]["type"] == "%off"
    assert broadcast_lesson["scope"] == "broadcast"
    assert broadcast_lesson["intent"]["label"] == "Ingredient/education"

    late_launch_offer = analyze_message(
        {
            "campaign_id": "synthetic-launch-with-footer",
            "subject": "First look at the new daily format",
            "visible_text": "A newly available option. " + "x" * 450 + " Save 15% today.",
        }
    )
    promotion_led = analyze_message(
        {
            "campaign_id": "synthetic-promotion-led",
            "subject": "Save 20% today",
            "visible_text": "A science guide explains the material.",
        }
    )

    assert late_launch_offer["offer"]["primary"]["type"] == "%off"
    assert late_launch_offer["intent"]["label"] == "New product launch"
    assert promotion_led["intent"]["label"] == "Promotion/offer"


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


def test_welcome_offer_with_apply_code_remains_lifecycle() -> None:
    scope = classify_scope_evidence(
        "Welcome to Northstar",
        "Apply code WELCOME15 at checkout",
        "Your first-order code is ready.",
        bulk_or_list=True,
    )

    assert scope == ("lifecycle", "lifecycle:welcome", 0.96)


def test_acquisition_eligibility_alone_does_not_create_lifecycle_scope() -> None:
    subject_offer = classify_scope_evidence(
        "First-order discount: 20% off",
        "An invitation for new customers",
        "Join the program to claim the offer.",
        bulk_or_list=True,
    )
    body_offer = classify_scope_evidence(
        "A membership invitation",
        "Program details",
        "First-order offer: save 20% after joining.",
        bulk_or_list=True,
    )
    recipient_state = classify_scope_evidence(
        "An account update",
        "Your benefit is ready",
        "Your first-order code is ready.",
        bulk_or_list=True,
    )

    assert subject_offer == ("broadcast", "bulk_or_list_header", 0.96)
    assert body_offer == ("broadcast", "bulk_or_list_header", 0.96)
    assert recipient_state == ("lifecycle", "lifecycle:welcome", 0.91)


def test_generic_welcome_language_does_not_create_lifecycle_scope() -> None:
    seasonal = classify_scope_evidence(
        "Welcome to summer",
        "The new edit is here",
        "Shop the collection.",
        bulk_or_list=True,
    )
    ordinary_phrase = classify_scope_evidence(
        "A member event",
        "You are welcome to save 20%",
        "The offer ends tonight.",
        bulk_or_list=True,
    )

    assert seasonal == ("broadcast", "bulk_or_list_header", 0.96)
    assert ordinary_phrase == ("broadcast", "bulk_or_list_header", 0.96)


@pytest.mark.parametrize(
    ("subject", "body"),
    (
        ("Your receipt", "View details"),
        ("Password changed", "A message for you"),
        ("Invoice available", "View details"),
        ("A message for you", "Read the update"),
    ),
)
def test_unrecognized_nonbulk_messages_are_uncertain(subject: str, body: str) -> None:
    scope = classify_scope_evidence(subject, "", body, bulk_or_list=False)

    assert scope == ("uncertain", "ambiguous_nonbulk_message", 0.55)


def test_positive_headerless_marketing_content_is_broadcast() -> None:
    scope = classify_scope_evidence(
        "Summer sale",
        "Save 20% today",
        "Shop the collection.",
        bulk_or_list=False,
    )

    assert scope == ("broadcast", "marketing_content", 0.72)


def test_recruiting_welcome_language_remains_a_broadcast() -> None:
    scope = classify_scope_evidence(
        "Welcome creators",
        "Apply to join our ambassador program",
        "Applications are open this week.",
        bulk_or_list=True,
    )

    assert scope == ("broadcast", "bulk_or_list_header", 0.96)


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


def test_existing_ai_cache_is_forced_private_before_read(tmp_path: Path) -> None:
    cache_dir = tmp_path / "external-cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "intent-cache.json"
    cache_path.write_text("{}", encoding="utf-8")
    cache_path.chmod(0o644)

    AnthropicIntentClassifier(cache_dir, client=_FakeClient())

    assert cache_path.stat().st_mode & 0o777 == 0o600


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
