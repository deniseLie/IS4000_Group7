"""Real SQL knowledge retrieval, publication boundaries and read-only app access.

Run with TEST_DATABASE_LOCAL=1 and the optional dbtest dependencies; see test_database_local.py.
Fixtures live only in rolled-back test transactions and never enter the application library.
Hosted RPC/schema-cache behaviour and the app adapter remain separate integration checks.
"""
import os
from typing import TYPE_CHECKING, Literal
import pytest
from tests.database_support import SqlRow, set_customer

if TYPE_CHECKING:
    from psycopg import Connection

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native temporary PostgreSQL with TEST_DATABASE_LOCAL=1')


def search(db: 'Connection[SqlRow]', query: str, categories: list[str], k: int) -> list[SqlRow]:
    return db.execute('select cite,article_id,version,anchor,publisher,scope,score from public.kb_search(%s,%s::text[],%s)',
        (query,categories,k)).fetchall()


def add_article(db: 'Connection[SqlRow]', article_id: str, status: Literal['draft','published','retired'], checked_offset: int, due_offset: int) -> None:
    db.execute("""insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due)
        values (%s,1,'Synthetic test source',array['retail'],'Test publisher','Synthetic SQL fixture','external',%s,
        'https://www.moneysense.gov.sg/understanding-bank-accounts/',current_date+%s,current_date+%s)""",
        (article_id,status,checked_offset,due_offset))
    db.execute("insert into kb.sections(article_id,version,anchor,heading,body,keywords) values (%s,1,'main','Quasar fixture','Quasar synthetic test guidance.','quasar')",
        (article_id,))


@pytest.mark.parametrize('role',['anon','authenticated'])
def test_approved_search_returns_versioned_citations_and_scope(database: 'Connection[SqlRow]', role: str) -> None:
    database.execute(f'set local role {role}')
    rows=search(database,'What happens if I withdraw my fixed deposit early?',['retail'],4)
    assert rows and any(row[1]=='sg-accounts' for row in rows)
    for cite,article_id,version,anchor,publisher,scope,score in rows:
        assert cite==f'kb:{article_id}@v{version}#{anchor}' and publisher and scope and score>0
    assert len(search(database,'fixed deposit',['retail'],1))==1
    assert search(database,'fixed deposit',['document'],4)==[]


@pytest.mark.parametrize('query',['','   ','the and or','quasarxyznonexistent','天气如何'])
def test_no_match_returns_no_unrelated_articles(database: 'Connection[SqlRow]', query: str) -> None:
    database.execute('set local role anon')
    assert search(database,query,[],5)==[]


def test_query_syntax_is_data_and_does_not_execute_sql(database: 'Connection[SqlRow]') -> None:
    database.execute('set local role anon')
    search(database,"' OR 1=1; drop table kb.articles; --",[],5)
    assert database.execute('select count(*) from kb.articles').fetchone()==(47,)  # 0098 seven new articles, support v5, security v2, statements v3, overseas-transfers v2; 0096 lost-card v4, card-limit-change v2, card-controls; 0086-0087 home-loans, personal-loans, loans v2; 0085 overseas-transfers; 0071 card-limit-change; 0077 statements v2; 0081 lost-card v3, unrecognised-charge v3, support v4


def test_publication_switch_preserves_old_citations_and_searches_only_new_version(database: 'Connection[SqlRow]') -> None:
    from psycopg.errors import UniqueViolation
    with pytest.raises(UniqueViolation):
        with database.transaction():
            database.execute("""insert into kb.articles select article_id,2,title,categories,publisher,scope,kind,status,
                reference_url,checked_on,review_due from kb.articles where article_id='sg-accounts'""")
    database.execute("update kb.articles set status='retired' where article_id='sg-accounts'")
    database.execute("""insert into kb.articles select article_id,2,title,categories,publisher,scope,kind,'published',
        reference_url,checked_on,review_due from kb.articles where article_id='sg-accounts'""")
    database.execute("""insert into kb.sections(article_id,version,anchor,heading,body,keywords)
        select article_id,2,anchor,heading,body,keywords from kb.sections where article_id='sg-accounts'""")
    database.execute('set local role anon')
    rows=search(database,'fixed deposit',['retail'],10)
    assert [row[0] for row in rows if row[1]=='sg-accounts']==['kb:sg-accounts@v2#main']
    assert database.execute("select version from kb.sections where article_id='sg-accounts' order by version").fetchall()==[(1,),(2,)]


@pytest.mark.parametrize('query,categories,k',[(None,[],5),('x'*4001,[],5),('loan',[],0),('loan',[],21),
    ('loan',[],None),('loan',None,5),('loan',['private'],5),('loan',[None],5)])
