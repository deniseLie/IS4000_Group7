"""STUB Knowledge Service (architecture §5.7.1): returns fixed sample chunks so agents can be built in parallel.
The live v1 pipeline still uses services/knowledge.py. Never import this from answers.py or api/chat.py."""
from app.schemas.contracts import Fact, KbChunk, RequestContext

STUB = True

_CHUNKS = [
    KbChunk(cite='kb:app-login@v1#steps', title='Trouble signing in to the app', heading='Steps to try',
            body='Check that the app is up to date, then reset your password inside the official app. After repeated failed attempts the account locks; call the official support line to unlock it.', score=0.82),
    KbChunk(cite='kb:pending-transfer@v1#timing', title='Pending transfers', heading='How long a transfer stays pending',
            body='Most transfers to other banks complete within 1 business day. Contact the bank if a transfer is still pending after 2 business days.', score=0.74),
]
_FACTS = {'fee.card_replacement': Fact(value=10.0, unit='SGD', source_id='kb:replacement-card@v1#fee')}


async def search(query: str, scope: str = 'public', k: int = 5, ctx: RequestContext | None = None) -> list[KbChunk]:
    return _CHUNKS[:k]


async def get_fact(fact_id: str) -> Fact | None:
    return _FACTS.get(fact_id)
