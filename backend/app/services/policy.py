"""Authorisation and consent facade; grants and revocations belong to confirmed actions."""
from app.services.authorization import AUTO_TYPES, decide
from app.services.consent import has_consent

STUB = False
