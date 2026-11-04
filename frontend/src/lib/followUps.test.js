import test from 'node:test';
import assert from 'node:assert/strict';
import {CARE_CHIP,MEETING_CHIP,followUps,topicOf} from './followUps.js';

const answer=(extra={})=>({role:'assistant',content:'Here is what I found.',...extra});

test('suggestions follow the topic of the question', () => {
  assert.equal(topicOf('I don\'t recognise the London charges on my card',answer()),'fraud');
  assert.equal(topicOf('Which bank statements do I upload for my review?',answer()),'document');
  assert.equal(topicOf('When are my bank statements due for the review?',answer()),'compliance');
  assert.equal(topicOf('How do I get my statement?',answer()),'statements');
  assert.equal(topicOf('did my transfer to lim interiors go thru?',answer()),'transfer');
  assert.equal(topicOf('hello',answer({sections:[{domain:'compliance'}]})),'compliance');
});

test('at most three, none already asked, personal ones only when signed in', () => {
  const asked=['What do I still need to send for my review?'];
  const signed=followUps({question:'when is my review due',message:answer(),signedIn:true,asked});
  assert.ok(signed.length<=3&&!signed.includes(asked[0])&&!signed.includes('When is my review due?'.toLowerCase()));
  const guest=followUps({question:'what is the minimum payment on a credit card?',message:answer(),signedIn:false});
  assert.deepEqual(guest,['Does paying only the minimum avoid interest?','Can I increase my credit limit?']);
});

test('a handed-over question suggests following the request; errors and empty answers suggest nothing', () => {
  assert.equal(followUps({question:'Should I invest in a structured product?',message:answer({needs_human:true}),signedIn:true})[0],'What is the status of my request?');
  assert.deepEqual(followUps({question:'x',message:answer({error:true}),signedIn:true}),[]);
  assert.deepEqual(followUps({question:'x',message:answer({content:''}),signedIn:true}),[]);
});

test('no suggestion would itself be handed over to the RM as a ticket', async () => {
  const all=new Set();
  for(const q of ['card','loan','statement','review','upload','transfer','limit','meeting','fraud','balance','hello'])
    for(const signedIn of [true,false])for(const needs_human of [false,true])
      followUps({question:q,message:answer({needs_human}),signedIn}).forEach(text=>all.add(text));
  const handover=/\b(?:should|would you recommend)\b[^.?!]{0,40}\b(?:invest|buy|sell)\b|\b(?:talk|speak|connect)\b[^.?!]{0,25}\b(?:rm|relationship manager|advisor)\b|\b(?:waive|negotiate|refund)\b|\bcall\s+me\b/i;
  for(const text of all)assert.ok(!handover.test(text),text);
});

test('a reply with an offer suggests nothing; its own Confirm and Cancel answer it (v5)', () => {
  const offer={offer_group:'12345678-1234-1234-1234-123456789011',expires_at:'2026-11-04T12:30:00Z',offers:[]};
  assert.deepEqual(followUps({question:'I lost my card',message:answer({offer}),signedIn:true}),[]);
});

test('the fraud charge list offers only the one-tap confirmation (CR12)', () => {
  const message={role:'assistant',content:'…If they are, reply “Yes, report it” and I’ll pass them to the fraud team as urgent.'};
  assert.deepEqual(followUps({question:'I do not recognise these charges',message,signedIn:true}),['Yes, report it']);
  assert.notDeepEqual(followUps({question:'I do not recognise these charges',message,signedIn:false}),['Yes, report it']);
});

test('a which-card question offers one tap per card and both', () => {
  const message={role:'assistant',content:'Which card’s outstanding balance do you mean: your Signature Card ending 7310 or your Travel Card ending 5592? You can also say “both”.'};
  assert.deepEqual(followUps({question:'whats my card balance',message,signedIn:true}),['Signature Card ending 7310','Travel Card ending 5592','Both cards']);
});

