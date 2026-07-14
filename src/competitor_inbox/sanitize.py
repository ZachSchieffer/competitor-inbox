"""Recipient-safe text and identifier sanitization."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from collections.abc import Mapping
from email.utils import getaddresses
from typing import Iterable
from urllib.parse import urlsplit


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
    r"\s*[=:]\s*[^\s&#<>'\"]{4,}",
    re.IGNORECASE,
)
GREETING_PERSONALIZATION_RE = re.compile(
    r"(?im)^(\s*(?:hi|hello|hey|dear|good\s+(?:morning|afternoon|evening))\s*[,!:]?\s+)"
    r"(?!\[)([^,\n!]{1,60})(?=\s*[,!])"
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
    "address",
    "customer",
    "email",
    "everyone",
    "friend",
    "hello",
    "inbox",
    "info",
    "marketing",
    "member",
    "news",
    "newsletter",
    "order",
    "orders",
    "reader",
    "shop",
    "store",
    "subscriber",
    "support",
    "team",
    "there",
}


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
            candidates.append(address.split("@", 1)[0])
    return normalize_recipient_terms(candidates)


def _redact_recipient_terms(text: str, recipient_terms: Iterable[str] | None) -> str:
    for term in normalize_recipient_terms(recipient_terms):
        pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
        text = pattern.sub("[recipient removed]", text)
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
    text = unicodedata.normalize("NFKC", html.unescape(str(value)))
    text = CONTROL_RE.sub(" ", text)
    text = MAILTO_RE.sub("[address removed]", text)
    text = EMAIL_RE.sub("[address removed]", text)
    text = URL_RE.sub("[link removed]", text)
    if strip_schemeless_urls:
        text = SCHEMELESS_URL_RE.sub("[link removed]", text)
    text = MERGE_TAG_RE.sub("[personalization removed]", text)
    text = TOKEN_ASSIGNMENT_RE.sub("[personalization removed]", text)
    text = GREETING_PERSONALIZATION_RE.sub(r"\1[personalization removed]", text)
    text = LEADING_NAME_RE.sub("[personalization removed], ", text)
    text = _redact_recipient_terms(text, recipient_terms)
    text = "\n".join(SPACE_RE.sub(" ", line).strip() for line in text.splitlines())
    text = BLANK_LINES_RE.sub("\n\n", text).strip()
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars].rstrip()
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
    text = str(value or "")
    if any(
        pattern.search(text)
        for pattern in (
            EMAIL_RE,
            URL_RE,
            SCHEMELESS_URL_RE,
            MAILTO_RE,
            MERGE_TAG_RE,
            TOKEN_ASSIGNMENT_RE,
            GREETING_PERSONALIZATION_RE,
            LEADING_NAME_RE,
        )
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
