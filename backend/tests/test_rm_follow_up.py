"""Follow-up questions about the last answer, and plain chat replies (rm_assistant v7)."""
import asyncio
from types import SimpleNamespace

from app.schemas.rm import RmAssistantRequest
from app.services.rm import graph, handlers
from app.services.rm import evidence as ev

LAST='Not known yet: 10 lower-ranked items are not listed here: 6 client requests, 2 confirmed meetings, 1 task and 1 milestone.'


def rt(message_history=True,named=(),outside=False):
    history=[{'role':'user','content':'What is on today?'},{'role':'assistant','content':LAST}] if message_history else []
    return SimpleNamespace(req=RmAssistantRequest(message='x',history=history),named=list(named),outside=outside)


def test_questions_about_the_last_answer_are_follow_ups():
    for text in ['1 not known is what','what does that gap mean?','why is that first','Explain point 2','what do you mean by this']:
        assert graph.rule_plan(rt(),text)==[{'intent':'follow_up'}],text


def test_new_requests_and_first_messages_are_not_follow_ups():
    assert not graph.is_follow_up(rt(message_history=False),'what does that mean')
    assert not graph.is_follow_up(rt(named=[{'name':'Michelle Ng'}]),'what about this for Michelle?')
    for text in ['draft a reply for that','brief me on this meeting','show me those tickets','summarise this client']:
        assert not graph.is_follow_up(rt(),text),text


class FakeTurn:
    def __init__(self,history,reply):
        self.req=RmAssistantRequest(message='1 not known is what',history=history)
        self.reply,self.calls=reply,[]
    async def say(self,_):pass
    def context(self,client_id):return {'client_id':client_id}
    async def model(self,intent,bundle,schema,client_id,**_):
        self.calls.append((intent,bundle.ids()));return self.reply


def test_follow_up_is_explained_from_the_last_answer_only():
    turn=FakeTurn([{'role':'user','content':'today'},{'role':'assistant','content':LAST}],
        {'status':'ok','summary':'It means 10 lower-ranked items are not listed: 6 client requests, 2 confirmed meetings, 1 task and 1 milestone.','sections':[]})
    reply=asyncio.run(handlers.h_follow_up(turn,None,None))
    assert turn.calls==[('follow_up',['previous-reply'])]
    assert reply['intent']=='follow_up' and reply['status']=='ok' and reply['sections']==[] and '10 lower-ranked' in reply['summary']


def test_a_follow_up_figure_not_in_the_last_answer_is_withheld():
    turn=FakeTurn([{'role':'assistant','content':LAST}],{'status':'ok','summary':'There are 37 overdue items worth SGD 9,999.','sections':[]})
    reply=asyncio.run(handlers.h_follow_up(turn,None,None))
    assert '37' not in reply['summary'] and reply['validation']['withheld']


def test_no_earlier_answer_needs_no_model_call():
    turn=FakeTurn([],{})
    reply=asyncio.run(handlers.h_follow_up(turn,None,None))
    assert turn.calls==[] and 'earlier answer' in reply['summary']


def test_help_is_a_plain_reply_not_couldnt_verify():
    reply=handlers.chat_reply('help',{'status':'ok','summary':'I can help with your clients, requests and meetings.','sections':[]},ev.Bundle(),{})
    assert reply['status']=='ok' and reply['summary'].startswith('I can help') and reply['sections']==[]
