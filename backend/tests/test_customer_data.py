"""Customer Data filtering must hold even if database row policies regress: the fake DB applies no RLS at all."""
import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from uuid import uuid4
import pytest
from app.schemas.contracts import Principal, RequestContext, ToolResult
from app.services import customer_data as cd
A,B=uuid4(),uuid4()
ts=datetime.fromisoformat
class Query:
    def __init__(self,rows,leaky):self.rows,self.leaky=rows,leaky
    def select(self,*a):return self
    def eq(self,k,v):
        if not(self.leaky and k=='customer_id'):self.rows=[r for r in self.rows if r.get(k)==v]
        return self
    def gte(self,k,v):self.rows=[r for r in self.rows if ts(r[k])>=ts(v)];return self
    def lt(self,k,v):self.rows=[r for r in self.rows if ts(r[k])<ts(v)];return self
    def in_(self,k,v):return self
    def limit(self,n):self.rows=self.rows[:n];return self
    def order(self,*a,**k):return self
    def execute(self):return SimpleNamespace(data=self.rows)
def rows(cid,tag):
    n=str(cid)
    return {
        'banking.bank_accounts':[{'customer_id':cid,'account_id':int(n+'1'),'account_type':'savings','masked_account_number':'******'+n,'balance':'5230.45','currency':'SGD','status':'active'}],
        'banking.credit_cards':[{'customer_id':cid,'card_id':int(n+'1'),'card_name':tag,'network':'visa','last4':n,'status':'active','credit_limit':'8000.00','outstanding_balance':'2464.20','minimum_payment_due':'50.00','payment_due_date':'2026-11-05'}],
        'banking.transactions':[
            {'customer_id':cid,'transaction_id':int(n+'11'),'card_id':int(n+'1'),'txn_date':'2026-10-17T13:42:00+00:00','merchant':tag,'amount':'-2400.00','currency':'SGD','status':'posted','failure_reason':None,'is_overseas':True},
            {'customer_id':cid,'transaction_id':int(n+'12'),'card_id':None,'txn_date':'2026-10-16T16:30:00+00:00','merchant':tag+' transfer','amount':'-1800.00','currency':'SGD','status':'pending','failure_reason':None,'is_overseas':False}],
        'banking.loans':[{'customer_id':cid,'loan_id':int(n+'1'),'loan_type':'personal','original_amount':'10000','outstanding_amount':'6200','monthly_repayment':'320','next_due_date':'2026-11-01','status':'active'}],
        'compliance.cases':[{'customer_id':cid,'case_id':f'case-{n}','case_type':'periodic_review','title':tag,'notice_text':'n','notice_date':'2026-10-01','due_date':'2026-11-15','status':'awaiting_client_documents','status_updated_at':'x'}],
        'compliance.requested_items':[{'customer_id':cid,'case_id':f'case-{n}','item_id':f'i{k}','label':f'{tag} item {k}','instructions':None,'submission_status':'unknown','client_mark':'not_prepared','client_marked_at':None,'sort_order':k,'updated_at':'x'} for k in (2,1)],
        'compliance.status_history':[{'customer_id':cid,'case_id':f'case-{n}','status':'review_opened','note':tag,'changed_at':'x'}],
        'compliance.info_requests':[{'customer_id':cid,'case_id':f'case-{n}','request_id':'r','question':tag,'instructions':None,'due_date':'2026-10-31','status':'open','responded_at':None,'created_at':'x'}],
        'rm.assignments':[{'customer_id':cid,'advisor_id':f'new-{n}','started_on':'2026-10-15','ended_on':None,'transition_reason_code':'team_restructure'},
                          {'customer_id':cid,'advisor_id':f'old-{n}','started_on':'2024-03-01','ended_on':'2026-10-14','transition_reason_code':None}],
        'rm.advisors':[{'advisor_id':f'{w}-{n}','full_name':f'{tag} {w}','role':'RM','team_name':'T','email':None,'phone':None,'timezone':'Asia/Singapore'} for w in ('new','old')],
    }
