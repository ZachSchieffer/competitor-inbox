"""Four-level deterministic message deduplication."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from typing import Iterable, Mapping

from .schema import NormalizedMessage, SourceCompleteness


@dataclass(slots=True)
class DedupeReport:
    messages: list[NormalizedMessage]
    input_count: int
    distinct_count: int
    variants_collapsed: int
    level_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "input_count": self.input_count,
            "distinct_count": self.distinct_count,
            "variants_collapsed": self.variants_collapsed,
            "level_counts": dict(self.level_counts),
        }


def _coerce(value: NormalizedMessage | Mapping[str, object]) -> NormalizedMessage:
    return value if isinstance(value, NormalizedMessage) else NormalizedMessage.from_dict(value)


def _normalized_copy(record: NormalizedMessage) -> NormalizedMessage:
    return replace(record, variant_ids=list(record.variant_ids), parse_errors=list(record.parse_errors))


def _text_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _similarity(left: str, right: str) -> float:
    a, b = _text_key(left), _text_key(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def _body_signal_matches(left: NormalizedMessage, right: NormalizedMessage) -> bool:
    if left.content_hash == right.content_hash:
        return True
    left_signal = left.visible_text[:800]
    right_signal = right.visible_text[:800]
    return _similarity(left_signal, right_signal) >= 0.88


def _within_days(left: NormalizedMessage, right: NormalizedMessage, days: int) -> bool:
    delta = abs((left.received_datetime - right.received_datetime).total_seconds())
    return delta <= days * 86_400


def _merge(canonical: NormalizedMessage, incoming: NormalizedMessage) -> NormalizedMessage:
    ids = list(
        dict.fromkeys(
            [canonical.id, *canonical.variant_ids, incoming.id, *incoming.variant_ids]
        )
    )
    return replace(
        canonical,
        source_completeness=(
            SourceCompleteness.CURATED_EXPORT.value
            if SourceCompleteness.CURATED_EXPORT.value
            in {canonical.source_completeness, incoming.source_completeness}
            else SourceCompleteness.COMPLETE.value
        ),
        variant_count=canonical.variant_count + incoming.variant_count,
        variant_ids=ids,
        parse_errors=list(dict.fromkeys([*canonical.parse_errors, *incoming.parse_errors])),
    )


def deduplicate_messages(
    records: Iterable[NormalizedMessage | Mapping[str, object]],
) -> DedupeReport:
    """Collapse duplicates using the approved priority order.

    Level 4 only collapses same-campaign messages inside 14 days, or near-equal
    subjects with matching body signals inside 1 day. Distinct creative remains
    distinct when those signals disagree.
    """

    incoming_records = sorted(
        (_normalized_copy(_coerce(value)) for value in records),
        key=lambda value: (value.received_datetime, value.id),
    )
    canonical: list[NormalizedMessage] = []
    source_index: dict[tuple[str, str, str | None, str], int] = {}
    message_index: dict[str, int] = {}
    hash_index: dict[tuple[str, str], list[int]] = {}
    campaign_index: dict[tuple[str, str], list[int]] = {}
    brand_index: dict[str, list[int]] = {}
    level_counts = {"level_1_source": 0, "level_2_message_id": 0, "level_3_content": 0, "level_4_campaign": 0}

    def append_unique(
        index_map: dict[tuple[str, str], list[int]],
        key: tuple[str, str],
        index: int,
    ) -> None:
        values = index_map.setdefault(key, [])
        if index not in values:
            values.append(index)

    def register(
        record: NormalizedMessage,
        index: int,
        *,
        add_brand_candidate: bool,
    ) -> None:
        source_index[record.source_identity_key] = index
        if record.message_id:
            message_index[record.message_id] = index
        append_unique(hash_index, (record.brand.casefold(), record.content_hash), index)
        if record.campaign_id:
            append_unique(campaign_index, (record.brand.casefold(), record.campaign_id), index)
        if add_brand_candidate:
            values = brand_index.setdefault(record.brand.casefold(), [])
            if index not in values:
                values.append(index)

    for record in incoming_records:
        match_index: int | None = None
        level: str | None = None

        source_key = record.source_identity_key
        if source_key in source_index:
            match_index = source_index[source_key]
            level = "level_1_source"

        if match_index is None and record.message_id and record.message_id in message_index:
            match_index = message_index[record.message_id]
            level = "level_2_message_id"

        if match_index is None:
            for candidate_index in hash_index.get((record.brand.casefold(), record.content_hash), []):
                if _within_days(canonical[candidate_index], record, 1):
                    match_index = candidate_index
                    level = "level_3_content"
                    break

        if match_index is None and record.campaign_id:
            for candidate_index in campaign_index.get(
                (record.brand.casefold(), record.campaign_id), []
            ):
                if _within_days(canonical[candidate_index], record, 14):
                    match_index = candidate_index
                    level = "level_4_campaign"
                    break

        if match_index is None:
            for candidate_index in reversed(brand_index.get(record.brand.casefold(), [])):
                candidate = canonical[candidate_index]
                if not _within_days(candidate, record, 1):
                    if candidate.received_datetime < record.received_datetime:
                        break
                    continue
                if _similarity(candidate.subject, record.subject) >= 0.90 and _body_signal_matches(
                    candidate, record
                ):
                    match_index = candidate_index
                    level = "level_4_campaign"
                    break

        if match_index is None:
            match_index = len(canonical)
            canonical.append(record)
            register(record, match_index, add_brand_candidate=True)
            continue

        canonical[match_index] = _merge(canonical[match_index], record)
        level_counts[level or "level_4_campaign"] += record.variant_count
        # Register every incoming identity against the retained canonical record,
        # but keep the chronological level-4 candidate list canonical-only. The
        # previous implementation re-appended an old canonical index at a recent
        # position, then incorrectly broke before examining newer candidates.
        register(record, match_index, add_brand_candidate=False)

    input_count = sum(record.variant_count for record in incoming_records)
    distinct_count = len(canonical)
    return DedupeReport(
        messages=canonical,
        input_count=input_count,
        distinct_count=distinct_count,
        variants_collapsed=max(0, input_count - distinct_count),
        level_counts=level_counts,
    )
