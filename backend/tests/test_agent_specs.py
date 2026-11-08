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
from app.services.agents.registry import CHAT_SPECS, TOOL_PERMISSION
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
    assert set(CHAT_SPECS)=={'guide','accounts','compliance','relationship','rm_client','rm_requests','rm_calendar'}
    assert set(registry.CLIENT_SPECS)=={'guide','accounts','compliance','relationship'}  # build plan 2b: no Security agent
    for spec in CHAT_SPECS.values():
        for tool in spec.tools:
            assert tool in tools.TOOLS and tool in TOOL_PERMISSION, (spec.name,tool)
            assert set(TOOL_PERMISSION[tool])<=KNOWN_TOOLS, (spec.name,tool)
        assert spec.offers<=KNOWN_TOOLS and not spec.offers&(BANK_ONLY_CARD_ACTIONS|CLIENT_ONLY_CARD_ACTIONS), spec.name
        assert spec.hands_to<=set(CHAT_SPECS)-{spec.name}
    assert set(TOOL_PERMISSION)<=set(tools.TOOLS)


def test_guide_sees_no_customer_rows():
    assert CHAT_SPECS['guide'].scope=='public' and not CHAT_SPECS['guide'].offers
    assert not registry.permissions(CHAT_SPECS['guide'])-{'kb.search','tickets.categories'}


def test_default_reads_name_real_tool_arguments_and_entities():
    for spec in CHAT_SPECS.values():
        assert spec.intents, spec.name
        for intent,reads in spec.intents.items():
            assert reads or (spec.role,intent) in {('rm','help'),('rm','follow_up')}, (spec.name,intent)  # RM help and follow-up read no records
            for read in reads:
                params=inspect.signature(tools.TOOLS[read.tool]).parameters
                assert set(read.entities)|set(read.fixed)<=set(params), (spec.name,intent,read.tool)
                assert all(entity and entity.isidentifier() for entity in read.entities.values())
    assert any(read.entities=={'query':QUESTION} for reads in CHAT_SPECS['guide'].intents.values() for read in reads)


def test_free_text_tools_are_never_loop_tools():
    text={(s.name,t) for s in CHAT_SPECS.values() for t,v in s.tools.items() if v.free_text}
    assert ('relationship','my_tickets') in text and ('compliance','my_compliance_case') in text
    assert not any(t in CHAT_SPECS[a].loop_tools for a,t in text if CHAT_SPECS[a].role!='rm')  # RM loops hide such reads from the step (h_open)
    base=CHAT_SPECS['relationship'].model_dump()
    with pytest.raises(ValidationError,match='free-text loop tools'):
        AgentSpec.model_validate({**base,'loop_tools':base['loop_tools']|{'my_tickets'}})
    with pytest.raises(ValidationError,match='tools not in its spec'):
        AgentSpec.model_validate({**base,'intents':{'x':[{'tool':'my_cards'}]}})
    with pytest.raises(ValidationError,match='not a listed intent'):
        AgentSpec.model_validate({**base,'intents':{'open':[{'tool':'my_rm'}]}})
    with pytest.raises(ValidationError,match='entity or a fixed value'):
        Read(tool='my_transactions',entities={'status':'status'},fixed={'status':'pending'})


def test_field_views_drop_excluded_fields():
    accounts=tools.shaped({'items':[CARD],'customer_id':1001,'call_id':'c1'},CHAT_SPECS['accounts'].tools['my_cards'])
    assert set(accounts)=={'items','call_id'} and 'card_id' not in accounts['items'][0]
    assert accounts['items'][0]['limit']==20000 and accounts['items'][0]['due_date']=='2026-11-25'
    rm=tools.shaped({'data':RM},CHAT_SPECS['relationship'].tools['my_rm'])['data']
    assert rm=={'current':{'name':'Sophie Lim (DEMO)','role':'RM','team':'Private','timezone':'Asia/Singapore',
        'contact':{'phone':'+65 6000 0000'},'started_on':'2026-01-01'},'previous':{'name':'Rachel (DEMO)','ended_on':'2025-12-31'}}
    ticket=tools.shaped({'items':[TICKET]},CHAT_SPECS['relationship'].tools['my_tickets'])['items'][0]
    assert set(ticket)=={'reference','category_label','summary','status','owner_team'}
    profile=tools.shaped({'data':{'name':'Michelle','preferred_language':'en','employment_status':'employed'}},CHAT_SPECS['compliance'].tools['my_profile'])
    assert profile['data']=={'name':'Michelle','employment_status':'employed'}
    assert tools.shaped({'items':[CARD]},ToolView())=={'items':[CARD]}  # no field list: the whole record


