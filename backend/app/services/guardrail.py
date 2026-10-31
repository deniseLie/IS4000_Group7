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
    elif human:fixed = FixedResponse(code='human_support', needs_human=True)
    elif req.mode == 'public' and PERSONAL.search(question):
        fixed = FixedResponse(code='guest_balance', needs_human=False)
    return RiskAssessment(redacted_message=question,
        redactions=[Redaction(type=kind, count=count) for kind, count in counts.items()],
        risk_flags=flags, priority='P0' if lost or fraud else 'P1' if counts else 'P3', fixed_response=fixed)
