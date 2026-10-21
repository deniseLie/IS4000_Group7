"""Credential screening is an explicit CLI boundary; authorisation still requires real server JWT verification."""
import jwt
import pytest
from pydantic import ValidationError
from app.schemas.staff_settings import StaffSettings

SIGNER='synthetic-signing-key-for-config-validation-only'
TOKEN=jwt.encode({'role':'authenticated','aal':'aal2'},SIGNER,algorithm='HS256')
PUBLIC='sb_publishable_synthetic_public_key_for_settings'


def test_public_publishable_and_legacy_anon_keys_are_accepted() -> None:
    for key in [PUBLIC,jwt.encode({'role':'anon'},SIGNER,algorithm='HS256')]:
        config=StaffSettings(supabase_url='https://demo.example.invalid',public_key=key,access_token=TOKEN)
        assert key not in repr(config) and TOKEN not in repr(config)


@pytest.mark.parametrize('key',['sb_secret_synthetic_value_must_never_be_printed',jwt.encode({'role':'service_role'},SIGNER,algorithm='HS256')])
def test_service_keys_are_rejected_without_exposing_input(key: str) -> None:
    with pytest.raises(ValidationError) as denied:StaffSettings(supabase_url='https://demo.example.invalid',public_key=key,access_token=TOKEN)
    assert key not in str(denied.value) and TOKEN not in str(denied.value)


@pytest.mark.parametrize('url',['http://demo.example.invalid','https://user:secret@demo.example.invalid','https://demo.example.invalid?secret=yes','file:///tmp/test'])
def test_staff_tokens_cannot_be_sent_to_cleartext_or_credential_urls(url: str) -> None:
    with pytest.raises(ValidationError):StaffSettings(supabase_url=url,public_key=PUBLIC,access_token=TOKEN)


def test_illegal_access_token_headers_are_rejected_without_exposing_input() -> None:
    token='private-input\nheader'
    with pytest.raises(ValidationError) as denied:StaffSettings(supabase_url='https://demo.example.invalid',public_key=PUBLIC,access_token=token)
    assert token not in str(denied.value)
