"""Deterministic input assessment. Historical secrets stay blocked; urgency follows the latest message.

English emergency/fraud patterns are intentionally bounded, not a semantic classifier.
No text, secret values or model calls belong in this module's audit/trace metadata.
"""
import re
from app.schemas.chat import ChatRequest
from app.schemas.contracts import FixedResponse, Redaction, RedactionType, RiskAssessment, RiskFlag
from app.services.privacy import redact_details
from app.services.safety import is_lost_card
from app.services.typos import normalise

HUMAN = re.compile(r'\b(talk|speak|connect)\b.*\b(person|human|staff|agent)\b', re.I)
DELEGATE_CLAIM=re.compile(r"\b(?:i['’]m|i am|acting)\b.{0,70}\b(?:assistant|delegate|representative)\b|\bon behalf of\b",re.I)
PRIVATE_REQUEST=re.compile(r'\b(?:review|passport|documents?|accounts?|cards?|balance|transactions?|upload|status)\b',re.I)
# An explicit request to obtain *another person's* private banking data is not
# a request for the signed-in customer's own records. Refuse before tool access.
THIRD_PARTY = re.compile(
    r"\b(?:another|other|different)\s+(?:client|customer|person|user)(?:['’]s)?\b.{0,65}\b(?:account|balance|statement|transactions?|card|loan|profile|information|details|income|assets)\b"
    r"|\b(?:someone|somebody|anyone)\s+else(?:['’]s)?\b.{0,65}\b(?:account|balance|statement|transactions?|card|loan|profile|information|details|income|assets)\b"
    r"|\b(?:my\s+)?(?:friend|colleague|neighbour|neighbor)(?:['’]s)?\b.{0,55}\b(?:account|balance|statement|transactions?|card|loan)\b",
    re.I)
# A named person's records ("brief me on David's portfolio", "show Tan Wei's card"). The name is case-sensitive (a
# capital letter, matched on the original text, never the lower-case typo copy), so "what's my balance" or "today's
# transactions" never match; the record words are not. Words that are not names are listed (NOT_A_NAME).
NOT_A_NAME = ('What|That|There|Here|It|Let|Who|Where|When|How|Why|He|She|This|Today|Tomorrow|Yesterday|Tonight|Week|Month|Year|'
    'Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|January|February|March|April|May|June|July|August|September|'
    'October|November|December|Bank|UBS Connect|UBS|Connect|Singapore|Visa|Mastercard|Apple|Google|Mother|Father|Valentine|Christmas|New Year')
# Financial records only: "Sophie's details" (her own RM's contact) is not another client's data.
NAMED_RECORDS = re.compile(
    r"\b(?!(?:" + NOT_A_NAME + r")['’]s\b)[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?['’]s\s+"
    r"(?i:(?:(?:credit|debit|savings|current|joint|bank|investment|fixed\s+deposit|home|personal|travel)\s+)?"
    r"(?:portfolios?|accounts?|balances?|statements?|transactions?|cards?|loans?|income|assets|"
    r"investments?|savings|records|holdings|spending|finances|deposits?))\b")
# Reject direct attempts to override the banking approval/access rules, without
# mistaking general questions about security or prompt injection for instructions.
OVERRIDE = re.compile(
    r"\b(?:ignore|disregard|override|bypass)\b.{0,90}\b(?:rules|instructions|safety|security|guardrails|restrictions)\b"
    r".{0,110}\b(?:approve|accept|mark|authori[sz]e|grant|release|transfer|loan|access|balance)\b"
    r"|\b(?:approve|grant|authori[sz]e)\s+(?:my|this|the)\s+(?:loan|application|credit|limit)\b"
    r".{0,100}\b(?:ignore|bypass|without\s+checks)\b", re.I)
BANK_DECISION = re.compile(r"\b(?:please\s+)?(?:approve|authori[sz]e|grant)\s+(?:my|this|the)\s+(?:loan|loan\s+application|credit|card\s+limit|mortgage|banking\s+request)\b"
    r"|\bmark\s+(?:all\s+)?(?:my|these|the)\b.{0,50}\b(?:documents?|review|checklist)\b.{0,40}\b(?:accepted|approved|complete(?:d)?)\b",re.I)