def test_limits_fill_in_or_refuse_windows_and_rows():
    acc=ctx('accounts');view=CHAT_SPECS['accounts'].tools['my_transactions']
    assert tools.bounded('my_transactions',{},view,acc)=={'days':90}
    assert tools.bounded('my_transactions',{'days':30,'status':'pending'},view,acc)=={'days':30,'status':'pending'}
    for days in (91,365,0):
        with pytest.raises(tools.ToolLimitExceeded):tools.bounded('my_transactions',{'days':days},view,acc)
    rows=tools.shaped({'items':[{'date':str(i)} for i in range(150)]},view)['items']
    assert len(rows)==view.max_rows==100
    rel=ctx('relationship');slots=CHAT_SPECS['relationship'].tools['free_slots'];first=tools.today(rel)
    assert tools.bounded('free_slots',{},slots,rel)=={'to_date':(first+timedelta(days=13)).isoformat()}
    for args in ({'to_date':(first+timedelta(days=14)).isoformat()},{'from_date':(first-timedelta(days=1)).isoformat()},{'limit':21}):
        with pytest.raises(tools.ToolLimitExceeded):tools.bounded('free_slots',args,slots,rel)


def test_policy_reads_the_spec_for_new_agents():
    for agent,allowed,forbidden in [('accounts','customer.get_loans','compliance.get_case'),
            ('guide','kb.search','customer.get_credit_cards'),('accounts','ticket.create','handoff.create'),('guide','kb.search','handoff.create')]:
        c=ctx(agent)
        assert decide(c.principal,allowed,{'customer_id':1001},c).allow, (agent,allowed)
        assert decide(c.principal,forbidden,None,c).reasons==['agent_tool_forbidden'], (agent,forbidden)
    # Ticket guidance (knowledge_article) is the Requests agent's only knowledge read; Client and Calendar have none.
    assert 'kb.search' in registry.permissions(CHAT_SPECS['rm_requests'])
    assert 'kb.search' not in registry.permissions(CHAT_SPECS['rm_client'])|registry.permissions(CHAT_SPECS['rm_calendar'])
    c=ctx('accounts')
    assert decide(c.principal,'card.temporary_block',{'customer_id':1001},c).reasons==['customer_only_action']
    assert decide(c.principal,'card.dispute',{'customer_id':1001},c).reasons==['bank_only_action']
    guest=ctx('guide',kind='guest')
    assert decide(guest.principal,'kb.search',None,guest).allow
    assert decide(guest.principal,'customer.get_accounts',None,guest).reasons==['guest_no_personal_data']


def test_v4_agents_keep_their_allow_lists_until_the_switch_over():
    assert registry.ENFORCED=={'guide','accounts','rm_client','rm_requests','rm_calendar'}
    for agent in ('compliance','relationship','retail'):assert registry.spec_for(agent) is None
    c=ctx('compliance')
    assert decide(c.principal,'compliance.update_checklist_item',{'customer_id':1001},c).allow
    d=ctx('document')
    assert decide(d.principal,'documents.precheck',{'customer_id':1001},d).allow  # was docs.precheck, which no service checks


@pytest.mark.parametrize('agent,name,args,error',[('accounts','my_compliance_case',{},'agent_tool_forbidden'),
    ('guide','my_cards',{},'agent_tool_forbidden'),('accounts','my_transactions',{'days':120},'at most 90 days')])
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


# Build plan 3a: three RM agents split by data class ----------------------------------------------------------------
RM_DATA={'rm_client_profile','rm_meeting_notes','rm_shared_summary','rm_handover_sources','rm_cash_events','rm_book_facts','rm_review_status',
    'rm_transaction_insights','rm_talking_points','rm_tickets','rm_ticket','rm_followups','rm_appointments','rm_availability','rm_meeting_requests',
    'request_categories','knowledge_article'}
WRITES=('handoff.create','ticket.create','appointment.','consent.','rm.','actions.','agenda.','document.','compliance.update','compliance.answer')


