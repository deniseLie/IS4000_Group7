"""Offline, synthetic client-intelligence regression checks (no live banking data).

Run: PYTHONPATH=backend python backend/scripts/check_client_intelligence.py
"""
from types import SimpleNamespace
from app.schemas.chat import Message
from app.services.agents.card_answers import respond
from app.services import knowledge,limit_context
from app.services.retrieval_context import retrieval_query

CARDS=[SimpleNamespace(name='Signature Card',last4='7310',status='active',currency='SGD',card_id='c1',network='visa'),
       SimpleNamespace(name='Travel Card',last4='5592',status='active',currency='SGD',card_id='c2',network='mastercard')]
RECORDS=SimpleNamespace(cards=CARDS,transactions=[])


def conversation(*queries):
    history=[];results=[]
    for query in queries:
        history.append(Message(role='user',content=query))
        answer=respond('t1',query,history,RECORDS)
        results.append(answer)
        history.append(Message(role='assistant',content=answer.text if answer else 'Unrelated'))
    return history,results


def run():
    count=0
    def verify(ok,label):
        nonlocal count
        assert ok,label
        count+=1
    h,a=conversation('How do I increase my card limit?', 'I only need it temporarily',
        "I am travelling to Japan in December", 'Please make the limit SGD 15,000',
        'Actually make that SGD 12,000 instead', 'travel', '2028')
    verify(a[-1] is not None and 'day and month' in a[-1].text,'A year alone cannot complete the date')
    verify('12,000' in a[-1].text and '15,000' not in a[-1].text,'Latest corrected request amount')
    verify('Travel Card ending 5592' in a[-1].text,'Short card selection has correct meaning')
    verify('travel as the reason' in a[-1].text,'Separate earlier travel reason retained')
    verify(limit_context.extract(h) is None,'Only user-turn active contexts are extracted')
    h,a=conversation('Increase my credit card limit to SGD 15000','temporary','Travel Card','2028','20 December')
    verify('20 December 2028' in a[-1].text,'Split year followed by day/month completes end date')
    verify('no limit has been changed' in a[-1].text,'No bank approval or limit change invented')
    h,a=conversation('I want to increase my card limit to SGD 12000', 'temporary', 'Travel Card')
    verify('travel as the reason' not in a[-1].text,'Card selection not confused with reason')
    h,a=conversation('I want to increase my card limit to SGD 12000','temporary', 'Signature Card', '2028')
    verify('Signature Card' in a[-1].text and 'day and month' in a[-1].text,'Year cannot override card selection')
    h,a=conversation('I want to increase my card limit to SGD 12000','temporary','Travel Card', 'Show my account balance','2028')
    verify(a[-1] is None or 'day and month' not in a[-1].text,'Unrelated topic closes intake')
    cases=[('wats the monthly fee for a savings acc?','savings-fees'),
           ('i got locked out of the app on my phone','app-login-recovery'),
           ('can i get a car loan','car-loans'),
           ('theres a $2400 charge i dont recognise','unrecognised-charge'),
           ('How do I get my statement?','statements')]
    for query,expected in cases:
        verify(bool(knowledge.search(query)) and knowledge.search(query)[0].id==expected,
               'Approved source search: '+expected)
    verify(bool(knowledge.search('credit card minimum payment interest')) and
        'sg-card-payments' in [article.id for article in knowledge.search('credit card minimum payment interest')],
        'Approved educational credit-card source retrieved')
    hist=[Message(role='user',content='How do I get a monthly statement?'),
          Message(role='assistant',content='Where do I find statements?'),
          Message(role='user',content='And for September?')]
    verify('statement' in retrieval_query(hist[-1].content,hist).lower(),'Recent follow-up expands retrieval terms')
    verify(retrieval_query('Show my recent card transactions',hist)=='Show my recent card transactions',
           'New topic does not reuse earlier query')
    print(f'PASS: {count} focused offline client-intelligence checks (synthetic data)')

if __name__=='__main__':run()
