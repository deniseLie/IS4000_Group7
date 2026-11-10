"""The schema the Minutes Writer sends the model (no `pattern`, which OpenRouter's providers reject as "Schema is too complex")
and the full Pydantic check that still runs on every reply. The real provider call is opt-in (TEST_MEETING_WRITER_LIVE=1)."""
import asyncio,json,os
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.schemas.meeting_chunks import MinutesSelection
from app.schemas.meeting_lifecycle import Cue,GenerationContext,MinutesProposal
from app.services.meetings.writer import model_schema,propose,repair_actions

NOTES=(('rm','You mentioned buying a condo next year. Is it a resale or new launch?'),
    ('client','Resale, within the next three months if the price is right.'),
    ('client',"I'd rather not sell my investments to fund the down payment."),
    ('rm','We can look at using the time deposit maturing on 11 Nov and compare home loan options.'),
    ('client','Please send me the home loan lock-in terms by Friday.'),
    ('rm',"I will send the lock-in terms and a short summary of today's call."))


def keywords(node,name):
    if isinstance(node,dict):return sum((k==name and not isinstance(v,dict))+keywords(v,name) for k,v in node.items())
    if isinstance(node,list):return sum(keywords(v,name) for v in node)
    return 0


@pytest.mark.parametrize('model',[MinutesProposal,MinutesSelection])
def test_the_sent_schema_drops_only_the_pattern_keywords(model):
    full=model.model_json_schema();sent=model_schema(full)
    assert keywords(full,'pattern')>0 and keywords(sent,'pattern')==0
    for name in ('maxLength','minLength','maxItems','minItems','anyOf','required','enum','const','additionalProperties','format'):
        assert keywords(sent,name)==keywords(full,name),name
    assert json.dumps(full)!=json.dumps(sent) and keywords(full,'pattern')==json.dumps(full).count('"pattern"')


def test_a_property_named_pattern_is_kept():
    assert model_schema({'properties':{'pattern':{'type':'string','pattern':'\\S'}},'required':['pattern']})=={'properties':{'pattern':{'type':'string'}},'required':['pattern']}


@pytest.mark.parametrize('quote,expected',[
    ('I will send the checklist on Thursday, 19 November 2026.','2026-11-19'),
    ('I will send the checklist by 2026-11-19.','2026-11-19'),
    ('I will send the checklist by next Thursday.',None),
    ('I can send it on 19 November 2026 or on 20 November 2026.',None),
])
def test_repair_keeps_only_one_explicit_date_from_the_action_quote(quote,expected):
    cue=Cue(cue_id='n1',speaker='rm',start_seconds=None,text=quote)
    proposal=MinutesProposal.model_validate({'title':'Review','summary':[{'text':'Checklist discussed','quote':quote,'cue_id':'n1'}],
        'decisions':[],'actions':[{'text':'Send the checklist','quote':quote,'cue_id':'n1','owner':'rm',
            'commitment':'agreed','condition':None,'due_on':None,'due_quote':None}]})
    action=repair_actions(proposal,(cue,)).actions[0]
    assert (action.due_on.isoformat() if action.due_on else None)==expected
    assert action.due_quote in quote if expected else action.due_quote is None


@pytest.mark.parametrize('field',['title','text','quote'])
def test_a_blank_reply_still_fails_the_full_check(field):
    reply={'title':'Property financing','summary':[{'text':'She plans a resale purchase.','cue_id':'n2','quote':'Resale, within the next three months'}],'decisions':[],'actions':[]}
    if field=='title':reply['title']='   '
    else:reply['summary'][0][field]='   '
    with pytest.raises(ValidationError):MinutesProposal.model_validate(reply)


@pytest.mark.skipif(os.getenv('TEST_MEETING_WRITER_LIVE')!='1',reason='Real provider minutes call is opt-in (TEST_MEETING_WRITER_LIVE=1, LLM_* settings)')
def test_the_real_provider_accepts_the_sent_schema_and_the_reply_validates():
    context=GenerationContext(meeting_id=uuid4(),revision=1,consent_epoch=1,source_id=uuid4(),source_hash='0'*64,source_kind='rm_notes',
        cues=tuple(Cue(cue_id=f'n{index}',speaker=speaker,start_seconds=None,text=text) for index,(speaker,text) in enumerate(NOTES,1)))
    piece=asyncio.run(propose(context))
    assert piece.content.summary and {item.cue_id for item in piece.content.summary}<={cue.cue_id for cue in context.cues}
