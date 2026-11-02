import test from 'node:test';
import assert from 'node:assert/strict';
import {canStartFraudReport} from './fraudEntry.js';
const fraud={role:'assistant',content:'If you suspect an unauthorised charge, contact official support now.',sections:[{domain:'fraud'}]};

test('show explicit report entry only in a signed-in latest fraud response',()=>{
  assert.equal(canStartFraudReport({message:fraud,signedIn:true,latest:true}),true);
  assert.equal(canStartFraudReport({message:fraud,signedIn:false,latest:true}),false);
  assert.equal(canStartFraudReport({message:fraud,signedIn:true,latest:false}),false);
  assert.equal(canStartFraudReport({message:{...fraud,sections:[{domain:'retail'}]},signedIn:true,latest:true}),false);
});
test('do not show a duplicate report action when an exact charge is waiting for confirmation',()=>{
  assert.equal(canStartFraudReport({message:{...fraud,content:'If they are, reply “Yes, report it” to submit.'},signedIn:true,latest:true}),false);
  assert.equal(canStartFraudReport({message:{...fraud,content:'Reply “Yes, report my card” or “No”.'},signedIn:true,latest:true}),false);
});
test('do not offer another entry once the fraud team was given a report',()=>{
  assert.equal(canStartFraudReport({message:{...fraud,content:'I’ve passed this to our fraud team (reference TCK-001234)'},signedIn:true,latest:true}),false);
});
test('a validation failure may still provide a safe entry to the reporting workflow',()=>{
  assert.equal(canStartFraudReport({message:{...fraud,response_origin:'validation',content:'Answer withheld'},signedIn:true,latest:true}),true);
});
