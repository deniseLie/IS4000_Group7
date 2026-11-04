"""Read-only RM evaluation target: real JWT, /rm/assistant, tools and model; never approves a write."""
from collections import Counter
from typing import Literal
from fastapi import FastAPI
import httpx
from pydantic import BaseModel,JsonValue,Field
from app.core.observability import span_sink
from app.schemas.rm import RmAssistantRequest,RmContext,RmTurn
from evaluation.targets import sse,turns_of


class Item(BaseModel):
    text: str


class Section(BaseModel):
    heading: str
    items: list[Item]


class Source(BaseModel):
    id: str
    title: str
    record: JsonValue


class Draft(BaseModel):
    text: str
    cautions: list[str]


class Reply(BaseModel):
    status: Literal['ok','clarify','denied','unverified']
    summary: str
    origin: str
    sections: list[Section]
    sources: list[Source]
    context: dict[str,JsonValue]
    draft: Draft | None=None


class Ticket(BaseModel):
    reference: str
    customer_id: int
    category: str
    status: str
    rm_owned: bool
    age_hours: float=Field(ge=0)


class TicketGroups(BaseModel):
    rm_owned: list[Ticket]
    specialist: list[Ticket]


class TicketEnvelope(BaseModel):
    data: TicketGroups


class BookClient(BaseModel):
    customer_id: int
    name: str


class BookEnvelope(BaseModel):
    data: list[BookClient]


def ticket_rows(payload: str) -> list[Ticket]:
    groups=TicketEnvelope.model_validate_json(payload).data
    return [*groups.rm_owned,*groups.specialist]


async def play(inputs: dict[str,JsonValue],app: FastAPI,token: str) -> dict[str,JsonValue]:
    """Follow real returned context; unsupported UI steps remain unscored and are never executed."""
    headers={'Authorization':'Bearer '+token}
    history: list[RmTurn]=[]
    context=RmContext.model_validate(inputs.get('context') or {})
    spans: list[tuple[str,dict[str,JsonValue]]]=[]
    replies: list[dict[str,JsonValue]]=[]
    skipped: list[dict[str,JsonValue]]=[]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://evaluation',timeout=300) as http:
        book=await http.get('/rm/book',headers=headers);book.raise_for_status()
        authorised=BookEnvelope.model_validate_json(book.text)
        before=await http.get('/rm/tickets',headers=headers);before.raise_for_status()
        old=ticket_rows(before.text)
        if 'ticket_selection' in inputs:
            if inputs['ticket_selection']!='newest_rm_owned':raise ValueError('Unsupported RM evaluation ticket selection')
            eligible=[t for t in old if t.rm_owned and t.customer_id==context.client_id]
            if not eligible:raise ValueError('RM evaluation requires an owned ticket for the selected client')
            context=context.model_copy(update={'ticket_ref':min(eligible,key=lambda t:(t.age_hours,t.reference)).reference})
        for step in turns_of(inputs):
            if 'say' not in step:
                skipped.append(step)
                continue
            request=RmAssistantRequest.model_validate({'message':step['say'],'history':[h.model_dump(mode='json') for h in history],
                'context':context.model_dump(mode='json'),'intent':inputs.get('intent') if not replies else None})
            start=len(spans);capture=span_sink.set(spans)
            try:response=await http.post('/rm/assistant',headers=headers,json=request.model_dump(mode='json'))
            finally:span_sink.reset(capture)
            row: dict[str,JsonValue]={'say':request.message,'status':response.status_code,'sources':[]}
            if response.status_code!=200:
                row['error']=response.text[:300]
            else:
                for event,data in sse(response.text):
                    if event=='error':row['error']=data
                    elif event=='answer':
                        reply=Reply.model_validate_json(data)
                        text=[reply.summary,*[s.heading+'\n'+'\n'.join(i.text for i in s.items) for s in reply.sections]]
                        if reply.draft:text+=['Draft (not sent): '+reply.draft.text,*reply.draft.cautions]
                        row.update(answer='\n'.join(text),origin=reply.origin,needs_human=False,
                            sources=[s.id for s in reply.sources],evidence=[s.model_dump(mode='json') for s in reply.sources],response_status=reply.status)
                        context=RmContext.model_validate({k:v for k,v in reply.context.items() if k in RmContext.model_fields})
            row['model_calls']=sum(n=='llm' for n,_ in spans[start:])
            row['agents']=[a['agent'] for n,a in spans[start:] if n=='llm' and a.get('agent')=='rm_assistant']
            replies.append(row)
            if 'error' in row:break
            history.extend([RmTurn(role='user',content=request.message[:6000],client_id=request.context.client_id),
                RmTurn(role='assistant',content=str(row.get('answer',''))[:6000],client_id=context.client_id)])
        after=await http.get('/rm/tickets',headers=headers);after.raise_for_status()
    new=ticket_rows(after.text)
    known={t.reference for t in old}
    return {'answer':'\n\n'.join(str(r.get('answer','')) for r in replies),'replies':replies,'routes':[None for _ in replies],
        'skipped':skipped,'not_offered':[],'played':bool(replies),'nodes':{'ran':[],'acted':[]},
        'agents':dict(Counter(a['agent'] for n,a in spans if n=='llm' and a.get('agent')=='rm_assistant')),
        'tools':sorted({str(a['tool']) for n,a in spans if n=='tool_call'}),'model_calls':sum(n=='llm' for n,_ in spans),
        'tickets':{'opened':[t.model_dump(mode='json') for t in new if t.reference not in known],
            'all':[t.model_dump(mode='json') for t in new]},'pipeline':'rm',
        'authorised_book':[client.model_dump(mode='json') for client in authorised.data]}
