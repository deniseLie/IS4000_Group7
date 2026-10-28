import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {boundedHistory,handoffSummary,recallStep,redact,readEvents,sentHistory,supportSummary} from './chatTools.js';

for(const c of JSON.parse(fs.readFileSync(new URL('../../../tests/fixtures/privacy-cases.json',import.meta.url),'utf8'))) {
 test(`shared privacy case: ${c.text}`,()=>{
   const cleaned=redact(c.text);
   if(c.secret){assert.ok(!cleaned.includes(c.secret));assert.ok(cleaned.includes('[private detail removed]'));for(const part of c.secret.split(/[\s-]+/).filter(p=>p.length>=3))assert.ok(!cleaned.includes(part));}
   else assert.equal(cleaned,c.text);
 });
}

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

test('handoff summary uses topic labels and recorded outcomes only, never customer text',()=>{
 const typed='theres a $2400 charge i dont recognise on card 4821, my OTP is 123456';
 const messages=[{role:'user',content:typed},{role:'assistant',content:'x',sections:[
  {task_id:'t1',domain:'fraud',priority:'P0',answer:'Card ending 4821 charge.',response_origin:'ai',decision:'Your card ending 4821 is temporarily blocked. More text.'},
  {task_id:'t2',domain:'retail',priority:'P3',answer:'Transfer 1,800.00 pending.',response_origin:'ai'},
  {task_id:'t3',domain:'retail',priority:'P3',answer:'Withheld.',response_origin:'validation'}]},
  {role:'user',content:'whats my balance'},{role:'assistant',content:'error',error:true}];
 const s=handoffSummary(messages);
 assert.equal(s,'Customer asked for a person. Topics: Fraud & security (urgent), Complex banking needs ×2. Card block: confirmed. Answers withheld: 1.');
 for(const word of ['4821','2400','1,800','123456','landlord','recognise','balance'])assert.ok(!s.includes(word),word);
 assert.ok(s.length<=1000);
 assert.equal(handoffSummary([{role:'user',content:'hi'}]),'Customer asked for a person. Topics: General questions.');
 assert.ok(handoffSummary([{role:'assistant',content:'x',sections:[{task_id:'t1',domain:'fraud',priority:'P0',answer:'a',response_origin:'ai',decision:'You chose not to block the card ending 4821; it stays active.'}]}]).includes('Card block: declined.'));
 const urgent=decision=>handoffSummary([{role:'assistant',content:'x',sections:[{task_id:'t1',domain:'fraud',priority:'P0',answer:'a',response_origin:'ai',decision}]}]);
 const sent='Your dispute draft for 3 charges on the card ending 4821 is recorded for bank staff review. Staff decide any refund or cancellation.';
 assert.equal(urgent('You chose not to block the card ending 4821; it stays active. '+sent),'Customer asked for a person. Topics: Fraud & security (urgent). Card block: declined. Dispute draft: sent to staff.');
 assert.ok(urgent('Your card ending 4821 is temporarily blocked. You chose not to send the dispute draft.').endsWith('Card block: confirmed. Dispute draft: declined.'));
 assert.ok(!urgent(sent).includes('4821'));
});
test('sent questions are recalled newest first without back-to-back repeats',()=>{
 const messages=[{role:'user',content:'a'},{role:'assistant',content:'x'},{role:'user',content:'b'},{role:'user',content:'b'},{role:'assistant',content:''},{role:'user',content:'a'}];
 assert.deepEqual(sentHistory(messages),['a','b','a']);
 assert.deepEqual(sentHistory([]),[]);
});
test('recall steps stop at the oldest question and return to the empty box',()=>{
 assert.equal(recallStep(0,null,true),null);
 assert.equal(recallStep(2,null,true),0);assert.equal(recallStep(2,0,true),1);assert.equal(recallStep(2,1,true),1);
 assert.equal(recallStep(2,1,false),0);assert.equal(recallStep(2,0,false),null);assert.equal(recallStep(2,null,false),null);
});
