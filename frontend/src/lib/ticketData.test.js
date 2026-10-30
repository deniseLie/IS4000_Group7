import test from 'node:test';
import assert from 'node:assert/strict';
import {nextStep,parseCategories,parseTicketResult,parseTicketTarget,parseTickets,requestFailed,ticketIntent,ticketResultMatches,ticketSummary,ticketTopic} from './ticketData.js';

const TICKET={ticket_id:'3c063770-db29-48f4-bbfd-4c45c7e1093a',reference:'TCK-001001',category:'statement_request',category_label:'Statement or document copy',
  summary:'Copy of the June statement.',details:{period:'June 2026'},urgency:'normal',status:'in_review',owner_team:'Operations team',
  decided_by:'Operations prepares the document.',source:'seed',created_at:'2026-10-27T09:00:00+08:00',updated_at:'2026-10-27T10:00:00+08:00',
  target_at:'2026-10-29T09:00:00+08:00',overdue:false,last_update:{at:'2026-10-27T10:00:00+08:00',status:'in_review',note:'The operations team is reviewing it.'}};

test('tickets parse with their plain-language next step and reject tampered data',()=>{
  const [ticket]=parseTickets({data:[TICKET]});
  assert.equal(ticket.reference,'TCK-001001');
  assert.match(nextStep(ticket),/No action needed from you\. The operations team will review it/);
  assert.match(nextStep({...ticket,status:'waiting_on_client',last_update:{...ticket.last_update,note:'Please upload proof of address.'}}),/proof of address/);
  assert.match(nextStep({...ticket,overdue:true}),/past the operations team's target time/);
  assert.throws(()=>parseTickets({data:[{...TICKET,status:'approved'}]}));
  assert.throws(()=>parseTickets({data:[TICKET,TICKET]}));
  assert.throws(()=>parseTickets({data:[{...TICKET,reference:'X-1'}]}));
});

test('a ticket action target and receipt must agree on the category and team',()=>{
  const target=parseTicketTarget({category:'card_limit_change',summary:'Trip',urgency:'normal',details:{card:'7310'},category_label:'Card limit change',owner_team:'Cards and credit team',decided_by:'The team decides.',target_hours:72});
  const result=parseTicketResult({ticket_id:TICKET.ticket_id,reference:'TCK-001002',category:'card_limit_change',status:'submitted',owner_team:'Cards and credit team',target_at:TICKET.target_at,created:true});
  assert.ok(ticketResultMatches(target,result));
  assert.ok(!ticketResultMatches(target,{...result,category:'general_support'}));
  assert.throws(()=>parseTicketTarget({...target,urgency:'urgent'}));
});

test('categories keep their fields and chat questions map to a category',()=>{
  const [category]=parseCategories([{category:'card_limit_change',label:'Card limit change',description:'Ask for a new limit.',owner_team:'Cards and credit team',
    decided_by:'The team decides.',target_hours:72,documents:['Proof of income'],customer_raisable:true,fields:[{key:'card',label:'Card',kind:'card',required:true}]}]);
  assert.equal(category.fields[0].kind,'card');
  assert.throws(()=>parseCategories([{...category,fields:[{key:'card',label:'Card',kind:'html',required:true}]}]));
  assert.equal(ticketIntent('Can you increase my credit card limit to 30k for my trip?'),'card_limit_change');
  assert.equal(ticketIntent('what is the status of my request?'),'status');
  assert.equal(ticketIntent('Does paying only the minimum avoid interest?'),null);
  assert.equal(ticketIntent('How do I get my statement for September?'),'statements');
  assert.equal(ticketIntent('I need a certified statement for my visa application'),'statement_request');
  assert.equal(ticketIntent('Can I get my statements from 2024?'),'statement_request');
  assert.equal(ticketIntent('Which bank statements do I upload for my review?'),null);
});

test('handover tickets show the client her question, the topic and attachments (0082)', () => {
  const summary='[Fees or rates] Client question for you: Can you waive the annual fee? | Earlier in this chat: “my card was charged”';
  assert.equal(ticketSummary({summary}),'Can you waive the annual fee?');
  assert.equal(ticketTopic({category:'rm_handover',summary}),'Fees or rates');
  assert.equal(ticketTopic({category:'statement_request',summary}),null);
  assert.equal(ticketSummary({summary:'Client question for you: older ticket'}),'older ticket');
});

test('a failed request says nothing was sent and offers Talk to a person (CR9)', () => {
  const text=requestFailed(new Error('Failed to fetch'));
  assert.match(text,/nothing has been sent/);assert.match(text,/Talk to a person/);assert.doesNotMatch(text,/Failed to fetch/);
  assert.match(requestFailed(new Error('This request category cannot be raised by a customer.')),/cannot be raised by a customer\)/);
});

test('attachments on a ticket are parsed by file name and tampered ones are rejected', () => {
  const [ticket]=parseTickets({data:[{...TICKET,attachments:[{document_id:'3c063770-db29-48f4-bbfd-4c45c7e1093b',file_name:'proof-of-address.pdf'}]}]});
  assert.deepEqual(ticket.attachments.map(item=>item.file_name),['proof-of-address.pdf']);
  assert.deepEqual(parseTickets({data:[TICKET]})[0].attachments,[]);
  assert.throws(()=>parseTickets({data:[{...TICKET,attachments:[{document_id:'x',file_name:'a.pdf'}]}]}));
});
