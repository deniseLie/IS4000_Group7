"""Offline retrieval evaluation: does knowledge search return the right approved article?

Memory store (default): python -m evaluation.retrieval
Database store:         python -m evaluation.retrieval --database-url postgresql://...   (calls public.kb_search, k=4)
Compare set-ups:        python -m evaluation.retrieval --local --search keyword,hybrid --respell on,off --embedding-cache .cache/embeddings.json

Keyword runs make no model calls. Hybrid runs (migration 0088) embed each question with the real embedding provider
(EMBEDDING_API_KEY, EMBEDDING_MODEL; never LLM_API_KEY) and call public.kb_hybrid_search. --local starts a temporary
pgserver database (dbtest extra) with every migration and embeds its sections first. --embedding-cache keeps question
vectors between quality runs; leave it out when timing, so each question is a real embedding request. Times are
measured from this process to the database (psycopg), not through the Data API; the app logs its own timings
(`knowledge_search` log events and LangSmith kb.search runs).

Cases in retrieval_cases.json are synthetic and hand-labelled. `expected` lists acceptable article ids (null: no approved
article covers it, so the right result is nothing). Tune keywords and the similarity minimum against split=tune only;
split=holdout shows whether a change generalises. Non-English cases are reported by group only. The `paraphrase` batch
(held out, written on 3 Nov 2026 before any hybrid run: questions sharing few words with their article, plus new gap,
off-topic and non-English questions) is reported on its own, so `overall` and the floors stay comparable with earlier runs.
"""
import argparse
import json
import statistics
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from app.core.config import settings
from app.services import knowledge
from app.services.typos import for_search

HERE=Path(__file__).parent
K=4  # the number of sections the pipelines pass to the model
Search=Callable[[str],list[str]]


def cases(path: Path=HERE/'retrieval_cases.json') -> list[dict]:
    return json.loads(path.read_text(encoding='utf-8'))


def memory_search(question: str) -> list[str]:
    return [article.id for article in knowledge.search(question)][:K]


@contextmanager
def respelling(on: bool) -> Iterator[None]:
    """KNOWLEDGE_RESPELL for this run only."""
    before=settings.knowledge_respell
    settings.knowledge_respell=on
    try:yield
    finally:settings.knowledge_respell=before


class Vectors:
    """Question vectors from the real embedding provider, optionally kept in a JSON file ({model: {text: vector}})."""
    def __init__(self,model: str,runner,path: Path | None=None) -> None:
        self.model,self.runner,self.path,self.times=model,runner,path,[]
        stored=json.loads(path.read_text(encoding='utf-8')) if path and path.exists() else {}
        self.stored=stored
        self.cache=stored.setdefault(model,{})

    def get(self,text: str) -> list[float]:
        if text not in self.cache:
            embedded=self.runner.embed_query(text,self.model)
            self.times.append(embedded.ms)
            self.cache[text]=embedded.vectors[0]
        return self.cache[text]

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            self.path.write_text(json.dumps(self.stored),encoding='utf-8')


def database_search(db,vectors: Vectors | None=None,min_similarity: float | None=None) -> Search:
    """`db` is an open psycopg connection to a database with the knowledge migrations applied. With `vectors`, hybrid."""
    def search(question: str) -> list[str]:
        text=for_search(question)  # same text services/kb.search sends
        if vectors is None:
            rows=db.execute("select article_id from public.kb_search(%s,'{}'::text[],%s)",(text,K)).fetchall()
        else:
            rows=db.execute("select article_id from public.kb_hybrid_search(%s,%s::real[],%s,%s::real,'{}'::text[],%s)",
                (text,vectors.get(text),vectors.model,min_similarity,K)).fetchall()
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


def percentiles(values: list[float]) -> dict:
    if not values:return {'p50_ms':None,'p95_ms':None}
    ordered=sorted(values)
    return {'p50_ms':round(statistics.median(ordered),1),'p95_ms':round(ordered[min(len(ordered)-1,round(0.95*(len(ordered)-1)))],1)}


def evaluate(search: Search,selected: list[dict]) -> dict:
    rows=[]
    for case in selected:
        start=time.perf_counter()
        found=search(case['question'])
        rows.append({**case,'found':found,'ms':round((time.perf_counter()-start)*1000,1),'rank':rank(case,found) if case['expected'] else None})
    earlier=[r for r in rows if not r.get('batch')]
    batch=[r for r in rows if r.get('batch')=='paraphrase']
    core=[r for r in earlier if r['group']!='non_english']
    groups=lambda selected:{group:summarise([r for r in selected if r['group']==group]) for group in dict.fromkeys(r['group'] for r in selected)}
    return {'k':K,'overall':summarise(core),
        'by_split':{split:summarise([r for r in core if r['split']==split]) for split in ('tune','holdout')},
        'by_group':groups(earlier),
        'paraphrase':{'overall':summarise([r for r in batch if r['group']!='non_english']),'by_group':groups(batch)},
        'latency':percentiles([r['ms'] for r in rows]),
        'rows':rows}


def misses(report: dict) -> list[str]:
    lines=[]
    for r in report['rows']:
        if r['expected'] and r['rank']!=1:lines.append(f"{'MISS' if r['rank'] is None else 'rank'+str(r['rank'])}  {r['id']}: want {r['expected']} got {r['found']}")
        elif not r['expected'] and r['found']:lines.append(f"HIT?  {r['id']}: want nothing got {r['found']}")
    return lines


