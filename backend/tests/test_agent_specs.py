"""v5 agent catalogue (architecture §5.6.1, §14.8; build plan phase 1): specs, field views, limits and policy.
Synthetic records only; refusals are checked before any database or network access."""
import asyncio
import inspect
from datetime import timedelta
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.schemas.contracts import QUESTION, AgentOutput, AgentSpec, Principal, Read, RequestContext, ToolView
from app.services import audit, tools
from app.services.agents import registry
from app.services.agents.registry import SPECS, TOOL_PERMISSION
from app.services.authorization import BANK_ONLY_CARD_ACTIONS, CLIENT_ONLY_CARD_ACTIONS, KNOWN_TOOLS, PolicyDenied, decide

CARD={'card_id':'card_4821','name':'Signature Card','network':'visa','last4':'4821','status':'active','currency':'SGD',
    'limit':20000,'outstanding':3850,'minimum_payment':50,'due_date':'2026-11-25','unfreeze_blocked_reason':None}
RM={'current':{'rm_id':str(uuid4()),'name':'Sophie Lim (DEMO)','role':'RM','team':'Private','timezone':'Asia/Singapore',
    'contact':{'email':'sophie@example.com','phone':'+65 6000 0000'},'started_on':'2026-01-01','ended_on':None,'transition_reason_code':'x'},
    'previous':{'rm_id':str(uuid4()),'name':'Rachel (DEMO)','ended_on':'2025-12-31','contact':{'email':'r@example.com','phone':None}}}
TICKET={'ticket_id':str(uuid4()),'reference':'TCK-1001','category_label':'Statement copy','summary':'June statement',
    'details':{'month':'June'},'status':'in_progress','handoff_id':str(uuid4()),'attachments':[],'owner_team':'Operations'}


def ctx(agent=None,kind='customer') -> RequestContext:
    principal=Principal(kind='customer',user_id=uuid4(),customer_id=1001,jwt='synthetic-test-token') if kind=='customer' else Principal(kind='guest')
    return RequestContext(session_id=uuid4(),agent=agent,principal=principal)


def test_every_spec_tool_is_a_registered_tool_with_known_permissions():
    assert set(SPECS)=={'guide','accounts','security','compliance','relationship','rm_client_insight','rm_service_desk'}
    assert set(registry.CLIENT_SPECS)=={'guide','accounts','security','compliance','relationship'}
    for spec in SPECS.values():
        for tool in spec.tools:
            assert tool in tools.TOOLS and tool in TOOL_PERMISSION, (spec.name,tool)
            assert set(TOOL_PERMISSION[tool])<=KNOWN_TOOLS, (spec.name,tool)
        assert spec.offers<=KNOWN_TOOLS and not spec.offers&(BANK_ONLY_CARD_ACTIONS|CLIENT_ONLY_CARD_ACTIONS), spec.name
        assert spec.hands_to<=set(SPECS)-{spec.name}
    assert set(TOOL_PERMISSION)<=set(tools.TOOLS)


def test_guide_sees_no_customer_rows():
    assert SPECS['guide'].scope=='public' and not SPECS['guide'].offers
    assert not registry.permissions(SPECS['guide'])-{'kb.search','tickets.categories'}


def test_default_reads_name_real_tool_arguments_and_entities():
    for spec in SPECS.values():
        assert spec.intents, spec.name
        for intent,reads in spec.intents.items():
            assert reads or (spec.role,intent)==('rm','help'), (spec.name,intent)  # RM help reads nothing
            for read in reads:
                params=inspect.signature(tools.TOOLS[read.tool]).parameters
                assert set(read.entities)|set(read.fixed)<=set(params), (spec.name,intent,read.tool)
                assert all(entity and entity.isidentifier() for entity in read.entities.values())
    assert any(read.entities=={'query':QUESTION} for reads in SPECS['guide'].intents.values() for read in reads)


def test_free_text_tools_are_never_loop_tools():
    text={(s.name,t) for s in SPECS.values() for t,v in s.tools.items() if v.free_text}
    assert ('relationship','my_tickets') in text and ('compliance','my_compliance_case') in text
    assert not any(t in SPECS[a].loop_tools for a,t in text)
    base=SPECS['relationship'].model_dump()
    with pytest.raises(ValidationError,match='free-text loop tools'):
        AgentSpec.model_validate({**base,'loop_tools':base['loop_tools']|{'my_tickets'}})
    with pytest.raises(ValidationError,match='tools not in its spec'):
        AgentSpec.model_validate({**base,'intents':{'x':[{'tool':'my_cards'}]}})
    with pytest.raises(ValidationError,match='not a listed intent'):
        AgentSpec.model_validate({**base,'intents':{'open':[{'tool':'my_rm'}]}})
    with pytest.raises(ValidationError,match='entity or a fixed value'):
        Read(tool='my_transactions',entities={'status':'status'},fixed={'status':'pending'})


