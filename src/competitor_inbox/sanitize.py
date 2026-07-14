"""Recipient-safe text and identifier sanitization."""

from __future__ import annotations

import hashlib
import html
import quopri
import re
import unicodedata
from collections.abc import Mapping
from email.utils import getaddresses
from typing import Iterable
from urllib.parse import unquote, urlsplit


EMAIL_RE = re.compile(
    r"(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]+@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}(?![\w.-])",
    re.IGNORECASE,
)
URL_RE = re.compile(r"(?:(?:https?://)|(?:www\.))[^\s<>'\"]+", re.IGNORECASE)
SCHEMELESS_URL_RE = re.compile(
    r"(?<![@\w.-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,63}(?::\d{2,5})?(?:[/\?][^\s<>'\"]*)?",
    re.IGNORECASE,
)
MAILTO_RE = re.compile(r"mailto:[^\s<>'\"]+", re.IGNORECASE)
MERGE_TAG_RE = re.compile(
    r"(?:\{\{[^{}]{1,200}\}\}|\{%[^%]{1,200}%\}|\*\|[^|]{1,100}\|\*|"
    r"\[%[^%]{1,200}%\]|\[\[[^\[\]]{1,200}\]\]|\$\{[^{}]{1,200}\}|"
    r"%%[^%]{1,100}%%|<%[^%]{1,200}%>|"
    r"\[(?:first_?name|last_?name|full_?name|fname|lname|customer_?name|"
    r"subscriber_?name|recipient_?name|email|email_?address)\])",
    re.IGNORECASE,
)
TOKEN_ASSIGNMENT_RE = re.compile(
    r"(?<![\w])(?:recipient(?:_id)?|subscriber(?:_id)?|customer(?:_id)?|contact(?:_id)?|"
    r"profile(?:_id)?|user(?:_id)?|member(?:_id)?|email(?:_id|_address)?|eid|uid|uuid|"
    r"token|auth|signature|sig|hash|key|code|mc_eid|klaviyo_id|k_id)"
    r"\s*(?:=3d|%(?:25)*3d|[=:])\s*[^\s&#<>'\"]{4,}",
    re.IGNORECASE,
)
_TRACKING_VALUE = r"[^\s&#<>'\"]+"
_OPAQUE_TRACKING_VALUE = r"[^\s&#<>'\"]{4,}"
ENCODED_SHORT_TRACKING_RE = re.compile(
    rf"(?<![\w])(?:r|k|m)\s*(?:=3d|%(?:25)*3d)\s*{_TRACKING_VALUE}",
    re.IGNORECASE,
)
QUERY_SHORT_TRACKING_RE = re.compile(
    rf"(?:[?&;]|%(?:25)*26)\s*(?:r|k|m)\s*=\s*{_OPAQUE_TRACKING_VALUE}",
    re.IGNORECASE,
)
SHORT_TRACKING_CLUSTER_RE = re.compile(
    rf"(?<![\w])(?:r|k|m)\s*=\s*{_TRACKING_VALUE}"
    rf"(?:\s*(?:[&;]|%(?:25)*26)\s*(?:r|k|m)\s*=\s*{_TRACKING_VALUE})+",
    re.IGNORECASE,
)
STANDALONE_SHORT_TRACKING_RE = re.compile(
    r"(?<![\w?&;])(?:r|k|m)\s*=\s*(?P<value>[A-Za-z0-9._~-]{12,})(?![\w.-])",
    re.IGNORECASE,
)
QUOTED_PRINTABLE_SOFT_BREAK_RE = re.compile(r"=\r?\n(?!\r?\n)")
QUOTED_PRINTABLE_RUN_RE = re.compile(r"(?:=[0-9A-F]{2}){2,}", re.IGNORECASE)
QUOTED_PRINTABLE_MARKUP_RE = re.compile(
    r"=(?:3D)*3C[^\r\n]{0,500}?=(?:3D)*3E", re.IGNORECASE
)
QUOTED_PRINTABLE_URL_RE = re.compile(
    r"https?=(?:3D)*3A=(?:3D)*2F=(?:3D)*2F", re.IGNORECASE
)
QUOTED_PRINTABLE_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]+=40"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}(?![\w.-])",
    re.IGNORECASE,
)
PERCENT_ENCODING_RE = re.compile(
    r"%(?:25)*(?:23|26|2f|3a|3c|3d|3e|3f|40|5b|5d|7b|7d)",
    re.IGNORECASE,
)
TRANSFER_TRAILING_EQUALS_RE = re.compile(r"(?m)[ \t]+=[ \t]*$")
RAW_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>\n]{0,500}>")
RAW_MARKUP_LINE_RE = re.compile(
    r"\s*(?:/?[A-Za-z][\w:-]*>|[A-Za-z][\w:-]*/[A-Za-z][\w:-]*>)\s*"
)
RAW_TRANSFER_LINE_RE = re.compile(r"\s*=\s*")
_REMOVED_TARGET_LABEL = (
    r"(?:address removed|link removed|tracking removed|redacted email|redacted url)"
)
MARKDOWN_PLACEHOLDER_LINK_RE = re.compile(
    rf"(?P<image>!)?\[(?P<label>[^\n]{{0,800}}?)\]\([ \t]*"
    rf"\[{_REMOVED_TARGET_LABEL}\][ \t]*\)?",
    re.IGNORECASE,
)
TRUNCATED_MARKDOWN_TARGET_RE = re.compile(
    r"(?P<image>!)?\[(?P<label>[^\n]{0,800}?)\]\([ \t]*\[[^\]\n]{0,80}$",
    re.IGNORECASE | re.MULTILINE,
)
ORPHAN_MARKDOWN_SUFFIX_RE = re.compile(r"\]\([ \t]*(?=$|\n)", re.MULTILINE)
WRAPPED_REMOVAL_TOKEN_RE = re.compile(
    rf"(?:<|\()\s*\[{_REMOVED_TARGET_LABEL}\]\s*(?:>|\))",
    re.IGNORECASE,
)
ORPHAN_OPEN_BEFORE_REMOVAL_RE = re.compile(
    rf"\[\s*(?=\[{_REMOVED_TARGET_LABEL}\])",
    re.IGNORECASE,
)
ORPHAN_TARGET_BEFORE_REMOVAL_RE = re.compile(
    rf"\]\(\s*(?=\[{_REMOVED_TARGET_LABEL}\](?!\s*\)))",
    re.IGNORECASE,
)
BRACKET_PERSONALIZATION_RE = re.compile(
    r"(?ix)\[(?!(?:address\s+removed|image\s+removed|personalization\s+removed|"
    r"redacted\s+(?:email|url)|"
    r"recipient\s+(?:address|identifier|name|phone)\s+removed|"
    r"recipient\s+account\s+value\s+removed|recipient\s+removed|"
    r"tracking\s+removed)\])[^\]\n]{0,80}\b(?:"
    r"first(?:\s+|_)name|last(?:\s+|_)name|full(?:\s+|_)name|recipient|"
    r"customer|subscriber|member|user|company|organisation|organization|"
    r"email|phone|account|street\s+address|mailing\s+address"
    r")\b[^\]\n]{0,80}\]"
)
SIMPLE_MARKDOWN_LABEL_RE = re.compile(r"\[([^\[\]\n]{1,500})\]")
EMPTY_WRAPPER_RE = re.compile(r"(?:<\s*>|\(\s*\)|\[\s*\])")
ORPHAN_WRAPPER_LINE_RE = re.compile(r"\s*[\[\]()<>|/\\-]+\s*")
INVISIBLE_FORMAT_RE = re.compile(
    "[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f"
    "\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\uffa0]"
)
INCOMPLETE_ENTITY_TAIL_RE = re.compile(
    r"(?:&#(?:x[0-9a-f]{0,8}|[0-9]{0,8})?)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
CSS_DECLARATION_RE = re.compile(
    r"(?i)(?:^|[;{]\s*)(?:background(?:-color|-image)?|border(?:-[\w-]+)?|"
    r"color|display|font(?:-[\w-]+)?|height|line-height|margin(?:-[\w-]+)?|"
    r"max-width|min-width|mso-[\w-]+|padding(?:-[\w-]+)?|text-[\w-]+|"
    r"vertical-align|width)\s*:\s*[^;{}]{1,300}(?=;|}|$)"
)
LONG_PAYLOAD_LINE_LIMIT = 1000
RESIDUAL_QUERY_ASSIGNMENT_RE = re.compile(
    r"(?:^|[?&;])\s*_?[A-Za-z][A-Za-z0-9_-]{0,40}\s*=\s*[^\s&;]+"
)
OPAQUE_RESIDUAL_TOKEN_RE = re.compile(
    r"(?<!\w)(?=[A-Za-z0-9._~-]{20,}(?!\w))"
    r"(?=[A-Za-z0-9._~-]*\d)"
    r"(?:(?=[A-Za-z0-9._~-]*[A-Z])|(?=(?:[A-Za-z0-9]*[-_.~]){2,}))"
    r"[A-Za-z0-9._~-]+"
)
RESIDUAL_TARGET_PREFIX_RE = re.compile(
    r"^\s*(?:[./?&;=_%-]|[A-Za-z0-9._~-]{20,}(?:[?&;=]|$))"
)
PERSONALIZED_ACCOUNT_VALUE_LINE_RE = re.compile(
    r"(?ix)^(?:"
    r"[^\n]*\byou(?:\s+(?:currently|now))?\s+have\s+\d[\d,.]*\s*"
    r"(?:points?|credits?|rewards?)\b[^\n]*"
    r"|[^\n]*\b(?:"
    r"your\b[^\n]{0,80}\b(?:points?|rewards?|loyalty|account|wallet|balance|credits?)\b"
    r"|(?:points?|rewards?|loyalty|account|wallet)\b[^\n]{0,50}"
    r"\b(?:balance|credit)\b"
    r"|(?:available|current)\s+(?:points?|rewards?|credit|balance)\b"
    r")[^\n]{0,120}(?:"
    r"[$€£]\s*\d[\d,.]*"
    r"|\b\d[\d,.]*\s*(?:points?|credits?)\b"
    r"|(?::|=|\bis\b)\s*\d[\d,.]*\b"
    r")[^\n]*"
    r"|\s*(?:your\s+)?(?:current\s+)?(?:vip\s+)?tier\s*(?::|=|\bis\b)\s*"
    r"[A-Za-z0-9][^\n]{0,80}"
    r")$"
)
GREETING_PERSONALIZATION_RE = re.compile(
    r"(?im)^(?P<prefix>[ \t]*(?:hi|hello|hey|dear|good[ \t]+"
    r"(?:morning|afternoon|evening))(?:(?:[ \t]*[,!:][ \t]*)|[ \t]+))"
    r"(?!\[)(?P<target>[^\n]{1,80})$"
)
LEADING_NAME_RE = re.compile(
    r"(?im)^\s*([A-Z][A-Za-zÀ-ÖØ-öø-ÿ' -]{1,60}),\s+(?=(?:your|you|we|thanks|thank|"
    r"here|this|just|a\s+quick|one\s+more)\b)"
)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SPACE_RE = re.compile(r"[ \t]+")
BLANK_LINES_RE = re.compile(r"\n{3,}")
DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)

