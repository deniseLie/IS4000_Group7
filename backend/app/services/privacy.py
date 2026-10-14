"""Best-effort redaction for the demo. Never log request or response bodies."""
import re
import unicodedata

PATTERNS = [
    re.compile(r'''(?i)\b(?:password|passcode)\s*(?:is\s+|[:=]\s*)(?:"[^"\n]+"|'[^'\n]+'|[^\s,;"']+)'''),
    re.compile(r'(?i)\b(?:pin|otp|cvv|cvc|one[- ]time (?:password|code))\s*(?:is\s+|[:=]\s*)?(?:\d[ -]?){2,9}\d\b'),
    re.compile(r'(?<!\d)(?:\d[ -]?){13,19}(?!\d)'),
    re.compile(r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b'),
]

def redact(text: str) -> tuple[str, bool]:
    normalized = re.sub('[\u200b\u200c\u200d\ufeff]', '', unicodedata.normalize('NFKC',text))
    cleaned = normalized
    for pattern in PATTERNS:
        cleaned = pattern.sub('[private detail removed]', cleaned)
    changed = cleaned != normalized
    return (cleaned if changed else text), changed
