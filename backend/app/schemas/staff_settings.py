"""Private staff CLI connection settings; key screening never substitutes for server JWT verification."""
import re
from typing import Self
from urllib.parse import urlsplit
import jwt
from pydantic import BaseModel,ConfigDict,SecretStr,model_validator


class StaffSettings(BaseModel):
    model_config=ConfigDict(extra='ignore',hide_input_in_errors=True)
    supabase_url: str
    public_key: SecretStr
    access_token: SecretStr

    @model_validator(mode='after')
    def public_connection(self) -> Self:
        url=urlsplit(self.supabase_url)
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('SUPABASE_URL must identify the Supabase server without credentials, query or fragment')
        if url.scheme=='http' and url.hostname not in ('127.0.0.1','localhost','::1'):
            raise ValueError('Staff access tokens require HTTPS except for a loopback native test server')
        key=self.public_key.get_secret_value()
        if not re.fullmatch(r'sb_publishable_[A-Za-z0-9_-]{20,}',key):
            try:claims=jwt.decode(key,options={'verify_signature':False})
            except jwt.InvalidTokenError as error:raise ValueError('SUPABASE_ANON_KEY must be a public publishable key or a legacy anon JWT; secret keys are forbidden') from error
            if claims.get('role')!='anon':raise ValueError('Staff tools require the public anon key; service-role keys are forbidden')
        if not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',self.access_token.get_secret_value()):
            raise ValueError('STAFF_ACCESS_TOKEN must contain the support user compact JWT from an aal2 Auth session')
        return self
