"""Deterministic message analysis with an optional, privacy-safe AI pass.

The deterministic layer owns every measurable claim. In particular, a numeric
offer is only emitted when the source text contains matching evidence. The AI
layer may refine intent or supply an unquantified offer type, but it cannot
invent a discount depth.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Protocol

from .sanitize import assert_recipient_safe, sanitize_text as sanitize_recipient_text
from .schema import NormalizedMessage


INTENT_TYPES = (
    "Promotion/offer",
    "Ingredient/education",
    "Founder/brand story",
    "New product launch",
    "Social proof/UGC",
    "Upsell",
    "Cross-sell",
    "Featured products",
    "Lifestyle/seasonal",
)

OFFER_TYPES = ("%off", "$off", "free_shipping", "bogo", "gift", "bundle", "other")

QUADRANTS = (
    "Evergreen content",
    "Everyday promotion",
    "Seasonal promotion",
    "Seasonal content",
)

_EMAIL_RE = re.compile(r"(?i)\b[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\]\[\"']+")
_TOKEN_RE = re.compile(
    r"(?i)\b(?:token|recipient|subscriber|customer|contact|profile|uid|uuid|email_id)\s*[:=]\s*"
    r"[a-z0-9._~+/=-]{8,}"
)
_SPACE_RE = re.compile(r"[ \t\f\v]+")

_PERCENT_PATTERNS = (
    re.compile(
        r"(?i)(?:=C2=A0|=A0|%C2%A0|%A0)(\d{1,2}(?:\.\d+)?)\s*%\s*"
        r"(?:off|discount|savings?)\b"
    ),
    re.compile(r"(?i)\b(\d{1,2}(?:\.\d+)?)\s*%\s*(?:off|discount|savings?)\b"),
    re.compile(
        r"(?i)\bsave\s+(?:an?\s+|up\s+to\s+|another\s+)?"
        r"(\d{1,2}(?:\.\d+)?)\s*%(?!\s*(?:more|less|higher|lower|water|energy|time|"
        r"protein|hydration|market\s+share|space|weight|fuel|data|emissions?)\b)"
    ),
    re.compile(
        r"(?i)\b(?:take|get|enjoy|extra|score|claim)\s+(?:an?\s+|up\s+to\s+|another\s+)?"
        r"(\d{1,2}(?:\.\d+)?)\s*%(?:\s*(?:off|discount|savings?)\b|"
        r"(?=\s*(?:$|[!,.?:;\n])))"
    ),
)
_DOLLAR_PATTERNS = (
    re.compile(r"(?i)\$\s?(\d{1,4}(?:\.\d{1,2})?)\s*(?:off|discount)\b"),
    re.compile(r"(?i)\bsave\s+\$\s?(\d{1,4}(?:\.\d{1,2})?)\b"),
)
_NON_NUMERIC_OFFER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "bogo",
        re.compile(
            r"(?i)\b(?:bogo|b1g1|buy\s+(?:one|1|\d+)\s*,?\s*get\s+(?:one|1|\d+)|"
            r"\d+\s+for\s+1)\b"
        ),
    ),
    (
        "gift",
        re.compile(
            r"(?i)\b(?:free\s+gift(?!\s+(?:ideas?|guide|edit))|complimentary\s+gift|"
            r"gift\s+with\s+(?:a\s+)?(?:qualifying\s+)?"
            r"purchase|gwp|(?:get|receive|choose|claim|enjoy)\s+(?:a\s+)?(?:free|complimentary)\s+"
            r"(?:gift|[a-z0-9][a-z0-9 '&-]{0,35})\s+(?:with|when)\s+(?:a\s+)?(?:qualifying\s+)?"
            r"(?:order|purchase|spend)|free\s+(?!ship(?:ping)?\b|delivery\b)[a-z0-9][a-z0-9 '&-]{0,35}\s+"
            r"(?:with\s+(?:your\s+)?(?:order|purchase)|when\s+you\s+(?:order|spend))|"
            r"(?:gift|free\s+(?!ship(?:ping)?\b|delivery\b)[a-z0-9][a-z0-9 '&-]{0,30})"
            r".{0,90}\b(?:qualifying|spend|order|purchase)|"
            r"(?:qualifying|spend|order|purchase).{0,90}\b(?:free|complimentary)?\s*gift|"
            r"(?:free|complimentary)\s+(?!ship(?:ping)?\b|delivery\b)"
            r"[a-z0-9][a-z0-9 '&-]{0,35}\s+(?:on|with)\s+orders?\s+"
            r"(?:over|above|of)?\s*\$?\d[\d,.]*|(?:the\s+)?gift\s+is\s+on\s+us)\b"
        ),
    ),
    (
        "bundle",
        re.compile(
            r"(?i)\b(?:bundle\s+(?:and|&)\s+save|bundle\s+(?:deal|discount|savings?)|"
            r"save\s+(?:on|with)\s+(?:the\s+|a\s+)?bundle|discounted\s+bundle|"
            r"stack\s+and\s+save|build\s+your\s+(?:own\s+)?bundle\s+and\s+save)\b"
        ),
    ),
    (
        "free_shipping",
        re.compile(r"(?i)\b(?:free\s+ship(?:ping)?|ships?\s+free|free\s+delivery)\b"),
    ),
)
_GENERIC_SALE_RE = re.compile(
    r"(?i)(?:\b(?:sale|discounts?|deals?|markdowns?|clearance)\b|"
    r"\bpromo\s+code\b|\bcoupon\b|\b(?:special|limited[- ]time)\s+offer\b|"
    r"\bearly\s+access\b|\brisk[- ]free\s+(?:trial|try)\b|"
    r"\b(?:try|test)\s+(?:it\s+)?risk[- ]free\b|"
    r"\b(?:\d{1,3}[- ]day\s+)?money[- ]back\s+guarantee\b|\bsavings\b|"
    r"\bsave\s+(?:on|when|with|today|now|big|more)\b)"
)

_ACTIVE_FREE_ITEM_RE = re.compile(
    r"(?i)(?:\b(?:ends?|ending|last\s+chance|limited(?:[- ]time)?|today|tonight)\b"
    r"[^.\n]{0,60}\b(?:free|complimentary)\s+"
    r"(?!ship(?:ping)?\b|delivery\b|returns?\b|trial\b)[a-z0-9][a-z0-9 '&-]{0,40}|"
    r"\b(?:free|complimentary)\s+"
    r"(?!ship(?:ping)?\b|delivery\b|returns?\b|trial\b)[a-z0-9][a-z0-9 '&-]{0,40}"
    r"[^.\n]{0,60}\b(?:ends?|ending|today|tonight|limited)\b)"
)

_LIFECYCLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "welcome",
        re.compile(
            r"(?im)(?:^\s*welcome\s*:\s*|^\s*welcome\s+(?:aboard|series)\b|"
            r"^\s*welcome\s+to\s+(?!(?:spring|summer|fall|autumn|winter)\b)[^\n]{1,100}|"
            r"\bthanks?\s+for\s+"
            r"(?:joining|signing\s+up|subscribing)|glad\s+you(?:['’]re|\s+are)\s+here|"
            r"new\s+subscriber|(?:your|this)\s+welcome\s+(?:offer|code|discount)|"
            r"(?:your|this)\s+first[- ]order\s+(?:offer|code|discount)\s+"
            r"(?:is\s+)?(?:ready|active|inside|waiting|expires?))\b"
        ),
    ),
    (
        "cart",
        re.compile(r"(?i)\b(?:(?:abandon(?:ed)?|left).{0,30}\bcart\b|cart\s+reminder)"),
    ),
    (
        "checkout",
        re.compile(r"(?i)\b(?:complete|finish|resume).{0,25}\bcheckout\b"),
    ),
    (
        "browse",
        re.compile(r"(?i)\b(?:still\s+thinking|caught\s+your\s+eye|viewed\s+items?)\b"),
    ),
    (
        "post-purchase",
        re.compile(r"(?i)\b(?:thanks?|thank\s+you)\s+for.{0,20}\b(?:order|purchase)\b"),
    ),
    (
        "transactional",
        re.compile(r"(?i)\b(?:order|payment|refund).{0,20}\b(?:confirmed|confirmation|receipt|failed)\b"),
    ),
    (
        "shipping",
        re.compile(r"(?i)\b(?:your\s+order\s+(?:has\s+)?shipped|shipping\s+update|out\s+for\s+delivery|"
                   r"package\s+delivered|tracking\s+number|track\s+your\s+(?:order|package))\b"),
    ),
    (
        "account",
        re.compile(r"(?i)\b(?:verify|activate|reset).{0,20}\b(?:account|password|email)\b"),
    ),
    ("back-in-stock", re.compile(r"(?i)\b(?:back\s+in\s+stock|restocked|available\s+again)\b")),
    ("replenishment", re.compile(r"(?i)\b(?:time\s+to\s+reorder|running\s+low|replenish|refill\s+reminder)\b")),
    ("winback", re.compile(r"(?i)\b(?:we\s+miss\s+you|come\s+back|been\s+a\s+while)\b")),
    (
        "loyalty",
        re.compile(r"(?i)\b(?:your\s+(?:loyalty|rewards?)\s+(?:points|balance|tier)|"
                   r"points?\s+(?:balance|earned)|rewards?\s+balance)\b"),
    ),
    ("referral", re.compile(r"(?i)\b(?:your\s+referral|referral\s+reward|refer\s+a\s+friend)\b")),
    ("subscription", re.compile(r"(?i)\bsubscription\s+(?:renewed|updated|confirmed|payment)\b")),
)

_WELCOME_RECIPIENT_STATE_RE = re.compile(
    r"(?i)\b(?:(?:your|this)\s+(?:(?:first[- ]order|welcome)\s+)?"
    r"(?:code|offer|discount)\s+(?:is\s+)?(?:ready|active|inside|waiting|expires?)|"
    r"subscriber\s+(?:code|offer|discount))\b"
)

_RECRUITING_RE = re.compile(
    r"(?i)\b(?:candidate|careers?|hiring|jobs?|recruit(?:ing|ment)?|"
    r"open\s+(?:role|position)|talent\s+community|"
    r"join\s+(?:our|the)\s+(?:team|ambassador|creator)\b|"
    r"(?:ambassador|creator)\s+(?:program|application|applications|team)\b|"
    r"(?:apply|application)\s+(?:to|for)\b.{0,60}\b(?:join|job|role|position|career|team|"
    r"ambassador|creator)\b)"
)

_MARKETING_CONTENT_RE = re.compile(
    r"(?i)\b(?:sale|discount|offer|save|shop|collection|"
    r"new\s+(?:arrival|drop|product|set|collection|formula|format|size|flavou?r|look)|"
    r"product|bundle|gift\s+guide|style\s+guide|ingredient|material|science|founder|"
    r"customer\s+(?:story|review)|testimonial|launch|event|early\s+access|"
    r"free\s+(?:shipping|gift)|limited[- ]time|now\s+available|back\s+in\s+stock|"
    r"subscribe\s+and\s+save)\b"
)

_OCCASION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Black Friday", re.compile(r"(?i)\b(?:black\s+friday|bfcm)\b")),
    ("Cyber Monday", re.compile(r"(?i)\b(?:cyber\s+monday|cyber\s+week)\b")),
    ("Thanksgiving", re.compile(r"(?i)\bthanksgiving\b")),
    (
        "New Year",
        re.compile(
            r"(?i)\b(?:new\s+year(?:['’]s)?(?:\s+eve)?|year[- ]end|nye|"
            r"(?:hello|welcome|cheers\s+to|ring\s+in|kick(?:ing)?\s+off|start(?:ing)?)\s+20\d{2}|"
            r"start\s+(?:off\s+)?the\s+(?:new\s+)?year)\b"
        ),
    ),
    ("Valentine's Day", re.compile(r"(?i)\bvalentine(?:'s)?(?:\s+day)?\b")),
    ("Presidents' Day", re.compile(r"(?i)\bpresidents?'?\s+day\b")),
    ("St. Patrick's Day", re.compile(r"(?i)\bst\.?\s+patrick(?:'s)?\s+day\b")),
    ("Easter", re.compile(r"(?i)\beaster\b")),
    (
        "Mother's Day",
        re.compile(r"(?i)\bmother(?:['’]s|s['’])?\s+day\b"),
    ),
    ("Memorial Day", re.compile(r"(?i)\bmemorial\s+day\b")),
    ("Father's Day", re.compile(r"(?i)\bfather(?:'s|s')?\s+day\b")),
    (
        "July 4",
        re.compile(r"(?i)\b(?:july\s+4(?:th)?|4th\s+of\s+july|fourth\s+of\s+july|independence\s+day)\b"),
    ),
    ("Prime Day", re.compile(r"(?i)\bprime\s+day\b")),
    ("Back to School", re.compile(r"(?i)\bback[- ]to[- ]school\b")),
    ("Labor Day", re.compile(r"(?i)\blabou?r\s+day\b")),
    ("Halloween", re.compile(r"(?i)\bhalloween\b")),
    (
        "Holiday gifting",
        re.compile(
            r"(?i)\b(?:christmas|xmas|merry\s+christmas|happy\s+holidays?|season(?:['’]s)?\s+greetings|"
            r"super\s+saturday|holiday\s+(?:gifts?|gifting|shop|sale|shipping|delivery|deadline|collection|order|season|"
            r"sets?|edit|ready)|for\s+the\s+holidays|holidays\s+are\s+here|"
            r"stocking\s+stuffer|season\s+of\s+giving)\b"
        ),
    ),
)

_GENERIC_HOLIDAY_GIFT_RE = re.compile(
    r"(?i)\b(?:gift\s+guide|shop\s+(?:the\s+)?gifts?)\b"
)
_GENERIC_MOTHERS_DAY_RE = re.compile(
    r"(?i)\b(?:gifts?\s+for\s+(?:mom|mum)|(?:mom|mum)(?:['’]s)?\s+gift\s+guide)\b"
)

_SEASON_RE = re.compile(r"(?i)\b(spring|summer|fall|autumn|winter)\b")
_SEASON_CONTEXT_RE = re.compile(
    r"(?i)\b(?:sale|offer|save|shop|collection|launch|drop|gift|guide|deadline|ship|order|ends?|"
    r"event|preview|arrivals?)\b"
)
_SEASON_MONTHS = {
    "spring": {3, 4, 5},
    "summer": {6, 7, 8},
    "fall": {9, 10, 11},
    "autumn": {9, 10, 11},
    "winter": {12, 1, 2},
}
_CURATED_SOURCE_TYPE = "curated_export"
_CURATED_OCCASIONS = {
    value.casefold(): value
    for value in (
        *(occasion for occasion, _ in _OCCASION_PATTERNS),
        "Spring",
        "Summer",
        "Fall",
        "Winter",
    )
}
_INTENT_BY_KEY = {value.casefold(): value for value in INTENT_TYPES}

_INTENT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "New product launch",
        re.compile(
            r"(?i)\b(?:introducing|meet\s+(?:the|our)\s+new|new\s+(?:drop|arrival|release|"
            r"collection|product|formula|format|flavou?rs?|size|look)|just\s+dropped|"
            r"launch(?:ing|es|ed)?|coming\s+soon|reimagined|now\s+available|available\s+now|"
            r"say\s+hello\s+to|the\s+wait\s+is\s+over|debut(?:ing|s|ed)?|first\s+look|"
            r"pre[- ]order|can\s+now\s+choose|now\s+(?:fit|made|built)\s+for)\b"
        ),
    ),
    (
        "Social proof/UGC",
        re.compile(
            r"(?i)\b(?:reviews?|testimonial|customer\s+story|as\s+seen\s+in|five[- ]star|5[- ]star|ugc|"
            r"what\s+(?:customers|people|they)\s+(?:say|said)|real\s+(?:results|stories)|"
            r"loved\s+by|ambassador(?:s|\s+program)?|join\s+(?:our|the)\s+(?:creator|ambassador)\s+program|"
            r"customer\s+favou?rites?|community\s+favou?rites?|award[- ]winning|featured\s+in|"
            r"rated\s+\d(?:\.\d)?|\d[\d,]*\+?\s+(?:star\s+)?reviews?)\b|"
            r"\b(?:style\s+advisor|advisor(?:['’]s)?\s+picks?|expert\s+picks?|"
            r"(?:swear|swears|swore)\s+by|worth\s+the\s+hype|proves?\s+the\s+hype)\b"
        ),
    ),
    (
        "Ingredient/education",
        re.compile(
            r"(?i)\b(?:ingredient|material|fabric|science|how\s+(?:it|this|to)\s+(?:works?|use|choose)|"
            r"what\s+(?:is|are)|why\s+(?:it|this|we)|guide\s+to|learn|explained|care\s+guide|"
            r"benefits?\s+of|made\s+with|inside\s+(?:the|our)|formula|"
            r"routine\s+(?:guide|tips?|explained)|how\s+to\s+(?:build|start|choose)\s+(?:a\s+)?routine|"
            r"tips?|burnout|nerd\s+out|"
            r"non[- ]toxic|toxins?|"
            r"cotton|linen|wool|denim|vitamin|protein|magnesium)\b"
        ),
    ),
    (
        "Founder/brand story",
        re.compile(r"(?i)\b(?:founder|our\s+story|our\s+mission|why\s+we|behind\s+the\s+brand|a\s+note\s+from)\b"),
    ),
    ("Upsell", re.compile(r"(?i)\b(?:upgrade|premium\s+tier|subscribe\s+and\s+save|add\s+another)\b")),
    (
        "Cross-sell",
        re.compile(r"(?i)\b(?:complete\s+the\s+look|pairs?\s+with|you\s+might\s+also|complementary|add[- ]on)\b"),
    ),
    (
        "Lifestyle/seasonal",
        re.compile(
            r"(?i)\b(?:weekend\s+edit|travel\s+edit|seasonal\s+edit|style\s+guide|holiday\s+edit|"
            r"gift\s+guide|(?:mother(?:['’]s|s['’])?\s+day|holiday)\s+gifts?|"
            r"what\s+to\s+wear|the\s+(?:spring|summer|fall|autumn|winter)\s+edit)\b"
        ),
    ),
)

_SUBJECT_LAUNCH_RE = re.compile(
    r"(?i)(?:^\s*new\s*:|^\s*new\b.{0,80}\b(?:is|are)\s+here\b|\breimagined\b|"
    r"\bnow\s+(?:fit|made|built)\s+for\b|"
    r"\bdrop\s+of\s+(?:the\s+)?(?:season|year)\b)"
)

_EDUCATIONAL_SUBJECT_RE = re.compile(
    r"(?i)\b(?:nerd\s+out|what\s+(?:is|are)|why\s+(?:it|this|we)|"
    r"how\s+(?:it|this|to)|science|learn|guide|experiment|versus|vs\.?)\b"
)


class IntentClassifier(Protocol):
    """Small protocol shared by the deterministic pipeline and test doubles."""

    def classify(self, subject: str, preheader: str, visible_text: str) -> Mapping[str, Any]: ...


def _record_value(record: Mapping[str, Any], *names: str, default: Any = "") -> Any:
    for name in names:
        value: Any = record
        found = True
        for part in name.split("."):
            if isinstance(value, Mapping) and part in value:
                value = value[part]
            else:
                found = False
                break
        if found and value is not None:
            return value
    return default


def sanitize_ai_text(value: Any, max_chars: int = 4000) -> str:
    """Remove identifiers and links before any text leaves the machine."""

    text = sanitize_recipient_text(str(value or ""), max_chars=max_chars)
    text = text.replace("[address removed]", "[redacted email]")
    text = text.replace("[link removed]", "[redacted url]")
    assert_recipient_safe(text)
    return text


def build_ai_payload(subject: Any, preheader: Any, visible_text: Any) -> dict[str, str]:
    """Return the only fields permitted in the optional AI request."""

    return {
        "subject": sanitize_ai_text(subject, 500),
        "preheader": sanitize_ai_text(preheader, 800),
        "visible_text": sanitize_ai_text(visible_text, 4000),
    }


def _inside_git_worktree(path: Path) -> bool:
    current = path.expanduser().resolve()
    if current.is_file():
        current = current.parent
    for parent in (current, *current.parents):
        if (parent / ".git").exists():
            return True
    return False


class AnthropicIntentClassifier:
    """Structured Claude classifier with an external, content-addressed cache."""

    SYSTEM_PROMPT = (
        "Classify one ecommerce marketing email. Return JSON only. Choose one intent from: "
        + ", ".join(INTENT_TYPES)
        + ". Also return uniqueness from 1 to 5, a short benefit_theme, and offer_type from: "
        + ", ".join(("none", *OFFER_TYPES))
        + ". Never infer a numeric discount. Judge only the sanitized text provided."
    )

    OUTPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": list(INTENT_TYPES)},
            "uniqueness": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
            "benefit_theme": {"type": "string"},
            "offer_type": {"type": "string", "enum": ["none", *OFFER_TYPES]},
        },
        "required": ["intent", "uniqueness", "benefit_theme", "offer_type"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        cache_dir: str | Path,
        model: str = "claude-sonnet-4-6",
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        if _inside_git_worktree(self.cache_dir):
            raise ValueError("AI cache must be outside every Git worktree")
        self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.cache_dir.chmod(0o700)
        except OSError:
            pass
        self.cache_path = self.cache_dir / "intent-cache.json"
        self.model = model
        self._cache: dict[str, dict[str, Any]] = {}
        if self.cache_path.exists():
            if self.cache_path.is_symlink() or not self.cache_path.is_file():
                raise RuntimeError("AI cache must be a private regular file")
            try:
                self.cache_path.chmod(0o600)
                loaded = json.loads(self.cache_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._cache = loaded
            except json.JSONDecodeError:
                self._cache = {}
            except OSError as exc:
                raise RuntimeError("AI cache permissions could not be secured") from exc

        if client is not None:
            self.client = client
            return
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("No Anthropic API key. Continue in deterministic-only mode.")
        try:
            import anthropic  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Install the optional anthropic package to enable AI classification") from exc
        self.client = anthropic.Anthropic(api_key=key)

    def _cache_key(self, payload: Mapping[str, str]) -> str:
        packed = json.dumps(
            {"model": self.model, "payload": payload}, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(packed).hexdigest()

    def _save(self) -> None:
        temporary = self.cache_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._cache, sort_keys=True, indent=2), encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, self.cache_path)

    @staticmethod
    def _response_text(response: Any) -> str:
        content = response.get("content", []) if isinstance(response, Mapping) else response.content
        for block in content:
            block_type = block.get("type") if isinstance(block, Mapping) else getattr(block, "type", "")
            if block_type == "text":
                return str(block.get("text", "") if isinstance(block, Mapping) else block.text)
        return "{}"

    @staticmethod
    def _coerce(raw: Mapping[str, Any]) -> dict[str, Any]:
        intent = str(raw.get("intent") or "Featured products")
        if intent not in INTENT_TYPES:
            intent = "Featured products"
        try:
            uniqueness = max(1, min(5, int(raw.get("uniqueness", 3))))
        except (TypeError, ValueError):
            uniqueness = 3
        offer_type = str(raw.get("offer_type") or "none")
        if offer_type not in ("none", *OFFER_TYPES):
            offer_type = "none"
        return {
            "intent": intent,
            "uniqueness": uniqueness,
            "benefit_theme": str(raw.get("benefit_theme") or "").strip()[:80],
            "offer_type": offer_type,
        }

    def classify(self, subject: str, preheader: str, visible_text: str) -> Mapping[str, Any]:
        payload = build_ai_payload(subject, preheader, visible_text)
        key = self._cache_key(payload)
        if key in self._cache:
            return copy.deepcopy(self._cache[key])
        response = self.client.messages.create(
            model=self.model,
            max_tokens=320,
            temperature=0,
            system=self.SYSTEM_PROMPT,
            output_config={"format": {"type": "json_schema", "schema": self.OUTPUT_SCHEMA}},
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
        result = self._coerce(json.loads(self._response_text(response)))
        self._cache[key] = result
        self._save()
        return copy.deepcopy(result)


def build_optional_classifier(
    cache_dir: str | Path,
    model: str = "claude-sonnet-4-6",
    api_key: str | None = None,
) -> AnthropicIntentClassifier | None:
    """Return ``None`` when AI is not configured so deterministic work continues."""

    if not (api_key or os.environ.get("ANTHROPIC_API_KEY")):
        return None
    return AnthropicIntentClassifier(cache_dir=cache_dir, model=model, api_key=api_key)


def _candidate(
    offer_type: str,
    source: str,
    match: re.Match[str],
    *,
    depth: float | None = None,
    unit: str = "other",
    confidence: float = 1.0,
) -> dict[str, Any]:
    return {
        "type": offer_type,
        "depth": depth,
        "unit": unit,
        "source": source,
        "evidence": match.group(0).strip(),
        "confidence": confidence,
        "deterministic": True,
        "position": match.start(),
    }


def _visible_lead(value: Any, max_chars: int = 2400) -> str:
    """Keep deterministic semantic rules above recurring footer boilerplate."""

    return str(value or "")[:max_chars]


def _standing_shipping_policy(text: str, match: re.Match[str]) -> bool:
    window = text[max(0, match.start() - 80) : match.end() + 100]
    line_start = text.rfind("\n", 0, match.start()) + 1
    line_end = text.find("\n", match.end())
    line = text[line_start : line_end if line_end >= 0 else len(text)]
    explicit_campaign = bool(
        re.search(
            r"(?i)\b(?:today|tonight|this\s+week(?:end)?|limited|ends?|code|unlock|"
            r"last\s+chance|through\s+(?:today|tonight|midnight))\b",
            window,
        )
    )
    standing_language = bool(
        re.search(
            r"(?i)\b(?:free\s+shipping\s+(?:on|for)\s+(?:all\s+)?orders?|"
            r"orders?\s+(?:over|above)\s+\$?\d|free\s+shipping\s+(?:and|&)\s+returns|"
            r"free\s+shipping.{0,80}\borders?\s+\$?\d+|"
            r"(?:member(?:ship)?|subscription)\s+(?:perks?|benefits?).{0,60}\bfree\s+shipping|"
            r"shipping\s+policy|terms\s+(?:apply|and\s+conditions)|standard\s+shipping)\b",
            window,
        )
    )
    standalone_perk = bool(
        re.fullmatch(r"(?i)\s*free\s+ship(?:ping)?\s*(?:&\s*easy\s+returns?)?\s*", line)
    )
    return not explicit_campaign and (
        match.start() >= 500 or standing_language or standalone_perk
    )


def _generic_offer_false_positive(text: str, match: re.Match[str]) -> bool:
    """Reject informational uses of offer vocabulary without an active benefit."""

    evidence = match.group(0).casefold()
    if evidence not in {"deal", "deals"}:
        return False
    window = text[max(0, match.start() - 50) : match.end() + 70]
    return bool(
        re.search(
            r"(?i)\bdeals?\s+(?:and|&|or)\s+(?:info(?:rmation)?|updates?|news)\b",
            window,
        )
    )


def _offer_context_is_non_campaign(
    text: str,
    match: re.Match[str],
    *,
    offer_type: str,
) -> bool:
    """Reject an unavailable benefit or a policy noun, not normal offer terms."""

    before = text[max(0, match.start() - 90) : match.start()]
    after = text[match.end() : match.end() + 90]
    evidence = match.group(0).casefold().strip()
    if re.match(
        r"(?i)^\s+(?:is|are|was|were)\s+not\s+(?:available|valid|eligible|offered)\b",
        after,
    ):
        return True
    if re.search(
        r"(?i)\bnot\s+(?:available|valid|eligible)\s+(?:for|on|with)\s*$",
        before,
    ):
        return True
    if offer_type == "other" and evidence in {
        "sale",
        "sales",
        "discount",
        "discounts",
        "deal",
        "deals",
    }:
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        line = text[line_start : line_end if line_end >= 0 else len(text)]
        if re.search(r"(?i)\b(?:final\s+sale|all\s+sales\s+(?:are\s+)?final)\b", line):
            return True
        if re.match(
            r"(?i)^\s+(?:cannot|can't|may\s+not)\s+(?:be\s+)?(?:combined|stacked|used|applied)\b",
            after,
        ):
            return True
        if re.search(
            r"(?i)\b(?:cannot|can't|may\s+not)\s+(?:be\s+)?(?:combined|stacked|used|applied)"
            r".{0,45}\b(?:other\s+)?$",
            before,
        ):
            return True
    return False


def extract_offers(subject: Any, preheader: Any, visible_text: Any) -> dict[str, Any]:
    """Extract offers in field-priority order and retain source evidence."""

    fields = (
        ("subject", str(subject or "")),
        ("preheader", str(preheader or "")),
        ("visible_text", str(visible_text or "")),
    )
    candidates: list[dict[str, Any]] = []
    for source, text in fields:
        candidate_text = _visible_lead(text) if source == "visible_text" else text
        field_candidates: list[dict[str, Any]] = []
        for pattern in _PERCENT_PATTERNS:
            for match in pattern.finditer(candidate_text):
                depth = float(match.group(1))
                if 1 <= depth <= 99 and not _offer_context_is_non_campaign(
                    candidate_text, match, offer_type="%off"
                ):
                    field_candidates.append(
                        _candidate("%off", source, match, depth=depth, unit="percent")
                    )
        for pattern in _DOLLAR_PATTERNS:
            for match in pattern.finditer(candidate_text):
                depth = float(match.group(1))
                if depth >= 1 and not _offer_context_is_non_campaign(
                    candidate_text, match, offer_type="$off"
                ):
                    field_candidates.append(
                        _candidate("$off", source, match, depth=depth, unit="dollar")
                    )
        semantic_text = candidate_text
        for offer_type, pattern in _NON_NUMERIC_OFFER_PATTERNS:
            for match in pattern.finditer(semantic_text):
                if offer_type == "free_shipping" and source == "visible_text" and _standing_shipping_policy(
                    semantic_text, match
                ):
                    continue
                if _offer_context_is_non_campaign(
                    semantic_text, match, offer_type=offer_type
                ):
                    continue
                field_candidates.append(_candidate(offer_type, source, match))
        if source in {"subject", "preheader"}:
            for match in _ACTIVE_FREE_ITEM_RE.finditer(semantic_text):
                if not _offer_context_is_non_campaign(
                    semantic_text, match, offer_type="gift"
                ):
                    field_candidates.append(_candidate("gift", source, match))
        if not field_candidates:
            generic_text = (
                _visible_lead(text, 800) if source == "visible_text" else text
            )
            for match in _GENERIC_SALE_RE.finditer(generic_text):
                if source == "visible_text" and match.group(0).casefold() == "savings":
                    continue
                if _generic_offer_false_positive(generic_text, match):
                    continue
                if _offer_context_is_non_campaign(
                    generic_text, match, offer_type="other"
                ):
                    continue
                field_candidates.append(
                    _candidate("other", source, match, confidence=0.95)
                )

        candidates.extend(field_candidates)

    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in candidates:
        identity = (item["type"], item["source"], item["evidence"].casefold())
        if identity not in seen:
            seen.add(identity)
            deduplicated.append(item)

    source_rank = {"subject": 0, "preheader": 1, "visible_text": 2}
    candidate_snapshot = tuple(deduplicated)

    def structured_offer_rank(item: Mapping[str, Any]) -> int:
        if item.get("type") != "bogo":
            return 1
        if any(
            candidate.get("depth") is not None
            and candidate.get("source") == item.get("source")
            and abs(int(candidate.get("position", 0)) - int(item.get("position", 0))) <= 120
            for candidate in candidate_snapshot
        ):
            return 0
        return 1

    def evidence_rank(item: Mapping[str, Any]) -> int:
        if item.get("depth") is not None:
            return 0
        if item.get("type") in {"bogo", "gift"}:
            return 1
        if item.get("source") in {"subject", "preheader"}:
            return 2
        if item.get("type") == "other":
            return 3
        return 4

    deduplicated.sort(
        key=lambda item: (
            structured_offer_rank(item),
            evidence_rank(item),
            source_rank.get(item["source"], 9),
            item["position"],
        )
    )
    for item in deduplicated:
        item.pop("position", None)
    primary = copy.deepcopy(deduplicated[0]) if deduplicated else None
    return {
        "present": bool(primary),
        "primary": primary,
        "candidates": deduplicated,
        "numeric_supported": bool(primary and primary.get("depth") is not None),
        "analysis_mode": "deterministic",
    }


def _bounded_confidence(value: Any, *, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _curated_offer_fallback(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Keep only typed, nonnumeric curated offers when text has no offer evidence."""

    values: list[Mapping[str, Any]] = []
    for name in ("offer.primary", "primary_offer"):
        value = _record_value(record, name, default=None)
        if isinstance(value, Mapping):
            values.append(value)
    for name in ("offer.candidates", "offer_candidates"):
        value = _record_value(record, name, default=[])
        if isinstance(value, (list, tuple)):
            values.extend(item for item in value if isinstance(item, Mapping))

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        offer_type = str(value.get("type") or "").strip().casefold()
        if (
            offer_type not in OFFER_TYPES
            or value.get("depth") is not None
            or offer_type in seen
        ):
            continue
        seen.add(offer_type)
        candidates.append(
            {
                "type": offer_type,
                "depth": None,
                "unit": "other",
                "source": _CURATED_SOURCE_TYPE,
                "evidence": "",
                "confidence": _bounded_confidence(
                    value.get("confidence"), default=0.8
                ),
                "deterministic": False,
            }
        )
    if not candidates:
        return None
    return {
        "present": True,
        "primary": copy.deepcopy(candidates[0]),
        "candidates": candidates,
        "numeric_supported": False,
        "analysis_mode": "curated_fallback",
    }


