"""Store embedding vectors for published knowledge sections (migration 0099, KNOWLEDGE_SEARCH=hybrid).

Run as the database owner (app roles only read kb.section_embeddings) after every knowledge migration. Uses the
embedding settings (EMBEDDING_API_KEY, EMBEDDING_BASE_URL, EMBEDDING_MODEL), never LLM_API_KEY. A section is embedded
as its title, heading and body; keywords are search metadata and are left out. Sections whose text hash already has a
vector for the model are skipped. Prints counts only.
Exit 0 = every published section has a current vector; 1 = some are missing or stale (--check); 2 = could not run.

  python -m scripts.embed_knowledge --database-url postgresql://...          # embed new or changed sections
  python -m scripts.embed_knowledge --database-url postgresql://... --check  # report only
"""
import argparse
import hashlib
import sys

BATCH=64
PUBLISHED="""select s.article_id,s.version,s.anchor,a.title,s.heading,s.body,e.content_sha256
from kb.sections s join kb.articles a using(article_id,version)
left join kb.section_embeddings e on e.article_id=s.article_id and e.version=s.version and e.anchor=s.anchor and e.model=%s
where a.status='published' order by 1,2,3"""


def text(title: str,heading: str,body: str) -> str:
    return f'{title}\n{heading}\n{body}'


def digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def pending(db,model: str) -> list[tuple[str,int,str,str]]:
    """(article_id, version, anchor, text) for published sections with no vector for this model, or a vector of older text."""
    rows=db.execute(PUBLISHED,(model,)).fetchall()
    return [(article_id,version,anchor,value) for article_id,version,anchor,title,heading,body,stored in rows
        if stored!=digest(value:=text(title,heading,body))]


def store(db,model: str,rows: list[tuple[str,int,str,str]],runner=None) -> int:
    """`runner` is an open embeddings.Runner; one is opened and closed here if not given."""
    from app.services import embeddings
    own=runner is None and bool(rows)
    runner=embeddings.Runner() if own else runner
    try:
        for start in range(0,len(rows),BATCH):
            batch=rows[start:start+BATCH]
            vectors=runner.embed([value for *_,value in batch],model).vectors
            for (article_id,version,anchor,value),vector in zip(batch,vectors):
                db.execute("""insert into kb.section_embeddings(article_id,version,anchor,model,content_sha256,embedding)
                    values (%s,%s,%s,%s,%s,%s::real[]::extensions.vector)
                    on conflict (article_id,version,anchor,model) do update
                    set content_sha256=excluded.content_sha256,embedding=excluded.embedding,embedded_at=now()""",
                    (article_id,version,anchor,model,digest(value),vector))
    finally:
        if own:runner.close()
    return len(rows)


def main(argv=None) -> int:
    from app.core.config import settings
    ap=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--database-url',required=True,help='Owner connection to the database with migration 0099 applied')
    ap.add_argument('--model',default=settings.embedding_model)
    ap.add_argument('--check',action='store_true',help='Report missing or stale vectors without embedding')
    a=ap.parse_args(argv)
    try:
        import psycopg
        with psycopg.connect(a.database_url) as db:
            rows=pending(db,a.model)
            if a.check:
                print(f'{len(rows)} published sections need a vector for {a.model}')
                return 1 if rows else 0
            print(f'embedded {store(db,a.model,rows)} sections with {a.model}')
    except Exception as error:
        print(f"could not embed knowledge: {getattr(error,'reason',type(error).__name__)}",file=sys.stderr)
        return 2
    return 0


if __name__=='__main__':
    sys.exit(main())
