"""Policy facade. Authorisation rules are real; has_consent remains an unused legacy stub.
No sharing workflow may use that stub; a JWT-scoped consent reader is still required.
"""
from app.services.authorization import AUTO_TYPES, decide

STUB = True

def has_consent(customer_id: int, purpose: str, recipient: str) -> dict:
    return {'granted': False, 'grant_id': None}
