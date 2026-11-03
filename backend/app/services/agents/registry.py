"""Domain registry and the tool boundaries to enforce as each shared service is integrated.

TOOLS is the v4 allow-list (permission names per agent). SPECS is the v5 agent catalogue (architecture §5.6.1, §14.8):
one AgentSpec per target agent is the single source for its tools, field views, limits, intents and default reads,
loop tools and offers. authorization.decide and tools.call both read it. `compliance` and `relationship` keep their v4
allow-lists until the phase 5 switch-over, so only the new names are enforced from SPECS before then (ENFORCED).
"""
from types import MappingProxyType
from app.schemas.contracts import QUESTION, AgentName, AgentSpec, Domain, Read, TargetAgent, ToolView

AGENT_FOR: dict[Domain, AgentName] = {'retail':'retail','smalltalk':'retail','fraud':'fraud',
    'compliance':'compliance','document':'document','relationship':'relationship'}
TOOLS: dict[AgentName, tuple[str, ...]] = {
    'retail': ('kb.search','kb.get_fact','customer.get_profile','customer.get_accounts',
        'customer.get_transactions','customer.get_credit_cards','customer.get_loans','tickets.list','handoff.create'),
    'fraud': ('customer.get_transactions','customer.get_credit_cards','actions.propose','handoff.create'),
    'compliance': ('compliance.get_case','compliance.workspace','compliance.update_checklist_item','kb.search','handoff.create'),
    'document': ('documents.precheck','compliance.workspace'),
    'relationship': ('kb.search','customer.get_profile','rm.get_assignment','rm.get_introduction','requests.list',
        'appointments.find_slots','appointments.list','consent.check','consent.records','actions.propose','agenda.save','consent.request','handoff.create'),
}

# Model-facing tool -> the permissions its service read checks. document_templates is fixed public configuration.
TOOL_PERMISSION: MappingProxyType[str, tuple[str, ...]] = MappingProxyType({
    'knowledge_search':('kb.search',),'request_categories':('tickets.categories',),'document_templates':(),
    'my_profile':('customer.get_profile',),'my_accounts':('customer.get_accounts',),'my_cards':('customer.get_credit_cards',),
    'my_loans':('customer.get_loans',),'my_transactions':('customer.get_transactions',),
    'travel_flags':('customer.get_credit_cards','customer.get_transactions'),'my_tickets':('tickets.list',),
    'my_compliance_case':('compliance.get_case',),'my_document_checks':('compliance.workspace',),
    'my_rm':('rm.get_assignment',),'my_appointments':('appointments.list',),'free_slots':('appointments.find_slots',),
    'my_consents':('consent.records',),
})

EXTERNAL=frozenset({'public_holidays','fx_rate'})  # services/external.TOOLS: public data, codes only
KB=ToolView()
ASK=Read(tool='knowledge_search',entities={'query':QUESTION})
CARD_FIELDS=frozenset({'last4','name','network','status','currency','limit','outstanding','minimum_payment','due_date','unfreeze_blocked_reason'})
TXN_DAYS,TXN_ROWS=90,100

GUIDE=AgentSpec(name='guide',role='public',scope='public',prompt='guide',
    answers='General banking questions with no personal data: products, fees, how-to steps, which request to raise. '
        'Personal questions get "sign in to check".',
    tools={'knowledge_search':KB,'request_categories':ToolView()},
    intents={'general':(ASK,),'how_to_request':(ASK,Read(tool='request_categories'))},
    loop_tools=frozenset({'knowledge_search','request_categories'}),external=frozenset({'public_holidays','fx_rate'}))

ACCOUNTS=AgentSpec(name='accounts',role='client',scope='own_customer',prompt='accounts',
    answers='The client\'s own accounts, cards, loans and transactions: balances, limits, due dates, fees, payments, '
        'transfers and statements. Card-limit and statement-copy requests.',
    tools={'my_accounts':ToolView(fields=frozenset({'type','number','currency','balance','status'})),
        'my_cards':ToolView(fields=CARD_FIELDS),
        'my_loans':ToolView(fields=frozenset({'type','currency','principal','outstanding','next_payment','next_amount','status'})),
        'my_transactions':ToolView(fields=frozenset({'date','description','amount','currency','status','card_last4','is_overseas','failure_reason'}),
            max_days=TXN_DAYS,max_rows=TXN_ROWS),
        'knowledge_search':KB,'request_categories':ToolView()},
    intents={'card_figures':(Read(tool='my_cards'),),
        'card_limit':(Read(tool='my_cards'),Read(tool='request_categories'),ASK),
        'card_fees':(Read(tool='my_cards'),ASK),
        'account_balance':(Read(tool='my_accounts'),),
        'transactions':(Read(tool='my_transactions',entities={'status':'status','days':'days'}),),
        'transfer_status':(Read(tool='my_transactions',fixed={'status':'pending'}),Read(tool='my_accounts')),
        'loans':(Read(tool='my_loans'),ASK),
        'statements':(Read(tool='my_accounts'),ASK),
        'product_info':(ASK,),
        'fx_rate':(Read(tool='fx_rate',entities={'base':'base','quote':'quote'}),ASK)},
    loop_tools=frozenset({'my_accounts','my_cards','my_loans','my_transactions','knowledge_search','fx_rate','public_holidays'}),
    external=frozenset({'fx_rate','public_holidays'}),offers=frozenset({'ticket.create'}),hands_to=frozenset({'relationship'}))