_RECIPIENT_TERM_STOPLIST = {
    "all",
    "address",
    "beautiful",
    "customer",
    "customers",
    "email",
    "everyone",
    "folks",
    "friend",
    "friends",
    "gorgeous",
    "hello",
    "inbox",
    "info",
    "marketing",
    "member",
    "members",
    "news",
    "newsletter",
    "order",
    "orders",
    "reader",
    "readers",
    "shopper",
    "shoppers",
    "shop",
    "store",
    "subscriber",
    "subscribers",
    "support",
    "team",
    "there",
    "valued customer",
    "loyal customer",
    "community",
}

AUTHORITATIVE_ADDRESS_CONTEXT_RE = re.compile(
    r"(?i)^\s*(?:(?:shipping|delivery|billing|mailing|your)\s+address|"
    r"(?:ship(?:ping)?|deliver(?:y|ing)?)\s+to)\s*:?[ \t]*$"
)
AMBIGUOUS_ADDRESS_CONTEXT_RE = re.compile(
    r"(?i)^\s*(?:shipping|delivery)\s+details\s*:?[ \t]*$"
)
INLINE_AUTHORITATIVE_ADDRESS_RE = re.compile(
    r"(?i)^\s*(?:(?:shipping|delivery|billing|mailing|your)\s+address|"
    r"(?:ship(?:ping)?|deliver(?:y|ing)?)\s+to)\s*[:=\-\u2013\u2014]\s*"
    r"(?P<value>\S[^\n]{0,500})$"
)
INLINE_AMBIGUOUS_ADDRESS_RE = re.compile(
    r"(?i)^\s*(?:shipping|delivery)\s+details\s*[:=\-\u2013\u2014]\s*"
    r"(?P<value>\S[^\n]{0,500})$"
)
ADDRESS_SECTION_BOUNDARY_RE = re.compile(
    r"(?i)^\s*(?:order\s+summary|items?|payment(?:\s+details|\s+method)?|"
    r"shipping\s+method|billing\s+method|subtotal|total|discount|tax|"
    r"track\s+(?:order|package)|customer\s+support)\s*:?\s*$"
)
STREET_ADDRESS_RE = re.compile(
    r"(?i)^\s*\d{1,6}[A-Za-z]?\s+[A-Za-z0-9.'#& -]{1,90}\b(?:"
    r"street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|court|ct|"
    r"circle|cir|highway|hwy|parkway|pkwy|place|pl|terrace|ter|trail|trl|way"
    r")\.?\s*(?:#|apt\.?|apartment|suite|unit)?\s*[A-Za-z0-9-]*\s*$"
)
INLINE_STREET_ADDRESS_RE = re.compile(
    r"(?i)\b\d{1,6}[A-Za-z]?\s+[A-Za-z0-9.'#& -]{1,90}\b(?:"
    r"street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|court|ct|"
    r"circle|cir|highway|hwy|parkway|pkwy|place|pl|terrace|ter|trail|trl|way"
    r")\b"
)
POSTAL_ADDRESS_RE = re.compile(
    r"(?i)(?:\b\d{5}(?:-\d{4})?\b|\b[A-Z]\d[A-Z][ -]?\d[A-Z]\d\b|"
    r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b)"
)
PO_BOX_RE = re.compile(
    r"(?i)\b(?:P\.?[ \t]*O\.?[ \t]*Box|Post(?:al)?[ \t]+Office[ \t]+Box)"
    r"[ \t]+[A-Za-z0-9-]+\b"
)
NUMBERED_ADDRESS_LINE_RE = re.compile(
    r"(?i)^\s*\d{2,6}[A-Za-z]?(?:[ \t,.-]+[A-Za-z\u00c0-\u024f][\w\u00c0-\u024f'\u2019.-]*)+\s*$"
)
INTERNATIONAL_ADDRESS_WORD_RE = re.compile(
    r"(?i)\b(?:rue|via|calle|avenida|av(?:enue)?|chemin|quai|strasse|stra\u00dfe|"
    r"gasse|weg|platz|laan|straat|ulica|aleja|prospekt|ulitsa|rural[ \t]+route|"
    r"flat|unit|suite|apartment|building|floor)\b"
)
COUNTRY_LINE_RE = re.compile(
    r"(?i)^\s*(?:united[ \t]+states(?:[ \t]+of[ \t]+america)?|usa|canada|mexico|"
    r"united[ \t]+kingdom|great[ \t]+britain|england|scotland|wales|ireland|france|"
    r"germany|deutschland|spain|espa\u00f1a|italy|portugal|netherlands|belgium|"
    r"switzerland|austria|denmark|sweden|norway|finland|poland|czechia|greece|"
    r"australia|new[ \t]+zealand|japan|china|india|singapore|south[ \t]+korea|"
    r"united[ \t]+arab[ \t]+emirates|uae|israel|brazil|argentina|south[ \t]+africa)\s*$"
)
CONTEXT_NAME_RE = re.compile(
    r"(?im)^[ \t]*(?:recipient(?:[ \t]+name)?|full[ \t]+name|first[ \t]+name|"
    r"last[ \t]+name|account[ \t]+holder|customer(?:[ \t]+name)?|name)[ \t]*"
    r"(?:(?:[:=\-\u2013\u2014])[ \t]*(?!\[)[^\n]{1,100}"
    r"|:?[ \t]*\n[ \t]*(?!\[)[^\n]{1,100})[ \t]*$"
)
UNPUNCTUATED_CONTEXT_NAME_RE = re.compile(
    r"(?m)^[ \t]*(?i:customer[ \t]+name|account[ \t]+holder)[ \t]+"
    r"(?!\[)(?P<value>"
    r"(?:(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof)\.[ \t]+)?"
    r"[A-Z\u00c0-\u00d6\u00d8-\u00de][A-Za-z\u00c0-\u024f'\u2019.-]{1,40}"
    r"(?:[ \t]+(?:[A-Z]\.|[A-Z\u00c0-\u00d6\u00d8-\u00de]"
    r"[A-Za-z\u00c0-\u024f'\u2019.-]{1,40})){1,4})[ \t]*$"
)
_GENERIC_CONTEXT_NAME_WORDS = {
    "best",
    "benefit",
    "benefits",
    "example",
    "examples",
    "field",
    "fields",
    "format",
    "formatting",
    "guidance",
    "optional",
    "policy",
    "policies",
    "practice",
    "practices",
    "required",
}
CONTEXT_PHONE_RE = re.compile(
    r"(?imx)^[ \t]*(?:customer[ \t]+phone|recipient[ \t]+phone|"
    r"contact[ \t]+(?:phone|number)|phone(?:[ \t]+number)?|"
    r"mobile(?:[ \t]+number)?|telephone)[ \t]*"
    r"(?:(?:[:=\-\u2013\u2014]|\bis\b)[ \t]*|[ \t]+(?=\+?[\d(])|"
    r":?[ \t]*\n[ \t]*)"
    r"(?!\[)(?=(?:[^\d\n]*\d){7,18}[^\d\n]*$)"
    r"[+0-9() .\-/]{7,50}(?:[ \t]*(?:ext\.?|extension|x)[ \t]*\d{1,6})?[ \t]*$"
)
CONTEXT_IDENTIFIER_RE = re.compile(
    r"(?m)^[ \t]*(?i:order[ \t]*(?:number|no\.?|id|#)|"
    r"tracking[ \t]*(?:number|no\.?|id|#)|customer[ \t]*(?:number|no\.?|id)|"
    r"account[ \t]*(?:number|no\.?|id)|recipient[ \t]+id|subscriber[ \t]+id|"
    r"member[ \t]+id)[ \t]*"
    r"(?:(?:[:=\-\u2013\u2014]|(?i:\bis\b)|#)[ \t]*|[ \t]*\n[ \t]*|[ \t]*)"
    r"(?!\[)(?P<identifier>(?:(?=[^\n]*\d)[A-Za-z0-9 ._/#-]{1,80}|"
    r"[A-Z]{2,12}))[ \t]*$"
)
INLINE_DELIVERY_IDENTIFIER_RE = re.compile(
    r"(?ix)"
    r"(?P<prefix>\b(?:order|shipment|package)[ \t]*"
    r"(?:(?:number|no\.?|id)[ \t]*)?"
    r"(?:[-#:=\u2013\u2014][ \t]*)?(?:\([ \t]*)?)"
    r"(?!\[)"
    r"(?P<identifier>(?:"
    r"(?-i:(?:(?=[A-Z0-9_./-]{0,19}\d)[A-Z0-9_./-]{2,20}[ \t]"
    r"[A-Z0-9_./-]{2,20}|[A-Z0-9_./-]{2,20}[ \t]"
    r"(?=[A-Z0-9_./-]{0,19}\d)[A-Z0-9_./-]{2,20}))"
    r"|(?=[A-Za-z0-9_./-]{4,40}(?![A-Za-z0-9_./-]))"
    r"(?=[A-Za-z0-9_./-]*[A-Za-z])(?=[A-Za-z0-9_./-]*\d)"
    r"[A-Za-z0-9][A-Za-z0-9_./-]{2,38}[A-Za-z0-9]|\d{4,20}))"
    r"(?=[ \t]*(?:\)[ \t]*)?(?:[,:;|.!?\-\u2013\u2014][ \t]*)?(?:"
    r"(?:(?:has|have|had|is|was|were|will(?:[ \t]+be)?)[ \t]+)?"
    r"(?:(?:just|now|already)[ \t]+)?(?:(?:been|being)[ \t]+)?"
    r"(?:confirmed|received|processed|ready|fulfilled|"
    r"shipped|dispatched|despatched|delivered|cancelled|canceled|refunded|returned)"
    r"|(?:is|was|will[ \t]+be)[ \t]+(?:(?:now|already)[ \t]+)?"
    r"(?:on[ \t]+(?:the|its)[ \t]+way|"
    r"out[ \t]+for[ \t]+delivery|in[ \t]+transit|ready[ \t]+for[ \t]+pickup)"
    r"|(?:(?:has|have|had)[ \t]+)?(?:(?:just|now)[ \t]+)?arrived"
    r"|arriv(?:es|ing)"
    r"|(?:delivery|shipment|tracking)[ \t]+(?:update|status|confirmation|"
    r"details?|information))\b)"
)
DELIVERY_STATUS_IDENTIFIER_RE = re.compile(
    r"(?ix)"
    r"(?P<prefix>\b(?:order|shipment|package|tracking)[ \t]+(?:"
    r"(?:confirmation|status|reference)(?:[ \t]+(?:number|no\.?|id|reference))?|"
    r"confirmed|(?:has|is|was)[ \t]+(?:been[ \t]+)?confirmed|"
    r"(?:tracking[ \t]+)?(?:number|no\.?|id))"
    r"[ \t]*(?:[-#:=\u2013\u2014][ \t]*){0,3})"
    r"(?!\[)"
    r"(?P<identifier>(?:"
    r"(?=[A-Za-z0-9_./-]{4,40}(?![A-Za-z0-9_./-]))"
    r"(?=[A-Za-z0-9_./-]*[A-Za-z])(?=[A-Za-z0-9_./-]*\d)"
    r"[A-Za-z0-9][A-Za-z0-9_./-]{2,38}[A-Za-z0-9]|\d{4,20}))\b"
)
DELIVERY_CODE_IDENTIFIER_RE = re.compile(
    r"(?ix)"
    r"(?P<prefix>\b(?:order|shipment|package|tracking)"
    r"(?:[ \t]+(?:confirmation|status|reference|tracking))?[ \t]+)"
    r"code[ \t]*(?:[-#:=\u2013\u2014][ \t]*){0,3}"
    r"(?!\[)"
    r"(?P<identifier>(?:"
    r"(?=[A-Za-z0-9_./-]{4,40}(?![A-Za-z0-9_./-]))"
    r"(?=[A-Za-z0-9_./-]*[A-Za-z])(?=[A-Za-z0-9_./-]*\d)"
    r"[A-Za-z0-9][A-Za-z0-9_./-]{2,38}[A-Za-z0-9]|\d{4,20}))\b"
)


