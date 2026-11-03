"""Safe, bounded public-knowledge query expansion for conversational follow-ups.

This is query text only. No prior message is treated as a verified banking fact.
"""
import re
from collections.abc import Sequence
FOLLOW_UP=re.compile(r"^\s*(?:what(?:\s+about|\s+if)|how about|and |also |is (?:that|it)|can (?:i|you)|does (?:that|it)|the (?:other|same)|actually|instead|why|when|where)\b",re.I)

def retrieval_query(question: str,history: Sequence) -> str:
    """Expand *short* context-dependent public knowledge questions, not new topics.

    Uses only recent redacted user messages. Never includes a generated assistant
    answer as trusted evidence. It cannot grant permissions or fetch personal data.
    """
    if len(question)>75 or not FOLLOW_UP.search(question) or not history:return question
    previous=[]
    for message in reversed(history[:-1]):
        if message.role!='user':continue
        previous.append(message.content)
        if len(previous)>=2:break
    if not previous:return question
    # Older questions only provide search vocabulary, not facts to cite.
    return f'{previous[0][:140]} {question}'

