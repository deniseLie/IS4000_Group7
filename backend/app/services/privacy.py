"""Best-effort redaction for the demo. Never log request or response bodies."""
import re
import unicodedata
from app.schemas.contracts import Redaction, RedactionType

PATTERNS = [
    re.compile(r'''(?i)\b(?:password|passcode)\s*(?:is\s+|[:=]\s*)(?:"[^"\n]+"|'[^'\n]+'|[^\s,;"']+)'''),
    re.compile(r'(?i)\b(?:pin|otp|cvv|cvc|one[- ]time (?:password|code))\s*(?:is\s+|[:=]\s*)?(?:\d[ -]?){2,9}\d\b'),
    re.compile(r'(?<!\d)(?:\d[ -]?){13,19}(?!\d)'),
    re.compile(r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b'),
    re.compile(r'(?i)\b[STFGM]\d{7}[A-Z]\b'),
]

def redact_details(text: str) -> tuple[str, list[Redaction]]:
    """Return redacted text and counts only; preserve ordinary Unicode text exactly."""
    normalized = re.sub('[\u200b\u200c\u200d\ufeff]', '', unicodedata.normalize('NFKC',text))
    cleaned = normalized
    counts: dict[RedactionType, int] = {}
    for index, pattern in enumerate(PATTERNS):
        for match in pattern.finditer(cleaned):
            kind: RedactionType
            if index == 0:kind = 'password'
            elif index == 1:
                label = match.group().lower()
                kind = 'pin' if label.startswith('pin') else 'cvv' if label.startswith(('cvv', 'cvc')) else 'otp'
            elif index == 2:kind = 'card_number'
            elif index == 3:kind = 'email'
            else:kind = 'nric'
            counts[kind] = counts.get(kind, 0) + 1
        cleaned = pattern.sub('[private detail removed]', cleaned)
    return (cleaned if counts else text), [Redaction(type=kind, count=count) for kind, count in counts.items()]


def redact(text: str) -> tuple[str, bool]:
    cleaned, redactions = redact_details(text)
    return cleaned, bool(redactions)