def stable_hash(value: str | bytes, *, prefix: str = "sha256") -> str:
    payload = value if isinstance(value, bytes) else value.encode("utf-8", "replace")
    return f"{prefix}:{hashlib.sha256(payload).hexdigest()}"


def normalize_recipient_terms(values: Iterable[str] | None) -> tuple[str, ...]:
    """Build a conservative deny-list without persisting the source headers."""

    output: set[str] = set()
    for value in values or ():
        candidate = unicodedata.normalize("NFKC", str(value or "")).strip()
        if not candidate:
            continue
        if "@" in candidate:
            candidate = candidate.split("@", 1)[0]
        # Plus-address tags commonly identify the subscribed brand, not the
        # mailbox owner. Adding that tag to the deny-list would erase valid
        # brand terms from every campaign sent to that alias.
        if "+" in candidate and not re.search(r"\s", candidate):
            candidate = candidate.split("+", 1)[0]
        candidate = re.sub(r"[._+\-]+", " ", candidate)
        candidate = SPACE_RE.sub(" ", candidate).strip(" '.,")
        pieces = [piece for piece in candidate.split() if len(piece) >= 3]
        for term in (candidate, *pieces):
            normalized = term.casefold().strip()
            if (
                3 <= len(normalized) <= 80
                and normalized not in _RECIPIENT_TERM_STOPLIST
                and not normalized.isdigit()
            ):
                output.add(normalized)
    return tuple(sorted(output, key=lambda term: (-len(term), term)))


