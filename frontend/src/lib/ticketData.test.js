import test from 'node:test';
import assert from 'node:assert/strict';
import {limitRequestPrefill,nextStep,parseCategories,parseTicketResult,parseTicketTarget,parseTickets,requestFailed,ticketIntent,ticketResultMatches,ticketSummary,ticketTopic,limitSummary,isLimitWorkflowReply,isExistingRequestStatus} from './ticketData.js';

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
  assert.match(text,/nothing has been sent/);assert.match(text,/official support channel/);assert.doesNotMatch(text,/Failed to fetch/);
  assert.match(requestFailed(new Error('This request category cannot be raised by a customer.')),/cannot be raised by a customer\)/);
});

test('attachments on a ticket are parsed by file name and tampered ones are rejected', () => {
  const [ticket]=parseTickets({data:[{...TICKET,attachments:[{document_id:'3c063770-db29-48f4-bbfd-4c45c7e1093b',file_name:'proof-of-address.pdf'}]}]});
  assert.deepEqual(ticket.attachments.map(item=>item.file_name),['proof-of-address.pdf']);
  assert.deepEqual(parseTickets({data:[TICKET]})[0].attachments,[]);
  assert.throws(()=>parseTickets({data:[{...TICKET,attachments:[{document_id:'x',file_name:'a.pdf'}]}]}));
});

test('the card limit request uses the latest amount she stated (CR11 corrections)', () => {
  assert.equal(limitSummary(['I want to raise my card limit to $15,000','actually make it 12000']),
    'Requested new limit: SGD 12,000 (corrected from SGD 15,000). actually make it 12000');
  assert.match(limitSummary(['increase my limit to 20k for my trip']),/^Requested new limit: SGD 20,000\. /);
  assert.equal(limitSummary(['can i increase my card limit']),'can i increase my card limit');
});

test('corrected client limit request remains available after short follow-ups',()=>{
  const turns=['How can I increase my card limit?', 'Only temporarily', "I'm travelling to Japan in December",'Can you help me request $15,000?', 'Actually, make that $12,000 instead.', 'travel', 'until 20 December 2026'];
  const summary=limitSummary(turns);
  assert.match(summary,/Requested new limit: SGD 12,000/);
  assert.match(summary,/corrected from SGD 15,000/);
  assert.match(summary,/Travel Card/);
  assert.match(summary,/End date: 20 December 2026/);
  assert.doesNotMatch(summary,/corrected from SGD 2,026/);
  assert.equal(isLimitWorkflowReply({role:'assistant',sections:[{domain:'retail'}],content:'Your requested new limit is SGD 12,000. Which card is this for?'}),true);
  assert.equal(isLimitWorkflowReply({role:'assistant',sections:[{domain:'retail'}],content:'Answer withheld',source_check:'rejected'}),false);
  assert.equal(isLimitWorkflowReply({role:'assistant',sections:[{domain:'compliance'}],content:'Your requested new limit is SGD 12,000.'}),false);
});

function limitTurns(extra=[]) {
  return [
    ['user','How can I increase my credit card limit?'],
    ['assistant','You may request one.'],
    ['user','What if I only need it temporarily?'],
    ['assistant','What new credit limit?'],
    ['user',"I'm travelling to Japan in December."],
    ['assistant','What amount?'],
    ['user','Can you help me request $15,000?'],
    ['assistant','Please confirm new amount.'],
    ['user','Actually, make that $12,000 instead.'],
    ['assistant','Which card is this for: Signature Card or Travel Card?'],
    ['user','travel'],
    ['assistant','What date should the temporary change end? Please include the year.'],
    ...extra,
  ].map(([role,content])=>({role,content}));
}

test('chat to form: year then day/month fills a validated full date and other form fields',()=>{
  const turns=limitTurns([['user','2028'],['assistant','You mentioned 2028 as the year. What day and month?'],['user','20 December']]);
  const prefill=limitRequestPrefill(turns);
  assert.deepEqual(prefill.details,{new_limit:'12000',duration:'temporary',until:'2028-12-20',reason:'Travel'});
  assert.equal(prefill.selectedCardLabel,'Travel Card');
  assert.match(prefill.summary,/20 December 2028/);
  assert.doesNotMatch(prefill.summary,/1 January 2027/);
  assert.match(prefill.summary,/corrected from SGD 15,000/);
  assert.equal(prefill.details.card,undefined); // no fabricated last-four digits
});

