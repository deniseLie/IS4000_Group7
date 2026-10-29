import test from 'node:test';
import assert from 'node:assert/strict';
import {followUps,topicOf} from './followUps.js';

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