PERSONAL = re.compile(r'\b(my|mine)\b.*\b(balance|transactions|due|limit|account number)\b', re.I)
FRAUD = re.compile(
    r"\b(?:unrecognised|unrecognized|unfamiliar|unauthorised|unauthorized|suspicious|fraudulent)\s+(?:(?:card|bank)\s+)?(?:charge|transaction|payment|transfer)s?\b"
    r"|\b(?:don['’]?t|do not|didn['’]?t|did not)\s+(?:recognise|recognize|authorise|authorize|make)\b.{0,80}\b(?:charge|transaction|payment|transfer)s?\b"
    r"|\b(?:charge|transaction|payment|transfer)s?\b.{0,80}\b(?:don['’]?t|do not|didn['’]?t|did not)\s+(?:recognise|recognize|authorise|authorize|make)\b"
    r"|\b(?:charge|transaction|payment)s?\b.{0,40}\bnot mine\b"
    # Informal reports of a card being used by somebody else, even without the word "transaction".
    r"|\b(?:someone|somebody|another person|a stranger)\s+(?:has\s+(?:been\s+)?|had\s+|is\s+|might\s+have\s+|may\s+have\s+|could\s+have\s+|was\s+)?(?:used|using|spent\s+on|made\s+(?:purchases|payments)\s+(?:with|on))\b.{0,55}\b(?:my|our)\s+(?:credit\s+|debit\s+)?card\b"
    r"|\b(?:my|our)\s+(?:credit\s+|debit\s+)?card\s+(?:has\s+been\s+|was\s+|is\s+)?(?:used|being\s+used|charged)\b.{0,65}\b(?:without\s+(?:my\s+)?(?:permission|consent)|by\s+(?:someone|somebody|another\s+person)|not\s+(?:me|mine))\b"
    r"|\b(?:someone|somebody)\s+(?:has\s+|might\s+have\s+)?(?:used|using)\s+(?:my\s+)?(?:credit\s+|debit\s+)?card\b"
    r"|\b(?:my|our)\s+(?:credit\s+|debit\s+)?card\b.{0,55}\b(?:without\s+(?:my\s+)?permission|compromised|misused)\b"
    r"|\bi\s+wasn['’]?t\s+(?:even\s+)?(?:there|at\s+(?:that\s+)?(?:shop|store|place))\b.{0,75}\b(?:charge|payment|transaction)s?\b"
    # An alibi after a charge ("why is there a $2,400 charge?? i was in singapore the whole night") disowns it too.
    r"|\b(?:charge|transaction|payment)s?\b.{0,80}\bi\s+(?:was\s+(?:in|at)\b[^.!?]{0,40}\b(?:whole|all)\s+(?:night|day|evening|weekend)"
    r"|wasn['’]?t\s+(?:even\s+)?(?:there|overseas|abroad))\b", re.I)