test('chat to form: day/month followed by year also fills the end date',()=>{
  const turns=limitTurns([['user','20 December'],['assistant','You said 20 December. Which year should the temporary change end?'],['user','2028']]);
  const prefill=limitRequestPrefill(turns);
  assert.equal(prefill.details.until,'2028-12-20');
});

test('chat to form: incomplete or invalid dates never silently fill the date field',()=>{
  const onlyYear=limitRequestPrefill(limitTurns([['user','2028']]));
  assert.equal(onlyYear.details.until,undefined);
  assert.match(onlyYear.summary,/year 2028/);
  const partial=limitRequestPrefill(limitTurns([['user','20 December']]));
  assert.equal(partial.details.until,undefined);
  assert.match(partial.summary,/year not confirmed/);
  const invalid=limitRequestPrefill(limitTurns([['user','31 February 2028']]));
  assert.equal(invalid.details.until,undefined);
});

test('chat to form: explicit complete date works without interpreting the year as money',()=>{
  const prefill=limitRequestPrefill(limitTurns([['user','until 20 December 2028']]));
  assert.equal(prefill.details.new_limit,'12000');
  assert.equal(prefill.details.until,'2028-12-20');
  assert.doesNotMatch(prefill.summary,/SGD 2,028/);
});

test('chat to form: explicit end-date correction replaces the previous date',()=>{
  const prefill=limitRequestPrefill(limitTurns([['user','20 December 2028'],['assistant','Request ready for your review.'],['user','Actually, 19 December instead.']]));
  assert.equal(prefill.details.until,'2028-12-19');
});

test('chat to form: unrelated topic does not contaminate saved card request',()=>{
  const prefill=limitRequestPrefill(limitTurns([['user','2028'],['user','What is my account balance?'],['assistant','Please sign in'],['user','20 December']]));
  assert.equal(prefill.details.until,undefined);
});

test('chat to form: choosing Travel Card alone does not invent a travel reason',()=>{
  const prefill=limitRequestPrefill([
    ['user','Increase my credit card limit to $12,000'],
    ['assistant','Which card is this for: Travel Card or Signature Card?'],
    ['user','travel']].map(([role,content])=>({role,content})));
  assert.equal(prefill.selectedCardLabel,'Travel Card');
  assert.equal(prefill.details.reason,undefined);
});


test('overdue specialist requests do not incorrectly assign follow-up to the RM',()=>{
  const ticket={status:'submitted',overdue:true,owner_team:'Fraud operations',last_update:null};
  const step=nextStep(ticket);
  assert.match(step,/fraud operations/);
  assert.doesNotMatch(step,/relationship manager/i);
});

test('v6: explanation questions do not start card limit requests',()=>{
  assert.equal(ticketIntent('What does a temporary credit limit increase mean?'),null);
  assert.equal(ticketIntent('wats the difference between card limit and account balance?'),null);
  assert.equal(ticketIntent('How does a temporary credit limit increase work?'),null);
  assert.equal(ticketIntent('i wan my credit limit increased'),'card_limit_change');
  assert.equal(ticketIntent('I want my credit limit increased'),'card_limit_change');
  assert.equal(ticketIntent('Please raise my card limit'),'card_limit_change');
});

test('v6: explanations never produce a prefilled limit-change request',()=>{
  const result=limitRequestPrefill([{role:'user',content:'What does a temporary credit limit increase mean?'},{role:'assistant',content:'It is a request for a set period.'}]);
  assert.equal(result.summary,'');
  assert.deepEqual(result.details,{});
  const result2=limitRequestPrefill([{role:'user',content:'I want my credit limit increased'},{role:'assistant',content:'What new limit would you like?'}]);
  assert.ok(result2.summary || result2.details);
});

test('approval and request-status questions never become a new card-limit application',()=>{
  for (const question of [
    'Has the bank approved my credit limit increase?',
    'What is the status of my request?',
    'What is the status of TCK-001040?',
    'Has my card limit change been approved?',
  ]) {
    assert.equal(isExistingRequestStatus(question),true,question);
    assert.equal(ticketIntent(question),'status',question);
  }
  assert.equal(isExistingRequestStatus('How do I get approved for a higher credit limit?'),false);
  assert.equal(ticketIntent('I want to increase my credit limit'),'card_limit_change');
  assert.equal(ticketIntent('What does a credit limit mean?'),null);
});
