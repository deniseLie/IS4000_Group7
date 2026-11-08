"""Domain registry and the tool boundaries to enforce as each shared service is integrated.

TOOLS is the v4 allow-list (permission names per agent). CHAT_SPECS is the v5 chat catalogue (architecture §5.6.1, §14.8):
one AgentSpec per target agent is the single source for its tools, field views, limits, intents and default reads,
loop tools and offers. authorization.decide and tools.call both read it. The client specs run on /chat since phase 5, but
the rules in agents/rules.py still call the v4 responders under their v4 names (retail, compliance, document,
relationship) with the v4 allow-lists, so only the names without a v4 entry are enforced from CHAT_SPECS (ENFORCED).
The three RM specs (Client, Requests, Calendar; build plan 3a) run on the RM graph (services/rm/graph.py) for a verified
`rm` login; the client planner never sees them (CLIENT_SPECS). SPECS also includes the separate meeting workflow.
"""
from types import MappingProxyType
from app.schemas.contracts import QUESTION, AgentName, AgentSpec, Domain, Read, TargetAgent, ToolView
from app.schemas.meeting_agents import MeetingAgent, MeetingAgentSpec, MeetingTool

AGENT_FOR: dict[Domain, AgentName] = {'retail':'retail','smalltalk':'retail','fraud':'fraud',
    'compliance':'compliance','document':'document','relationship':'relationship'}
TOOLS: dict[AgentName, tuple[str, ...]] = {
    'retail': ('kb.search','customer.get_profile','customer.get_accounts',
        'customer.get_transactions','customer.get_credit_cards','customer.get_loans','tickets.list','handoff.create'),
    'fraud': ('customer.get_transactions','customer.get_credit_cards','actions.propose','handoff.create'),
    'compliance': ('compliance.get_case','compliance.workspace','compliance.update_checklist_item','kb.search','handoff.create'),
    'document': ('documents.precheck','compliance.workspace'),
    'relationship': ('kb.search','customer.get_profile','rm.get_assignment','rm.get_introduction','requests.list',
        'appointments.find_slots','appointments.list','consent.check','consent.records','actions.propose','agenda.save','consent.request','handoff.create',
        'meeting_requests.list',  # agents/meeting_answers counts her open meeting requests before offering another (0106 allows 3)
        'consent.sharing','insights.read'),  # consent v2 (6e): agents/consent_answers reads her cards and topics to offer a change
}

