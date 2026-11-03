"""Offline contract tests for client v6. No LLM, tokens, database or credentials.
Run from backend: PYTHONPATH=. python scripts/check_client_v6.py
Live evaluation remains necessary; these are deterministic regression checks only.
"""
from types import SimpleNamespace
from app.schemas.agents import PlannedIssue
from app.schemas.chat import Message
from app.schemas.customer_data import BankAccount
from app.services import client_intents,limit_context
from app.services.agents import card_answers,account_answers

count=0

def check(condition,label):
    global count
    if not condition: raise AssertionError(label)
    count+=1

change=['i wan my credit limit increased','I want my credit limit increased','Please raise my credit limit','Can you increase my card limit?']
for question in change:
    h=[Message(role='user',content=question)]
    check(limit_context.active(h),f'Card change recognised: {question}')

concept=['What does a temporary credit limit increase mean?','wats the difference between card limit and account balance?',
    'What is the difference between my credit card limit and my account balance?', 'Explain a credit limit increase']
for question in concept:
    h=[Message(role='user',content=question)]
    check(client_intents.explanation(question),f'Concept detected {question}')
    check(not limit_context.active(h),f'Concept never starts action {question}')

ask='What is my balance, and how do I get my September statement?'
check(client_intents.account_balance(ask),'Authenticated bank account balance intent recognised')
check(not client_intents.explanation(ask),'Balance enquiry is not a definition')
check(not client_intents.explanation('What is my account balance?'),'Personal balance enquiry is not a definition')
check(not client_intents.account_balance('What is the difference between card limit and account balance?'),'Comparison is not personal read')

def plan(text,items):return client_intents.reconcile(text,[PlannedIssue(**item) for item in items])
rows=plan(ask,[{'domain':'retail','question':'What is my balance?'},{'domain':'document','question':'How do I get my September statement?'}])
check(len(rows)==1 and rows[0].domain=='retail' and rows[0].question==ask,'Balance+statement remains one retail issue')
rows=plan('How can I download my September statement?',[{'domain':'document','question':'Inspect my uploaded compliance file'}])
check(len(rows)==1 and rows[0].domain=='retail' and rows[0].question=='How can I download my September statement?','No invented doc inspection')
rows=plan('What does a temporary credit limit increase mean?',[{'domain':'retail','question':'Pick a card'},{'domain':'retail','question':'Request an increase'}])
check(len(rows)==1 and rows[0].question=='What does a temporary credit limit increase mean?','No planned actions for explanation')
rows=plan('Can I upload my proof of address?',[{'domain':'document','question':'Can I upload proof of address?'}])
check(rows[0].domain=='document','Actual upload remains document')
rows=plan('Can you arrange a meeting with my RM?', [{'domain':'relationship','question':'Book a meeting'}])
check(rows[0].domain=='relationship','Explicit RM request retained')

cards=[SimpleNamespace(name='Signature Card',network='visa',last4='7310',status='active'),
    SimpleNamespace(name='Travel Card',network='mastercard',last4='5592',status='active')]
records=SimpleNamespace(cards=cards,transactions=[])
for question in change:
    result=card_answers.respond('t1',question,[Message(role='user',content=question)],records)
    check(result is not None and result.status=='needs_clarification' and 'new credit limit' in result.text.lower(),f'Change asks amount, not recorded figure: {question}')
for question in concept:
    result=card_answers.respond('t1',question,[Message(role='user',content=question)],records)
    check(result is None,f'Explanation left to RAG answer, not card selector: {question}')

accounts=[BankAccount(account_id='acc_1',type='multi_currency',number='****7734',currency='SGD',balance=250000.0,status='active')]
records=SimpleNamespace(accounts=accounts)
evidence=[SimpleNamespace(id='statements',source=SimpleNamespace(title='Getting a statement'),text='Open Overview. Scroll to Statements. Choose the account or card and the month. Select View statement, Print or save as PDF.')]
response=account_answers.respond('t1',ask,records,evidence)
check(response is not None and 'SGD 250,000.00' in response.text,'Account balance copied directly from authorised snapshot')
check('Overview → Statements' in response.text and not 'RM' in response.text,'Statement guide joined without RM escalation')
check({c.source_id for c in response.citations}=={'retail-accounts','statements'},'Figures and guidance sourced independently')
response=account_answers.respond('t1',ask,records,[])
check('cannot verify statement navigation' in response.text,'No fabricated statement instructions without evidence')
check('retail-accounts' in {c.source_id for c in response.citations},'Balance source retained if no KB')
response=account_answers.respond('t1','What is the difference between card limit and account balance?',records,evidence)
check(response is None,'Conceptual comparison never discloses personal account figures')
print(f'PASS: {count} focused offline contract assertions')
