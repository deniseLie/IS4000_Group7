"""Offline client-intake regression checks. Run: PYTHONPATH=backend python backend/scripts/check_client_limit_flow.py

No LLM provider, credentials or live banking database is used. All card records
below are clearly synthetic. The separate integrated test suite is still needed.
"""
from types import SimpleNamespace
from app.schemas.chat import Message
from app.services import limit_context
from app.services.agents import card_answers
from app.services.source_checks import check_sources

CARDS=[SimpleNamespace(name='Signature Card',last4='7310',status='active',currency='SGD',card_id='c1',network='visa'),
       SimpleNamespace(name='Travel Card',last4='5592',status='active',currency='SGD',card_id='c2',network='mastercard')]
RECORDS=SimpleNamespace(cards=CARDS,transactions=[])

def conversation(*queries):
    history=[];responses=[]
    for query in queries:
        history.append(Message(role='user',content=query))
        response=card_answers.respond('t1',query,history,RECORDS)
        responses.append(response)
        history.append(Message(role='assistant',content=response.text if response else 'Unrelated response'))
    return history,responses

def check():
    count=0
    def verify(condition,label):
        nonlocal count
        assert condition,label
        count+=1
    history,answers=conversation('How can I increase my credit card limit?',
        'What if I only need it temporarily?', "I'm travelling to Japan in December.",
        'Can you help me request $15,000?', 'Actually, make that $12,000 instead.',
        'travel','until 20 December 2026')
    verify(all(row is not None and row.status!='error' for row in answers),'No withheld answers in limit flow')
    verify('temporary' in answers[-1].text,'Temporary request retained')
    verify('SGD 12,000' in answers[-1].text and 'SGD 15,000' not in answers[-1].text,'Latest correction retained')
    verify('Travel Card ending 5592' in answers[-1].text,'Travel Card selected')
    verify('20 December 2026' in answers[-1].text,'End date retained')
    verify('no limit has been changed' in answers[-1].text,'No unsupported approval or modification')
    verify('Which card is this for' in answers[-2].text or 'What date should' in answers[-2].text,'Card selection advances flow')
    verify('Which card is this for' in answers[-3].text,'Travel context did not silently select Travel Card')
    _,answers=conversation('I want to increase my card limit to SGD 15,000','permanent','signature')
    verify('SGD 15,000' in answers[-1].text and 'Signature Card' in answers[-1].text,'Permanent request')
    _,answers=conversation('I want to increase my card limit to SGD 15,000','temporary','Travel Card','until 20 December')
    verify('Which year' in answers[-1].text,'Ambiguous year needs clarification')
    h=[Message(role='user',content='I want to increase my card limit'),Message(role='assistant',content='Okay.'),
       Message(role='user',content='What is my current credit limit?')]
    verify(not limit_context.active(h),'Current recorded limit is not a limit-change request')
    h[-1]=Message(role='user',content='Show my recent transactions')
    verify(not limit_context.active(h),'Unrelated topic does not use card-limit context')
    h.append(Message(role='assistant',content='Recent transactions'))
    h.append(Message(role='user',content='travel'))
    verify(not limit_context.active(h),'Context does not revive after topic switch')
    _,answers=conversation('Increase my credit card limit to SGD 18,000','temporary','Travel Card','20 Dec','2026')
    verify(answers[-1] is not None and 'until 20 Dec 2026' in answers[-1].text,'Split date/year accepted')
    verify('travel as the reason' not in answers[-1].text,'Card name alone is not a travel reason')
    _,answers=conversation('Can I increase my card limit?')
    verify('Raise a card limit request' in answers[0].text and 'approval isn’t guaranteed' in answers[0].text,'General guidance retained')
    bank_evidence=[{'id':'cards','text':'The approved recorded card limit is SGD 10,000.'}]
    verify(check_sources('The approved recorded limit is SGD 11,000.',['cards'],bank_evidence)=='unsupported_figure',
        'Unverified bank figures still blocked')
    verify(check_sources('The approved recorded limit is SGD 10,000.',['cards'],bank_evidence) is None,
        'Supported bank figures still allowed')
    print(f'PASS: {count} client card-limit regression checks (offline synthetic records)')

if __name__=='__main__':check()
