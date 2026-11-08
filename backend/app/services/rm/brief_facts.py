"""Code renders the records, the model writes the judgement (docs/planning/rm-answer-speed.md).

Measured 5 Nov 2026: on the Claude Code transport a model call takes about 5 s plus 1 s per ~235 tokens it writes,
thinking included, and input size barely matters. A brief that asked the model for nine sections wrote 10-18k tokens
(45-80 s) and used up the request budget (R22). So for the client reports (meeting brief, client overview, handover
brief, meeting history):

- record sections are written here by code from the records, each item citing its record id; figures, dates and
  statuses are copied from the record, so there is nothing for the checks to withhold;
- facts(): a small bundle for the model with the same ids as the full records, so citations and the Sources list still
  match: the latest note only (or the newest few for a handover), bounded lists, at most three large movements, insight
  figures, topics, preferences and the consent state. No older notes, no transaction rows, no calendar rows;
- points_schema(): the only part the model writes (a summary and a few points per heading), checked item by item
  against the facts it saw (checks.check_sections).
"""
from datetime import date
from decimal import Decimal,InvalidOperation
from typing import Any
from app.services.rm import evidence as ev

SHOWN=5  # items listed before a count line
TOP=3  # milestones, tasks, requests, preferences, movements, topics given to the model
NOTE_CHARS,TEXT_CHARS=600,200
STATUS_WORDS={'submitted':'submitted','in_review':'in review','waiting_on_client':'waiting on the client','open':'open',
    'not_submitted':'not sent yet','rejected':'returned for correction','active':'active','confirmed':'confirmed','held':'held'}
GAP_WORDS={'no_recorded_preferences':'No preferences are recorded for this client.','no_interaction_in_180_days':'No meeting is recorded in the last 180 days.',
    'no_open_compliance_review':'No open compliance review is recorded.','no_upcoming_appointment':'No upcoming meeting with you is booked.',
    'no_meeting_notes':'No meeting notes are recorded.'}
BRIEF_GAPS=('no_recorded_preferences','no_interaction_in_180_days')  # the brief's other sections already say the rest
BRIEF_ORDER=['The meeting','Who the client is','History and commitments','Open matters','What the client has told us',
    'Conversation starters','Suggested discussion points','Questions to ask','Gaps to check']
OVERVIEW_ORDER=['Snapshot','Accounts and products','Open requests','Upcoming','Recent large movements','Transaction insights',
    'Client chat insights','Preferences and recent interactions','Worth raising','Missing or unavailable']
# (key in the model's JSON, heading, at most, kind): what the model writes for each report
BRIEF_POINTS=(('conversation_starters','Conversation starters',3),('discussion_points','Suggested discussion points',4),('questions','Questions to ask',3))
OVERVIEW_POINTS=(('worth_raising','Worth raising',3),)
HANDOVER_POINTS=(('introductory_topics','Suggested introductory topics',4),('gaps','Gaps to clarify',3,'gap'))
HISTORY_POINTS=(('gaps','Gaps to check',3,'gap'),)
HISTORY_SHOWN=5  # notes in a meeting history before a count line


# Small helpers ----------------------------------------------------------------------------------------------------------
def item(text: str,kind: str,owner: str,ids: list[str]) -> dict:return {'text':text,'kind':kind,'owner':owner,'source_ids':list(dict.fromkeys(ids))}


def gap(text: str,ids: list[str]) -> dict:return item(text,'gap','none',ids)


def clip(text: Any,limit: int) -> str:
    text=' '.join(str(text or '').split())
    return text if len(text)<=limit else text[:limit-1].rstrip()+'…'


def money(amount: Any,currency: str|None) -> str:
    try:value=f'{Decimal(str(amount)):,.2f}'
    except (InvalidOperation,ValueError):value=str(amount)
    return f'{currency} {value}' if currency else value


def day(value: Any) -> str:
    """'2026-11-03' as '3 Nov 2026' (how the records' *_text fields and the prompts write dates); anything else as given."""
    try:return ev.day_words(date.fromisoformat(str(value)[:10]))
    except ValueError:return str(value or '')


def month(value: Any) -> str:
    """'2026-10' as 'Oct 2026'; anything else as given."""
    try:return f'{date.fromisoformat(str(value)+"-01"):%b %Y}'
    except ValueError:return str(value or '')