def recipient_terms_from_headers(values: Iterable[str] | None) -> tuple[str, ...]:
    """Derive names and mailbox-local aliases from recipient header values."""

    candidates: list[str] = []
    for name, address in getaddresses(list(values or ())):
        if name:
            candidates.append(name)
        if address and "@" in address:
            local_part = address.split("@", 1)[0].split("+", 1)[0]
            candidates.append(local_part)
    return normalize_recipient_terms(candidates)


def _redact_recipient_terms(text: str, recipient_terms: Iterable[str] | None) -> str:
    for term in normalize_recipient_terms(recipient_terms):
        pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
        text = pattern.sub("[recipient removed]", text)
    return text


def _unescape_html_layers(value: str, *, max_layers: int = 3) -> str:
    """Decode nested character references without interpreting email HTML."""

    current = value
    for _ in range(max_layers):
        decoded = html.unescape(current)
        if decoded == current:
            break
        current = decoded
    return current


def _decode_encoding_layers(value: str, *, max_layers: int = 16) -> str:
    """Reach a bounded fixed point across nested transport encodings.

    Broken exports can alternate quoted-printable, percent encoding, and HTML
    entities. Decoding one family to completion before another can leave an
    unsafe inner URL or token untouched, so each bounded pass tries all three.
    """

    current = value
    for _ in range(max_layers):
        previous = current
        current = _decode_quoted_printable_layers(current, max_layers=1)
        if PERCENT_ENCODING_RE.search(current):
            current = unquote(current, errors="replace")
        current = html.unescape(current)
        current = unicodedata.normalize("NFKC", current)
        if current == previous:
            break
    return current