# Model-facing tool -> the permissions its service read checks. document_templates is fixed public configuration.
TOOL_PERMISSION: MappingProxyType[str, tuple[str, ...]] = MappingProxyType({
    'knowledge_search':('kb.search',),'knowledge_article':('kb.search',),'request_categories':('tickets.categories',),'document_templates':(),
    'my_profile':('customer.get_profile',),'my_accounts':('customer.get_accounts',),'my_cards':('customer.get_credit_cards',),
    'my_loans':('customer.get_loans',),'my_portfolio':('customer.get_portfolio',),'my_transactions':('customer.get_transactions',),
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
    answers='General banking questions with no personal data: products, fees, how-to steps, which request to raise, '
        'login trouble, unexpected one-time codes and phishing. Personal questions get "sign in to check".',
    tools={'knowledge_search':KB,'request_categories':ToolView()},
    intents={'general':(ASK,),'how_to_request':(ASK,Read(tool='request_categories')),'login_help':(ASK,),'phishing':(ASK,)},
    loop_tools=frozenset({'knowledge_search','request_categories'}),external=frozenset({'public_holidays','fx_rate'}))

ACCOUNTS=AgentSpec(name='accounts',role='client',scope='own_customer',prompt='accounts',
    answers='The client\'s own accounts, cards, loans, investment portfolio (holdings, her time deposit and its maturity) and transactions: '
        'balances, limits, due dates, fees, payments, transfers and statements, and whether her savings stay above an amount after paying her card balances. '
        'Card-limit and statement-copy requests.',
    tools={'my_accounts':ToolView(fields=frozenset({'type','number','currency','balance','status'})),
        'my_cards':ToolView(fields=CARD_FIELDS),
        'my_loans':ToolView(fields=frozenset({'type','currency','principal','outstanding','next_payment','next_amount','status'})),
        'my_portfolio':ToolView(fields=frozenset({'asset_class','description','currency','market_value','mandate','as_of','portfolio_total'})),  # 0116
        'my_transactions':ToolView(fields=frozenset({'date','description','amount','currency','status','card_last4','is_overseas','failure_reason'}),
            max_days=TXN_DAYS,max_rows=TXN_ROWS),
        'knowledge_search':KB,'request_categories':ToolView()},
    intents={'card_figures':(Read(tool='my_cards'),),
        'card_limit':(Read(tool='my_cards'),Read(tool='request_categories'),ASK),
        'card_fees':(Read(tool='my_cards'),ASK),
        'account_balance':(Read(tool='my_accounts'),Read(tool='my_portfolio')),
        'portfolio':(Read(tool='my_portfolio'),ASK),  # 0116: holdings, her time deposit and when it matures
        'transactions':(Read(tool='my_transactions',entities={'status':'status','days':'days'}),),
        'transfer_status':(Read(tool='my_transactions',fixed={'status':'pending'}),Read(tool='my_accounts')),
        'loans':(Read(tool='my_loans'),ASK),
        'statements':(Read(tool='my_accounts'),ASK),
        'product_info':(ASK,),
        'fx_rate':(Read(tool='fx_rate',entities={'base':'base','quote':'quote'}),ASK),
        'trip_due_dates':(Read(tool='my_cards'),Read(tool='my_loans')),  # + public holidays per trip year (agents/derived.py)
        'savings_after_cards':(Read(tool='my_accounts'),Read(tool='my_cards'))},  # savings minus card balances vs her amount (derived.py)
    loop_tools=frozenset({'my_accounts','my_cards','my_loans','my_portfolio','my_transactions','knowledge_search','fx_rate','public_holidays'}),
    external=frozenset({'fx_rate','public_holidays'}),offers=frozenset({'ticket.create'}),hands_to=frozenset({'relationship'}),
    exports={'due_in_window':'bool','due_dates':'list[date]','account_refs':'list[account_ref]','below_threshold':'bool'},sort_by={'account_refs':'last4'})

# No Security agent since build plan 2b: a lost card or a charge she doesn't recognise gets fixed steps and one urgent
# request to her RM (orchestrator, rm_handover.urgent_*), and login, OTP and phishing questions are Guide's.

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
    hands_to=frozenset({'relationship'}),imports=frozenset({'account_refs'}),  # account type, currency, last 4; no balances
    exports={'check_failed':'bool'})  # computed by code on the rules path (rules.compliance_rules, derived.check_failed)

RELATIONSHIP=AgentSpec(name='relationship',role='client',scope='own_customer',prompt='relationship',
    answers='Everything about a person at the bank: the client\'s RM, meeting requests and free times, consent to share, '
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
        # 2a: a meeting is a request (purpose, topics, up to 3 free times) she confirms; the chat never books directly.
        'meeting_request':(Read(tool='my_rm'),Read(tool='free_slots',entities={'from_date':'from_date','to_date':'to_date'})),
        'appointments':(Read(tool='my_appointments'),),
        'request_status':(Read(tool='my_tickets'),),
        'consent':(Read(tool='my_consents'),ASK),
        'service_options':(Read(tool='request_categories'),ASK)},
    loop_tools=frozenset({'my_rm','my_appointments','free_slots','my_consents','request_categories','knowledge_search'}),
    external=frozenset({'public_holidays'}),imports=frozenset({'due_dates'}),
    offers=frozenset({'meeting.request','appointment.cancel','handoff.create','tickets.follow_up',
        'consent.grant','consent.revoke','rm.share_summary','insight.share',  # insight.share: a compose rule, never the model (0108)
        'insight.delete',  # consent v2 (6e): rules in agents/consent_answers offer consent.grant/revoke and insight.delete
        'ticket.create'}))  # 0146: and a consent_withdrawal request to stop recording her meetings