def plural(n: int,one: str,many: str|None=None) -> str:return f'{n} {one if n==1 else many or one+"s"}'


def joined(parts: list[str]) -> str:return ', '.join(parts[:-1])+' and '+parts[-1] if len(parts)>1 else ''.join(parts)


def words(code: Any) -> str:return STATUS_WORDS.get(str(code),str(code or '').replace('_',' '))


def lists(record: Any,limit: int=TOP,last: tuple[str,...]=('monthly','by_month')) -> Any:
    """A bounded copy of a record for the model: every list keeps its first `limit` items (the last ones for monthly
    totals), nested records are bounded the same way."""
    if isinstance(record,list):return [lists(x,limit) for x in record[:limit]]
    if isinstance(record,dict):return {k:[lists(x,limit) for x in v[-limit:]] if isinstance(v,list) and k in last else lists(v,limit) for k,v in record.items()}
    return record


def counted(rows: list[dict],what: str,where: str) -> list[dict]:
    """The first SHOWN rows, then one line counting the rest (citing them)."""
    rest=rows[SHOWN:]
    if not rest:return rows
    return rows[:SHOWN]+[item(f"{plural(len(rest),what)} more {'is' if len(rest)==1 else 'are'} not listed here; {where}.",'record','rm',
        [i for r in rest for i in r['source_ids']])]


# Record items (code) ------------------------------------------------------------------------------------------------------
def review_items(data: dict) -> list[dict]:
    review=data.get('compliance') or {}
    if not review.get('title'):return []
    todo=[x.get('label') for x in review.get('outstanding_items') or [] if isinstance(x,dict) and x.get('label')]
    asked=review.get('open_bank_questions') or []
    asked=asked if isinstance(asked,int) else len(asked)
    return [item(f"{review['title']}: {words(review.get('status'))}"+(f", due {review['due_text']}" if review.get('due_text') else '')
        +(f"; still to send: {', '.join(todo)}" if todo else '')+(f"; {plural(asked,'bank question')} open" if asked else '')+'.','record','rm',['compliance'])]


def ticket_line(t: dict) -> str:
    who='your request' if t.get('rm_owned') else f"specialist-managed ({words(t.get('owner_team'))}), read-only"
    when=', past its target time' if t.get('overdue') else f", target {t['target_sgt']}" if t.get('target_sgt') else ''
    status=t.get('client_safe_status') if not t.get('rm_owned') and t.get('client_safe_status') else words(t.get('status'))
    return f"{t['reference']} {t.get('category_label') or words(t.get('category'))}: {status}, {who}{when}."


def ticket_items(data: dict) -> list[dict]:
    tickets=sorted(data.get('tickets') or [],key=lambda t:not t.get('rm_owned'))  # her own work first
    return [item(ticket_line(t),'record','rm' if t.get('rm_owned') else 'specialist',['ticket-'+t['reference']]) for t in tickets]


def request_items(data: dict) -> list[dict]:
    return [item(f"Request to you: {clip(r.get('what'),TEXT_CHARS)} ({words(r.get('status'))}"+(f", {r['urgency']} urgency" if r.get('urgency') else '')+').',
        'client_stated','client',['rm-requests']) for r in data.get('rm_requests') or []]


def task_items(data: dict) -> list[dict]:
    return [item(f"Your task: {clip(k.get('title'),TEXT_CHARS)}"+(f", due {k['due_text']}" if k.get('due_text') else '')+(' (overdue)' if k.get('overdue') else '')+'.',
        'record','rm',['tasks']) for k in data.get('tasks') or []]


def milestone_items(data: dict) -> list[dict]:
    return [item(f"{clip(m.get('title'),TEXT_CHARS)} on {m.get('due_text')}"+(f", {money(m['amount'],m.get('currency'))}" if m.get('amount') is not None else '')+'.',
        'record','rm',['milestones']) for m in data.get('milestones') or []]