def _curated_seasonality_fallback(
    record: Mapping[str, Any],
) -> dict[str, Any] | None:
    if _record_value(record, "seasonality.seasonal", "seasonal", default=False) is not True:
        return None
    raw_occasion = str(
        _record_value(record, "seasonality.occasion", "occasion", default="")
    ).strip()
    occasion = _CURATED_OCCASIONS.get(raw_occasion.casefold())
    if not occasion:
        return None
    confidence = _record_value(
        record,
        "seasonality.confidence",
        default=0.8,
    )
    return {
        "seasonal": True,
        "occasion": occasion,
        "source": _CURATED_SOURCE_TYPE,
        "evidence": "",
        "confidence": _bounded_confidence(confidence, default=0.8),
    }


def _curated_intent(record: Mapping[str, Any]) -> dict[str, Any] | None:
    raw_intent = str(
        _record_value(record, "intent.label", "intent", default="")
    ).strip()
    label = _INTENT_BY_KEY.get(raw_intent.casefold())
    if not label:
        return None
    confidence = _record_value(
        record,
        "intent.confidence",
        "intent_confidence",
        default=0.8,
    )
    return {
        "label": label,
        "source": _CURATED_SOURCE_TYPE,
        "confidence": _bounded_confidence(confidence, default=0.8),
    }


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None


