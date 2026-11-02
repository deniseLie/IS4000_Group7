"""Offline retrieval evaluation: does knowledge search return the right approved article? No model calls.

Memory store (default): python -m evaluation.retrieval
Database store:         python -m evaluation.retrieval --database-url postgresql://...   (calls public.kb_search, k=4)

Cases in retrieval_cases.json are synthetic and hand-labelled. `expected` lists acceptable article ids (null: no approved
article covers it, so the right result is nothing). Tune keywords against split=tune only; split=holdout shows whether a
change generalises. Non-English cases are reported separately because both stores match English words only.
"""
import argparse
import json
from collections.abc import Callable
from pathlib import Path
from app.services import knowledge
from app.services.typos import respell

HERE=Path(__file__).parent
K=4  # the number of sections the pipelines pass to the model
Search=Callable[[str],list[str]]


def cases(path: Path=HERE/'retrieval_cases.json') -> list[dict]:
    return json.loads(path.read_text(encoding='utf-8'))


def memory_search(question: str) -> list[str]:
    return [article.id for article in knowledge.search(question)][:K]


def database_search(db) -> Search:
    """`db` is an open psycopg connection to a database with the knowledge migrations applied."""
    def search(question: str) -> list[str]:
        # Same spelling fix as services/kb.search before the RPC.
        rows=db.execute("select article_id from public.kb_search(%s,'{}'::text[],%s)",(respell(question),K)).fetchall()
        return list(dict.fromkeys(row[0] for row in rows))  # sections -> article ranking
    return search


def rank(case: dict,found: list[str]) -> int | None:
    return next((i+1 for i,article in enumerate(found) if article in case['expected']),None)


def summarise(rows: list[dict]) -> dict:
    covered=[r for r in rows if r['expected']]
    uncovered=[r for r in rows if not r['expected']]
    ranks=[r['rank'] for r in covered]
    return {'covered':len(covered),
        'hit_at_1':round(sum(rank==1 for rank in ranks)/len(covered),3) if covered else None,
        f'hit_at_{K}':round(sum(rank is not None for rank in ranks)/len(covered),3) if covered else None,
        'mrr':round(sum(1/rank for rank in ranks if rank)/len(covered),3) if covered else None,
        'uncovered':len(uncovered),
        'no_match_accuracy':round(sum(not r['found'] for r in uncovered)/len(uncovered),3) if uncovered else None}


def evaluate(search: Search,selected: list[dict]) -> dict:
    rows=[{**case,'found':(found:=search(case['question'])),'rank':rank(case,found) if case['expected'] else None} for case in selected]
    english=[r for r in rows if r['group']!='non_english']
    return {'k':K,'overall':summarise(english),
        'by_split':{split:summarise([r for r in english if r['split']==split]) for split in ('tune','holdout')},
        'by_group':{group:summarise([r for r in rows if r['group']==group]) for group in dict.fromkeys(r['group'] for r in rows)},
        'rows':rows}


def misses(report: dict) -> list[str]:
    lines=[]
    for r in report['rows']:
        if r['expected'] and r['rank']!=1:lines.append(f"{'MISS' if r['rank'] is None else 'rank'+str(r['rank'])}  {r['id']}: want {r['expected']} got {r['found']}")
        elif not r['expected'] and r['found']:lines.append(f"HIT?  {r['id']}: want nothing got {r['found']}")
    return lines


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--database-url',help='Evaluate public.kb_search in this database instead of the in-memory library')
    parser.add_argument('--split',choices=('all','tune','holdout'),default='all')
    parser.add_argument('--output',type=Path,help='Write the full report as JSON')
    parser.add_argument('--misses',action='store_true',help='List every case not ranked first')
    args=parser.parse_args()
    selected=[c for c in cases() if args.split=='all' or c['split']==args.split]
    if args.database_url:
        import psycopg
        with psycopg.connect(args.database_url,autocommit=True) as db:report={'store':'database',**evaluate(database_search(db),selected)}
    else:report={'store':'memory',**evaluate(memory_search,selected)}
    if args.output:args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:report[key] for key in ('store','k','overall','by_split','by_group')},indent=2))
    if args.misses:print('\n'.join(misses(report)))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
