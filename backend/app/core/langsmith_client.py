"""LangSmith client for evaluation runs and opt-in local tracing (docs/evaluation/README.md §2).
Every string in run inputs, outputs, metadata and errors passes through privacy.redact before upload.
Installing the client never enables tracing: that stays with LANGSMITH_TRACING (off by default)."""
import langsmith
from app.services.privacy import redact

def scrub(value):
    """Redact every string in JSON-shaped run data; keys and non-string values are kept."""
    if isinstance(value,str):return redact(value)[0]
    if isinstance(value,dict):return {k:scrub(v) for k,v in value.items()}
    if isinstance(value,list):return [scrub(v) for v in value]
    return value

def client(**kwargs) -> langsmith.Client:
    return langsmith.Client(anonymizer=scrub,**kwargs)

def install(**kwargs) -> langsmith.Client:
    """Make the redacting client the one LangChain, LangGraph and @traceable upload through."""
    c=client(**kwargs)
    langsmith.configure(client=c)
    return c

def install_if_tracing() -> langsmith.Client | None:
    """App startup: the client starts a background upload thread, so only build it when tracing is switched on."""
    return install() if langsmith.utils.tracing_is_enabled() else None
