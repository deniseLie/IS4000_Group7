"""Conservative checks, not a semantic proof that each claim is true."""
import re
import unicodedata
from decimal import Decimal

# Ignore ordered-list labels, but not amounts, percentages or waiting periods.
NUMBER = re.compile(r'(?<![\d.])\d+(?:,\d{3})*(?:\.\d+)?(?!\d)')

def numbers(text):
    text = unicodedata.normalize('NFKC', text)
    text = re.sub(r'^\s*\d+[.)]\s+', '', text, flags=re.M)
    return {Decimal(m.group().replace(',', '')) for m in NUMBER.finditer(text)}

def check_sources(answer, ids, evidence):
    """Return a stable reason code. Only cited evidence may support figures.

    This deliberately rejects new calculations. Matching figures still need human
    review for currency, meaning and attribution. Written-out numbers and semantic
    contradictions are not comprehensively detected by this check.
    """
    available = {item['id']: item['text'] for item in evidence}
    if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in available for i in ids):
        return 'invalid_source_ids'
    if not numbers(answer) <= numbers('\n'.join(available[i] for i in ids)):
        return 'unsupported_figure'
    return None
