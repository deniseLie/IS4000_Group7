"""Client query speech-acts and per-turn planner boundaries. No banking decisions.

Only the latest client utterance can authorise a new action/domain. An LLM draft
is a proposal, not permission to start a document or RM workflow. These checks
are conservative: substantive answers still require approved evidence.
"""
import re

EXPLANATION = re.compile(
    r"^\s*(?:what\s+(?:does|do)\b.{0,100}\bmean\b|"
    r"(?:what(?:'s|s| is)|wats)\s+the\s+(?:difference|meaning)\b|"
    r"what\s+(?:is|are)\s+(?:a|an|the)\s+(?:temporary|permanent|credit\s+limit|card\s+limit)\b|"
    r"how\s+does\b.{0,100}\bwork\b|explain\b|define\b|"
    r"tell\s+me\s+(?:about|the difference)\b|difference\s+between\b)"
    r"|\b(?:difference\s+between|compared\s+to|versus|vs\.?)\b", re.I)

DOCUMENT_ACTION = re.compile(r"\b(?:uploaded?|uploading|submit(?:ted)?|inspect(?:ion)?|pre[- ]?check|file\s+check|page\s+count|ocr|pdf|docx)\b",re.I)
COMPLIANCE = re.compile(r"\b(?:compliance|kyc|review\s+notice|document\s+checklist|document\s+requirements|"
    r"proof\s+of\s+address|review\s+deadline)\b",re.I)
RELATIONSHIP = re.compile(r"\b(?:relationship\s+manager|\bRM\b|advisor|adviser|banker|appointment|meeting|"
    r"book\s+a\s+time|talk\s+to\s+(?:a\s+)?person|human\s+agent)\b",re.I)
BALANCE = re.compile(r"\b(?:my|our)\s+(?:(?:bank|account|current|savings|multi[- ]currency)\s+)?balances?\b"
    r"|\baccount\s+balances?\b", re.I)
STATEMENT = re.compile(r"\bstatements?\b", re.I)
CARD_METRIC = re.compile(r"\b(?:credit\s+limit|card\s+limit|outstanding\s+balance|credit\s+card)\b", re.I)

def explanation(question: str) -> bool:
    """Explanation/comparison is not an instruction to change a banking record."""
    return bool(EXPLANATION.search(question))

def account_balance(question: str) -> bool:
    return bool(BALANCE.search(question)) and not explanation(question) and not bool(
        re.search(r"\b(?:card\s+balance|credit\s+balance|outstanding\s+balance)\b",question,re.I))

def balance_and_statement(question: str) -> bool:
    return account_balance(question) and bool(STATEMENT.search(question))

def reconcile(question: str, issues: list) -> list:
    """Reconcile a model draft with *current* request scope; never invent new work.

    Returns objects of the same type as supplied (PlannedIssue in production).
    A single multi-part retail question stays within one retail agent to avoid
    contradictory personal answers and unrelated document/RM service actions.
    """
    if not issues:return issues
    factory=type(issues[0])
    # One conceptual banking comparison is one retail request, whatever the
    # model suggests about previous cards/documents/RM exchanges.
    if explanation(question) and (CARD_METRIC.search(question) or BALANCE.search(question)):
        return [factory(domain='retail',question=question)]
    corrected=[]
    for issue in issues:
        domain=issue.domain
        # Downloading/viewing a statement is retail guidance, not a document
        # inspection or a compliance review.
        if domain=='document' and STATEMENT.search(question) and not DOCUMENT_ACTION.search(question):domain='retail'
        if domain=='compliance' and STATEMENT.search(question) and not COMPLIANCE.search(question):domain='retail'
        # The model cannot turn account or statement enquiries into RM handovers.
        if domain=='relationship' and not RELATIONSHIP.search(question) and not COMPLIANCE.search(question):
            domain='retail' if BALANCE.search(question) or STATEMENT.search(question) or CARD_METRIC.search(question) else domain
        # Never start document review from a bare statement/account enquiry.
        if domain=='document' and not DOCUMENT_ACTION.search(question) and not COMPLIANCE.search(question):
            if BALANCE.search(question) or STATEMENT.search(question):domain='retail'
        # If the planner misrouted the request, do not preserve invented
        # sub-questions from the misrouted domain. Reuse the client's words.
        corrected.append(factory(domain=domain,question=question if domain!=issue.domain else issue.question))
    if balance_and_statement(question):
        return [factory(domain='retail',question=question)]
    if STATEMENT.search(question) and not DOCUMENT_ACTION.search(question) and not COMPLIANCE.search(question) and all(i.domain=='retail' for i in corrected):
        return [factory(domain='retail',question=question)]
    # One turn, one conceptual question: no multiplication of sub-agents.
    if explanation(question) and all(i.domain=='retail' for i in corrected):
        return [factory(domain='retail',question=question)]
    return corrected

# Status enquiries are *reads of existing bank tickets*, not permissions to
# initiate another request. Approval/rejection is not inferred from resolution.
LIST_REQUESTS = re.compile(
    r"\b(?:show|list|display|view|see|check|find)\b.{0,45}\b(?:my|our)\s+(?:(?:open|active|pending|recent|all)\s+)?(?:requests?|tickets?|cases?)\b"
    r"|\b(?:my|our)\s+(?:open|active|pending)\s+(?:requests?|tickets?|cases?)\b", re.I)
CANCEL_TICKET = re.compile(
    r"\b(?:cancel|withdraw|stop)\b.{0,45}\b(?:my|the|that|previous|existing)\s+(?:\w+\s+){0,2}(?:requests?|tickets?|cases?)\b",re.I)
STATUS_QUESTION = re.compile(
    r"\b(?:status|progress|update(?:s)?|outcome|decision|approved|rejected|accepted|processed|completed|delayed|overdue|"
    r"gone\s+through|heard\s+back)\b", re.I)
EXISTING_REQUEST = re.compile(
    r"\b(?:my|our|the|that|previous|existing|earlier)\b.{0,85}\b(?:requests?|tickets?|cases?|"
    r"(?:card\s+|credit\s+)?limit\s+(?:increase|change)|limit\s+request)\b"
    r"|\b(?:requests?|tickets?|cases?)\b.{0,65}\b(?:status|progress|approved|rejected|decision|update)\b"
    r"|\bTCK-\d{6,}\b", re.I)

def request_status(question: str) -> bool:
    """Recognise an enquiry about an *existing* request, not product eligibility."""
    text=question.strip()
    if LIST_REQUESTS.search(text) or CANCEL_TICKET.search(text) or (re.search(r"\b(?:taking\s+so\s+long|still\s+waiting|not\s+heard\s+back)\b",text,re.I) and re.search(r"\b(?:request|ticket|case)\b",text,re.I)):return True
    if not text or not STATUS_QUESTION.search(text):return False
    if re.search(r"\b(?:how\s+(?:can|do)\s+i|what\s+(?:is|are)\s+(?:the\s+)?requirements?)\b",text,re.I) and not re.search(r"\bmy\s+(?:request|ticket|case)\b",text,re.I):
        return False
    return bool(EXISTING_REQUEST.search(text) or re.search(
        r"\b(?:has|have|did|is|was)\s+(?:the\s+bank\s+)?(?:my\s+)?(?:card\s+|credit\s+)?limit\s+(?:increase|change|request)\b",text,re.I))
