"""Record-backed personal account balances and mixed balance/statement enquiries.

No LLM calculations, hypothetical debits, RM escalation or statement downloads.
All figures come from the caller-authorised account snapshot; directions only
come from a retrieved approved article. Does not run for conceptual comparisons.
"""
from app.schemas.contracts import AgentResult,Citation
from app.services.client_intents import account_balance,STATEMENT


def respond(task_id,question,records,public_evidence):
    if not account_balance(question):return None
    accounts=[a for a in records.accounts if a.status!='closed']
    if not accounts:
        text="I can't see a recorded active account balance for your profile. You can check Overview → Accounts."
        citations=[]
        kind='conversation'
    else:
        rows=[]
        for acc in accounts:
            name=acc.type.replace('_','-').title()
            rows.append(f'{name} account ending {acc.number[-4:]}: {acc.currency} {acc.balance:,.2f}')
        text=('Your recorded account balance is: ' if len(rows)==1 else 'Your recorded account balances are: ')+ '; '.join(rows)+'.'
        text+=' These are recorded figures, not a prediction of pending transaction outcomes.'
        citations=[Citation(source_id='retail-accounts')]
        kind='banking'
    if STATEMENT.search(question):
        # The instructions are cited only when an actually retrieved, approved
        # source supports them. Database mode does not silently use memory copy.
        approved=next((item for item in public_evidence if
            ('statement' in item.source.title.lower() or 'statement' in item.id.lower())
            and 'Overview' in item.text and 'Statements' in item.text),None)
        if approved is None:
            text+=' I cannot verify statement navigation instructions from the available approved information right now.'
        else:
            text+=' For your statement, open Overview → Statements, select the account or card and the month, then select View statement. You can print or save it as a PDF from there.'
            citations.append(Citation(source_id=approved.id))
    return AgentResult(task_id=task_id,agent='retail',status='ok',answer_kind=kind,text=text,citations=citations)