def classify_seasonality(subject: Any, preheader: Any, visible_text: Any, observed_at: Any) -> dict[str, Any]:
    """Require explicit language; the date can only confirm commercial season text."""

    fields = (
        ("subject", str(subject or "")),
        ("preheader", str(preheader or "")),
        ("visible_text", str(visible_text or "")),
    )
    for source, text in fields:
        for occasion, pattern in _OCCASION_PATTERNS:
            match = pattern.search(text)
            if match:
                return {
                    "seasonal": True,
                    "occasion": occasion,
                    "source": source,
                    "evidence": match.group(0).strip(),
                    "confidence": 1.0,
                }

    observed_date = _parse_date(observed_at)
    if observed_date and observed_date.month in {4, 5}:
        for source, text in fields:
            match = _GENERIC_MOTHERS_DAY_RE.search(text)
            if match:
                return {
                    "seasonal": True,
                    "occasion": "Mother's Day",
                    "source": source,
                    "evidence": match.group(0).strip(),
                    "confidence": 0.9,
                }
    generic_mothers_day_language = any(
        _GENERIC_MOTHERS_DAY_RE.search(text) for _source, text in fields
    )
    if (
        observed_date
        and observed_date.month in {10, 11, 12}
        and not generic_mothers_day_language
    ):
        for source, text in fields:
            match = _GENERIC_HOLIDAY_GIFT_RE.search(text)
            if match:
                return {
                    "seasonal": True,
                    "occasion": "Holiday gifting",
                    "source": source,
                    "evidence": match.group(0).strip(),
                    "confidence": 0.9,
                }
    if observed_date:
        for source, text in fields:
            season_match = _SEASON_RE.search(text)
            context_match = _SEASON_CONTEXT_RE.search(text)
            if not (season_match and context_match):
                continue
            season = season_match.group(1).casefold()
            if observed_date.month in _SEASON_MONTHS[season]:
                canonical = "Fall" if season == "autumn" else season.title()
                return {
                    "seasonal": True,
                    "occasion": canonical,
                    "source": source,
                    "evidence": season_match.group(0).strip(),
                    "confidence": 0.9,
                }

    return {"seasonal": False, "occasion": "", "source": "", "evidence": "", "confidence": 1.0}


