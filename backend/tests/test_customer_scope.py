"""Application filtering must work even if database row policies regress."""
import json
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.core.auth import get_customer_context
class Query:
    def __init__(self,rows):self.rows=rows
    def select(self,*a):return self
    def eq(self,k,v):self.rows=[r for r in self.rows if r.get(k)==v];return self
    def limit(self,n):self.rows=self.rows[:n];return self
    def order(self,*a,**k):return self
    def execute(self):return SimpleNamespace(data=self.rows)
class Database:
    def __init__(self):
        self.rows={'customer_profiles':[{'customer_id':1,'auth_user_id':'A','income_range':'A-income'},{'customer_id':2,'auth_user_id':'B','income_range':'B-income'}]}
        for t in ['bank_accounts','credit_cards','loans','transactions']:
            self.rows[t]=[{'customer_id':1,'marker':'A-only'},{'customer_id':2,'marker':'B-only'}]
        for t in ['credit_card_products','loan_products']:self.rows[t]=[{'is_active':True,'marker':'active-product'},{'is_active':False,'marker':'inactive-product'}]
    def schema(self,name):assert name=='banking';return self
    def table(self,name):return Query(self.rows[name].copy())
@pytest.mark.parametrize('customer,own,other',[(1,'A-only','B-only'),(2,'B-only','A-only')])
def test_explicit_customer_filters(customer,own,other):
    result=get_customer_context(Database(),customer)
    assert result.count(own)==4 and other not in result
    assert 'active-product' in result and 'inactive-product' not in result
@pytest.mark.parametrize('duplicate',[False,True])
def test_missing_or_ambiguous_profile_cannot_load_records(duplicate):
    db=Database()
    if duplicate:db.rows['customer_profiles'].append(db.rows['customer_profiles'][0])
    with pytest.raises(HTTPException) as e:get_customer_context(db,1 if duplicate else 99)
    assert e.value.status_code==403
