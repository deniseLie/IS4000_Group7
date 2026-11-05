import test from 'node:test';
import assert from 'node:assert/strict';
import {EXPIRED_LINE,expiresIn,fill,offerState,parseOffer,placeholders} from './offers.js';

const id=n=>`12345678-1234-1234-1234-1234567890${String(n).padStart(2,'0')}`;
const group=(extra={})=>({offer_group:id(1),expires_at:'2026-11-04T12:30:00Z',
  offers:[{command_id:id(2),type:'handoff.create',summary:'Report the card to our fraud team',expires_at:'2026-11-04T12:30:00Z'}],...extra});

test('an offer group is read strictly and copied, never trusted as given',()=>{
  const value=group();
  assert.deepEqual(parseOffer(value),{...value,offers:[{...value.offers[0],optional:false}]});  // saved before 0114: no switches
  assert.equal(parseOffer(null),null);
  assert.equal(parseOffer(undefined),null);
  for(const bad of [{offers:[]},{offer_group:'x'},{expires_at:'soon'},{offers:[{...value.offers[0],summary:' '}]},
    {offers:[value.offers[0],value.offers[0]]},{offers:Array.from({length:11},(_,i)=>({...value.offers[0],command_id:id(10+i)}))}])
    assert.throws(()=>parseOffer({...value,...bad}));
  assert.deepEqual(Object.keys(parseOffer({...value,offers:[{...value.offers[0],target:{secret:1}}]}).offers[0]),['command_id','type','summary','expires_at','optional']);
});

test('an offer is open until it expires or she replies after it',()=>{
  const offer=parseOffer(group()),messages=[{role:'user',content:'I lost my card'},{role:'assistant',content:'x',offer}];
  const before=Date.parse('2026-11-04T12:00:00Z'),after=Date.parse('2026-11-04T12:30:00Z');
  assert.equal(offerState(messages,1,before),'open');
  assert.equal(offerState(messages,1,after),'expired');
  assert.equal(offerState([...messages,{role:'user',content:'yes'}],1,before),'answered');
  assert.equal(offerState(messages,0,before),null);
  assert.equal(expiresIn(offer,before),'Expires in 30 minutes');
  assert.equal(expiresIn(offer,after-30000),'Expires in 1 minute');
  assert.equal(expiresIn(offer,after),'Expired');
  assert.equal(EXPIRED_LINE,'Ask again if you still want this.');
});

test('sections fill their planned places as they stream, in plan order',()=>{
  const live=placeholders({tasks:[{task_id:'t2',domain:'fraud',priority:'P1'},{task_id:'t1',domain:'retail',priority:'P3'}]});
  assert.deepEqual(live.map(s=>[s.task_id,s.pending]),[['t2',true],['t1',true]]);
  const one=fill(live,{task_id:'t1',domain:'retail',priority:'P3',answer:'Balance',sources:[]});
  assert.deepEqual(one.map(s=>[s.task_id,!!s.pending]),[['t2',true],['t1',false]]);
  assert.equal(fill(one,{task_id:'t9',answer:'late'}).at(-1).task_id,'t9');
  assert.equal(live[1].pending,true);
});
