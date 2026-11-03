import test from 'node:test';
import assert from 'node:assert/strict';
import {cleanTicketDetails,missingTicketFields,prepareTicketAction} from './ticketWorkflow.js';

const cat={category:'card_limit_change',customer_raisable:true,fields:[
  {key:'card',label:'Card (last 4 digits)',required:true,kind:'card'},
  {key:'new_limit',label:'Requested limit',required:true,kind:'amount'},
  {key:'duration',label:'Temporary or permanent',required:true,kind:'choice'},
  {key:'until',label:'Needed until',required:false,kind:'date'},
  {key:'reason',label:'Reason',required:false,kind:'text'},
]};
test('only category-defined fields are included in a ticket target',()=>{
  assert.deepEqual(cleanTicketDetails(cat,{new_limit:' 12000 ',duration:'temporary',card:'5592',until:'2028-12-20',token:'private',reason:''}),
    {card:'5592',new_limit:'12000',duration:'temporary',until:'2028-12-20'});
});
test('a temporary request requires an end date but a permanent request does not',()=>{
  assert.deepEqual(missingTicketFields(cat,{card:'5592',new_limit:'12000',duration:'temporary'}),['Needed until']);
  assert.deepEqual(missingTicketFields(cat,{card:'5592',new_limit:'12000',duration:'permanent'}),[]);
});
test('missing fields are rejected before opening an action case or creating a proposal',async()=>{
  await assert.rejects(prepareTicketAction({category:cat,summary:'Change the limit',values:{new_limit:'12000'}},'token'),/required request details/);
});
test('unsupported categories are rejected before any action is proposed',async()=>{
  await assert.rejects(prepareTicketAction({category:{...cat,customer_raisable:false},summary:'Test',values:{}},'token'),/cannot be submitted/);
});
test('ticket descriptions must be present and bounded',async()=>{
  await assert.rejects(prepareTicketAction({category:cat,summary:'',values:{card:'5592',new_limit:'12000',duration:'permanent'}},'token'),/short summary/);
  await assert.rejects(prepareTicketAction({category:cat,summary:'x'.repeat(501),values:{card:'5592',new_limit:'12000',duration:'permanent'}},'token'),/short summary/);
});

test('editing extracted card details updates the draft summary, not the earlier value',async()=>{
  const {cardLimitDraftSummary}=await import('./ticketWorkflow.js');
  const summary=cardLimitDraftSummary({new_limit:'10000',duration:'temporary',until:'2028-12-20',reason:'Travel'},'Travel Card');
  assert.match(summary,/SGD 10,000/);
  assert.match(summary,/2028-12-20/);
  assert.doesNotMatch(summary,/12,000/);
});
