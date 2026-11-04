"""Domain registry and the tool boundaries to enforce as each shared service is integrated.

TOOLS is the v4 allow-list (permission names per agent). SPECS is the v5 agent catalogue (architecture §5.6.1, §14.8):
one AgentSpec per target agent is the single source for its tools, field views, limits, intents and default reads,
loop tools and offers. authorization.decide and tools.call both read it. All five specs run on /chat since phase 5, but
the rules in agents/rules.py still call the v4 responders under their v4 names (retail, fraud, compliance, document,
relationship) with the v4 allow-lists, so only the names without a v4 entry are enforced from SPECS (ENFORCED).
The two RM specs (phase 9) run on the RM graph (services/rm/graph.py) for a verified `rm` login; the client planner
never sees them (CLIENT_SPECS).
"""
from types import MappingProxyType
from app.schemas.contracts import QUESTION, AgentName, AgentSpec, Domain, Read, TargetAgent, ToolView

AGENT_FOR: dict[Domain, AgentName] = {'retail':'retail','smalltalk':'retail','fraud':'fraud',
    'compliance':'compliance','document':'document','relationship':'relationship'}
TOOLS: dict[AgentName, tuple[str, ...]] = {
    'retail': ('kb.search','customer.get_profile','customer.get_accounts',
        'customer.get_transactions','customer.get_credit_cards','customer.get_loans','tickets.list','handoff.create'),
    'fraud': ('customer.get_transactions','customer.get_credit_cards','actions.propose','handoff.create'),
    'compliance': ('compliance.get_case','compliance.workspace','compliance.update_checklist_item','kb.search','handoff.create'),
    'document': ('documents.precheck','compliance.workspace'),
    'relationship': ('kb.search','customer.get_profile','rm.get_assignment','rm.get_introduction','requests.list',
        'appointments.find_slots','appointments.list','consent.check','consent.records','actions.propose','agenda.save','consent.request','handoff.create'),
}

# Model-facing tool -> the permissions its service read checks. document_templates is fixed public configuration.
TOOL_PERMISSION: MappingProxyType[str, tuple[str, ...]] = MappingProxyType({
    'knowledge_search':('kb.search',),'knowledge_article':('kb.search',),'request_categories':('tickets.categories',),'document_templates':(),
    'my_profile':('customer.get_profile',),'my_accounts':('customer.get_accounts',),'my_cards':('customer.get_credit_cards',),
    'my_loans':('customer.get_loans',),'my_transactions':('customer.get_transactions',),
    'travel_flags':('customer.get_credit_cards','customer.get_transactions'),'my_tickets':('tickets.list',),
    'my_compliance_case':('compliance.get_case',),'my_document_checks':('compliance.workspace',),
    'my_rm':('rm.get_assignment',),'my_appointments':('appointments.list',),'free_slots':('appointments.find_slots',),
    'my_consents':('consent.records',),
    # RM reads: SQL checks the verified rm login and the book (rm.current_advisor, rm.in_book; 0089) and audits each read.
    **{name:() for name in ('rm_book','rm_match_clients','rm_client_profile','rm_meeting_notes','rm_shared_summary','rm_tickets',
        'rm_ticket','rm_review_status','rm_appointments','rm_availability','rm_followups','rm_handover_sources','rm_cash_events','rm_book_facts',
        'rm_transaction_insights','rm_talking_points','rm_meeting_requests')},  # the gated ones also check rm.consent_active (0106)
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
        'fx_rate':(Read(tool='fx_rate',entities={'base':'base','quote':'quote'}),ASK),
        'trip_due_dates':(Read(tool='my_cards'),Read(tool='my_loans'))},  # + public holidays per trip year (agents/derived.py)
    loop_tools=frozenset({'my_accounts','my_cards','my_loans','my_transactions','knowledge_search','fx_rate','public_holidays'}),
    external=frozenset({'fx_rate','public_holidays'}),offers=frozenset({'ticket.create'}),hands_to=frozenset({'relationship'}),
    exports={'due_in_window':'bool','due_dates':'list[date]','account_refs':'list[account_ref]'},sort_by={'account_refs':'last4'})

SECURITY=AgentSpec(name='security',role='client',scope='own_customer',prompt='security',
    answers='Unrecognised charges, lost or stolen cards, login trouble, unexpected codes, phishing and travel readiness. '
        'Reports to the fraud team and sends travel notices; never freezes, unfreezes, disputes or cancels.',
    tools={'my_cards':ToolView(fields=frozenset({'card_id','last4','status'})),
        'my_transactions':ToolView(fields=frozenset({'txn_id','date','description','amount','currency','status','card_id','card_last4','is_overseas'}),
            max_days=TXN_DAYS,max_rows=TXN_ROWS),
        'travel_flags':ToolView(),'knowledge_search':KB},
    intents={'unrecognised_charge':(Read(tool='my_cards'),Read(tool='my_transactions'),Read(tool='travel_flags'),ASK),
        'lost_card':(Read(tool='my_cards'),ASK),'login_help':(ASK,),'phishing':(ASK,),
        'travel_readiness':(Read(tool='my_cards'),ASK)},
    external=frozenset({'public_holidays'}),offers=frozenset({'handoff.create','card.order_replacement','ticket.create'}))  # ticket: travel notice (0101)

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
    hands_to=frozenset({'relationship'}),imports=frozenset({'account_refs'}))  # C24: account type, currency, last 4; no balances

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
    external=frozenset({'public_holidays'}),imports=frozenset({'due_dates'}),
    offers=frozenset({'appointment.hold','appointment.confirm','appointment.cancel','handoff.create','tickets.follow_up',
        'consent.grant','consent.revoke','rm.share_summary'}))