class Database:
    def __init__(self,leaky=False):
        self.leaky,self.jwts=leaky,[]
        self.rows={'banking.customer_profiles':[{'customer_id':1001,'auth_user_id':str(A),'full_name':'Alex Tan (DEMO)','preferred_language':'en','employment_status':'employed'},
                                                {'customer_id':1003,'auth_user_id':str(B),'full_name':'David Lee (DEMO)','preferred_language':'en','employment_status':'employed'}]}
        for cid,tag in [(1001,'A-only'),(1003,'B-only')]:
            for k,v in rows(cid,tag).items():self.rows.setdefault(k,[]).extend(v)
        self.rows['compliance.case_internal']=[{'case_id':'case-1001','risk_rating':'medium','reviewer_note':'INTERNAL'}]
    def schema(self,name):return SimpleNamespace(table=lambda t:Query(list(self.rows.get(f'{name}.{t}',[])),self.leaky))
@pytest.fixture
def db(monkeypatch):
    d=Database()
    monkeypatch.setattr(cd,'_client',lambda jwt:(d.jwts.append(jwt),d)[1])
    return d
def ctx(user=A,kind='customer',jwt='jwt-A'):
    return RequestContext(session_id=uuid4(),principal=Principal(kind=kind,user_id=user,customer_id=1003 if user==B else 1001,jwt=jwt))
def run(fn,c,**k):
    params={'from_':None,'to':None,'filters':None,**k} if fn=='get_transactions' else k
    return asyncio.run(getattr(cd,fn)(c,**params))
READERS=['get_profile','get_accounts','get_transactions','get_credit_cards','get_loans','get_compliance_case','get_rm_assignment']

@pytest.mark.parametrize('leaky',[False,True])
@pytest.mark.parametrize('fn',READERS)
@pytest.mark.parametrize('user,own,other',[(A,'A-only','B-only'),(B,'B-only','A-only')])
def test_only_own_rows_even_without_rls(db,fn,user,own,other,leaky):
    db.leaky=leaky  # leaky: the customer_id filter in the query is lost too, only the code-side check remains
    r=run(fn,ctx(user,jwt=f'jwt-{own}'))
    assert isinstance(r,ToolResult) and other not in str(r.data) and 'INTERNAL' not in str(r.data)
    assert db.jwts==[f'jwt-{own}']  # every read uses the caller's own JWT
    if fn not in ('get_profile','get_accounts','get_loans'):assert own in str(r.data)
    if fn=='get_loans':assert [l['loan_id'] for l in r.data]==[f"loan_{1001 if user==A else 1003}1"]

def test_masking_and_shapes(db):
    acc=run('get_accounts',ctx()).data[0];card=run('get_credit_cards',ctx()).data[0]
    assert acc['number']=='****1001' and acc['balance']==5230.45 and card['last4']=='1001'
    for row in [acc,card]:assert not {'pan','card_number','masked_account_number'}&set(row)
    assert run('get_loans',ctx()).data[0]['outstanding']==6200.0

def test_transactions_local_dates_and_filters(db):
    t=run('get_transactions',ctx()).data
    assert [x['date'] for x in t]==['2026-10-17','2026-10-17'] and t[0]['card_last4']=='1001' and t[1]['card_last4'] is None
    assert [x['description'] for x in run('get_transactions',ctx(),filters={'status':'pending'}).data]==['A-only transfer']
    assert run('get_transactions',ctx(),from_=date(2026,10,18)).data==[]
    assert len(run('get_transactions',ctx(),from_=date(2026,10,17),to=date(2026,10,17)).data)==2

def test_compliance_case_and_rm(db):
    case=run('get_compliance_case',ctx()).data
    assert [i['label'] for i in case['requested_items']]==['A-only item 1','A-only item 2'] and 'risk_rating' not in str(case)
    rm=run('get_rm_assignment',ctx(B)).data
    assert rm['current']['name']=='B-only new' and rm['previous']['name']=='B-only old' and rm['previous']['ended_on']=='2026-10-14'
    db.rows['rm.assignments']=[]
    assert run('get_rm_assignment',ctx(B)).data is None

@pytest.mark.parametrize('c',[ctx(kind='guest'),ctx(jwt=None),ctx(user=None),ctx(user=uuid4())])
def test_guest_missing_login_or_unlinked_profile_is_denied(db,c):
    with pytest.raises(PermissionError):run('get_accounts',c)

def test_duplicate_profile_is_denied(db):
    db.rows['banking.customer_profiles'].append(dict(db.rows['banking.customer_profiles'][0]))
    with pytest.raises(PermissionError):run('get_accounts',ctx())
