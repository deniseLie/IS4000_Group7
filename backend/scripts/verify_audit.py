"""Audit chain check (system-architecture.md §6, F3, migration 0005). Calls public.audit_verify_chain, compares the result
with the anchor recorded last time (docs/evaluation/evidence/audit-anchor.json, kept in git so it lives outside the database),
and with --record saves the current head as the new anchor. Prints counts and hashes only.

Needs SUPABASE_URL, SUPABASE_ANON_KEY and a login file: JSON {"email","password"} or a list of them (first is used),
from --login-file or TEST_PERSONA_FIXTURE_FILE. The function is granted to signed-in users only.
Exit 0 = intact and consistent with the anchor; 1 = broken or inconsistent; 2 = could not check.

  python -m scripts.verify_audit            # check against the anchor
  python -m scripts.verify_audit --record   # check, then record the current head
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse

ANCHOR = Path(__file__).resolve().parents[2] / 'docs' / 'evaluation' / 'evidence' / 'audit-anchor.json'


def problems(result, anchor):
    """result: one row of audit_verify_chain(anchor['checked']); anchor: the recorded {checked, head_hash} or None."""
    found = []
    if result.get('first_bad_event_id') is not None:
        found.append(f"chain broken at event {result['first_bad_event_id']} (row edited or removed)")
    if anchor:
        if result['checked'] < anchor['checked']:
            found.append(f"chain has {result['checked']} events, anchor recorded {anchor['checked']} (rows removed)")
        elif result.get('hash_at') != anchor['head_hash']:
            found.append(f"hash after {anchor['checked']} events differs from the anchor (history rewritten)")
    return found


def fetch(at, login):
    from supabase import create_client
    sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_ANON_KEY'])
    sb.auth.sign_in_with_password({'email': login['email'], 'password': login['password']})
    return sb.rpc('audit_verify_chain', {'p_at': at}).execute().data[0]


def main(argv=None):
    from dotenv import load_dotenv
    load_dotenv()
    ap = argparse.ArgumentParser(description='Verify the audit hash chain against the recorded anchor.')
    ap.add_argument('--anchor', type=Path, default=ANCHOR)
    ap.add_argument('--login-file', default=os.getenv('TEST_PERSONA_FIXTURE_FILE'))
    ap.add_argument('--record', action='store_true', help='record the current head as the new anchor after a passing check')
    a = ap.parse_args(argv)
    if not (a.login_file and os.getenv('SUPABASE_URL') and os.getenv('SUPABASE_ANON_KEY')):
        print('needs SUPABASE_URL, SUPABASE_ANON_KEY and --login-file (or TEST_PERSONA_FIXTURE_FILE)')
        return 2
    login = json.loads(Path(a.login_file).read_text())
    login = login[0] if isinstance(login, list) else login
    project = urlparse(os.environ['SUPABASE_URL']).hostname
    anchor = json.loads(a.anchor.read_text()) if a.anchor.exists() else None
    if anchor and anchor.get('project') != project:
        print(f"anchor is for {anchor.get('project')}, not {project}")
        return 2
    try:
        result = fetch(anchor['checked'] if anchor else None, login)
    except Exception as e:
        print(f'could not check: {type(e).__name__}')
        return 2
    print(json.dumps({k: result.get(k) for k in ('checked', 'first_bad_event_id', 'head_hash')}))
    found = problems(result, anchor)
    for p in found:
        print('FAIL', p)
    if found:
        return 1
    print('OK, ' + (f"consistent with the anchor of {anchor['checked']} events" if anchor else 'no anchor recorded yet'))
    if a.record:
        a.anchor.parent.mkdir(parents=True, exist_ok=True)
        a.anchor.write_text(json.dumps({'project': project, 'checked': result['checked'], 'head_hash': result['head_hash'],
                                        'recorded_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}, indent=2) + '\n')
        print(f"anchor recorded: {result['checked']} events -> {a.anchor}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