def test_invalid_search_parameters_fail_explicitly(database: 'Connection[SqlRow]', query: str | None, categories: list[str | None] | None, k: int | None) -> None:
    from psycopg.errors import InvalidParameterValue
    database.execute('set local role anon')
    with pytest.raises(InvalidParameterValue):
        with database.transaction():
            database.execute('select * from public.kb_search(%s,%s::text[],%s)',(query,categories,k))


def test_search_excludes_draft_expired_and_not_yet_checked_articles(database: 'Connection[SqlRow]') -> None:
    for article_id,status,checked,due in [('expired','published',-10,-1),('future','published',1,10),
        ('draft','draft',-1,10),('current','published',0,0)]:
        add_article(database,article_id,status,checked,due)
    database.execute('set local role anon')
    rows=search(database,'quasar',[],10)
    assert [row[1] for row in rows]==['current']
    # Historical source details remain visible; unpublished content is never returned by RLS.
    assert database.execute("select article_id from kb.articles where article_id in ('expired','draft')").fetchall()==[('expired',)]
    assert database.execute("select article_id from kb.sections where article_id='draft'").fetchall()==[]


@pytest.mark.parametrize('role',['anon','authenticated'])
def test_app_roles_cannot_modify_knowledge(database: 'Connection[SqlRow]', role: str) -> None:
    from psycopg.errors import InsufficientPrivilege
    database.execute(f'set local role {role}')
    for table in ['kb.articles','kb.sections','kb.section_embeddings']:
        with pytest.raises(InsufficientPrivilege):
            with database.transaction():database.execute(f'delete from {table}')


def test_dataset_article_ids_match_the_database(database: 'Connection[SqlRow]') -> None:
    from evaluation.langsmith_sync import ARTICLE_IDS
    assert ARTICLE_IDS=={row[0] for row in database.execute('select article_id from kb.articles').fetchall()}


def test_facts_table_is_gone_and_articles_are_read_whole(database: 'Connection[SqlRow]') -> None:
    # 0100: figures live in the section text only; kb_get_article returns one published, in-review article in anchor order.
    assert database.execute("select to_regclass('kb.facts'),to_regprocedure('public.kb_get_fact(text)')").fetchone()==(None,None)
    for article_id,status,checked,due in [('draft','draft',-1,10),('expired','published',-10,-1),('current','published',-1,10)]:
        add_article(database,article_id,status,checked,due)
    database.execute('set local role anon')
    for article_id in ('draft','expired','no-such-article'):
        assert database.execute('select * from public.kb_get_article(%s)',(article_id,)).fetchall()==[]
    assert database.execute("select cite,score from public.kb_get_article('current')").fetchall()==[('kb:current@v1#main',0.0)]
    rows=database.execute("select article_id,version,anchor from public.kb_get_article('statements')").fetchall()
    assert rows and {row[:2] for row in rows}=={('statements',3)} and [row[2] for row in rows]==sorted(row[2] for row in rows)


@pytest.mark.parametrize('url',['http://www.moneysense.gov.sg/page','https://www.moneysense.gov.sg.evil.invalid/page',
    'https://user@www.moneysense.gov.sg/page','https://evil.invalid/page'])
def test_external_source_host_constraints(database: 'Connection[SqlRow]', url: str) -> None:
    from psycopg.errors import CheckViolation
    add_article(database,'external','published',-1,10)
    with pytest.raises(CheckViolation):
        with database.transaction():database.execute("update kb.articles set reference_url=%s where article_id='external'",(url,))


@pytest.mark.parametrize('query,expected',[('wats the monthly fee for a savings acc?','savings-fees'),
 ('i got locked out of the app on my phone','app-login-recovery'),('can i get a car loan','car-loans'),
 ('theres a $2400 charge i dont recognise','unrecognised-charge')])
def test_workflow_one_guidance_is_searchable(database: 'Connection[SqlRow]', query: str, expected: str) -> None:
    database.execute('set local role anon')
    rows=search(database,query,[],4)
    assert rows and rows[0][1]==expected and rows[0][4]=='DemoBank coursework project'


@pytest.mark.parametrize('query,expected',[('can i get a home loan','home-loans'),('refinance my mortgage','home-loans'),
 ('how do i apply for a personal loan','personal-loans'),('which loans do you offer','loans')])
def test_loan_guidance_is_searchable(database: 'Connection[SqlRow]', query: str, expected: str) -> None:
    # CR16: home and personal loans (0086) join car loans; the general article v2 (0087) replaces v1, which stays citable.
    database.execute('set local role anon')
    rows=search(database,query,[],4)
    assert rows and rows[0][1]==expected and rows[0][4]=='DemoBank coursework project'
    assert database.execute("select version from kb.articles where article_id='loans' and status='published'").fetchall()==[(2,)]
    assert database.execute("select article_id from public.kb_get_section('loans',1,'main')").fetchall()==[('loans',)]