def _looks_quoted_printable(value: str) -> bool:
    """Identify transfer-encoded residue without treating ``x=20`` as payload."""

    return bool(
        QUOTED_PRINTABLE_SOFT_BREAK_RE.search(value)
        or QUOTED_PRINTABLE_RUN_RE.search(value)
        or QUOTED_PRINTABLE_MARKUP_RE.search(value)
        or QUOTED_PRINTABLE_URL_RE.search(value)
        or QUOTED_PRINTABLE_EMAIL_RE.search(value)
        or ENCODED_SHORT_TRACKING_RE.search(value)
    )


def _decode_quoted_printable_layers(value: str, *, max_layers: int = 6) -> str:
    """Decode malformed nested transfer payloads before URL and token removal."""

    current = value
    for _ in range(max_layers):
        if not _looks_quoted_printable(current):
            break
        decoded = quopri.decodestring(current.encode("utf-8", "replace")).decode(
            "utf-8", "replace"
        )
        if decoded == current:
            break
        current = decoded
    return current


def _opaque_short_tracking_value(value: str) -> bool:
    """Distinguish opaque recipient keys from ordinary equations like r=rate."""

    return (
        len(value) >= 20
        or any(character.isdigit() or character.isupper() for character in value)
        or sum(value.count(separator) for separator in "-_.~") >= 2
    )


def _redact_standalone_short_tracking(match: re.Match[str]) -> str:
    value = match.group("value")
    return "[tracking removed]" if _opaque_short_tracking_value(value) else match.group(0)


def _redact_inline_delivery_identifier(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}[recipient identifier removed]"


def _unpunctuated_context_name_is_private(match: re.Match[str]) -> bool:
    words = {
        word.casefold().strip(".'\u2019-")
        for word in SPACE_RE.split(match.group("value").strip())
    }
    return not bool(words & _GENERIC_CONTEXT_NAME_WORDS)


def _redact_unpunctuated_context_name(match: re.Match[str]) -> str:
    if _unpunctuated_context_name_is_private(match):
        return "[recipient name removed]"
    return match.group(0)


def _markdown_link_label(match: re.Match[str]) -> str:
    label = match.group("label").strip()
    return label or "[image removed]"


def _plain_markdown_label(match: re.Match[str]) -> str:
    label = match.group(1).strip()
    if label.casefold() in {
        "address removed",
        "image removed",
        "link removed",
        "personalization removed",
        "redacted email",
        "redacted url",
        "recipient address removed",
        "recipient account value removed",
        "recipient identifier removed",
        "recipient name removed",
        "recipient phone removed",
        "recipient removed",
        "tracking removed",
    }:
        return match.group(0)
    return label


def _greeting_target_parts(match: re.Match[str]) -> tuple[str, str]:
    raw_target = SPACE_RE.sub(" ", match.group("target")).strip()
    ending_match = re.search(r"(?:[,!:?]+|\.)[ \t]*$", raw_target)
    if ending_match:
        return raw_target[: ending_match.start()].rstrip(), ending_match.group(0).strip()
    return raw_target, ""


def _redact_greeting_target(match: re.Match[str]) -> str:
    """Remove likely rendered vocatives while preserving generic greetings."""

    target, ending = _greeting_target_parts(match)
    if target.casefold() in _RECIPIENT_TERM_STOPLIST:
        return match.group(0)
    return (
        match.group("prefix")
        + "[personalization removed]"
        + ending
    )


def _contains_rendered_greeting(value: str) -> bool:
    for match in GREETING_PERSONALIZATION_RE.finditer(value):
        target, _ = _greeting_target_parts(match)
        if target.casefold() not in _RECIPIENT_TERM_STOPLIST:
            return True
    return False