def meeting_items(data: dict) -> list[dict]:
    rows=[]
    for a in data.get('appointments') or []:
        how=ev.FORMAT_WORDS.get(a.get('format'),words(a.get('format')))
        held=f", held until {a['hold_expires']} (not confirmed)" if a.get('status')=='held' else ''
        rows.append(item(f"Meeting {a.get('when')}, {how}{held}"+(f": {clip(a['purpose'],TEXT_CHARS)}" if a.get('purpose') else '')+'.','record','rm',['appointments']))
    return rows


def open_items(data: dict) -> list[dict]:
    return [*review_items(data),*ticket_items(data),*request_items(data),*task_items(data),*milestone_items(data)]


def topic_items(bundle: ev.Bundle,limit: int=TOP) -> list[dict]:
    """Client chat insights: inferred ones are suggestions ("Not confirmed by the client"), the ones she shared are hers;
    a gap when she has not shared them or some are not available yet."""
    topics=bundle.get('talking-points')
    if topics is None:return []
    record=topics.data if isinstance(topics.data,dict) else {}
    rows=record.get('shared_by_client') if topics.kind=='consent' else record.get('points')
    items=[]
    for row in (rows if isinstance(rows,list) else [])[:limit]:
        inferred=row.get('label')=='ai_suggestion'
        items.append(item(f"Client chat insight: {row['line']} (last mentioned {row.get('last_seen')})."+(' Not confirmed by the client.' if inferred else ''),
            'suggestion' if inferred else 'client_stated','rm' if inferred else 'client',['talking-points']))
    if topics.kind=='consent':items.append(gap('The client has not shared chat insights with you.',['talking-points']))
    elif record.get('pending'):items.append(gap('Some client chat insights are not available yet.',['talking-points']))
    return items


def preference_items(data: dict,limit: int=TOP) -> list[dict]:
    return [item(f"{clip(p.get('preference'),TEXT_CHARS)} (recorded {p.get('recorded_text')}).",'client_stated','client',['preferences'])
        for p in (data.get('preferences') or [])[:limit]]


def shared_items(bundle: ev.Bundle,gap_when_none: bool=True) -> list[dict]:
    """The handover note she shared under active consent (her words), or one gap saying none is shared."""
    note=bundle.get('shared-note')
    if note is not None:
        rows=[item(f"Handover note the client shared ({n.get('shared_text')}): {clip(n.get('content'),400)}",'client_stated','client',['shared-note'])
            for n in (note.data.get('notes') or [])[:2]]
        excluded=note.data.get('excluded_notes_without_active_consent') or 0
        if excluded:rows.append(gap(f"{plural(excluded,'earlier handover note')} {'was' if excluded==1 else 'were'} left out because consent ended.",['shared-note']))
        return rows
    status=bundle.get('consent-status')
    if status is not None and gap_when_none and 'consent_active' in (status.data or {}):
        return [gap('No handover note is shared with you under active consent.',['consent-status'])]
    return []


def insights_gap(bundle: ev.Bundle) -> list[dict]:
    insights=bundle.get('transaction-insights')
    return [gap('The client has not shared transaction insights with you.',['transaction-insights'])] if insights is not None and insights.kind=='consent' else []


def note_head(note: dict) -> str:
    who='your note' if note.get('written_by_current_rm') else f"written by {note.get('author')}, the previous relationship manager"
    how=ev.FORMAT_WORDS.get(note.get('format'),str(note.get('format') or '').replace('_',' '))
    return f"{note.get('met_on_text')}{', '+how if how else ''} ({who})"+(f": {clip(note['title'],TEXT_CHARS)}" if note.get('title') else '')


def note_items(note: dict,source_id: str,summary: bool=True) -> list[dict]:
    """One note's summary, commitments (recorded, not necessarily completed), client requests and decisions."""
    items=[item(clip(note['summary'],NOTE_CHARS),'record','rm',[source_id])] if summary and note.get('summary') else []
    items+=[item(f'Commitment: {clip(c,TEXT_CHARS)} (recorded, not necessarily completed).','record','rm',[source_id]) for c in note.get('commitments') or []]
    items+=[item(f'The client asked: {clip(r,TEXT_CHARS)}','client_stated','client',[source_id]) for r in note.get('client_requests') or []]
    items+=[item(f'Decision: {clip(d,TEXT_CHARS)}','record','rm',[source_id]) for d in note.get('decisions') or []]
    return items


