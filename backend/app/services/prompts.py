"""Versioned prompt files: app/prompts/<version>/{system,format,public_only}.md. Never edit a released version; add a new directory."""
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import re
from app.core.config import settings

ROOT = Path(__file__).resolve().parents[1] / 'prompts'
PARTS = ('system', 'format', 'public_only')

@lru_cache
def load(version):
    if not re.fullmatch(r'v\d+', version) or not (ROOT / version).is_dir():
        raise ValueError(f'Unknown prompt version: {version!r}')
    parts = {p: (ROOT / version / f'{p}.md').read_bytes().decode() for p in PARTS}
    digest = sha256(''.join(parts[p] for p in PARTS).encode()).hexdigest()[:8]
    return {**parts, 'version': version, 'id': f'{version}+{digest}'}

def current():
    return load(settings.prompt_version)