def test_revised_guidance_publishes_v2_and_keeps_v1_citations(database: 'Connection[SqlRow]') -> None:
    database.execute('set local role anon')
    assert database.execute("select article_id,version from kb.articles where article_id in ('lost-card','support') and status='published' order by 1").fetchall()==[('lost-card',4),('support',5)]  # 0096, 0098
    assert {row[2] for row in search(database,'lost stolen card block',['fraud'],4) if row[1]=='lost-card'}=={4}
    assert database.execute("select article_id from public.kb_get_section('support',1,'main')").fetchall()==[('support',)]


def test_unexpected_code_guidance_publishes_login_recovery_v2(database: 'Connection[SqlRow]') -> None:
    database.execute('set local role anon')
    assert database.execute("select version,status from kb.articles where article_id='app-login-recovery' order by 1").fetchall()==[(1,'retired'),(2,'published')]
    rows=search(database,'also i got a weird OTP last night and now my app is locked',[],4)
    assert rows and rows[0][1]=='app-login-recovery' and rows[0][2]==2
    body=database.execute("select body from public.kb_get_section('app-login-recovery',2,'unexpected-code')").fetchone()[0]
    for guidance in ['did not request','the lock is protecting you','only inside the official DemoBank app','Never use a link in an SMS',
        'including callers, bank staff and this chat','cannot see sign-in attempts, devices or where a sign-in came from']:
        assert guidance in body  # the section orchestrator.linked_evidence fetches by id
    assert database.execute("select article_id from public.kb_get_section('app-login-recovery',1,'steps')").fetchall()==[('app-login-recovery',)]


def test_workflow_one_part_b_guidance_is_published(database: 'Connection[SqlRow]') -> None:
    database.execute('set local role anon')
    assert database.execute("select article_id,version from kb.articles where article_id in ('unrecognised-charge','transfer-timing','account-security-after-fraud') and status='published' order by 1").fetchall()==[
        ('account-security-after-fraud',1),('transfer-timing',1),('unrecognised-charge',3)]  # 0081
    assert 'transfer-timing' in [row[1] for row in search(database,"my rent transfer still says pending, will it make it",[],4)]
    assert database.execute("select article_id from public.kb_get_section('unrecognised-charge',1,'urgent-steps')").fetchall()==[('unrecognised-charge',)]


# Hybrid search (0099). Vectors here are hand-made 3-number fixtures that test the SQL ranking rules only;
# real embeddings come from the provider (scripts/embed_knowledge.py, evaluation/retrieval.py --search hybrid).
FIXTURE_MODEL='test/fixture'


def floats(vector: list[float | None] | None) -> list[float | None] | None:
    return None if vector is None else [None if value is None else float(value) for value in vector]


def embed_section(db: 'Connection[SqlRow]', article_id: str, version: int, anchor: str, vector: list[float], model: str=FIXTURE_MODEL) -> None:
    db.execute('insert into kb.section_embeddings(article_id,version,anchor,model,content_sha256,embedding) values (%s,%s,%s,%s,%s,%s::real[]::extensions.vector)',
        (article_id,version,anchor,model,'0'*64,floats(vector)))


def hybrid(db: 'Connection[SqlRow]', query: str, vector: list[float], minimum: float=0.5, categories: list[str] | None=None, k: int=4, model: str=FIXTURE_MODEL) -> list[str]:
    return [row[0] for row in db.execute('select cite from public.kb_hybrid_search(%s::text,%s::real[],%s::text,%s::real,%s::text[],%s::int)',
        (query,floats(vector),model,minimum,categories or [],k)).fetchall()]


@pytest.mark.parametrize('role',['anon','authenticated'])
def test_hybrid_search_finds_a_section_by_similarity_alone(database: 'Connection[SqlRow]', role: str) -> None:
    embed_section(database,'sg-accounts',1,'main',[1,0,0])
    database.execute(f'set local role {role}')
    assert search(database,'zxqv',[],4)==[]  # no keyword match
    assert hybrid(database,'zxqv',[1,0.1,0])==['kb:sg-accounts@v1#main']
    assert hybrid(database,'zxqv',[0,1,0])==[]  # below the minimum similarity: nothing, never the nearest anyway
    assert hybrid(database,'zxqv',[1,0,0],model='other/model')==[]  # vectors of another model are ignored
    assert hybrid(database,'zxqv',[1,0,0,0])==[]  # and so are vectors of another size
    assert hybrid(database,'zxqv',[1,0,0],categories=['document'])==[]