def classify_scope_evidence(
    subject: Any,
    preheader: Any,
    visible_text: Any,
    *,
    bulk_or_list: bool = False,
) -> tuple[str, str, float]:
    """Classify scope from the campaign lead, never footer boilerplate."""

    subject_text = str(subject or "")
    preheader_text = str(preheader or "")
    primary = f"{subject_text}\n{preheader_text}"
    lead = _visible_lead(visible_text, 700)
    recruiting = bool(_RECRUITING_RE.search(f"{primary}\n{lead}"))

    for label, pattern in _LIFECYCLE_PATTERNS:
        match = pattern.search(primary)
        if match and not (label == "welcome" and recruiting):
            return "lifecycle", f"lifecycle:{label}", 0.96

    # Triggered messages sometimes put the decisive subscriber/cart language in
    # the first content block. Footer-only loyalty, referral, and shipping terms
    # are deliberately excluded from this second pass.
    lead_labels = {
        "welcome",
        "cart",
        "checkout",
        "browse",
        "post-purchase",
        "transactional",
        "account",
        "replenishment",
        "winback",
        "subscription",
    }
    for label, pattern in _LIFECYCLE_PATTERNS:
        if label not in lead_labels:
            continue
        match = pattern.search(lead)
        if match and not (label == "welcome" and recruiting):
            return "lifecycle", f"lifecycle:{label}", 0.91

    if _WELCOME_RECIPIENT_STATE_RE.search(lead):
        return "lifecycle", "lifecycle:welcome", 0.91

    combined = f"{primary}\n{lead}"
    if not combined.strip():
        return "uncertain", "insufficient_scope_evidence", 0.45
    if bulk_or_list:
        return "broadcast", "bulk_or_list_header", 0.96
    if _MARKETING_CONTENT_RE.search(combined):
        return "broadcast", "marketing_content", 0.72
    return "uncertain", "ambiguous_nonbulk_message", 0.55