# RM agents (architecture §5.6.1; build plan 3a): row scope is the RM's own book. Each owns one data class (RM_OWNER) and is
# the only proposer of one kind of change. A known intent runs the reads its handler in services/rm/handlers.py makes
# (no step call); `open` runs the bounded loop over loop_tools (rm/graph.py → runner.loop). The client, ticket or meeting
# is resolved before the agent runs (rm/graph.py).
WHOLE=ToolView()
TEXT=ToolView(free_text=True)  # notes, ticket messages and what clients wrote: untrusted data, never shown to a loop's step model
PROFILE=Read(tool='rm_client_profile')
NOTES=Read(tool='rm_meeting_notes')


def gated(tool: str,purpose: str) -> Read:
    """A consent-gated read (0106/0107): the purpose is set here by code from the intent, never by a model."""
    return Read(tool=tool,fixed={'purpose':purpose})


RM_CLIENT=AgentSpec(name='rm_client',role='rm',scope='rm_book',prompt='rm_assistant',
    answers="One client in the RM's book (360 overview with consented transaction insights and talking points, meeting "
        "history, meeting briefs, handover briefs, the client's shared handover note under consent) and questions across "
        'the book (reviews due, book facts). Read-only: proposes nothing.',
    tools={'rm_client_profile':TEXT,'rm_meeting_notes':TEXT,'rm_shared_summary':TEXT,'rm_handover_sources':TEXT,
        'rm_cash_events':WHOLE,'rm_book_facts':WHOLE,'rm_review_status':WHOLE,'rm_transaction_insights':WHOLE,'rm_talking_points':WHOLE},
    intents={'client_overview':(PROFILE,Read(tool='rm_cash_events'),gated('rm_transaction_insights','client_view'),gated('rm_talking_points','client_view')),
        'meeting_history':(PROFILE,NOTES),
        'meeting_brief':(PROFILE,NOTES,Read(tool='rm_shared_summary'),Read(tool='rm_cash_events'),
            gated('rm_transaction_insights','meeting_prep'),gated('rm_talking_points','meeting_prep')),
        'handover_brief':(Read(tool='rm_handover_sources'),),'shared_note':(Read(tool='rm_shared_summary'),),
        'reviews':(Read(tool='rm_review_status'),),'book_question':(Read(tool='rm_book_facts'),),'help':()},
    loop_tools=frozenset({'rm_client_profile','rm_meeting_notes','rm_review_status','rm_book_facts'}),
    exports={'client_ref':'client_ref','latest_note':'reference','reviews_due':'list[client_ref]','clients_due':'list[client_ref]','consent_active':'bool'},
    sort_by={'reviews_due':'due_date','clients_due':'due_date'})