def test_every_rm_data_tool_has_exactly_one_owning_agent():
    rm=registry.RM_SPECS
    assert set(rm)=={'rm_client','rm_requests','rm_calendar'} and set(registry.RM_OWNER)==RM_DATA
    for tool in RM_DATA:
        owners={name for name,spec in rm.items() if tool in spec.tools and tool not in registry.BORROWED.get(name,frozenset())}
        assert owners=={registry.RM_OWNER[tool]}, tool
    for name,borrowed in registry.BORROWED.items():  # another class's record, read only as a fixed read, never consent-gated
        assert borrowed<=set(rm[name].tools) and not borrowed&rm[name].loop_tools and not borrowed&registry.CONSENT_GATED
        assert all(registry.RM_OWNER[t]!=name for t in borrowed)
    assert set(registry.BORROWED)=={'rm_requests'}  # the Client and Calendar agents read only their own class
    assert set(rm['rm_calendar'].tools)=={'rm_appointments','rm_availability','rm_meeting_requests'}  # never client financial data
    assert all(rm[name].loop_tools<={t for t in rm[name].tools if registry.RM_OWNER.get(t)==name} for name in rm)  # loops read their own class


def test_no_loop_reaches_a_consent_gated_or_write_tool():
    for spec in CHAT_SPECS.values():
        assert not spec.loop_tools&registry.CONSENT_GATED, spec.name
        assert not any(t.startswith(WRITES) or t in KNOWN_TOOLS-set(TOOL_PERMISSION) for t in spec.loop_tools), spec.name
        assert spec.loop_tools<=set(tools.TOOLS)|spec.external
    assert not set(CHAT_SPECS['rm_requests'].tools)&registry.CONSENT_GATED  # the Requests agent never sees consent-gated data
    assert all(registry.RM_OWNER[t]=='rm_client' for t in registry.CONSENT_GATED)
    assert not any(spec.offers for spec in registry.RM_SPECS.values())  # RM cards are approved through the /rm routes, never offers


def test_gated_reads_take_their_purpose_from_the_intent():
    purposes={(intent,r.tool):r.fixed for intent,reads in registry.RM_CLIENT.intents.items() for r in reads if r.tool in {'rm_transaction_insights','rm_talking_points'}}
    assert purposes=={('client_overview','rm_transaction_insights'):{'purpose':'client_view'},('client_overview','rm_talking_points'):{'purpose':'client_view'},
        ('meeting_brief','rm_transaction_insights'):{'purpose':'meeting_prep'},('meeting_brief','rm_talking_points'):{'purpose':'meeting_prep'}}
    assert all(not r.entities for reads in registry.RM_CLIENT.intents.values() for r in reads)  # never a model or planner argument


def test_book_facts_carry_no_talking_points():
    assert registry.RM_CLIENT.intents['book_question']==(Read(tool='rm_book_facts'),)
    assert 'rm_talking_points' not in registry.RM_CLIENT.loop_tools and 'rm_book_facts' not in registry.CONSENT_GATED
    assert set(inspect.signature(tools.TOOLS['rm_book_facts']).parameters)==set()  # book-wide and never per client topic


def test_rm_exports_and_imports_follow_the_plan():
    rm=registry.RM_SPECS
    assert set(rm['rm_client'].exports)=={'client_ref','latest_note','reviews_due','clients_due','consent_active'}
    assert rm['rm_calendar'].exports=={'meetings_next_7d':'list[meeting_ref]','requests_to_answer':'list[meeting_ref]'}  # 3b: today and priorities
    assert set(rm['rm_requests'].exports)=={'needs_meeting','topics'}
    assert {'client_ref','latest_note','reviews_due','meetings_next_7d','requests_to_answer'}<=rm['rm_requests'].imports
    assert registry.INTENT_AGENT['today']=='rm_requests' and not {'rm_meeting_requests','rm_review_status'}&set(rm['rm_requests'].tools)
    assert {'needs_meeting','topics'}<=rm['rm_calendar'].imports
    assert registry.INTENT_AGENT['priorities']=='rm_requests' and registry.INTENT_AGENT['schedule']=='rm_calendar'
    assert {i for i,a in registry.INTENT_AGENT.items() if a=='rm_calendar'}=={'schedule','meeting_requests','decide_request','availability_change'}