def classify_scope_detail(record: Mapping[str, Any]) -> tuple[str, str, float]:
    existing = str(_record_value(record, "scope", default="")).casefold()
    if (
        str(_record_value(record, "source_type", default="")).casefold()
        == _CURATED_SOURCE_TYPE
        and existing in {"broadcast", "lifecycle", "uncertain"}
    ):
        return (
            existing,
            str(_record_value(record, "scope_reason", default="curated_export")),
            _bounded_confidence(
                _record_value(record, "scope_confidence", default=1.0), default=1.0
            ),
        )

    subject = _record_value(record, "sanitized.subject", "subject", default="")
    preheader = _record_value(
        record, "sanitized.preheader", "preheader", "preview", default=""
    )
    body = _record_value(
        record, "sanitized.visible_text", "visible_text", "body_text", default=""
    )
    prior_reason = str(_record_value(record, "scope_reason", default=""))
    bulk_or_list = bool(
        _record_value(
            record,
            "headers.list_unsubscribe",
            "list_unsubscribe",
            "headers.list_id",
            "list_id",
            "campaign_id",
            default="",
        )
    ) or prior_reason == "bulk_or_list_header"
    return classify_scope_evidence(
        subject, preheader, body, bulk_or_list=bulk_or_list
    )


def classify_scope(record: Mapping[str, Any]) -> str:
    return classify_scope_detail(record)[0]


