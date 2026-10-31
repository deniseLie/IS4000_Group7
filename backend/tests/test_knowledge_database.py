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
    assert database.execute('select count(*) from kb.articles').fetchone()==(33,)  # 0086 home-loans, personal-loans; 0087 loans v2; 0085 overseas-transfers; 0071 card-limit-change; 0077 statements v2; 0081 lost-card v3, unrecognised-charge v3, support v4


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
    for table in ['kb.articles','kb.sections','kb.facts']:
        with pytest.raises(InsufficientPrivilege):
            with database.transaction():database.execute(f'delete from {table}')


def test_facts_require_exact_published_source_and_never_invent_missing_rates(database: 'Connection[SqlRow]') -> None:
    database.execute("insert into kb.facts(fact_id,value,unit,article_id,version,anchor) values ('fixture.amount','0','SGD','accounts',1,'main')")
    set_customer(database,1001)
    assert database.execute("select * from public.kb_get_fact('fixture.amount')").fetchall()==[(0,'SGD','kb:accounts@v1#main')]
    assert database.execute("select * from public.kb_get_fact('fee.card_replacement')").fetchall()==[]
    database.execute('reset role')
    database.execute("update kb.articles set status='retired' where article_id='accounts'")
    database.execute('set local role anon')
    assert database.execute("select * from public.kb_get_fact('fixture.amount')").fetchall()==[]
    assert database.execute("select article_id from kb.sections where article_id='accounts'").fetchall()==[('accounts',)]


def test_facts_cannot_reference_unapproved_or_expired_evidence(database: 'Connection[SqlRow]') -> None:
    for article_id,status,checked,due in [('draft','draft',-1,10),('expired','published',-10,-1)]:
        add_article(database,article_id,status,checked,due)
        database.execute("insert into kb.facts(fact_id,value,unit,article_id,version,anchor) values (%s,'1','SGD',%s,1,'main')",(article_id,article_id))
    database.execute('set local role anon')
    assert database.execute("select * from kb.facts where fact_id='draft'").fetchall()==[]
    assert database.execute("select * from public.kb_get_fact('draft')").fetchall()==[]
    assert database.execute("select * from public.kb_get_fact('expired')").fetchall()==[]


def test_fact_requires_a_real_section_source(database: 'Connection[SqlRow]') -> None:
    from psycopg.errors import ForeignKeyViolation
    with pytest.raises(ForeignKeyViolation):
        with database.transaction():
            database.execute("insert into kb.facts(fact_id,value,unit,article_id,version,anchor) values ('fixture.missing','1','SGD','accounts',1,'missing')")


@pytest.mark.parametrize('url',['http://www.moneysense.gov.sg/page','https://www.moneysense.gov.sg.evil.invalid/page',
    'https://user@www.moneysense.gov.sg/page','https://evil.invalid/page'])
def test_external_source_host_constraints(database: 'Connection[SqlRow]', url: str) -> None:
    from psycopg.errors import CheckViolation
    add_article(database,'external','published',-1,10)
    with pytest.raises(CheckViolation):
        with database.transaction():database.execute("update kb.articles set reference_url=%s where article_id='external'",(url,))


@pytest.mark.parametrize('value',['true','null','{}','[]','""','"   "'])
def test_fact_values_are_typed_and_nonempty(database: 'Connection[SqlRow]', value: str) -> None:
    from psycopg.errors import CheckViolation
    with pytest.raises(CheckViolation):
        with database.transaction():
            database.execute("insert into kb.facts(fact_id,value,unit,article_id,version,anchor) values ('fixture.invalid',%s::jsonb,'SGD','accounts',1,'main')",(value,))


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
    assert database.execute("select article_id,version from kb.articles where article_id in ('lost-card','support') and status='published' order by 1").fetchall()==[('lost-card',3),('support',4)]  # 0081
    assert {row[2] for row in search(database,'lost stolen card block',['fraud'],4) if row[1]=='lost-card'}=={3}
    assert database.execute("select article_id from public.kb_get_section('support',1,'main')").fetchall()==[('support',)]
    assert database.execute("select * from public.kb_get_fact('savings.fall_below_fee')").fetchall()==[(2,'SGD','kb:savings-fees@v1#main')]


def test_unexpected_code_guidance_publishes_login_recovery_v2(database: 'Connection[SqlRow]') -> None:
    database.execute('set local role anon')
    assert database.execute("select version,status from kb.articles where article_id='app-login-recovery' order by 1").fetchall()==[(1,'retired'),(2,'published')]
    rows=search(database,'also i got a weird OTP last night and now my app is locked',[],4)
    assert rows and rows[0][1]=='app-login-recovery' and rows[0][2]==2
    body=database.execute("select body from public.kb_get_section('app-login-recovery',2,'unexpected-code')").fetchone()[0]
    assert 'the lock is protecting you' in body and 'including callers, bank staff and this chat' in body and 'cannot see sign-in attempts' in body
    assert database.execute("select article_id from public.kb_get_section('app-login-recovery',1,'steps')").fetchall()==[('app-login-recovery',)]


def test_workflow_one_part_b_guidance_is_published(database: 'Connection[SqlRow]') -> None:
    database.execute('set local role anon')
    assert database.execute("select article_id,version from kb.articles where article_id in ('unrecognised-charge','transfer-timing','account-security-after-fraud') and status='published' order by 1").fetchall()==[
        ('account-security-after-fraud',1),('transfer-timing',1),('unrecognised-charge',3)]  # 0081
    assert 'transfer-timing' in [row[1] for row in search(database,"my rent transfer still says pending, will it make it",[],4)]
    assert database.execute("select article_id from public.kb_get_section('unrecognised-charge',1,'urgent-steps')").fetchall()==[('unrecognised-charge',)]