SECURITY=AgentSpec(name='security',role='client',scope='own_customer',prompt='security',
    answers='Unrecognised charges, lost or stolen cards, login trouble, unexpected codes, phishing and travel readiness. '
        'Reports to the fraud team; never freezes, unfreezes, disputes or cancels.',
    tools={'my_cards':ToolView(fields=frozenset({'card_id','last4','status'})),
        'my_transactions':ToolView(fields=frozenset({'txn_id','date','description','amount','currency','status','card_id','card_last4','is_overseas'}),
            max_days=TXN_DAYS,max_rows=TXN_ROWS),
        'travel_flags':ToolView(),'knowledge_search':KB},
    intents={'unrecognised_charge':(Read(tool='my_cards'),Read(tool='my_transactions'),Read(tool='travel_flags'),ASK),
        'lost_card':(Read(tool='my_cards'),ASK),'login_help':(ASK,),'phishing':(ASK,),
        'travel_readiness':(Read(tool='my_cards'),ASK)},
    external=frozenset({'public_holidays'}),offers=frozenset({'handoff.create','card.order_replacement'}))

COMPLIANCE=AgentSpec(name='compliance',role='client',scope='own_customer',prompt='compliance',
    answers='The client\'s compliance review: checklist, missing items, deadlines, bank questions, uploads and check '
        'results, tax-form pre-fill. Never a bank decision.',
    tools={'my_compliance_case':ToolView(free_text=True),'my_document_checks':ToolView(),'document_templates':ToolView(),
        'my_profile':ToolView(fields=frozenset({'name','employment_status'})),'knowledge_search':KB},
    intents={'review_status':(Read(tool='my_compliance_case'),Read(tool='my_document_checks')),
        'missing_items':(Read(tool='my_compliance_case'),),
        'document_findings':(Read(tool='my_document_checks'),Read(tool='document_templates')),
        'bank_question':(Read(tool='my_compliance_case'),),
        'tax_form':(Read(tool='my_profile'),ASK),
        'guidance':(ASK,Read(tool='document_templates'))},
    loop_tools=frozenset({'my_document_checks','document_templates','knowledge_search'}),
    offers=frozenset({'compliance.update_checklist_item','compliance.answer_request','document.submit','handoff.create'}),
    hands_to=frozenset({'relationship'}))

RELATIONSHIP=AgentSpec(name='relationship',role='client',scope='own_customer',prompt='relationship',
    answers='Everything about a person at the bank: the client\'s RM, meetings and free times, consent to share, '
        'and the status of her own requests and tickets. RM handovers.',
    tools={'my_rm':ToolView(fields=frozenset({'current.name','current.role','current.team','current.contact.phone',
            'current.timezone','current.started_on','previous.name','previous.ended_on'})),
        'my_appointments':ToolView(fields=frozenset({'starts_at','ends_at','format','status','hold_expires_at'}),max_rows=50),
        'free_slots':ToolView(fields=frozenset({'starts_at','ends_at'}),max_days=14,max_rows=20),
        'my_consents':ToolView(fields=frozenset({'requests.purpose','requests.categories','requests.recipient_name','requests.status',
            'requests.grant_expires_at','grants.purpose','grants.categories','grants.recipient_name','grants.active','grants.granted_at',
            'grants.revoked_at','grants.expires_at'})),
        'my_tickets':ToolView(fields=frozenset({'reference','category_label','summary','urgency','status','owner_team','decided_by',
            'created_at','updated_at','target_at','overdue','last_update'}),max_rows=50,free_text=True),
        'request_categories':ToolView(),'knowledge_search':KB},
    intents={'my_rm':(Read(tool='my_rm'),),
        'meeting_times':(Read(tool='my_rm'),Read(tool='free_slots',entities={'from_date':'from_date','to_date':'to_date'})),
        'appointments':(Read(tool='my_appointments'),),
        'request_status':(Read(tool='my_tickets'),),
        'consent':(Read(tool='my_consents'),ASK),
        'service_options':(Read(tool='request_categories'),ASK)},
    loop_tools=frozenset({'my_rm','my_appointments','free_slots','my_consents','request_categories','knowledge_search'}),
    external=frozenset({'public_holidays'}),
    offers=frozenset({'appointment.hold','appointment.confirm','appointment.cancel','handoff.create','tickets.follow_up',
        'consent.grant','consent.revoke','rm.share_summary'}))

SPECS: MappingProxyType[TargetAgent, AgentSpec] = MappingProxyType({s.name:s for s in (GUIDE,ACCOUNTS,SECURITY,COMPLIANCE,RELATIONSHIP)})
ENFORCED = frozenset(SPECS) - frozenset(TOOLS)  # phase 5 enforces every spec


def spec_for(agent: str | None) -> AgentSpec | None:
    """The enforced v5 spec for this backend-assigned agent, or None (no agent, or a v4 agent)."""
    return SPECS[agent] if agent in ENFORCED else None


def permissions(spec: AgentSpec) -> frozenset[str]:
    return frozenset(p for tool in spec.tools for p in TOOL_PERMISSION[tool])
