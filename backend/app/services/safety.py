"""Small explicit emergency patterns; unknown wording still needs model review."""
import re
import unicodedata

LOST_CARD = re.compile(
    r"\b(?:lost|missing|misplaced|dropped|stole|stolen)\s+(?:(?:my|the|a|our|your)\s+)?(?:(?:new|old|physical)\s+)?(?:(?:credit|debit|bank|ATM)\s+)?cards?\b"
    r"|\bcards?\s+(?:(?:is|are|was|were|has been|have been|went|got)\s+)?(?:just\s+)?(?:lost|stolen|missing|misplaced|gone)\b"
    r"|\b(?:cannot|can't|can’t|can't seem to|can’t seem to)\s+find\s+(?:(?:my|the|a|our|your)\s+)?(?:(?:credit|debit|bank|ATM)\s+)?cards?\b", re.I)


def is_lost_card(text):
    return bool(LOST_CARD.search(unicodedata.normalize('NFKC',text)))