def withheld_gap(withheld: int) -> list[dict]:
    return [gap(f"{plural(withheld,'private note')} by other relationship managers {'is' if withheld==1 else 'are'} not available.",['notes-withheld'])] if withheld else []


def who_items(data: dict,products: bool=True) -> list[dict]:
    p,rel=data['profile'],data.get('relationship') or {}
    line=p['name']+(f" has been with you since {rel['assigned_since_text']}." if rel.get('assigned_since_text') else '.')
    if rel.get('inherited') and rel.get('previous_advisor'):
        line+=f" Inherited from {rel['previous_advisor']}"+(f" on {rel['previous_to_text']}" if rel.get('previous_to_text') else '')+'.'
    items=[item(line,'record','rm',['profile'])]
    details=[x for x in (p.get('employment_status') and f"employment: {words(p['employment_status'])}",
        p.get('income_range') and f"income range: {p['income_range']}",p.get('preferred_language') and f"preferred language: {p['preferred_language']}") if x]
    if details:items.append(item('; '.join(details).capitalize()+'.','record','rm',['profile']))
    if products:
        parts,ids=[],[]
        for key,label in (('accounts','account'),('cards','credit card'),('loans','loan')):
            rows=data.get(key) or []
            if rows:
                names=list(dict.fromkeys(words(r.get('type') or r.get('name') or label) for r in rows))
                parts.append(f"{plural(len(rows),label)} ({', '.join(names)})");ids.append(key)
        if (pf:=data.get('portfolio')):
            parts.append(f"an {words(pf.get('mandate'))} portfolio of {money(pf.get('total_value'),pf.get('currency'))}");ids.append('portfolio')
        if parts:items.append(item('Products: '+joined(parts)+'.','record','rm',ids))
    return items


def gap_items(data: dict,codes: tuple[str,...]|None=None) -> list[dict]:
    return [gap(GAP_WORDS[c],['profile']) for c in data.get('gaps') or [] if c in GAP_WORDS and (codes is None or c in codes)]


# Meeting brief (RM4) ----------------------------------------------------------------------------------------------------
def meeting_section(chosen: dict|None) -> dict:
    if not chosen:return {'heading':'The meeting','items':[gap('No single confirmed meeting with you was identified, so this brief is not tied to a meeting.',['selected-appointment'])]}
    how=ev.FORMAT_WORDS.get(chosen.get('format'),str(chosen.get('format') or 'format not recorded').replace('_',' '))
    purpose=f" Purpose: {clip(chosen['purpose'],TEXT_CHARS)}" if chosen.get('purpose') else ' No purpose is recorded.'
    return {'heading':'The meeting','items':[item(f"{chosen['when']}, {how}.{purpose}",'record','rm',['selected-appointment'])]}


def who_section(data: dict) -> dict:return {'heading':'Who the client is','items':who_items(data)}


def history_section(notes: list[dict],withheld: int=0) -> dict:
    """The latest note in full (commitments said to be recorded, not done), the rest counted."""
    if not notes:items=[gap('No meeting notes are recorded.',['notes-none'])]
    else:
        ids=ev.note_ids(notes)
        items=[item('Latest note: '+note_head(notes[0])+'.','record','rm',[ids[0]]),*note_items(notes[0],ids[0])]
        if len(notes)>1:items.append(item(f"{plural(len(notes)-1,'earlier note')} {'is' if len(notes)==2 else 'are'} recorded, the most recent on "
            f"{notes[1].get('met_on_text')}. Ask for the meeting history to see them.",'record','rm',ids[1:]))
    return {'heading':'History and commitments','items':items+withheld_gap(withheld)}


def open_section(data: dict) -> dict:
    rows=open_items(data)
    if not rows:return {'heading':'Open matters','items':[item('No open review, ticket, request, task or upcoming milestone is recorded.','record','rm',['profile'])]}
    rest=rows[SHOWN:]
    if rest:rows=rows[:SHOWN]+[item(f"{plural(len(rest),'more open item')} {'is' if len(rest)==1 else 'are'} not listed here; the client page shows them.",
        'record','rm',[i for r in rest for i in r['source_ids']])]
    return {'heading':'Open matters','items':rows}


