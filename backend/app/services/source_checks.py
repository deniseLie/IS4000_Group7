"""Conservative checks, not a semantic proof that each claim is true."""
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation

# Ignore ordered-list labels, but not amounts, percentages or waiting periods.
NUMBER = re.compile(r'(?<![\d.])\d+(?:,\d{3})*(?:\.\d+)?(?!\d)')
CURRENCIES = 'SGD USD EUR GBP AUD CAD CHF CNY HKD INR JPY MYR NZD THB'.split()
DOLLARS = frozenset({'SGD','USD','HKD','AUD','CAD','NZD'})
UNIT = r'(?:'+'|'.join(CURRENCIES)+r'|S\$|US\$|HK\$|\$|€|£|%|percent\b|per cent\b)'
VALUE = r'[+-]?\d+(?:,\d{3})*(?:\.\d+)?'
UNIT_BEFORE = re.compile(r'(?<!\w)('+UNIT+r')\s*('+VALUE+r')(?!\d)',re.I)
UNIT_AFTER = re.compile(r'(?<![\w.])('+VALUE+r')\s*('+UNIT+r')(?!\w)',re.I)
MONEY_FIELDS = {'balance','amount','credit_limit','outstanding_balance','minimum_payment_due',
                'annual_fee','outstanding_amount','monthly_repayment','min_income','min_loan_amount','max_loan_amount',
                # Card and loan reads (0083 records their currency): a figure counts only with the currency on the same record.
                'limit','outstanding','minimum_payment','principal','next_amount'}
POLICY_CLAIM = re.compile(r"\b(?:our|DemoBank['’]s)\s+(?:card\s+)?(?:terms|policy|policies)\s+(?:say|says|state|states|require|requires|is|are)\b|\b(?:under|according to)\s+our\s+(?:card\s+)?(?:terms|policy)\b",re.I)


def unit_values(text):
    """Explicit amount/unit pairs; a shared digit alone is not evidence of a unit."""
    text = unicodedata.normalize('NFKC',text)
    aliases = {'S$':'SGD','US$':'USD','HK$':'HKD','€':'EUR','£':'GBP','PERCENT':'%','PER CENT':'%'}
    def pair(unit,value):
        return aliases.get(unit.upper(),unit.upper()),Decimal(value.replace(',',''))
    return {pair(u,v) for u,v in UNIT_BEFORE.findall(text)} | {pair(u,v) for v,u in UNIT_AFTER.findall(text)}


def unit_matches(claimed,recorded):
    """A bare '$' names some dollar currency with the same signed value; other units must match exactly."""
    return claimed==recorded or claimed[0]=='$' and recorded[0] in DOLLARS and claimed[1]==recorded[1]


def record_units(text):
    """Use currency on the same JSON record, never inherit it across records."""
    try:
        records=json.loads(text[text.index('{'):])
    except (ValueError,TypeError):return set()
    found=set()
    def walk(value):
        if isinstance(value,dict):
            currency=value.get('currency')
            if isinstance(currency,str) and currency.upper() in CURRENCIES:
                for key in MONEY_FIELDS & value.keys():
                    amount=value[key]
                    if isinstance(amount,(int,float,str)) and not isinstance(amount,bool):
                        try:
                            amount=Decimal(str(amount).replace(',',''))
                            if amount.is_finite():found.add((currency.upper(),amount))
                        except InvalidOperation:pass
            for child in value.values():walk(child)
        elif isinstance(value,list):
            for child in value:walk(child)
    walk(records)
    return found

def numbers(text):
    text = unicodedata.normalize('NFKC', text)
    text = re.sub(r'^\s*\d+[.)]\s+', '', text, flags=re.M)
    return {Decimal(m.group().replace(',', '')) for m in NUMBER.finditer(text)}

def check_sources(answer, ids, evidence):
    """Return a stable reason code. Only cited evidence may support figures.

    This deliberately rejects new calculations. Explicit common currency and
    percentage pairs are checked; bare '$', written-out numbers, omitted units,
    translations and semantic contradictions still require human review.
    """
    available = {item['id']: item['text'] for item in evidence}
    if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in available for i in ids):
        return 'invalid_source_ids'
    if not numbers(answer) <= numbers('\n'.join(available[i] for i in ids)):
        return 'unsupported_figure'
    supported=set()
    for i in ids:supported.update(unit_values(available[i]) | record_units(available[i]))
    if not all(any(unit_matches(claim,item) for item in supported) for claim in unit_values(answer)):return 'unsupported_unit'
    external=any(item['id'] in ids and item.get('publisher') in {'MoneySense','Association of Banks in Singapore'} for item in evidence)
    if external and POLICY_CLAIM.search(answer):return 'unsupported_attribution'
    return None
