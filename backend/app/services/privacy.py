"""Best-effort redaction for the demo. Never log request or response bodies."""
import re

PATTERNS = [
    re.compile(r'(?i)\b(?:password|passcode)\s*(?:is\s+|[:=]\s*)["\']?[^\s,;"\']+'),
    re.compile(r'(?i)\b(?:pin|otp|cvv|cvc|one[- ]time (?:password|code))\s*(?:is\s+|[:=]\s*)?\d{3,10}\b'),
    re.compile(r'(?<!\d)(?:\d[ -]?){13,19}(?!\d)'),
    re.compile(r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b'),
]

def redact(text: str) -> tuple[str, bool]:
    cleaned = text
    for pattern in PATTERNS:
        cleaned = pattern.sub('[private detail removed]', cleaned)
    return cleaned, cleaned != text