def told_section(data: dict,bundle: ev.Bundle) -> dict:
    """Preferences, client chat insights (inferred ones as suggestions), the shared handover note, and one gap per
    consent category she has not shared."""
    items=[*preference_items(data),*topic_items(bundle),*shared_items(bundle),*insights_gap(bundle)]
    if not items:items=[item('Nothing the client stated is recorded.','record','rm',['preferences'])]
    return {'heading':'What the client has told us','items':items}


def gaps_section(data: dict,chosen: dict|None,upcoming: list[dict]) -> dict:
    items=gap_items(data,BRIEF_GAPS)
    if not chosen and len(upcoming)>1:items.append(gap(f"{plural(len(upcoming),'confirmed meeting')} are booked; say which one to prepare for.",['appointments']))
    return {'heading':'Gaps to check','items':items or [item('Nothing missing was found in the records read for this brief.','record','rm',['profile'])]}


def brief_sections(data: dict,notes: list[dict],withheld: int,bundle: ev.Bundle,chosen: dict|None,upcoming: list[dict]) -> list[dict]:
    """Every record section of a meeting brief, in BRIEF_ORDER, written by code."""
    return [meeting_section(chosen),who_section(data),history_section(notes,withheld),open_section(data),told_section(data,bundle),
        gaps_section(data,chosen,upcoming)]


# Client overview (RM2) --------------------------------------------------------------------------------------------------
def product_items(data: dict) -> list[dict]:
    rows=[]
    for a in data.get('accounts') or []:
        rows.append(item(f"{words(a.get('type')).capitalize()} account {a.get('number') or ''}".rstrip()+f": {money(a.get('balance'),a.get('currency'))}, {words(a.get('status'))}.",
            'record','rm',['accounts']))
    for c in data.get('cards') or []:
        due=f"; minimum payment {money(c['minimum_payment_due'],c.get('currency'))} due {day(c.get('payment_due_date'))}" if c.get('minimum_payment_due') else ''
        ending=f" ending {c['last4']}" if c.get('last4') else ''
        rows.append(item(f"{c.get('name') or 'Credit card'}{ending}: limit {money(c.get('credit_limit'),c.get('currency'))}, outstanding "
            f"{money(c.get('outstanding_balance'),c.get('currency'))}{due}, {words(c.get('status'))}.",'record','rm',['cards']))
    for loan in data.get('loans') or []:
        rows.append(item(f"{words(loan.get('type')).capitalize()} loan: outstanding {money(loan.get('outstanding_amount'),loan.get('currency'))}, monthly repayment "
            f"{money(loan.get('monthly_repayment'),loan.get('currency'))}"+(f", next due {day(loan['next_due_date'])}" if loan.get('next_due_date') else '')
            +f", {words(loan.get('status'))}.",'record','rm',['loans']))
    return rows+portfolio_items(data)


COVER_WORDS={'covered':'covered by the lending value of the collateral','below_limit':'above the lending value of the collateral'}


def portfolio_items(data: dict) -> list[dict]:
    """0116: the portfolio by asset class (values as recorded), then Lombard borrowing against the collateral's lending value."""
    rows,pf,fin=[],data.get('portfolio'),data.get('financing')
    if pf:
        parts=[f"{words(h.get('asset_class'))} {money(h.get('market_value'),h.get('currency'))}" for h in pf.get('holdings') or []]
        rows.append(item(f"{words(pf.get('mandate')).capitalize()} portfolio: {money(pf.get('total_value'),pf.get('currency'))}"
            +(f" as of {day(pf['as_of'])}" if pf.get('as_of') else '')+(f" ({joined(parts)})" if parts else '')+'.','record','rm',['portfolio']))
    if fin:
        status=fin.get('collateral_status')
        line=f"Lombard borrowing {money(fin.get('lombard_outstanding'),fin.get('currency'))} against a lending value of {money(fin.get('lending_value'),fin.get('currency'))}"
        line+=f": {COVER_WORDS.get(status,words(status))}"+(f", short by {money(fin.get('shortfall'),fin.get('currency'))}" if status=='below_limit' else '')+'.'
        rows.append(item(line,'record','rm',['financing']))
    return rows


