"""Audit trail walkthrough (F24): print one conversation case end to end for an administrator or reviewer.

  python -m scripts.audit_walkthrough --case-id <uuid> --database-url "$ADMIN_DATABASE_URL"      # owner view
  python -m scripts.audit_walkthrough --case-id <uuid> --database-url ... --markdown > case.md     # for slides

Use the project's direct Postgres connection string from Supabase (Project settings > Database), kept out of git.
Rows hold ids, enums and counts only: no chat text and no customer records. The hash chain is verified first,
so the printed trail is shown to be complete and unedited. Customers can see their own case through
public.my_case_timeline(case_id).
"""
import argparse
import json
import sys
from uuid import UUID

COLUMNS=('seq','occurred_at','source','actor','action','outcome','resource','details','request_id')


def render(rows: list[tuple],markdown: bool) -> str:
    if markdown:
        lines=['| # | Time | Source | Actor | Step | Outcome | Record | Details |','|---|---|---|---|---|---|---|---|']
        lines+=[f"| {r[0]} | {r[1]:%H:%M:%S} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} | {json.dumps(r[7],separators=(',',':'))} |" for r in rows]
        return '\n'.join(lines)
    return '\n'.join(f"{r[0]:>3} {r[1]:%Y-%m-%d %H:%M:%S} {r[2]:<8} {r[3]:<34} {r[4]:<40} {r[5]:<8} {r[6]} {json.dumps(r[7],separators=(',',':'))}" for r in rows)


def main(argv: list[str] | None=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--case-id',required=True,type=UUID)
    parser.add_argument('--database-url',required=True)
    parser.add_argument('--markdown',action='store_true')
    args=parser.parse_args(argv)
    import psycopg
    with psycopg.connect(args.database_url) as db:
        bad=db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()[0]
        rows=db.execute('select * from audit.case_timeline(%s)',(args.case_id,)).fetchall()
    if bad is not None:print(f'Audit chain broken at event {bad}: the trail below cannot be trusted.',file=sys.stderr)
    if not rows:print('No audit events for this case.',file=sys.stderr);return 2
    print(render(rows,args.markdown))
    return 1 if bad is not None else 0


if __name__=='__main__':sys.exit(main())