def _looks_like_address_payload(values: Iterable[str]) -> bool:
    """Require concrete address evidence for ambiguous detail labels."""

    lines = [SPACE_RE.sub(" ", str(value)).strip() for value in values if str(value).strip()]
    if not lines:
        return False
    joined = "\n".join(lines)
    components = [
        component.strip()
        for line in lines
        for component in re.split(r"\s*,\s*", line)
        if component.strip()
    ]
    has_country = any(COUNTRY_LINE_RE.fullmatch(component) for component in components)
    if (
        INLINE_STREET_ADDRESS_RE.search(joined)
        or POSTAL_ADDRESS_RE.search(joined)
        or PO_BOX_RE.search(joined)
        or any(NUMBERED_ADDRESS_LINE_RE.fullmatch(line) for line in lines)
    ):
        return True
    if INTERNATIONAL_ADDRESS_WORD_RE.search(joined) and (
        has_country or any(character.isdigit() for character in joined)
    ):
        return True
    # Country plus at least a recipient/locality pair covers digitless rural
    # and international formats without treating a one-line policy as PII.
    return has_country and len(components) >= 3


def _inline_address_is_private(value: str) -> bool:
    authoritative = INLINE_AUTHORITATIVE_ADDRESS_RE.fullmatch(value)
    if authoritative:
        return True
    ambiguous = INLINE_AMBIGUOUS_ADDRESS_RE.fullmatch(value)
    return bool(
        ambiguous
        and _looks_like_address_payload(
            re.split(r"\s*,\s*", ambiguous.group("value"))
        )
    )


def _address_block_spans(value: str) -> list[tuple[int, int]]:
    """Locate multi-line postal-address blocks without retaining their values."""

    lines = value.splitlines()
    spans: list[tuple[int, int]] = []

    # Address labels are authoritative. The word "details" alone is not,
    # because ordinary campaigns use it for shipping policy and delivery time.
    for context_index, line in enumerate(lines):
        authoritative = AUTHORITATIVE_ADDRESS_CONTEXT_RE.fullmatch(line)
        ambiguous = AMBIGUOUS_ADDRESS_CONTEXT_RE.fullmatch(line)
        if not authoritative and not ambiguous:
            continue
        cursor = context_index + 1
        while cursor < len(lines) and not lines[cursor].strip():
            cursor += 1
        end = cursor
        while end < min(len(lines), cursor + 10):
            if not lines[end].strip() or ADDRESS_SECTION_BOUNDARY_RE.fullmatch(lines[end]):
                break
            end += 1
        block = lines[cursor:end]
        if block and (authoritative or _looks_like_address_payload(block)):
            spans.append((context_index, end))

    for street_index, line in enumerate(lines):
        if not STREET_ADDRESS_RE.fullmatch(line):
            continue
        postal_index = next(
            (
                index
                for index in range(
                    street_index + 1, min(len(lines), street_index + 7)
                )
                if POSTAL_ADDRESS_RE.search(lines[index])
            ),
            None,
        )
        following_nonblank = [
            index
            for index in range(street_index + 1, min(len(lines), street_index + 7))
            if lines[index].strip()
        ]
        context_index = next(
            (
                index
                for index in range(street_index - 1, max(-1, street_index - 7), -1)
                if AUTHORITATIVE_ADDRESS_CONTEXT_RE.fullmatch(lines[index])
                or AMBIGUOUS_ADDRESS_CONTEXT_RE.fullmatch(lines[index])
            ),
            None,
        )
        if postal_index is None and not (
            (context_index is not None and following_nonblank)
            or len(following_nonblank) >= 2
        ):
            continue
        start = context_index if context_index is not None else max(0, street_index - 1)
        if context_index is None and start < street_index and not lines[start].strip():
            start = street_index

        anchor_index = postal_index if postal_index is not None else street_index
        end = anchor_index + 1
        while end < min(len(lines), street_index + 7) and lines[end].strip():
            end += 1
        spans.append((start, end))

    if not spans:
        return []
    spans.sort()
    merged = [spans[0]]
    for start, end in spans[1:]:
        prior_start, prior_end = merged[-1]
        if start <= prior_end:
            merged[-1] = (prior_start, max(prior_end, end))
        else:
            merged.append((start, end))
    return merged


def _redact_address_blocks(value: str) -> str:
    lines = [
        "[recipient address removed]"
        if _inline_address_is_private(line)
        else line
        for line in value.splitlines()
    ]
    normalized = "\n".join(lines)
    spans = _address_block_spans(normalized)
    if not spans:
        return normalized
    output: list[str] = []
    cursor = 0
    for start, end in spans:
        output.extend(lines[cursor:start])
        output.append("[recipient address removed]")
        cursor = end
    output.extend(lines[cursor:])
    return "\n".join(output)