def movement_items(bundle: ev.Bundle) -> list[dict]:
    """Large movements (10,000 or more, 180 days) as recorded: amounts only, never a reason. None when not shared."""
    cash=bundle.get('cash-events')
    if cash is None or cash.kind=='consent' or not isinstance(cash.data,list):return []
    rows=[]
    for r in cash.data:
        party=f" {'from' if r.get('direction')=='incoming' else 'to'} {r['counterparty']}" if r.get('counterparty') else ''
        notes=[x for x in ('recurring' if r.get('recurring') else '','overseas' if r.get('overseas') else '',
            words(r.get('status')) if r.get('status') not in (None,'posted','completed') else '') if x]
        rows.append(item(f"{r.get('date_text') or day(r.get('date'))}: {r.get('direction') or 'movement'} {money(r.get('amount'),r.get('currency'))}{party}"
            +(f" ({', '.join(notes)})" if notes else '')+'.','record','rm',['cash-events']))
    return counted(rows,'large movement',"ask about her movements to see them")


def insight_items(bundle: ev.Bundle) -> list[dict]:
    """Transaction insights as recorded (SQL computed them under her consent), or one gap when she has not shared them."""
    e=bundle.get('transaction-insights')
    if e is None:return []
    if e.kind=='consent':return insights_gap(bundle)
    d,ids=e.data if isinstance(e.data,dict) else {},['transaction-insights']
    rows=[item(e.title+'.','record','rm',ids)]
    for m in (d.get('monthly') or d.get('by_month') or [])[-TOP:]:
        cur=m.get('currency')
        rows.append(item(f"{month(m.get('month'))}: money in {money(m.get('money_in'),cur)}, money out {money(m.get('money_out'),cur)}"
            +(f" ({plural(m['count'],'transaction')})" if m.get('count') else '')+'.','record','rm',ids))
    for c in d.get('card_spending') or []:
        rows.append(item(f"Card spending: {money(c.get('amount'),c.get('currency'))}"+(f" ({plural(c['count'],'payment')})" if c.get('count') else '')+'.','record','rm',ids))
    overseas=d.get('overseas') or {}
    if isinstance(overseas,dict) and overseas.get('count'):
        rows.append(item(f"Overseas spending: {plural(overseas['count'],'payment')}"+(f", {joined([money(v,k) for k,v in (overseas.get('amounts') or {}).items()])}"
            if overseas.get('amounts') else '')+'.','record','rm',ids))
    elif isinstance(d.get('overseas_card_spend'),(int,float)):rows.append(item(f"Overseas card spending: {money(d['overseas_card_spend'],None)}.",'record','rm',ids))
    for r in (d.get('recurring') or [])[:TOP]:
        rows.append(item(f"Recurring {r.get('direction') or 'movement'}: {r.get('counterparty')}, {plural(r.get('count') or 0,'time')}, typically "
            f"{money(r.get('typical_amount'),r.get('currency'))}"+(f", last on {day(r['last_date'])}" if r.get('last_date') else '')+'.','record','rm',ids))
    return rows


def overview_sections(data: dict,bundle: ev.Bundle) -> list[dict]:
    """Every record section of a client overview (RM2), written by code; empty sections are dropped by in_order."""
    notes=data.get('interactions') or []
    recent=[item('Latest note: '+note_head(notes[0])+'.','record','rm',[ev.note_ids(notes)[0]])] if notes else []
    return [{'heading':'Snapshot','items':who_items(data,products=False)},
        {'heading':'Accounts and products','items':product_items(data)},
        {'heading':'Open requests','items':counted([*ticket_items(data),*request_items(data)],'open request','the client page shows them')},
        {'heading':'Upcoming','items':counted([*meeting_items(data),*review_items(data),*milestone_items(data),*task_items(data)],'item','the client page shows them')},
        {'heading':'Recent large movements','items':movement_items(bundle)},
        {'heading':'Transaction insights','items':insight_items(bundle)},
        {'heading':'Client chat insights','items':topic_items(bundle)},
        {'heading':'Preferences and recent interactions','items':[*preference_items(data),*recent]},
        {'heading':'Missing or unavailable','items':gap_items(data)}]