def _classify_content_intent(
    subject: Any,
    preheader: Any,
    visible_text: Any,
) -> tuple[dict[str, Any], int]:
    """Return the strongest non-offer intent and its first evidence field."""

    fields = (
        ("subject", str(subject or ""), 6.0),
        ("preheader", str(preheader or ""), 4.0),
        ("visible_text", _visible_lead(visible_text, 400), 2.0),
    )
    scores: dict[str, float] = {}
    first_positions: dict[str, tuple[int, int]] = {}
    for field_index, (_source, text, weight) in enumerate(fields):
        for label, pattern in _INTENT_RULES:
            matches = list(pattern.finditer(text))
            if not matches:
                continue
            scores[label] = scores.get(label, 0.0) + weight + min(
                2, len(matches) - 1
            ) * 0.25
            position = (field_index, matches[0].start())
            first_positions[label] = min(
                first_positions.get(label, position), position
            )

    if _SUBJECT_LAUNCH_RE.search(f"{subject or ''}\n{preheader or ''}"):
        scores["New product launch"] = scores.get("New product launch", 0.0) + 5.5
        first_positions.setdefault("New product launch", (0, 0))

    if not scores:
        return (
            {
                "label": "Featured products",
                "source": "deterministic",
                "confidence": 0.7,
            },
            9,
        )

    rule_rank = {label: index for index, (label, _pattern) in enumerate(_INTENT_RULES)}
    label = min(
        scores,
        key=lambda value: (
            -scores[value],
            first_positions.get(value, (9, 9_999)),
            rule_rank.get(value, 99),
        ),
    )
    field_index = first_positions.get(label, (2, 0))[0]
    confidence = 0.9 if field_index < 2 else 0.86
    return (
        {"label": label, "source": "deterministic", "confidence": confidence},
        field_index,
    )


