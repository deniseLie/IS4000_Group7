"""Safe, bounded public-knowledge query expansion for conversational follow-ups.

This is query text only. No prior message is treated as a verified banking fact.
"""
import re
from collections.abc import Sequence
FOLLOW_UP=re.compile(r"^\s*(?:what(?:\s+about|\s+if)|how about|and |also |is (?:that|it)|can (?:i|you)|does (?:that|it)|the (?:other|same)|actually|instead|why|when|where)\b",re.I)
REFORMULATION=re.compile(r"^\s*(?:explain|rephrase|summari[sz]e|simplify)\s+(?:that|this|it|your (?:previous |last )?answer)\b",re.I)
LOAN_TYPES=re.compile(r"有抵押贷款|无抵押贷款|\bpinjaman\s+(?:tidak\s+)?bercagar\b|பிணைய(?:ுள்ள|ில்லாத)\s+கட",re.I)

def retrieval_query(question: str,history: Sequence) -> str:
    """Expand *short* context-dependent public knowledge questions, not new topics.

    Uses only recent redacted user messages. Never includes a generated assistant
    answer as trusted evidence. It cannot grant permissions or fetch personal data.
    """
    if len(question)>75 or not (FOLLOW_UP.search(question) or REFORMULATION.search(question)) or not history:return question
    previous=[]
    for message in reversed(history[:-1]):
        if message.role!='user':continue
        previous.append(message.content)
        if len(previous)>=2:break
    if not previous:return question
    # Older questions only provide search vocabulary, not facts to cite.
    return f'{previous[0][:140]} {question}'


def public_query(question: str,history: Sequence) -> str:
    """Add search vocabulary for an explicit rephrasing request or a supported translated term."""
    query=retrieval_query(question,history) if REFORMULATION.search(question) else question
    if LOAN_TYPES.search(query):return f'{query[:3900]} secured unsecured loan collateral'
    return query

