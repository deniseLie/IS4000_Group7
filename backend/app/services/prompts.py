"""Versioned prompt files: app/prompts/<agent>/<version>/*.md (system.md required). Never edit a released version; add a new directory and docs/prompt-log/<agent>/<version>.md."""
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import re
from app.core.config import settings

ROOT = Path(__file__).resolve().parents[1] / 'prompts'
ORDER = ('system', 'format', 'public_only')  # digest order; keeps the ids of released answer prompts unchanged

@lru_cache
def load(agent, version):
    folder = ROOT / agent / version
    if not re.fullmatch(r'[a-z][a-z_]*', agent) or not re.fullmatch(r'v\d+', version) or not (folder / 'system.md').is_file():
        raise ValueError(f'Unknown prompt: {agent!r} {version!r}')
    parts = {p.stem: p.read_bytes().decode() for p in sorted(folder.glob('*.md'))}
    names = [p for p in ORDER if p in parts] + sorted(set(parts) - set(ORDER))
    digest = sha256(''.join(parts[p] for p in names).encode()).hexdigest()[:8]
    return {**parts, 'agent': agent, 'version': version, 'id': f'{version}+{digest}'}

def version_for(agent):
    """PROMPT_VERSION selects the answer prompt; other agents are set in PROMPT_VERSIONS (JSON object)."""
    version = settings.prompt_version if agent == 'answer' else settings.prompt_versions.get(agent)
    if not version:raise ValueError(f'No prompt version configured for {agent!r}')
    return version

def current(agent='answer'):
    return load(agent, version_for(agent))

def released():
    """Every (agent, version) on disk, for the prompt-log and immutability tests."""
    return sorted((d.parent.name, d.name) for d in ROOT.glob('*/v*') if (d / 'system.md').is_file())