# Handover brief (RM16) --------------------------------------------------------------------------------------------------
def told_items(data: dict) -> list[dict]:
    """What the client was told, newest first (the bank, the chat assistant, relationship managers), with dates and who."""
    rows=[item(f"{t.get('at_sgt')}, {t.get('by')}"+(f" about {t['reference']}" if t.get('reference') else '')+f": {clip(t.get('text'),TEXT_CHARS)}",
        'record','rm',['told']) for t in reversed(data.get('told') or [])]
    return counted(rows,'earlier message',"the tickets show them")


def commitment_items(notes: list[dict]) -> list[dict]:
    ids=ev.note_ids(notes)
    rows=[item(f"{clip(c,TEXT_CHARS)} (recorded {n.get('met_on_text')} by {'you' if n.get('written_by_current_rm') else n.get('author')}; not necessarily completed).",
        'record','rm',[i]) for i,n in zip(ids,notes) for c in n.get('commitments') or []]
    return counted(rows,'commitment','the meeting history shows them')


def handover_sections(data: dict,bundle: ev.Bundle,shared: bool=True) -> list[dict]:
    """The seven record headings of a handover brief (handlers.HANDOVER_SECTIONS); an empty one becomes a gap later."""
    notes=data.get('notes') or []
    open_rows=[*ticket_items(data),*request_items(data),*task_items(data)]
    return [{'heading':'Client overview','items':who_items(data)},
        {'heading':'Open requests and tickets','items':counted(open_rows,'open item','the client page shows them')},
        {'heading':'Recorded commitments','items':commitment_items(notes) or [gap('No commitments are recorded in the notes you can read.',ev.note_ids(notes)[:1] or ['profile'])]},
        {'heading':'Pending reviews','items':[*review_items(data),*milestone_items(data)] or [gap(GAP_WORDS['no_open_compliance_review'],['compliance'])]},
        {'heading':'Recorded preferences','items':[*preference_items(data,SHOWN),*(shared_items(bundle) if shared else [])]},
        {'heading':'What the client was previously told','items':told_items(data) or [gap('Nothing the client was told is recorded.',['told'])]},
        {'heading':'Gaps to clarify','items':[*gap_items(data),*withheld_gap(data.get('withheld_private_notes') or 0)]}]


# Meeting history (RM3) --------------------------------------------------------------------------------------------------
def history_sections(notes: list[dict],withheld: int=0) -> list[dict]:
    """One section per note, newest first (the last HISTORY_SHOWN), then a line counting the older ones."""
    if not notes:return [{'heading':'Meeting notes','items':[gap('No meeting notes are recorded.',['notes-none']),*withheld_gap(withheld)]}]
    ids=ev.note_ids(notes)
    out=[{'heading':note_head(n),'items':note_items(n,i) or [gap('This note has no summary.',[i])]}
        for i,n in zip(ids[:HISTORY_SHOWN],notes[:HISTORY_SHOWN])]
    rest=ids[HISTORY_SHOWN:]
    older=[item(f"{plural(len(rest),'older note')} {'is' if len(rest)==1 else 'are'} recorded, the earliest on {notes[-1].get('met_on_text')}. "
        'Ask about a meeting by its date to see it.','record','rm',rest)] if rest else []
    if older or withheld:out.append({'heading':'Older and private notes','items':[*older,*withheld_gap(withheld)]})
    return out


