import test from 'node:test';
import assert from 'node:assert/strict';
import {parseInbox,parseNotification} from './notifications.js';
function notification() {
  return {notification_id:'12345678-1234-1234-1234-123456789012',customer_id:1001,event_id:2,template_key:'card.blocked',template_version:2,
    locale:'en',priority:'P0',title:'Synthetic card block recorded',body:'Your confirmed demo command temporarily blocked the synthetic card.',
    is_service:true,read_at:null,created_at:'2026-10-19T12:00:00Z'};
}
test('inbox parsing retains verified read state, scope and template identity without input mutation',()=>{
  const value=notification(),before=JSON.stringify(value);
  assert.deepEqual(parseInbox({customer_id:1001,items:[value]}).items,[value]);
  assert.equal(parseNotification({...value,read_at:'2026-10-19T12:01:00Z'}).read_at,'2026-10-19T12:01:00Z');
  assert.equal(JSON.stringify(value),before);
});
test('inbox parsing rejects cross-customer records, duplicates and invalid acknowledgement fields',()=>{
  const value=notification();
  for(const changed of [{customer_id:'1001'},{read_at:'invalid'},{event_id:0},{template_version:0},{is_service:'true'},{body:''}])assert.throws(()=>parseNotification({...value,...changed}));
  assert.throws(()=>parseInbox({customer_id:1003,items:[value]}));
  assert.throws(()=>parseInbox({customer_id:1001,items:[value,value]}));
  assert.deepEqual(parseInbox({customer_id:1001,items:[]}),{customer_id:1001,items:[]});
});
