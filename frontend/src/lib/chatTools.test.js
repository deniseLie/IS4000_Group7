import test from 'node:test';
import assert from 'node:assert/strict';
import {boundedHistory,redact,readEvents,supportSummary} from './chatTools.js';

test('history stays bounded at the 16th and 50th questions',()=>{
 for(const turns of [16,50]) {
   const messages=Array.from({length:turns*2-1},(_,i)=>({role:i%2?'assistant':'user',content:`message ${i}`}));
   const result=boundedHistory(messages);assert.equal(result.length,29);assert.equal(result.at(-1).content,messages.at(-1).content);
 }
});
test('filters failed responses and limits characters',()=>{
 const result=boundedHistory([{role:'assistant',content:'failure',error:true},...Array.from({length:20},()=>({role:'user',content:'x'.repeat(4000)}))]);
 assert.ok(result.reduce((n,m)=>n+m.content.length,0)<=16000);assert.ok(!result.some(m=>m.content==='failure'));
});
test('redacts secrets but not a security question',()=>{
 for(const input of ['PIN is 123456','password: DemoSecret!','CVV=123','4111 1111 1111 1111'])assert.ok(redact(input).includes('[private detail removed]'));
 assert.equal(redact('How do I reset my password?'),'How do I reset my password?');
});
test('parses every possible split in an SSE stream including CRLF and Unicode',async()=>{
 const data=new TextEncoder().encode('event: answer\r\ndata: {"answer":"你好"}\r\n\r\nevent: done\r\ndata: \r\n\r\n');
 for(let split=1;split<data.length;split++) {
   const events=[];const body=new ReadableStream({start(c){c.enqueue(data.slice(0,split));c.enqueue(data.slice(split));c.close();}});
   await readEvents(body,(...args)=>events.push(args));assert.equal(events[0][0],'answer');assert.equal(JSON.parse(events[0][1]).answer,'你好');
 }
});
test('interrupted stream fails rather than pretending completion',async()=>{
 const body=new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('event: status\ndata: wait\n\n'));c.close();}});
 await assert.rejects(()=>readEvents(body,()=>{}),/interrupted/);
});
test('support draft removes private details and does not claim a ticket was sent',()=>{
 const s=supportSummary([{role:'user',content:'My PIN is 1234'}]);assert.ok(!s.includes('1234'));assert.ok(s.includes('not sent'));
});