RM_REQUESTS=AgentSpec(name='rm_requests',role='rm',scope='rm_book',prompt='rm_assistant',
    answers="Requests work in the RM's book: what to do first, her day in priority order, the ticket inbox, one ticket, client "
        'message drafts (sent only on Approve), documenting a meeting and follow-ups from a saved note (tasks and service requests she approves).',
    tools={'rm_tickets':TEXT,'rm_ticket':TEXT,'rm_followups':WHOLE,'request_categories':WHOLE,'knowledge_article':WHOLE,
        # BORROWED fixed reads (never in loop_tools, never consent-gated): the client's name, open requests and tasks for a
        # draft or documentation, the notes a recap or follow-ups quote, the free times a draft may offer, meetings to rank.
        'rm_client_profile':TEXT,'rm_meeting_notes':TEXT,'rm_availability':WHOLE,'rm_appointments':WHOLE},
    # knowledge_article: the approved article a ticket's request type names (category_info.kb_article), read by id after rm_ticket.
    # priorities and today rank with services/rm/priority.py. today takes its meetings, meeting requests and reviews from the
    # Calendar and Client agents' exports (rm/graph.py FEEDS); priorities takes only meeting requests from a feed, reads its
    # own 30-day meetings and takes reviews from rm_followups.
    intents={'priorities':(Read(tool='rm_tickets'),Read(tool='rm_followups'),Read(tool='rm_appointments')),
        'today':(Read(tool='rm_tickets'),Read(tool='rm_followups')),'ticket_inbox':(Read(tool='rm_tickets'),),
        'ticket_summary':(PROFILE,Read(tool='rm_ticket'),Read(tool='knowledge_article')),
        'draft_message':(PROFILE,Read(tool='rm_ticket'),NOTES,Read(tool='rm_availability'),Read(tool='knowledge_article')),
        'document_interaction':(Read(tool='rm_appointments'),PROFILE,Read(tool='request_categories')),
        'actions_from_note':(PROFILE,NOTES,Read(tool='request_categories'))},
    loop_tools=frozenset({'rm_tickets','rm_ticket','rm_followups','request_categories','knowledge_article'}),
    exports={'needs_meeting':'bool','topics':'list[label]'},
    imports=frozenset({'clients_due','latest_note','client_ref','reviews_due','meetings_next_7d','requests_to_answer'}))

RM_CALENDAR=AgentSpec(name='rm_calendar',role='rm',scope='rm_book',prompt='rm_assistant',
    answers="The RM's calendar: her appointments, free times and working hours, clients' meeting requests, and cards to "
        'accept a requested time, propose another, decline, block time or change her hours (each applied only on Approve). '
        'Never client financial data.',
    tools={'rm_appointments':WHOLE,'rm_availability':WHOLE,'rm_meeting_requests':TEXT},  # purposes are the client's own words
    intents={'schedule':(Read(tool='rm_appointments'),),'meeting_requests':(Read(tool='rm_meeting_requests'),),
        'decide_request':(Read(tool='rm_meeting_requests'),Read(tool='rm_availability')),'availability_change':(Read(tool='rm_availability'),)},
    loop_tools=frozenset({'rm_appointments','rm_availability','rm_meeting_requests'}),
    exports={'meetings_next_7d':'list[meeting_ref]','requests_to_answer':'list[meeting_ref]'},sort_by={'meetings_next_7d':'starts_at','requests_to_answer':'respond_by'},
    imports=frozenset({'needs_meeting','topics','client_ref'}))
# No RM agent makes ledger offers: drafts, documentation, proposed tasks or requests and calendar decisions are review
# cards the RM approves one by one through the /rm write routes (rm.send_client_update, rm.save_interaction,
# rm.create_tasks, /rm/meeting-requests/{id}/decide, /rm/availability/...). No write tool is a model-facing tool.

CHAT_SPECS: MappingProxyType[TargetAgent, AgentSpec] = MappingProxyType({s.name:s for s in (GUIDE,ACCOUNTS,COMPLIANCE,RELATIONSHIP,RM_CLIENT,RM_REQUESTS,RM_CALENDAR)})

# Fixed connectors for the separate meeting workflow. The model never chooses or invokes them.
BRIEF_WRITER = MeetingAgentSpec('brief_writer', 'brief_writer', 'v4',
    'Select references from the assigned RM’s currently permitted preparation records.', (
        MeetingTool('meeting_preparation_inputs', 'assigned_rm', 'read'),
        MeetingTool('meeting_save_brief', 'assigned_rm', 'draft_write'),
    ))
PROPOSAL_DRAFTER = MeetingAgentSpec('proposal_drafter', 'proposal_drafter', 'v1',
    'Draft a discussion pack from the agreed agenda and approved catalogue.', (
        MeetingTool('meeting_pack_inputs', 'assigned_rm', 'read'),
        MeetingTool('meeting_save_pack', 'assigned_rm', 'draft_write'),
    ))
