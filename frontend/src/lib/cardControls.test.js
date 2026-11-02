// Card controls (0096, CR19): the new request field kinds, the dispute window and the limit declaration hint.
import test from 'node:test';
import assert from 'node:assert/strict';
import {parseCategories} from './ticketData.js';
import {missingTicketFields} from './ticketWorkflow.js';
import {DISPUTE_DAYS,disputableCharges} from './customerData.js';

const LIMIT={category:'card_limit_change',label:'Card limit change',description:'Ask for a different limit.',owner_team:'Cards and credit team',decided_by:'The cards and credit team decides.',
  target_hours:72,customer_raisable:true,documents:[],fields:[{key:'card',label:'Card (last 4 digits)',kind:'card',required:true},
  {key:'new_limit',label:'New limit',kind:'amount',required:true},{key:'duration',label:'Temporary or permanent',kind:'choice',required:true,choices:['temporary','permanent']},
  {key:'until',label:'Needed until',kind:'date',required:false},{key:'reason',label:'Reason',kind:'choice',required:true,choices:['Travel','Lower my spending']},
  {key:'declaration',label:'For a higher limit: details up to date',kind:'confirm',required:false}]};
const DISPUTE={...LIMIT,category:'card_dispute',label:'Dispute a card charge',fields:[{key:'card',label:'Card',kind:'card',required:true},{key:'transaction',label:'Charge',kind:'transaction',required:true}]};

test('categories accept the confirm and transaction kinds',()=>{
  const [limit,dispute]=parseCategories([LIMIT,DISPUTE]);
  assert.equal(limit.fields.at(-1).kind,'confirm');
  assert.equal(dispute.fields[1].kind,'transaction');
  assert.throws(()=>parseCategories([{...DISPUTE,fields:[{key:'x',label:'X',kind:'signature',required:true}]}]));
});

test('a higher limit asks for the declaration, lowering spending does not',()=>{
  const base={card:'7310',new_limit:'25000',duration:'permanent',reason:'Travel'};
  assert.deepEqual(missingTicketFields(LIMIT,base),['For a higher limit: details up to date']);
  assert.deepEqual(missingTicketFields(LIMIT,{...base,declaration:'yes'}),[]);
  assert.deepEqual(missingTicketFields(LIMIT,{...base,new_limit:'15000',reason:'Lower my spending'}),[]);
  assert.deepEqual(missingTicketFields(LIMIT,{...base,duration:'temporary',declaration:'yes'}),['Needed until']);
});

test('only posted card charges from the last 60 days can be disputed, newest first',()=>{
  const today=new Date('2026-11-02T08:00:00Z');
  const row=(txn_id,date,amount,extra={})=>({txn_id,date,amount,currency:'SGD',status:'posted',card_id:'card_10021',card_last4:'7310',description:'X',...extra});
  const rows=[row('txn_1','2026-10-27',-68.2),row('txn_2','2026-08-01',-50),row('txn_3','2026-10-28',405.5),row('txn_4','2026-10-29',-12,{status:'pending'}),
    row('txn_5','2026-10-30',-20,{card_id:null,card_last4:null}),row('txn_6','2026-10-31',-9)];
  assert.equal(DISPUTE_DAYS,60);
  assert.deepEqual(disputableCharges(rows,today).map(r=>r.txn_id),['txn_6','txn_1']);
});
