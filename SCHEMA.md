# Normalized Message Schema

`master.json` is a versioned private document under `<DATA_ROOT>`. It contains
sanitized visible text and provider-safe hashes. It never contains recipients,
credentials, personalized URLs, raw HTML, attachments, or remote assets.

Angle-bracket values below are public schema examples.

## Document envelope

```json
{
  "schema_version": "1.0.0",
  "generated_at": "<ISO_8601_TIMESTAMP>",
  "record_count": 1,
  "metadata": {
    "source_window": {
      "start": "<ISO_8601_TIMESTAMP>",
      "end": "<ISO_8601_TIMESTAMP>"
    },
    "timezone": "America/Phoenix",
    "source_mode": "imap"
  },
  "records": []
}
```

## Canonical record

### Identity and provenance

| Field | Type | Description |
|---|---|---|
| `id` | string | Stable SHA-256 record identity |
| `schema_version` | string | Record schema version |
| `source_type` | string | `imap`, `mbox`, or `synthetic` |
| `source_uid` | string | Provider UID inside the private store |
| `uidvalidity` | string or null | IMAP UID namespace |
| `mailbox` | string | Sanitized logical mailbox label |
| `message_id` | string or null | Hash of normalized RFC Message-ID |
| `list_id` | string or null | Hash of normalized List-ID |
| `campaign_id` | string or null | Hash of a supported campaign header |
| `content_hash` | string | Hash of canonical sanitized content |
| `variant_count` | integer | Number of collapsed delivery variants |
| `variant_ids` | array[string] | Stable private IDs in the cluster |

Provider identities remain private. Public exports use aggregate counts only.

### Brand and dates

| Field | Type | Description |
|---|---|---|
| `brand` | string | Canonical display brand |
| `sender_name` | string | Sanitized sender display name |
| `sender_domain` | string | Canonical sender domain |
| `canonical_received_at` | string | IMAP `INTERNALDATE` or mbox receipt timestamp |
| `received_at_source` | string | Provenance such as `imap_internaldate`, `received_header`, or `mbox_separator` |
| `received_at_trusted` | boolean | Whether the timestamp may satisfy coverage and hook-day gates |
| `header_date` | string or null | Parsed RFC822 Date header |
| `date_skew_days` | number or null | Absolute difference between receipt and header date |

A `header_date_skew_over_7_days` code is added to `parse_errors` when the 2
timestamps differ by more than 7 days.

For mbox imports, `X-Delivery-Time`/`Delivery-Date`, `Received`/`X-Received`,
and the mbox separator are trusted in that order. A message `Date` header or
the mbox file modification time is retained only as an untrusted library
fallback. Records with an untrusted fallback still count in the census, but
they appear under `Unknown receipt date` in monthly aggregates. They cannot
create first/last-observed spans, observed days, annual or prior-season
coverage, or hook eligibility. Legacy mbox records that do not declare these
fields are treated as untrusted.

### Sanitized content

| Field | Type | Description |
|---|---|---|
| `subject` | string | Decoded and redacted subject |
| `preheader` | string | Decoded and redacted preheader |
| `visible_text` | string | Redacted visible text, never executable HTML |
| `redaction_status` | string | `sanitized` for persisted records |

The sanitizer removes addresses, URLs, mailto values, merge tags, assigned
recipient tokens, and control characters. HTML is parsed locally for visible
text. Images, scripts, styles, attachments, and network requests are ignored.

### Scope

| Field | Type | Description |
|---|---|---|
| `scope` | enum | `broadcast`, `lifecycle`, or `uncertain` |
| `scope_reason` | string | Deterministic evidence code |
| `scope_confidence` | number | Value from 0 through 1 |

Lifecycle covers welcome, cart, checkout, browse, post-purchase,
transactional, shipping, account, back-in-stock, replenishment, winback,
loyalty, and referral messages. Lifecycle and uncertain records remain
browsable but are excluded from broadcast metrics.

### Analysis

| Field | Type | Description |
|---|---|---|
| `intent` | string or null | Dominant intent label |
| `intent_source` | string or null | `deterministic`, `ai`, or `manual` |
| `intent_confidence` | number or null | Value from 0 through 1 |
| `classification_model` | string or null | Model identifier when optional AI ran |
| `offer_candidates` | array[object] | All supported offer candidates |
| `primary_offer` | object or null | Selected offer candidate |
| `seasonal` | boolean or null | Explicit supported seasonality |
| `occasion` | string or null | Recognized calendar occasion |

Intent labels are `Promotion/offer`, `Ingredient/education`, `Founder/brand
story`, `New product launch`, `Social proof/UGC`, `Upsell`, `Cross-sell`,
`Featured products`, and `Lifestyle/seasonal`.

Each offer candidate contains `type`, `depth`, `unit`, `source`, `evidence`,
`confidence`, and `deterministic`. A numeric depth is valid only when the
evidence occurs in the subject, preheader, or sanitized visible text and
`deterministic` is true. Optional AI can add an unquantified offer type but
cannot create a numeric depth.

Seasonality requires a recognized occasion or explicit commercial seasonal
language inside the relevant calendar window. Date or lifestyle aesthetics
alone never set `seasonal` to true.

### Processing

| Field | Type | Description |
|---|---|---|
| `parse_status` | string | `parsed` for canonical records |
| `parse_errors` | array[string] | Content-free error codes |

Parse failures live in the private failure ledger with source type, private
source UID, error code, and the best available brand assignment.

## Four-quadrant derivation

| Quadrant | Rule |
|---|---|
| Evergreen content | No supported offer and non-seasonal |
| Everyday promotion | Supported offer and non-seasonal |
| Seasonal promotion | Supported offer and seasonal |
| Seasonal content | No supported offer and seasonal |

## Public export contract

Public aggregates and redacted screenshots may include brand names, counts,
percentages, date windows, coverage labels, and derived classifications. They
exclude provider IDs, inbox identity, recipient information, sender addresses,
message bodies, personalized links, raw evidence, raw messages, and HTML.