MINUTES_WRITER = MeetingAgentSpec('minutes_writer', 'minutes_writer', 'v5',
    'Draft minutes for one meeting from reviewed RM notes or a consented redacted transcript.', (
        MeetingTool('meeting_notes_context', 'assigned_rm', 'read'),
        MeetingTool('meeting_save_reviewed_draft', 'assigned_rm', 'draft_write'),
        MeetingTool('meeting_job_context', 'claimed_worker', 'read'),
        MeetingTool('meeting_save_job_draft', 'claimed_worker', 'draft_write'),
    ))
MINUTES_OVERVIEW = MeetingAgentSpec('minutes_overview', 'minutes_overview', 'v2',
    'Consolidate supported statements already selected by Minutes Writer.', ())
MEETING_SPECS: MappingProxyType[MeetingAgent, MeetingAgentSpec] = MappingProxyType({
    s.name:s for s in (BRIEF_WRITER, PROPOSAL_DRAFTER, MINUTES_WRITER, MINUTES_OVERVIEW)
})
MEETING_TOOLS = frozenset(tool.name for spec in MEETING_SPECS.values() for tool in spec.tools)
SPECS: MappingProxyType[TargetAgent | MeetingAgent, AgentSpec | MeetingAgentSpec] = MappingProxyType({**CHAT_SPECS, **MEETING_SPECS})

CLIENT_SPECS: MappingProxyType[TargetAgent, AgentSpec] = MappingProxyType({n:s for n,s in CHAT_SPECS.items() if s.role!='rm'})
RM_SPECS: MappingProxyType[TargetAgent, AgentSpec] = MappingProxyType({n:s for n,s in CHAT_SPECS.items() if s.role=='rm'})
INTENT_AGENT: MappingProxyType[str, TargetAgent] = MappingProxyType({i:s.name for s in RM_SPECS.values() for i in s.intents})  # RM intents are unique
# Build plan 3a: each RM data tool has exactly one owning agent; another RM agent may hold it only as a BORROWED fixed read.
RM_OWNER: MappingProxyType[str, TargetAgent] = MappingProxyType({**{t:'rm_client' for t in ('rm_client_profile','rm_meeting_notes','rm_shared_summary',
        'rm_handover_sources','rm_cash_events','rm_book_facts','rm_review_status','rm_transaction_insights','rm_talking_points')},
    **{t:'rm_requests' for t in ('rm_tickets','rm_ticket','rm_followups','request_categories','knowledge_article')},
    **{t:'rm_calendar' for t in ('rm_appointments','rm_availability','rm_meeting_requests')}})
BORROWED: MappingProxyType[TargetAgent, frozenset[str]] = MappingProxyType({'rm_requests':frozenset({'rm_client_profile','rm_meeting_notes','rm_availability','rm_appointments'})})
# Reads SQL answers only under the client's consent (rm.consent_active, 0106; rm_brief for the shared note and handover):
# fixed reads of the Client agent only, never in loop_tools, their purpose set by the intent.
CONSENT_GATED=frozenset({'rm_transaction_insights','rm_talking_points','rm_cash_events','rm_shared_summary','rm_handover_sources'})
ENFORCED = frozenset(CHAT_SPECS) - frozenset(TOOLS)  # guide, accounts and the RM agents; the rest once the v4 responders move into specs
# A condition or for_each export only this intent computes: a link that names it sets the upstream task's intent
# (orchestrator.export_intents), so a declared link is not skipped as unknown because the planner chose another intent.
EXPORT_INTENT: MappingProxyType[str, str] = MappingProxyType({'due_in_window':'trip_due_dates','due_dates':'trip_due_dates',
    'below_threshold':'savings_after_cards','check_failed':'document_findings'})


def spec_for(agent: str | None) -> AgentSpec | None:
    """The enforced v5 spec for this backend-assigned agent, or None (no agent, or a v4 agent)."""
    return CHAT_SPECS[agent] if agent in ENFORCED else None


def permissions(spec: AgentSpec) -> frozenset[str]:
    return frozenset(p for tool in spec.tools for p in TOOL_PERMISSION[tool])