def test_hybrid_search_keeps_keyword_matches_and_ranks_agreement_first(database: 'Connection[SqlRow]') -> None:
    embed_section(database,'sg-accounts',1,'main',[0,1,0])
    embed_section(database,'savings-fees',1,'main',[1,0,0])
    database.execute('set local role anon')
    keyword=[row[0] for row in search(database,'fixed deposit',[],4)]
    found=hybrid(database,'fixed deposit',[0,1,0],minimum=0.99)
    assert found[0]=='kb:sg-accounts@v1#main' and set(keyword)<=set(found)  # keyword and similarity agree
    assert 'kb:savings-fees@v1#main' not in found  # similarity 0 is under the minimum
    scores=[row[0] for row in database.execute("select score from public.kb_hybrid_search('fixed deposit',array[0,1,0]::real[],%s,0.99,'{}'::text[],4)",(FIXTURE_MODEL,)).fetchall()]
    assert scores==sorted(scores,reverse=True) and all(score>0 for score in scores)


def test_hybrid_search_never_returns_retired_or_out_of_review_sections(database: 'Connection[SqlRow]') -> None:
    article_id,version,anchor=database.execute("""select s.article_id,s.version,s.anchor from kb.sections s join kb.articles a using(article_id,version)
        where a.status='retired' order by 1,2,3 limit 1""").fetchone()
    embed_section(database,str(article_id),int(version),str(anchor),[1,0,0])
    for name,status,checked,due in [('expired','published',-10,-1),('draft','draft',-1,10)]:
        add_article(database,name,status,checked,due)
        embed_section(database,name,1,'main',[1,0,0])
    database.execute('set local role anon')
    assert hybrid(database,'zxqv',[1,0,0])==[]


@pytest.mark.parametrize('vector,model,minimum,k',[([],FIXTURE_MODEL,0.5,4),([1,None,0],FIXTURE_MODEL,0.5,4),(None,FIXTURE_MODEL,0.5,4),
    ([1,0,0],'Bad Model',0.5,4),([1,0,0],None,0.5,4),([1,0,0],FIXTURE_MODEL,1.5,4),([1,0,0],FIXTURE_MODEL,None,4),
    ([1,0,0],FIXTURE_MODEL,0.5,0),([1,0,0],FIXTURE_MODEL,0.5,21)])
def test_invalid_hybrid_parameters_fail_explicitly(database: 'Connection[SqlRow]', vector: list[float | None] | None, model: str | None, minimum: float | None, k: int) -> None:
    from psycopg.errors import InvalidParameterValue
    database.execute('set local role anon')
    with pytest.raises(InvalidParameterValue):
        with database.transaction():
            database.execute('select * from public.kb_hybrid_search(%s::text,%s::real[],%s::text,%s::real,%s::text[],%s::int)',('loan',floats(vector),model,minimum,[],k))


@pytest.mark.parametrize('role',['anon','authenticated'])
def test_app_roles_cannot_write_embeddings(database: 'Connection[SqlRow]', role: str) -> None:
    from psycopg.errors import InsufficientPrivilege
    database.execute(f'set local role {role}')
    with pytest.raises(InsufficientPrivilege):
        with database.transaction():embed_section(database,'sg-accounts',1,'main',[1,0,0])


def test_embed_script_lists_only_sections_without_a_current_vector(database: 'Connection[SqlRow]') -> None:
    from scripts.embed_knowledge import digest, pending
    published=database.execute("select count(*) from kb.sections s join kb.articles a using(article_id,version) where a.status='published'").fetchone()[0]
    rows=pending(database,FIXTURE_MODEL)
    assert len(rows)==published and all(value.count('\n')>=2 for *_,value in rows)  # title, heading, body
    (article_id,version,anchor,value),(other_id,other_version,other_anchor,_)=rows[0],rows[1]
    database.execute('insert into kb.section_embeddings(article_id,version,anchor,model,content_sha256,embedding) values (%s,%s,%s,%s,%s,%s::extensions.vector)',
        (article_id,version,anchor,FIXTURE_MODEL,digest(value),'[1,0,0]'))
    database.execute('insert into kb.section_embeddings(article_id,version,anchor,model,content_sha256,embedding) values (%s,%s,%s,%s,%s,%s::extensions.vector)',
        (other_id,other_version,other_anchor,FIXTURE_MODEL,'0'*64,'[1,0,0]'))  # stale: text changed since
    left={(row[0],row[1],row[2]) for row in pending(database,FIXTURE_MODEL)}
    assert (article_id,version,anchor) not in left and (other_id,other_version,other_anchor) in left and len(left)==published-1
    assert len(pending(database,'other/model'))==published
