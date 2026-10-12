"""Opt-in live evaluation. Uses synthetic questions; requires human answer review."""
import asyncio
import json
import time
from pathlib import Path
from app.schemas.chat import ChatRequest
from app.services.answers import make_answer

async def main():
    cases=json.loads(Path(__file__).with_name('cases.json').read_text())
    results=[]
    for case in cases:
        started=time.monotonic()
        messages=case.get('history',[])+[{'role':'user','content':case['question']}]
        try:
            answer=await make_answer(ChatRequest(messages=messages,language=case['language']))
            results.append({'id':case['id'],'review_check':case['check'],'seconds':round(time.monotonic()-started,2),**answer})
            print(case['id'],': response recorded; review required')
        except Exception as exc:
            results.append({'id':case['id'],'error_type':type(exc).__name__})
            print(case['id'],': request failed')
        Path('evaluation-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))

if __name__=='__main__':asyncio.run(main())