def _content_intent_over_offer(
    subject: Any,
    preheader: Any,
    visible_text: Any,
    offer: Mapping[str, Any],
    content_intent: Mapping[str, Any],
    content_field_index: int,
) -> bool:
    """Keep a strong campaign thesis when the offer is a secondary body module."""

    primary = offer.get("primary")
    if not isinstance(primary, Mapping) or primary.get("source") != "visible_text":
        return False
    label = str(content_intent.get("label") or "")
    if label == "Featured products" or content_field_index >= 2:
        return False

    campaign_lead = f"{subject or ''}\n{preheader or ''}"
    lead = _visible_lead(visible_text, 900)
    if label == "Ingredient/education" and _EDUCATIONAL_SUBJECT_RE.search(
        campaign_lead
    ):
        if any(
            candidate_label == label and pattern.search(lead)
            for candidate_label, pattern in _INTENT_RULES
        ):
            return True

    evidence = str(primary.get("evidence") or "").strip()
    if not evidence:
        return False
    evidence_position = str(visible_text or "").casefold().find(evidence.casefold())
    return evidence_position >= 400


def classify_intent(
    subject: Any,
    preheader: Any,
    visible_text: Any,
    offer: Mapping[str, Any],
    *,
    scope: str = "broadcast",
) -> dict[str, Any]:
    content_intent, content_field_index = _classify_content_intent(
        subject, preheader, visible_text
    )
    if offer.get("present"):
        if _content_intent_over_offer(
            subject,
            preheader,
            visible_text,
            offer,
            content_intent,
            content_field_index,
        ):
            return dict(content_intent)
        return {"label": "Promotion/offer", "source": "deterministic", "confidence": 1.0}
    return content_intent


def quadrant_for(offer_present: bool, seasonal: bool) -> str:
    if offer_present and seasonal:
        return "Seasonal promotion"
    if offer_present:
        return "Everyday promotion"
    if seasonal:
        return "Seasonal content"
    return "Evergreen content"


def numeric_offer_is_supported(record: Mapping[str, Any]) -> bool:
    nested_offer = record.get("offer")
    if isinstance(nested_offer, Mapping):
        primary = nested_offer.get("primary")
        candidates = nested_offer.get("candidates", [])
    else:
        primary = record.get("primary_offer")
        candidates = record.get("offer_candidates", [])
    values: list[Mapping[str, Any]] = []
    if isinstance(primary, Mapping):
        values.append(primary)
    if isinstance(candidates, (list, tuple)):
        values.extend(value for value in candidates if isinstance(value, Mapping))
    for value in values:
        if value.get("depth") is None:
            continue
        source = str(value.get("source") or "")
        evidence = str(value.get("evidence") or "").strip()
        if (
            not value.get("deterministic")
            or source not in {"subject", "preheader", "visible_text"}
            or not evidence
        ):
            return False
        field_value = str(
            _record_value(record, f"sanitized.{source}", source, default="")
        )
        if evidence.casefold() not in field_value.casefold():
            return False
        evidence_offer = extract_offers(evidence, "", "")
        claimed_type = str(value.get("type") or "")
        try:
            claimed_depth = float(value["depth"])
        except (KeyError, TypeError, ValueError):
            return False
        if not any(
            str(candidate.get("type") or "") == claimed_type
            and candidate.get("depth") is not None
            and float(candidate["depth"]) == claimed_depth
            for candidate in evidence_offer.get("candidates", [])
            if isinstance(candidate, Mapping)
        ):
            return False
    return True


