"""Versioned prompt files: app/prompts/<agent>/<version>/*.md (system.md required). Never edit a released version; add a new directory and docs/prompt-log/<agent>/<version>.md.
Each agent uses one version, chosen only in VERSIONS below and loaded with current(agent); no setting overrides it."""
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / 'prompts'
LOG = Path(__file__).resolve().parents[3] / 'docs' / 'prompt-log'
NOTE_FIELDS = {'goal': ('Goal', 'Scope'), 'change': ('Change from previous', 'Behaviour'), 'results': ('Results', 'Validation'), 'decision': ('Decision',)}
ORDER = ('system', 'format', 'public_only')  # digest order; keeps the ids of released answer prompts unchanged

# The one place a prompt version is chosen: prompt family -> released version. To change one, add app/prompts/<family>/vN
# with its prompt-log entry and edit the version here. The meeting agents keep theirs in registry.MEETING_SPECS (in_use adds them).
VERSIONS = {
    'answer': 'v4',  # the v1 pipeline's one model call (llm.answer_request)
    'planner': 'v13',  # keep related card figures together and separate fee lookup from fee negotiation
    'guide': 'v4', 'accounts': 'v3',  # the v5 runner agents (runner.PROMPTED); v4/v3 (8 Nov): no FX or holiday tools
    'runner_common': 'v3',  # the runner's shared answer contract and step format (v2: never restate her own figures, C12)
    'retail': 'v12',
    'compliance': 'v8',  # v7 (batch 3, P05): her account list from Accounts (input-account_refs) is citable
    'relationship': 'v8',
    'document': 'v6',
    'notice_requirements': 'v2', 'document_evidence': 'v2',  # notice_checks
    'talking_points': 'v3',  # stateful lane 1 (0143), with explicit topic evidence and timeframe guidance
    'rm_planner': 'v6',
    'rm_assistant': 'v9',  # v7: help is a plain reply; follow_up explains the previous answer; v8: UBS Connect name; v9: Zoom check in schedule
}

@lru_cache
def load(agent, version):
    folder = ROOT / agent / version
    if not re.fullmatch(r'[a-z][a-z_]*', agent) or not re.fullmatch(r'v\d+', version) or not (folder / 'system.md').is_file():
        raise ValueError(f'Unknown prompt: {agent!r} {version!r}')
    parts = {p.stem: p.read_bytes().decode() for p in sorted(folder.glob('*.md'))}
    names = [p for p in ORDER if p in parts] + sorted(set(parts) - set(ORDER))
    digest = sha256(''.join(parts[p] for p in names).encode()).hexdigest()[:8]
    return {**parts, 'agent': agent, 'version': version, 'id': f'{version}+{digest}'}

def current(agent):
    """The released prompt this agent uses, by its family name in VERSIONS."""
    if agent not in VERSIONS:raise ValueError(f'No prompt version for {agent!r}')
    return load(agent, VERSIONS[agent])

def in_use():
    """{family: version} for every prompt the running code loads: VERSIONS plus the meeting agents' versions, which the
    meeting side keeps in registry.MEETING_SPECS. The admin dashboard compares it with what actually ran (llm runs)."""
    from app.services.agents import registry
    found = {**VERSIONS, **{spec.prompt: spec.version for spec in registry.MEETING_SPECS.values()}}
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