# Signs she may need extra care (RM copilot build plan 2a): job loss, debt trouble, bereavement, serious illness, or
# someone pressuring her about money. It only changes the UI's suggestions (a person instead of product or meeting
# chips); the answer, routing and priority stay the same. Ordinary loan, investment and payment questions do not match.
CARE = re.compile(
    r"\b(?:lost|losing)\s+(?:my|our)\s+(?:job|income|livelihood)\b|\b(?:laid\s+off|retrenched|retrenchment|made\s+redundant|unemployed|out\s+of\s+(?:a\s+)?(?:work|job))\b"
    r"|\b(?:got|been|was|were)\s+(?:fired|sacked|let\s+go)\b"
    r"|\b(?:can['’]?t|cannot|unable\s+to|struggling\s+to|could\s+not|couldn['’]?t)\s+(?:afford|repay|make\s+ends\s+meet"
    r"|keep\s+up\s+with\s+(?:(?:my|our|the)\s+)?(?:payments?|repayments?|bills|loans?|mortgage|debts?|instal?ments?))\b"
    r"|\b(?:can['’]?t|cannot|unable\s+to|struggling\s+to)\s+pay\s+(?:(?:my|our|the)\s+)?(?:bills|rent|debts?|loans?|mortgage|instal?ments?|repayments?)\b"
    r"|\b(?:in|into|drowning\s+in|deep\s+in|over\s+my\s+head\s+in)\s+(?:serious\s+|a\s+lot\s+of\s+)?debts?\b(?!\s+(?:funds?|securities|instruments?|markets?|etfs?))|\bdebt\s+collectors?\b|\bloan\s+sharks?\b|\bbankrupt\w*"
    r"|\bbehind\s+(?:on|with)\s+(?:(?:my|our|the)\s+)?(?:payments?|repayments?|bills|rent|mortgage|loan|instal?ments?)\b|\bfinancial\s+(?:hardship|difficult(?:y|ies)|trouble)\b"
    r"|\bpassed\s+away\b|\bbereave\w*|\bfuneral\b|\bwidow(?:ed|er)?\b"
    r"|\b(?:my|our)\s+late\s+(?:husband|wife|partner|father|mother|dad|mum|mom|son|daughter|parents?|brother|sister|grand\w+)\b"
    r"|\b(?:husband|wife|partner|father|mother|dad|mum|mom|son|daughter|parents?|brother|sister|grand\w+)\s+(?:has\s+|had\s+|just\s+)*died\b"
    r"|\b(?:diagnosed\s+with|(?:have|has|got|with)\s+cancer|chemo\w*|terminal(?:ly)?\s+ill|(?:seriously|critically|very)\s+ill|in\s+(?:the\s+)?hospital|hospitali[sz]ed|dementia|had\s+a\s+stroke)\b"
    r"|\b(?:pressur\w*|forc\w*|push\w*|threaten\w*|blackmail\w*|scar\w*)\s+me\s+(?:in)?to\s+(?:send|transfer|give|pay|lend|withdraw|sign|take\s+out|borrow|hand\s+over)\b"
    r"|\b(?:pressur\w*|threaten\w*|blackmail\w*)\s+me\b.{0,40}\b(?:money|cash|savings|account|loan|transfer|pay)\w*"
    r"|\btold\s+me\s+not\s+to\s+tell\s+(?:the\s+bank|anyone|anybody)\b|\b(?:won['’]?t|doesn['’]?t|does\s+not|will\s+not)\s+let\s+me\s+(?:access|see|use|touch)\s+my\s+(?:own\s+)?(?:money|account|savings|card)\b", re.I)


def needs_care(messages) -> bool:
    """Any of her messages in this (bounded) history: once she has said it, later suggestions stay careful too."""
    return any(CARE.search(text) or CARE.search(normalise(text)) for text in (redact_details(m.content)[0] for m in messages if m.role=='user'))


def assess_request(req: ChatRequest) -> RiskAssessment:
    """Assess validated history without modifying it; fixed responses precede any AI or customer read."""
    assessments = [redact_details(message.content) for message in req.messages]
    question = assessments[-1][0]
    counts: dict[RedactionType, int] = {}
    for _, redactions in assessments:
        for redaction in redactions:
            counts[redaction.type] = counts.get(redaction.type, 0) + redaction.count
    flags: list[RiskFlag] = []
    if counts:flags.append('secret_shared')
    # Typos must not hide urgency: "i lsot my crad" is a lost card. The forgiving copy is only used to match.
    probe = normalise(question)
    lost = is_lost_card(question) or is_lost_card(probe)
    if lost:flags.append('lost_card')
    fraud = bool(FRAUD.search(question) or FRAUD.search(probe))
    if fraud:flags.append('fraud_suspected')
    human = bool(HUMAN.search(question) or HUMAN.search(probe))
    if human:flags.append('human_requested')
    fixed: FixedResponse | None = None
    if counts:fixed = FixedResponse(code='secrets', needs_human=True)
    elif lost:fixed = FixedResponse(code='lost_card', needs_human=True)
    elif THIRD_PARTY.search(probe) or (req.mode=='public' and DELEGATE_CLAIM.search(probe) and PRIVATE_REQUEST.search(probe)):
        fixed = FixedResponse(code='other_customer_data', needs_human=False)
    elif OVERRIDE.search(probe) or BANK_DECISION.search(probe):fixed = FixedResponse(code='instruction_override', needs_human=False)
    elif NAMED_RECORDS.search(question):fixed = FixedResponse(code='other_customer_data', needs_human=False)  # after an override, as before
    elif human:fixed = FixedResponse(code='human_support', needs_human=True)
    elif req.mode == 'public' and PERSONAL.search(question):
        fixed = FixedResponse(code='guest_balance', needs_human=False)
    return RiskAssessment(redacted_message=question,
        redactions=[Redaction(type=kind, count=count) for kind, count in counts.items()],
        risk_flags=flags, priority='P0' if lost or fraud else 'P1' if counts else 'P3', fixed_response=fixed, care=needs_care(req.messages))
