"""Field views on the rules path: an agent's rule code reads whole records within its spec's tools and limits (the ids
an offer or a later read needs), and every piece of evidence it hands to a model shows only the spec's field view
(tools.visible). Unit checks only: synthetic records, no database or model."""
import json
from datetime import datetime,timedelta,timezone
from uuid import UUID,uuid4
from app.schemas.contracts import ToolView
from app.schemas.relationship_records import RelationshipRecords
from app.schemas.retail import RetailRecords
from app.services import tools
from app.services.agents import registry,relationship,retail
from tests.test_rm_change import MARCUS,RACHEL,introduction,request

SGT=timezone(timedelta(hours=8))
NOW=datetime(2026,10,26,9,tzinfo=SGT)


def leaves(value: object,prefix: str='') -> set[str]:
    """Dotted paths of a JSON value's leaf fields; lists are transparent, as in ToolView.fields."""
    if isinstance(value,list):return {path for item in value for path in leaves(item,prefix)}
    if isinstance(value,dict) and value:return {path for key,item in value.items() for path in leaves(item,f'{prefix}{key}.')}
    return {prefix[:-1]} if prefix else set()


def outside(value: object,fields: frozenset[str],added: frozenset[str]=frozenset()) -> set[str]:
    return {path for path in leaves(value) if path not in added and not any(path==f or path.startswith(f+'.') for f in fields)}


def records(cid: int=1003) -> RelationshipRecords:
    def envelope(data: object) -> dict[str,object]:return {'customer_id':cid,'call_id':str(uuid4()),'as_of':NOW.isoformat(),'data':data}
    advisor=lambda rm,name,team,start,end,code:{'rm_id':str(rm),'name':name,'role':'Relationship Manager','team':team,
        'contact':{'email':name.split()[0].lower()+'@demobank.example','phone':'+65 6000 0000'},'timezone':'Asia/Singapore',
        'started_on':start,'ended_on':end,'transition_reason_code':code}
    assignment={'current':advisor(MARCUS,'Marcus Chua (DEMO)','Private Clients Team B','2026-10-15',None,'team_restructure'),
        'previous':advisor(RACHEL,'Rachel Goh (DEMO)','Private Clients Team A','2024-03-01','2026-10-14','team_restructure')}
    appointment={'appointment_id':str(uuid4()),'advisor_id':str(MARCUS),'starts_at':'2026-10-28T10:00:00+08:00','ends_at':'2026-10-28T10:30:00+08:00',
        'format':'video','hold_expires_at':None,'status':'confirmed','case_id':str(uuid4())}
    slot={'customer_id':cid,'advisor_id':str(MARCUS),'starts_at':'2026-10-29T14:00:00+08:00','ends_at':'2026-10-29T14:30:00+08:00'}
    return RelationshipRecords.model_validate({'assignment':envelope(assignment),'requests':envelope([request().model_dump(mode='json')]),
        'appointments':envelope([appointment]),'slots':envelope([slot]),'search':{'from_date':'2026-10-26','to_date':'2026-11-08','limit':20},
        'introduction':envelope(introduction(['team-restructure','continuity']).model_dump(mode='json'))})


def test_rule_code_gets_whole_records_within_the_limits_and_the_model_only_the_view():
    view=ToolView(fields=frozenset({'reference','status'}),max_rows=2)
    result={'customer_id':1003,'call_id':'c','as_of':'t','items':[{'ticket_id':str(n),'reference':f'TCK-{n}','status':'open'} for n in range(3)]}
    assert tools.shaped(result,view)=={'call_id':'c','as_of':'t','items':[{'reference':'TCK-0','status':'open'},{'reference':'TCK-1','status':'open'}]}
    whole=tools.shaped(result,view,whole=True)
    assert whole['customer_id']==1003 and whole['items']==result['items'][:2]  # the ids an offer needs, still at most max_rows
    assert tools.visible('relationship','my_cards',[{'card_id':'k1','last4':'4821','status':'active','limit':5000}])==[{'last4':'4821','status':'active'}]