# RM agents (architecture §5.6.1): row scope is the RM's own book. Each intent's reads are the ones its handler in
# services/rm/handlers.py makes; the client, ticket or meeting is resolved before the agent runs (rm/graph.py).
WHOLE=ToolView()
TEXT=ToolView(free_text=True)  # notes, ticket messages and what clients wrote: untrusted data, default reads only
PROFILE=Read(tool='rm_client_profile')
NOTES=Read(tool='rm_meeting_notes')

RM_INSIGHT=AgentSpec(name='rm_client_insight',role='rm',scope='rm_book',prompt='rm_assistant',
    answers="One client in the RM's book (360 overview, meeting history, meeting briefs, handover briefs, the client's "
        'shared handover note under consent) and questions across the book (reviews due, follow-ups, book facts).',
    tools={'rm_client_profile':TEXT,'rm_meeting_notes':TEXT,'rm_shared_summary':TEXT,'rm_handover_sources':TEXT,
        'rm_cash_events':WHOLE,'rm_book_facts':WHOLE,'rm_followups':WHOLE,'rm_review_status':WHOLE,'rm_appointments':WHOLE,
        # 0106: consent-gated reads for the meeting brief and the overview, and clients' meeting requests (their purposes
        # are the client's words). Build plan phase 3 moves rm_meeting_requests to the Calendar agent.
        'rm_transaction_insights':WHOLE,'rm_talking_points':WHOLE,'rm_meeting_requests':TEXT},
    intents={'client_overview':(PROFILE,Read(tool='rm_cash_events')),'meeting_history':(PROFILE,NOTES),
        'meeting_brief':(PROFILE,NOTES,Read(tool='rm_shared_summary'),Read(tool='rm_cash_events')),
        'handover_brief':(Read(tool='rm_handover_sources'),),'shared_note':(Read(tool='rm_shared_summary'),),
        'reviews':(Read(tool='rm_review_status'),),'priorities':(Read(tool='rm_followups'),Read(tool='rm_appointments')),
        'book_question':(Read(tool='rm_book_facts'),),'help':()},
    exports={'clients_due':'list[client_ref]','latest_note':'reference','consent_active':'bool'},sort_by={'clients_due':'due_date'})

RM_DESK=AgentSpec(name='rm_service_desk',role='rm',scope='rm_book',prompt='rm_assistant',
    answers="Ticket work in the RM's book: the inbox, one ticket, client message drafts (sent only on Approve), "
        'documenting a meeting, follow-ups from a saved note, her calendar.',
    tools={'rm_tickets':TEXT,'rm_ticket':TEXT,'rm_client_profile':TEXT,'rm_meeting_notes':TEXT,'rm_appointments':WHOLE,
        'rm_availability':WHOLE,'request_categories':WHOLE,'knowledge_article':WHOLE},
    # knowledge_article: the approved article a ticket's request type names (category_info.kb_article), read by id after rm_ticket.
    intents={'ticket_inbox':(Read(tool='rm_tickets'),),'schedule':(Read(tool='rm_appointments'),),
        'ticket_summary':(PROFILE,Read(tool='rm_ticket'),Read(tool='knowledge_article')),
        'draft_message':(PROFILE,Read(tool='rm_ticket'),NOTES,Read(tool='rm_availability'),Read(tool='knowledge_article')),
        'document_interaction':(Read(tool='rm_appointments'),PROFILE,Read(tool='request_categories')),
        'actions_from_note':(PROFILE,NOTES,Read(tool='request_categories'))},
    imports=frozenset({'clients_due','latest_note'}))
# Neither RM agent makes ledger offers: drafts, documentation and proposed tasks or requests are review cards the RM
# approves one by one through the /rm write routes (rm.send_client_update, rm.save_interaction, rm.create_tasks, ...).

SPECS: MappingProxyType[TargetAgent, AgentSpec] = MappingProxyType({s.name:s for s in (GUIDE,ACCOUNTS,SECURITY,COMPLIANCE,RELATIONSHIP,RM_INSIGHT,RM_DESK)})
CLIENT_SPECS: MappingProxyType[TargetAgent, AgentSpec] = MappingProxyType({n:s for n,s in SPECS.items() if s.role!='rm'})
RM_SPECS: MappingProxyType[TargetAgent, AgentSpec] = MappingProxyType({n:s for n,s in SPECS.items() if s.role=='rm'})
INTENT_AGENT: MappingProxyType[str, TargetAgent] = MappingProxyType({i:s.name for s in RM_SPECS.values() for i in s.intents})  # RM intents are unique
ENFORCED = frozenset(SPECS) - frozenset(TOOLS)  # guide, accounts, security and the RM agents; the rest once the v4 responders move into specs


def spec_for(agent: str | None) -> AgentSpec | None:
    """The enforced v5 spec for this backend-assigned agent, or None (no agent, or a v4 agent)."""
    return SPECS[agent] if agent in ENFORCED else None


def permissions(spec: AgentSpec) -> frozenset[str]:
    return frozenset(p for tool in spec.tools for p in TOOL_PERMISSION[tool])
