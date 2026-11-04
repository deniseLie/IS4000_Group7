import os
from collections.abc import Iterator
import pytest

pytest_plugins=['tests.database_support','tests.data_api_support','tests.langsmith_support','tests.legacy_card_actions']

os.environ['LANGSMITH_TRACING']='false'  # tests must never send runs to LangSmith, even when the root .env traces
os.environ['AUDIT_STORE']='0'  # nor audit events (append-only, cannot be cleaned up)
os.environ['GUEST_RATE_CAPACITY']=os.environ['RATE_CAPACITY']='100000'  # one test client address; tests/test_gateway.py checks the limit itself
os.environ['CHAT_TRANSPORT']='api'  # unit suites must never use the signed-in CLI
os.environ['PIPELINE']='v1'  # individual integration fixtures opt in to their pipeline
if os.getenv('TEST_EMBEDDINGS_LIVE')!='1':os.environ['EMBEDDING_API_KEY']=''  # keyword-only search: no embedding calls unless opted in
os.environ['CONVERSATION_STORE']='off'
os.environ['RUN_WORKER_IN_API']='false'  # native worker fixtures opt in with their restricted local login


@pytest.fixture(autouse=True)
def isolated_audit_batch() -> Iterator[None]:
    """Direct service checks must not inherit another test's request-scoped audit batch."""
    from app.services import audit
    token=audit.current_batch.set(None)
    try:yield
    finally:audit.current_batch.reset(token)