def test_relationship_evidence_shows_only_its_field_views():
    data=records()
    evidence={item.id:item for item in relationship.evidence_for(data)}
    body={key:json.loads(item.text)['records'] for key,item in evidence.items()}
    views=registry.RELATIONSHIP.tools
    assert outside(body['relationship-assignment'],views['my_rm'].fields)==set()
    assert outside(body['relationship-requests'],views['my_requests'].fields,frozenset({'advisor_name','recorded_with_current_advisor'}))==set()
    assert outside(body['relationship-appointments'],views['my_appointments'].fields,frozenset({'advisor_name'}))==set()
    assert outside(body['relationship-slots']['slots'],views['free_slots'].fields,frozenset({'advisor_name'}))==set()
    # The previous RM's team, role and start, every id, email and reason code stay out; names code finds from the ids stay in.
    text=json.dumps([item.model_dump(mode='json',exclude={'source'}) for item in evidence.values()])
    hidden=[str(MARCUS),str(RACHEL),'demobank.example','team_restructure','Private Clients Team A','2024-03-01',
        str(data.requests.data[0].request_id),str(data.appointments.data[0].appointment_id),str(data.appointments.data[0].case_id)]
    assert [value for value in hidden if value in text]==[]
    assert body['relationship-assignment']['previous']=={'name':'Rachel Goh (DEMO)','ended_on':'2026-10-14'}
    assert body['relationship-requests'][0]['advisor_name']=='Rachel Goh (DEMO)' and body['relationship-appointments'][0]['advisor_name']=='Marcus Chua (DEMO)'
    assert evidence['relationship-transition'].publisher=='UBS Connect'  # notice.publisher is in the my_introduction view


def test_accounts_evidence_shows_only_its_field_views():
    """The retail prompt Accounts answers with sees her records without their ids or profile fields beyond her name;
    the rule code itself keeps the ids (cards and transactions match by card_id)."""
    data=RetailRecords.model_validate({'profile':{'name':'Michelle Ng (DEMO)','preferred_language':'HIDDEN-language','employment_status':'self_employed'},
        'accounts':[{'account_id':'acc_987651','type':'savings','number':'****4321','currency':'SGD','balance':250000.0,'status':'active'}],
        'cards':[{'card_id':'card_987652','name':'Travel Card','network':'visa','last4':'5592','status':'active','currency':'SGD','limit':8000.0,
            'outstanding':1240.5,'minimum_payment':50.0,'due_date':'2026-11-20','unfreeze_blocked_reason':None}],
        'loans':[{'loan_id':'loan_987653','type':'home','currency':'SGD','principal':500000.0,'outstanding':410000.0,'next_payment':'2026-11-05',
            'next_amount':2100.0,'status':'active'}],
        'transactions':[{'txn_id':'txn_987654','date':'2026-10-05','description':'Annual card fee','amount':-196.2,'currency':'SGD','status':'posted',
            'card_id':'card_987652','card_last4':'5592','is_overseas':False,'failure_reason':None}],'captured_at':NOW.isoformat()})
    evidence={item.id:item for item in retail.evidence_for(data)}
    views=registry.ACCOUNTS.tools
    for key,tool in (('profile','my_profile'),('accounts','my_accounts'),('cards','my_cards'),('loans','my_loans'),('transactions','my_transactions')):
        assert outside(json.loads(evidence['retail-'+key].text)['records'],views[tool].fields)==set(),key
    text=json.dumps([item.model_dump(mode='json',exclude={'source'}) for item in evidence.values()])
    assert '98765' not in text and 'HIDDEN' not in text and 'self_employed' not in text
    assert json.loads(evidence['retail-cards'].text)['records'][0]['outstanding']==1240.5 and data.transactions[0].card_id=='card_987652'