def _looks_css_payload_line(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    declarations = CSS_DECLARATION_RE.findall(stripped)
    return len(declarations) >= 2 or bool(
        re.match(r"(?i)^(?:@media\b|mso-[\w-]+\s*:|\.[\w-]+\s*\{)", stripped)
    )


def _is_residual_tracking_line(value: str) -> bool:
    """Detect link-target tails left behind by malformed transfer encodings."""

    text = value.strip()
    if not text:
        return False
    query_assignments = len(RESIDUAL_QUERY_ASSIGNMENT_RE.findall(text))
    opaque_matches = list(OPAQUE_RESIDUAL_TOKEN_RE.finditer(text))
    has_tracking_placeholder = "[tracking removed]" in text.casefold()
    percent_octets = len(re.findall(r"%[0-9A-F]{2}", text, re.IGNORECASE))
    target_prefix = bool(RESIDUAL_TARGET_PREFIX_RE.search(text))
    alphanumeric_count = sum(character.isalnum() for character in text)
    opaque_count = sum(len(match.group()) for match in opaque_matches)
    opaque_dominant = bool(opaque_matches) and opaque_count >= max(
        20, alphanumeric_count // 2
    )

    return bool(
        query_assignments >= 2
        or (
            has_tracking_placeholder
            and (
                query_assignments
                or opaque_matches
                or (target_prefix and len(text) > 25)
            )
        )
        or (
            target_prefix
            and (query_assignments or opaque_matches or percent_octets >= 2)
        )
        or (opaque_dominant and any(character in text for character in "?&="))
        or (opaque_dominant and len(text) < 100)
    )


def _strip_payload_lines(value: str) -> str:
    """Remove non-content markup, tracking tails, and recipient account state."""

    output: list[str] = []
    for line in value.splitlines():
        if (
            len(line) >= LONG_PAYLOAD_LINE_LIMIT
            or RAW_MARKUP_LINE_RE.fullmatch(line)
            or RAW_TRANSFER_LINE_RE.fullmatch(line)
            or ORPHAN_WRAPPER_LINE_RE.fullmatch(line)
            or _looks_css_payload_line(line)
            or _is_residual_tracking_line(line)
        ):
            continue
        if PERSONALIZED_ACCOUNT_VALUE_LINE_RE.search(line):
            line = "[recipient account value removed]"
        output.append(line)
    return "\n".join(output)


def _sanitize_residual_layers(
    value: str,
    *,
    strip_schemeless_urls: bool,
    max_passes: int = 6,
) -> str:
    """Normalize wrappers until no cleanup step exposes a new unsafe layer."""

    text = value
    for _ in range(max_passes):
        previous = text
        text = URL_RE.sub("[link removed]", text)
        if strip_schemeless_urls:
            text = SCHEMELESS_URL_RE.sub("[link removed]", text)
        text = CONTEXT_NAME_RE.sub("[recipient name removed]", text)
        text = UNPUNCTUATED_CONTEXT_NAME_RE.sub(
            _redact_unpunctuated_context_name,
            text,
        )
        text = CONTEXT_PHONE_RE.sub("[recipient phone removed]", text)
        text = CONTEXT_IDENTIFIER_RE.sub("[recipient identifier removed]", text)
        text = INLINE_DELIVERY_IDENTIFIER_RE.sub(
            _redact_inline_delivery_identifier,
            text,
        )
        text = DELIVERY_STATUS_IDENTIFIER_RE.sub(
            _redact_inline_delivery_identifier,
            text,
        )
        text = DELIVERY_CODE_IDENTIFIER_RE.sub(
            _redact_inline_delivery_identifier,
            text,
        )
        text = BRACKET_PERSONALIZATION_RE.sub("[personalization removed]", text)
        text = MARKDOWN_PLACEHOLDER_LINK_RE.sub(_markdown_link_label, text)
        text = TRUNCATED_MARKDOWN_TARGET_RE.sub(_markdown_link_label, text)
        text = WRAPPED_REMOVAL_TOKEN_RE.sub("", text)
        text = ORPHAN_OPEN_BEFORE_REMOVAL_RE.sub("", text)
        text = ORPHAN_TARGET_BEFORE_REMOVAL_RE.sub(" ", text)
        text = ORPHAN_MARKDOWN_SUFFIX_RE.sub("", text)
        text = SIMPLE_MARKDOWN_LABEL_RE.sub(_plain_markdown_label, text)
        text = EMPTY_WRAPPER_RE.sub("", text)
        text = _redact_address_blocks(text)
        text = GREETING_PERSONALIZATION_RE.sub(_redact_greeting_target, text)
        text = LEADING_NAME_RE.sub("[personalization removed], ", text)
        text = _strip_payload_lines(text)
        if text == previous:
            break
    return text


def sanitize_text(
    value: str | None,
    *,
    max_chars: int | None = None,
    recipient_terms: Iterable[str] | None = None,
    strip_schemeless_urls: bool = True,
) -> str:
    """Remove direct identifiers, URLs, merge tags, and control characters."""

    if not value:
        return ""
    source_text = str(value)
    had_transfer_payload = _looks_quoted_printable(source_text)
    # Encoded one-letter query keys are unambiguous tracking residue. Remove
    # them before transfer decoding makes the value look like an ordinary
    # equation.
    text = ENCODED_SHORT_TRACKING_RE.sub("[tracking removed]", source_text)
    text = _decode_encoding_layers(text)
    text = INVISIBLE_FORMAT_RE.sub("", text)
    text = INCOMPLETE_ENTITY_TAIL_RE.sub("", text)
    text = CONTROL_RE.sub(" ", text)
    # Remove whole links before query-token substitutions can fragment them
    # into residual paths and percent-encoded recipient values.
    text = MAILTO_RE.sub("[address removed]", text)
    text = URL_RE.sub("[link removed]", text)
    if strip_schemeless_urls:
        text = SCHEMELESS_URL_RE.sub("[link removed]", text)
    text = EMAIL_RE.sub("[address removed]", text)
    text = RAW_HTML_TAG_RE.sub(" ", text)
    text = CONTEXT_NAME_RE.sub("[recipient name removed]", text)
    text = UNPUNCTUATED_CONTEXT_NAME_RE.sub(
        _redact_unpunctuated_context_name,
        text,
    )
    text = CONTEXT_PHONE_RE.sub("[recipient phone removed]", text)
    text = CONTEXT_IDENTIFIER_RE.sub("[recipient identifier removed]", text)
    text = INLINE_DELIVERY_IDENTIFIER_RE.sub(
        _redact_inline_delivery_identifier,
        text,
    )
    text = DELIVERY_STATUS_IDENTIFIER_RE.sub(
        _redact_inline_delivery_identifier,
        text,
    )
    text = DELIVERY_CODE_IDENTIFIER_RE.sub(
        _redact_inline_delivery_identifier,
        text,
    )
    text = MERGE_TAG_RE.sub("[personalization removed]", text)
    text = TOKEN_ASSIGNMENT_RE.sub("[personalization removed]", text)
    text = ENCODED_SHORT_TRACKING_RE.sub("[tracking removed]", text)
    text = SHORT_TRACKING_CLUSTER_RE.sub("[tracking removed]", text)
    text = QUERY_SHORT_TRACKING_RE.sub("[tracking removed]", text)
    text = STANDALONE_SHORT_TRACKING_RE.sub(_redact_standalone_short_tracking, text)
    text = _redact_address_blocks(text)
    text = GREETING_PERSONALIZATION_RE.sub(_redact_greeting_target, text)
    text = LEADING_NAME_RE.sub("[personalization removed], ", text)
    text = _redact_recipient_terms(text, recipient_terms)
    if had_transfer_payload:
        text = TRANSFER_TRAILING_EQUALS_RE.sub("", text)
    text = _sanitize_residual_layers(
        text,
        strip_schemeless_urls=strip_schemeless_urls,
    )
    text = "\n".join(SPACE_RE.sub(" ", line).strip() for line in text.splitlines())
    text = BLANK_LINES_RE.sub("\n\n", text).strip()
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars].rstrip()
        text = _sanitize_residual_layers(
            text,
            strip_schemeless_urls=strip_schemeless_urls,
        ).strip()
    return text