def test_field_views_drop_excluded_fields():
    security=tools.shaped({'items':[CARD],'customer_id':1001,'call_id':'c1'},SPECS['security'].tools['my_cards'])
    assert security=={'items':[{'card_id':'card_4821','last4':'4821','status':'active'}],'call_id':'c1'}
    accounts=tools.shaped({'items':[CARD]},SPECS['accounts'].tools['my_cards'])['items'][0]
    assert 'card_id' not in accounts and accounts['limit']==20000 and accounts['due_date']=='2026-11-25'
    rm=tools.shaped({'data':RM},SPECS['relationship'].tools['my_rm'])['data']
    assert rm=={'current':{'name':'Sophie Lim (DEMO)','role':'RM','team':'Private','timezone':'Asia/Singapore',
        'contact':{'phone':'+65 6000 0000'},'started_on':'2026-01-01'},'previous':{'name':'Rachel (DEMO)','ended_on':'2025-12-31'}}
    ticket=tools.shaped({'items':[TICKET]},SPECS['relationship'].tools['my_tickets'])['items'][0]
    assert set(ticket)=={'reference','category_label','summary','status','owner_team'}
    profile=tools.shaped({'data':{'name':'Michelle','preferred_language':'en','employment_status':'employed'}},SPECS['compliance'].tools['my_profile'])
    assert profile['data']=={'name':'Michelle','employment_status':'employed'}
    assert tools.shaped({'items':[CARD]},ToolView())=={'items':[CARD]}  # no field list: the whole record


def test_limits_fill_in_or_refuse_windows_and_rows():
    sec=ctx('security');view=SPECS['security'].tools['my_transactions']
    assert tools.bounded('my_transactions',{},view,sec)=={'days':90}
    assert tools.bounded('my_transactions',{'days':30,'status':'pending'},view,sec)=={'days':30,'status':'pending'}
    for days in (91,365,0):
        with pytest.raises(tools.ToolLimitExceeded):tools.bounded('my_transactions',{'days':days},view,sec)
    rows=tools.shaped({'items':[{'date':str(i)} for i in range(150)]},view)['items']
    assert len(rows)==view.max_rows==100
    rel=ctx('relationship');slots=SPECS['relationship'].tools['free_slots'];first=tools.today(rel)
    assert tools.bounded('free_slots',{},slots,rel)=={'to_date':(first+timedelta(days=13)).isoformat()}
    for args in ({'to_date':(first+timedelta(days=14)).isoformat()},{'from_date':(first-timedelta(days=1)).isoformat()},{'limit':21}):
        with pytest.raises(tools.ToolLimitExceeded):tools.bounded('free_slots',args,slots,rel)


def test_policy_reads_the_spec_for_new_agents():
    for agent,allowed,forbidden in [('accounts','customer.get_loans','compliance.get_case'),
            ('security','customer.get_transactions','customer.get_loans'),('guide','kb.search','customer.get_credit_cards'),
            ('accounts','ticket.create','handoff.create'),('security','handoff.create','rm.share_summary')]:
        c=ctx(agent)
        assert decide(c.principal,allowed,{'customer_id':1001},c).allow, (agent,allowed)
        assert decide(c.principal,forbidden,None,c).reasons==['agent_tool_forbidden'], (agent,forbidden)
    # Ticket guidance (knowledge_article) is the Service Desk's only knowledge read; Client Insight has none.
    assert 'kb.search' in registry.permissions(SPECS['rm_service_desk']) and 'kb.search' not in registry.permissions(SPECS['rm_client_insight'])
    c=ctx('security')
    assert decide(c.principal,'card.temporary_block',{'customer_id':1001},c).reasons==['customer_only_action']
    assert decide(c.principal,'card.dispute',{'customer_id':1001},c).reasons==['bank_only_action']
    guest=ctx('guide',kind='guest')
    assert decide(guest.principal,'kb.search',None,guest).allow
    assert decide(guest.principal,'customer.get_accounts',None,guest).reasons==['guest_no_personal_data']


def test_v4_agents_keep_their_allow_lists_until_the_switch_over():
    assert registry.ENFORCED=={'guide','accounts','security','rm_client_insight','rm_service_desk'}
    for agent in ('compliance','relationship','retail'):assert registry.spec_for(agent) is None
    c=ctx('compliance')
    assert decide(c.principal,'compliance.update_checklist_item',{'customer_id':1001},c).allow
    d=ctx('document')
    assert decide(d.principal,'documents.precheck',{'customer_id':1001},d).allow  # was docs.precheck, which no service checks


@pytest.mark.parametrize('agent,name,args,error',[('security','my_loans',{},'agent_tool_forbidden'),
    ('guide','my_cards',{},'agent_tool_forbidden'),('security','my_transactions',{'days':120},'at most 90 days')])
def test_tool_calls_outside_the_spec_are_refused_before_any_read(agent,name,args,error):
    c=ctx(agent)
    batch=audit.start_batch(session_id=c.session_id)
    try:
        with pytest.raises(PolicyDenied,match=error):asyncio.run(tools.call(name,args,c))
        if error=='agent_tool_forbidden':
            event=batch.events[0]
            assert event.outcome=='denied' and event.actor_id==agent and event.details['tool']==name and audit.is_safe(event.details)
    finally:audit.current_batch.set(None)


def test_agent_output_handoff_shape():
    AgentOutput(task_id='t1',agent='accounts',status='out_of_scope',suggested='relationship')
    with pytest.raises(ValidationError,match='out_of_scope'):AgentOutput(task_id='t1',agent='accounts',status='ok',suggested='relationship')
