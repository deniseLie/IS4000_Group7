"""Versioned prompt files: app/prompts/<agent>/<version>/*.md (system.md required). Never edit a released version; add a new directory and docs/prompt-log/<agent>/<version>.md."""
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import re
from app.core.config import settings

ROOT = Path(__file__).resolve().parents[1] / 'prompts'
LOG = Path(__file__).resolve().parents[3] / 'docs' / 'prompt-log'
NOTE_FIELDS = {'goal': ('Goal', 'Scope'), 'change': ('Change from previous', 'Behaviour'), 'results': ('Results', 'Validation'), 'decision': ('Decision',)}
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

def in_use():
    """{family: version} for every prompt the running code is configured to load: settings for the v1 answer and the
    shared domain prompt, module constants for the rest. The admin dashboard compares it with what actually ran (llm runs)."""
    from app.services import notice_checks, orchestrator, talking_points
    from app.services.agents import compliance, document, registry, relationship, retail, runner
    from app.services.rm import graph, handlers
    found = {'answer': settings.prompt_version, 'domain_common': settings.prompt_versions.get('domain_common'), 'planner': orchestrator.PLANNER,
        'retail': retail.PROMPT, 'compliance': compliance.PROMPT, 'relationship': relationship.PROMPT, 'document': document.PROMPT,
        'runner_common': runner.COMMON, 'rm_planner': graph.PLANNER_VERSION, 'rm_assistant': handlers.ASSISTANT_VERSION,
        'notice_requirements': notice_checks.PLAN_PROMPT, 'document_evidence': notice_checks.EVIDENCE_PROMPT, 'talking_points': talking_points.PROMPT,
        **{registry.SPECS[agent].prompt: version for agent, version in runner.PROMPTS.items()}}
    found['minutes_writer']='v1'
    return {family: version for family, version in sorted(found.items()) if version}

def released():
    """Every (agent, version) on disk, for the prompt-log and immutability tests."""
    return sorted((d.parent.name, d.name) for d in ROOT.glob('*/v*') if (d / 'system.md').is_file())

@lru_cache
def notes():
    """(agent, version) -> date, goal, change, results and decision from docs/prompt-log/<agent>/<version>.md (the log's table rows;
    older entries name some fields differently). Empty where the docs are not deployed."""
    found = {}
    for path in sorted(LOG.glob('*/v*.md')):
        text = path.read_text(encoding='utf-8')
        rows = dict(re.findall(r'^\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|\s*$', text, re.M))
        date = re.search(r'\((\d{4}-\d{2}-\d{2})\)', text.partition('\n')[0])
        found[(path.parent.name, path.stem)] = {'date': date.group(1) if date else None,
            **{key: next((rows[f][:400] for f in fields if rows.get(f)), None) for key, fields in NOTE_FIELDS.items()}}
    return found