test('free times listed for a meeting request offer one tap each; a drafted request has only its offer buttons (2a)', () => {
  const listed={role:'assistant',content:'Here are free 30-minute times with your relationship manager (Singapore time):\n• Mon 9 Nov, 10:00 am\n• Tue 10 Nov, 2:30 pm\nTell me which times suit you and what you’d like to discuss, and I’ll prepare a meeting request for you to confirm.'};
  assert.deepEqual(followUps({question:'when is my RM free?',message:listed,signedIn:true}),['Mon 9 Nov, 10:00 am','Tue 10 Nov, 2:30 pm']);
  const six={...listed,content:listed.content.replace('• Tue','• Mon 9 Nov, 10:30 am\n• Mon 9 Nov, 11:00 am\n• Mon 9 Nov, 11:30 am\n• Mon 9 Nov, 12:00 pm\n• Tue')};
  assert.equal(followUps({question:'when is my RM free?',message:six,signedIn:true}).length,6);
  assert.notDeepEqual(followUps({question:'when is my RM free?',message:listed,signedIn:false}),['Mon 9 Nov, 10:00 am','Tue 10 Nov, 2:30 pm']);
  const offer={offer_group:'12345678-1234-1234-1234-123456789011',expires_at:'2026-11-04T12:30:00Z',offers:[]};
  assert.deepEqual(followUps({question:'Can I meet Sophie next week?',message:answer({offer}),signedIn:true}),[]);
});

test('loan, property, investment and large-transfer answers offer a meeting request when signed in (2a)', () => {
  for(const question of ['What is the interest rate on a home loan?','Can I refinance my condo?','What is a unit trust?','I need to transfer 500,000 SGD to Hong Kong']) {
    const chips=followUps({question,message:answer(),signedIn:true});
    assert.ok(chips.length<=3&&chips.at(-1)===MEETING_CHIP,`${question}: ${chips}`);
    assert.ok(!followUps({question,message:answer(),signedIn:false}).includes(MEETING_CHIP),question);
  }
  for(const question of ['What is my account balance?','When is my card payment due?','Can I meet my RM next week?'])
    assert.ok(!followUps({question,message:answer(),signedIn:true}).includes(MEETING_CHIP),question);
  assert.ok(!followUps({question:'What is a home loan?',message:answer(),signedIn:true,asked:[MEETING_CHIP]}).includes(MEETING_CHIP));
});

test('a reply with care offers a person, never product or meeting chips (2a)', () => {
  for(const signedIn of [true,false])for(const needs_human of [false,true]) {
    const chips=followUps({question:'I lost my job, can I pause my home loan?',message:answer({care:true,needs_human}),signedIn});
    assert.deepEqual(chips,[CARE_CHIP]);
  }
  assert.deepEqual(followUps({question:'x',message:answer({care:true}),signedIn:true,asked:[CARE_CHIP]}),[]);
  assert.deepEqual(followUps({question:'x',message:answer({care:true,error:true}),signedIn:true}),[]);
});

test('a handover preview offers send/cancel, or add/new/cancel when a request is open (CR8)', () => {
  const preview={role:'assistant',content:'Of course. Here’s what I’d send to your relationship manager:\n• Topic: Fees or rates\nReply “Send to my RM” to send it.'};
  assert.deepEqual(followUps({question:'waive my fee',message:preview,signedIn:true}),['Send to my RM','Cancel']);
  const open={...preview,content:preview.content+' Reply “Add to TCK-001050” to add this question to it'};
  assert.deepEqual(followUps({question:'waive my fee',message:open,signedIn:true}),['Add to TCK-001050','Open a new request','Cancel']);
});

test('the lost-card offer asks before reporting (CR12)', () => {
  const message={role:'assistant',content:'Call the fraud line now.\n\nWould you like me to report this to our fraud team as urgent now? Reply “Yes, report my card” or “No”.'};
  assert.deepEqual(followUps({question:'i lost my card',message,signedIn:true}),['Yes, report my card','No']);
});

test('card-limit follow-up provides quick replies only for listed authorised cards',()=>{
  const message=answer({content:'Your requested new limit is 12,000. Which card is this for: Signature Card ending 7310, Travel Card ending 5592?'});
  assert.deepEqual(followUps({question:'Actually make it $12,000',message,signedIn:true}),['Signature Card ending 7310','Travel Card ending 5592']);
});


test('natural-language report keeps security follow-up category',()=>{
  assert.equal(topicOf('Someone used my card without permission',answer()),'fraud');
});


test('rejected replies do not invent new banking follow-up actions',()=>{
  const failed={role:'assistant',content:'I could not verify this.',response_origin:'validation',source_check:'rejected'};
  assert.deepEqual(followUps({question:'Can I see my balance?',message:failed,signedIn:true}),[]);
});