def sanitize_domain(value: str | None) -> str:
    """Return a normalized host only when it is syntactically safe."""

    if not value:
        return ""
    candidate = value.strip().lower().rstrip(".")
    if "://" in candidate:
        candidate = (urlsplit(candidate).hostname or "").lower()
    if candidate.startswith("www."):
        candidate = candidate[4:]
    return candidate if DOMAIN_RE.fullmatch(candidate) else ""


def sanitize_brand(value: str | None, *, fallback_domain: str = "") -> str:
    candidate = sanitize_text(value, max_chars=120, strip_schemeless_urls=False)
    candidate = re.sub(r"\s+(?:newsletter|news|support|team|store)$", "", candidate, flags=re.I)
    candidate = re.sub(r"[^\w&+' .-]", " ", candidate, flags=re.UNICODE)
    candidate = SPACE_RE.sub(" ", candidate).strip(" .-_'")
    if candidate:
        return candidate
    host = sanitize_domain(fallback_domain)
    if host:
        label = host.split(".")[-2] if host.count(".") else host
        return label.replace("-", " ").title()
    return "Unknown Brand"


def canonical_brand(
    sender_name: str | None,
    sender_domain: str | None,
    aliases: Mapping[str, str] | None = None,
) -> str:
    """Resolve configured aliases before deriving a display-safe brand."""

    aliases = aliases or {}
    domain = sanitize_domain(sender_domain)
    clean_name = sanitize_brand(sender_name, fallback_domain=domain)
    normalized_aliases = {str(key).strip().lower(): str(value) for key, value in aliases.items()}
    for key in (domain, clean_name.lower()):
        if key and key in normalized_aliases:
            return sanitize_brand(normalized_aliases[key], fallback_domain=domain)
    return clean_name


def sanitize_identifier(value: str | None, *, namespace: str) -> str | None:
    """Hash opaque source identifiers so they cannot expose mailbox metadata."""

    if not value:
        return None
    normalized = " ".join(str(value).split()).strip().lower()
    if not normalized:
        return None
    digest = hashlib.sha256(f"{namespace}\0{normalized}".encode()).hexdigest()
    return f"{namespace}:{digest}"


def contains_direct_identifier(
    value: str,
    *,
    recipient_terms: Iterable[str] | None = None,
) -> bool:
    source_text = str(value or "")
    had_transfer_payload = _looks_quoted_printable(source_text)
    text = _decode_encoding_layers(source_text)
    if any(
        pattern.search(text)
        for pattern in (
            EMAIL_RE,
            URL_RE,
            SCHEMELESS_URL_RE,
            MAILTO_RE,
            MERGE_TAG_RE,
            TOKEN_ASSIGNMENT_RE,
            ENCODED_SHORT_TRACKING_RE,
            QUERY_SHORT_TRACKING_RE,
            SHORT_TRACKING_CLUSTER_RE,
            RAW_HTML_TAG_RE,
            MARKDOWN_PLACEHOLDER_LINK_RE,
            TRUNCATED_MARKDOWN_TARGET_RE,
            ORPHAN_OPEN_BEFORE_REMOVAL_RE,
            ORPHAN_TARGET_BEFORE_REMOVAL_RE,
            ORPHAN_MARKDOWN_SUFFIX_RE,
            BRACKET_PERSONALIZATION_RE,
            CONTEXT_NAME_RE,
            CONTEXT_PHONE_RE,
            CONTEXT_IDENTIFIER_RE,
            INLINE_DELIVERY_IDENTIFIER_RE,
            DELIVERY_STATUS_IDENTIFIER_RE,
            DELIVERY_CODE_IDENTIFIER_RE,
            PERSONALIZED_ACCOUNT_VALUE_LINE_RE,
            LEADING_NAME_RE,
        )
    ):
        return True
    if _contains_rendered_greeting(text):
        return True
    if any(
        _unpunctuated_context_name_is_private(match)
        for match in UNPUNCTUATED_CONTEXT_NAME_RE.finditer(text)
    ):
        return True
    if had_transfer_payload or _looks_quoted_printable(text):
        return True
    if INVISIBLE_FORMAT_RE.search(text):
        return True
    if INCOMPLETE_ENTITY_TAIL_RE.search(text):
        return True
    if _address_block_spans(text):
        return True
    if any(_inline_address_is_private(line) for line in text.splitlines()):
        return True
    if any(
        len(line) >= LONG_PAYLOAD_LINE_LIMIT
        or RAW_MARKUP_LINE_RE.fullmatch(line)
        or RAW_TRANSFER_LINE_RE.fullmatch(line)
        or ORPHAN_WRAPPER_LINE_RE.fullmatch(line)
        or _looks_css_payload_line(line)
        or PERSONALIZED_ACCOUNT_VALUE_LINE_RE.search(line)
        or _is_residual_tracking_line(line)
        for line in text.splitlines()
    ):
        return True
    if any(
        _opaque_short_tracking_value(match.group("value"))
        for match in STANDALONE_SHORT_TRACKING_RE.finditer(text)
    ):
        return True
    folded = text.casefold()
    return any(
        re.search(rf"(?<!\w){re.escape(term)}(?!\w)", folded)
        for term in normalize_recipient_terms(recipient_terms)
    )


def assert_recipient_safe(
    value: str,
    *,
    recipient_terms: Iterable[str] | None = None,
) -> None:
    """Fail closed when a persistence or export boundary still has an identifier."""

    if contains_direct_identifier(value, recipient_terms=recipient_terms):
        raise ValueError("recipient or personalization identifier survived sanitization")