# The model's facts ------------------------------------------------------------------------------------------------------
def trimmed(e: ev.Evidence,keep: set[str]) -> Any:
    """What the model sees of one record (None leaves it out)."""
    d=e.data
    if e.kind=='note':
        if e.id not in keep:return None  # older notes are counted in the record sections only
        return {'met_on':d.get('met_on_text'),'format':d.get('format'),'written_by':'you' if d.get('written_by_current_rm') else d.get('author'),
            'title':d.get('title'),'summary':clip(d.get('summary'),NOTE_CHARS),'commitments':d.get('commitments') or [],
            'client_requests':d.get('client_requests') or [],'decisions':d.get('decisions') or []}
    if e.id=='profile':
        rel=d.get('relationship') or {}
        return {'name':d.get('name'),'with_you_since':rel.get('assigned_since_text'),'inherited_from':rel.get('previous_advisor') if rel.get('inherited') else None,
            'employment_status':d.get('employment_status'),'preferred_language':d.get('preferred_language'),'today':d.get('today'),
            'missing_information':d.get('missing_information') or []}
    if e.kind=='ticket':
        if e.id not in keep:return None  # the record section lists the first SHOWN and counts the rest
        return {k:(clip(v,TEXT_CHARS) if k=='summary' and v else v) for k,v in d.items() if k in
            ('reference','category_label','status','rm_owned','owner_team','urgency','overdue','target_sgt','summary','client_safe_status')}
    if e.id=='accounts':return [{k:r.get(k) for k in ('type','currency','balance','status')} for r in d]
    if e.id=='cards':return [{k:r.get(k) for k in ('name','status','currency','credit_limit','outstanding_balance','payment_due_date')} for r in d]
    if e.id=='loans':return [{k:r.get(k) for k in ('type','currency','outstanding_amount','monthly_repayment','next_due_date','status')} for r in d]
    if e.id=='portfolio':return {k:d.get(k) for k in ('mandate','currency','total_value','as_of')}|{'asset_classes':[
        {'asset_class':h.get('asset_class'),'market_value':h.get('market_value')} for h in d.get('holdings') or []]}
    if e.id in ('appointments','free-slots'):return None  # the selected meeting is its own record
    if e.id=='cash-events' and isinstance(d,list):return lists(sorted(d,key=lambda r:-abs(float(r.get('amount') or 0))))
    if e.id=='told' and isinstance(d,list):return [{**t,'text':clip(t.get('text'),TEXT_CHARS)} for t in d[-TOP:]]
    if e.id=='shared-note':return {**d,'notes':[{**n,'content':clip(n.get('content'),400)} for n in (d.get('notes') or [])[:2]]}
    return lists(d)


def facts(bundle: ev.Bundle,notes: list[dict],keep_notes: int=1) -> ev.Bundle:
    """The model's bundle: every record bounded (trimmed), with the full bundle's ids, titles and as-of times. Notes:
    the newest keep_notes only; tickets: the first TOP in the record section's order (hers first), however many are open."""
    keep=set(ev.note_ids(notes)[:keep_notes])
    keep|={e.id for e in sorted((e for e in bundle.items if e.kind=='ticket'),key=lambda e:not e.data.get('rm_owned'))[:TOP]}
    out=ev.Bundle()
    for e in bundle.items:
        if (data:=trimmed(e,keep)) is not None:out.add(ev.Evidence(e.id,e.title,e.kind,data,e.as_of,e.customer_id))
    return out


# The model's part -------------------------------------------------------------------------------------------------------
def _ids(ids: list[str]) -> dict:return {'type':'array','items':{'type':'string',**({'enum':ids} if ids else {})}}


def points_schema(ids: list[str],keys: tuple=BRIEF_POINTS,title: str='rm_brief_points') -> dict:
    """Only what the model writes: a summary and, per heading, short points citing the facts' ids."""
    point={'type':'object','additionalProperties':False,'properties':{'text':{'type':'string'},'source_ids':_ids(ids)},'required':['text','source_ids']}
    props={'summary':{'type':'string'},**{key[0]:{'type':'array','items':point} for key in keys}}
    return {'title':title,'type':'object','additionalProperties':False,'properties':props,'required':list(props)}


def point_sections(data: dict,keys: tuple=BRIEF_POINTS) -> list[dict]:
    """The model's points under their headings (suggestions, or gaps where the key says so), at most `limit` each;
    checked afterwards."""
    out=[]
    for key,heading,limit,*kind in keys:
        rows=[p for p in data.get(key) or [] if isinstance(p,dict)][:limit]
        k=kind[0] if kind else 'suggestion'
        out.append({'heading':heading,'items':[{'text':str(p.get('text') or ''),'kind':k,'owner':'rm' if k=='suggestion' else 'none',
            'source_ids':list(p.get('source_ids') or [])} for p in rows]})
    return out


def in_order(sections: list[dict],order: list[str]) -> list[dict]:
    """Sections in the report's order, empty ones dropped, and a heading written by both code and the model kept once
    (code's items first)."""
    merged: dict[str,list[dict]]={}
    for s in sections:merged.setdefault(s['heading'],[]).extend(s['items'])
    place={h:n for n,h in enumerate(order)}
    return sorted([{'heading':h,'items':items} for h,items in merged.items() if items],key=lambda s:place.get(s['heading'],len(order)))