def analyze_message(
    record: Mapping[str, Any], classifier: IntentClassifier | None = None
) -> dict[str, Any]:
    """Return a copied record enriched with analysis fields."""

    result: MutableMapping[str, Any] = copy.deepcopy(dict(record))
    subject = _record_value(record, "sanitized.subject", "subject", default="")
    preheader = _record_value(record, "sanitized.preheader", "preheader", "preview", default="")
    body = _record_value(record, "sanitized.visible_text", "visible_text", "body_text", default="")
    observed_at = _record_value(
        record,
        "dates.received_at",
        "canonical_received_at",
        "received_at",
        "observed_at",
        "date",
        default="",
    )
    scope, scope_reason, scope_confidence = classify_scope_detail(record)
    offer = extract_offers(subject, preheader, body)
    seasonality = classify_seasonality(subject, preheader, body, observed_at)
    intent = classify_intent(subject, preheader, body, offer, scope=scope)
    deterministic_intent_is_fallback = (
        intent.get("label") == "Featured products"
        and intent.get("source") == "deterministic"
        and intent.get("confidence") == 0.7
    )
    curated_export = (
        str(_record_value(record, "source_type", default="")).strip().casefold()
        == _CURATED_SOURCE_TYPE
    )
    curated_intent = _curated_intent(record) if curated_export else None
    if curated_export and not offer["present"]:
        offer = _curated_offer_fallback(record) or offer
    if curated_export and not seasonality["seasonal"]:
        seasonality = _curated_seasonality_fallback(record) or seasonality

    ai_result: Mapping[str, Any] | None = None
    if classifier is not None and scope == "broadcast":
        try:
            ai_result = classifier.classify(str(subject), str(preheader), str(body))
        except Exception as exc:  # keep the deterministic product usable
            result.setdefault("analysis_errors", []).append(f"AI classification unavailable: {type(exc).__name__}")
    if ai_result:
        ai_intent = str(ai_result.get("intent") or "")
        if ai_intent in INTENT_TYPES:
            intent = {
                "label": ai_intent,
                "source": "ai",
                "confidence": 0.8,
                "model": getattr(classifier, "model", "configured"),
                "uniqueness": max(1, min(5, int(ai_result.get("uniqueness", 3)))),
                "benefit_theme": str(ai_result.get("benefit_theme") or "")[:80],
            }
        ai_offer_type = str(ai_result.get("offer_type") or "none")
        if not offer["present"] and ai_offer_type in OFFER_TYPES:
            ai_candidate = {
                "type": ai_offer_type,
                "depth": None,
                "unit": "other",
                "source": "ai",
                "evidence": "",
                "confidence": 0.65,
                "deterministic": False,
            }
            offer = {
                "present": True,
                "primary": ai_candidate,
                "candidates": [ai_candidate],
                "numeric_supported": False,
                "analysis_mode": "ai_fallback",
            }
            if intent["label"] != "Promotion/offer":
                intent = {**intent, "label": "Promotion/offer"}

    if curated_export:
        if offer["present"]:
            primary = offer.get("primary")
            primary_source = (
                str(primary.get("source") or "")
                if isinstance(primary, Mapping)
                else ""
            )
            intent_source = (
                primary_source
                if primary_source in {_CURATED_SOURCE_TYPE, "ai"}
                else "deterministic"
            )
            confidence = (
                _bounded_confidence(primary.get("confidence"), default=1.0)
                if isinstance(primary, Mapping)
                else 1.0
            )
            intent = {
                "label": "Promotion/offer",
                "source": intent_source,
                "confidence": confidence,
            }
        elif curated_intent and not ai_result and deterministic_intent_is_fallback:
            intent = curated_intent

    result["scope"] = scope
    result["scope_reason"] = scope_reason
    result["scope_confidence"] = scope_confidence
    result["offer"] = offer
    result["seasonality"] = seasonality
    result["intent"] = intent
    result["quadrant"] = quadrant_for(bool(offer["present"]), bool(seasonality["seasonal"]))
    result["analysis_mode"] = "ai+deterministic" if ai_result else "deterministic-only"
    if curated_export:
        # Replace the flat canonical annotations too, so a rejected curated
        # number cannot survive beside the authoritative nested analysis.
        result["offer_candidates"] = [
            copy.deepcopy(value) for value in offer.get("candidates", [])
        ]
        result["primary_offer"] = (
            copy.deepcopy(offer.get("primary")) if offer.get("primary") else None
        )
        result["seasonal"] = bool(seasonality["seasonal"])
        result["occasion"] = str(seasonality.get("occasion") or "") or None
        result["intent_source"] = str(intent.get("source") or "") or None
        result["intent_confidence"] = float(intent.get("confidence") or 0.0)
    if not numeric_offer_is_supported(result):
        raise ValueError("Numeric promotion claim has no deterministic source evidence")
    return dict(result)


def analyze_records(
    records: Iterable[Mapping[str, Any]], classifier: IntentClassifier | None = None
) -> list[dict[str, Any]]:
    return [analyze_message(record, classifier=classifier) for record in records]


def analyze_normalized_messages(
    records: Iterable[NormalizedMessage],
    classifier: IntentClassifier | None = None,
) -> list[NormalizedMessage]:
    """Enrich canonical records while retaining the flat storage contract.

    ``analyze_message`` deliberately works with generic mappings. The private
    master store and coverage census use :class:`NormalizedMessage`, so this
    adapter maps the nested analytical result back to the canonical flat
    fields without retaining raw evidence outside the sanitized record.
    """

    output: list[NormalizedMessage] = []
    for record in records:
        analyzed = analyze_message(record.to_dict(), classifier=classifier)
        offer = analyzed["offer"]
        seasonality = analyzed["seasonality"]
        intent = analyzed["intent"]
        output.append(
            replace(
                record,
                scope=str(analyzed.get("scope") or "uncertain"),
                scope_reason=str(
                    analyzed.get("scope_reason") or "insufficient_scope_evidence"
                ),
                scope_confidence=float(analyzed.get("scope_confidence") or 0.0),
                intent=str(intent.get("label") or "Featured products"),
                intent_source=str(intent.get("source") or "deterministic"),
                intent_confidence=float(intent.get("confidence") or 0.0),
                offer_candidates=[dict(value) for value in offer.get("candidates", [])],
                primary_offer=dict(offer["primary"]) if offer.get("primary") else None,
                seasonal=bool(seasonality.get("seasonal")),
                occasion=str(seasonality.get("occasion") or "") or None,
                classification_model=str(intent.get("model") or "") or None,
            )
        )
    return output


__all__ = [
    "AnthropicIntentClassifier",
    "INTENT_TYPES",
    "IntentClassifier",
    "OFFER_TYPES",
    "QUADRANTS",
    "analyze_message",
    "analyze_normalized_messages",
    "analyze_records",
    "build_ai_payload",
    "build_optional_classifier",
    "classify_scope",
    "classify_scope_detail",
    "classify_scope_evidence",
    "classify_seasonality",
    "extract_offers",
    "numeric_offer_is_supported",
    "quadrant_for",
    "sanitize_ai_text",
]
