"""Anchor comparison in scripts/verify_audit.py. Rows are sample audit_verify_chain results; the SQL function itself
is exercised against a database (migration 0005)."""
from scripts.verify_audit import problems

H1, H2, H3 = 'a' * 64, 'b' * 64, 'c' * 64
ANCHOR = {'project': 'demo.supabase.co', 'checked': 10, 'head_hash': H1}


def row(checked, head, hash_at=None, bad=None):
    return {'checked': checked, 'first_bad_event_id': bad, 'head_hash': head, 'hash_at': hash_at}


def test_intact_without_anchor():
    assert problems(row(3, H1), None) == []


def test_grown_chain_consistent_with_anchor():
    assert problems(row(12, H2, hash_at=H1), ANCHOR) == []


def test_broken_row_is_reported():
    assert 'broken at event 7' in problems(row(7, H2, bad=7), None)[0]


def test_removed_newest_rows_are_reported():
    assert 'rows removed' in problems(row(8, H2), ANCHOR)[0]


def test_rewritten_history_is_reported():
    assert 'history rewritten' in problems(row(12, H3, hash_at=H3), ANCHOR)[0]