@contextmanager
def local_database() -> Iterator[str]:
    """Temporary pgserver PostgreSQL with the test bootstrap and every migration, as in tests/database_support.py."""
    import pgserver
    import psycopg
    backend=HERE.parent
    server=pgserver.get_server(Path(tempfile.mkdtemp(prefix='demobank-retrieval-'))/'data',cleanup_mode='delete')
    try:
        uri=server.get_uri()
        with psycopg.connect(uri,autocommit=True) as db:
            db.execute((backend/'tests'/'local_bootstrap.sql').read_text())
            for customer_id,name in [(1001,'alex'),(1002,'michelle'),(1003,'david')]:
                db.execute('insert into auth.users(id,email) values (%s,%s)',(uuid5(NAMESPACE_URL,f'demobank-test-persona:{customer_id}'),f'demo.{name}@example.com'))
            for migration in sorted((backend/'supabase'/'migrations').glob('*.sql')):db.execute(migration.read_text())
        yield uri
    finally:server.cleanup()


def headline(report: dict) -> str:
    overall,batch=report['overall'],report['paraphrase']
    pick=lambda groups,group,key:groups.get(group,{}).get(key)
    embed=report.get('embedding_latency') or {}
    return (f"{report['store']:8} {report['search']:7} respell={'on ' if report['respell'] else 'off'} min={report['min_similarity']!s:5} | "
        f"earlier cases: hit@1 {overall['hit_at_1']} hit@{K} {overall[f'hit_at_{K}']} mrr {overall['mrr']} no-match {overall['no_match_accuracy']} "
        f"holdout hit@1 {report['by_split']['holdout']['hit_at_1']} informal hit@1 {pick(report['by_group'],'informal','hit_at_1')} | "
        f"paraphrase batch: hit@1 {batch['overall']['hit_at_1']} hit@{K} {batch['overall'][f'hit_at_{K}']} no-match {batch['overall']['no_match_accuracy']} "
        f"non-English hit@{K} {pick(batch['by_group'],'non_english',f'hit_at_{K}')} | "
        f"search p50 {report['latency']['p50_ms']} p95 {report['latency']['p95_ms']} ms, embed p50 {embed.get('p50_ms')} p95 {embed.get('p95_ms')} ms")


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    where=parser.add_mutually_exclusive_group()
    where.add_argument('--database-url',help='Evaluate this database (owner connection) instead of the in-memory library')
    where.add_argument('--local',action='store_true',help='Evaluate a temporary local database with every migration (dbtest extra)')
    parser.add_argument('--search',default='keyword',help='Comma list: keyword,hybrid (hybrid needs a database)')
    parser.add_argument('--respell',default='on',help='Comma list: on,off (the chat-spelling table before search)')
    parser.add_argument('--min-similarity',default=str(settings.embedding_min_similarity),help='Comma list for hybrid runs, e.g. 0.3,0.4,0.5')
    parser.add_argument('--embedding-cache',type=Path,help='JSON file of question vectors kept between runs (leave out when timing)')
    parser.add_argument('--split',choices=('all','tune','holdout'),default='all')
    parser.add_argument('--output',type=Path,help='Write the full reports as JSON')
    parser.add_argument('--misses',action='store_true',help='List every case not ranked first')
    args=parser.parse_args()
    searches=args.search.split(',')
    respells=[value=='on' for value in args.respell.split(',')]
    minimums=[float(value) for value in args.min_similarity.split(',')]
    if not set(searches)<={'keyword','hybrid'} or not set(args.respell.split(','))<={'on','off'}:parser.error('unknown --search or --respell value')
    if 'hybrid' in searches and not (args.database_url or args.local):parser.error('hybrid search needs --database-url or --local')
    selected=[c for c in cases() if args.split=='all' or c['split']==args.split]
    reports=[]

    def run(store: str,search: str,respell: bool,minimum: float | None,fn: Search,vectors: Vectors | None=None) -> None:
        with respelling(respell):report=evaluate(fn,selected)
        reports.append({'store':store,'search':search,'respell':respell,'min_similarity':minimum,**report,
            'embedding_model':vectors.model if vectors else None,'embedding_latency':percentiles(vectors.times) if vectors else None})
        if vectors:vectors.times.clear()

    if not (args.database_url or args.local):
        for respell in respells:run('memory','keyword',respell,None,memory_search)
    else:
        import psycopg
        with (local_database() if args.local else nullcontext(args.database_url)) as uri, psycopg.connect(uri,autocommit=True) as db:
            from app.services.embeddings import Runner
            runner=Runner() if 'hybrid' in searches else None
            vectors=Vectors(settings.embedding_model,runner,args.embedding_cache) if runner else None
            try:
                if args.local and runner:
                    from scripts.embed_knowledge import pending, store
                    print(f'embedded {store(db,settings.embedding_model,pending(db,settings.embedding_model),runner)} sections with {settings.embedding_model}')
                for respell in respells:
                    if 'keyword' in searches:run('database','keyword',respell,None,database_search(db))
                    if vectors:
                        for minimum in minimums:run('database','hybrid',respell,minimum,database_search(db,vectors,minimum),vectors)
            finally:
                if vectors:vectors.save()
                if runner:runner.close()
    if args.output:args.output.write_text(json.dumps(reports,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for report in reports:
        print(headline(report))
        if args.misses:print('\n'.join(misses(report))+'\n')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
