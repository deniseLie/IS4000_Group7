import test from 'node:test';
import assert from 'node:assert/strict';
import {parseCheck,parseWorkspace,primaryStatus,findingText} from './compliance.js';
const id='00000000-0000-4000-8000-100200000001',item='00000000-0000-4000-8000-100200000011',doc='00000000-0000-4000-8000-100200000031';
const report={check_id:id,document_id:doc,case_id:id,item_id:item,customer_id:1002,rule_id:'proof_address',rule_version:1,scope:'synthetic_template_only',sha256:'a'.repeat(64),status:'no_issues',findings:[],extraction:'text',page_count:1,checked_at:'2026-10-24T00:00:00Z'};
function snapshot(){return {call_id:id,as_of:report.checked_at,data:{policy:{validity_hours:24,status_stale_days:7,statement_period_mode:'completed_months'},review:{case_id:id,status:'awaiting_client_documents',status_updated_at:report.checked_at,title:'Demo review',notice_text:'Demo notice',requested_items:[{item_id:item,label:'Address',client_mark:'prepared',submission_status:'not_submitted'}],info_requests:[]},documents:[],rules:{},current_documents:{[item]:doc},checks:[{...report}]}};}
test('bank state outranks preparation and preliminary findings',()=>{
 assert.equal(primaryStatus({client_mark:'prepared',submission_status:'unknown'}),'Unknown');
 assert.equal(primaryStatus({client_mark:'not_prepared',submission_status:'submitted_awaiting_review'}),'Submitted – awaiting review');
 assert.equal(primaryStatus({client_mark:'prepared',submission_status:'accepted'}),'Accepted by bank');
 assert.equal(primaryStatus({client_mark:'prepared',submission_status:'not_submitted'}),'Prepared');
});
test('inconsistent or foreign check envelopes cannot render as a success',()=>{
 assert.equal(parseCheck({...report}).status,'no_issues');
 for(const change of [{findings:[{code:'missing_field',field:'Address'}]},{sha256:'bad'},{scope:'approved'},{page_count:21},{document_id:'bad'}])assert.throws(()=>parseCheck({...report,...change}));
 assert.equal(parseWorkspace(snapshot()).checks.length,1);
 const stale=snapshot();stale.data.current_documents[item]=id;assert.throws(()=>parseWorkspace(stale));
 const foreign=snapshot();foreign.data.checks[0].case_id=doc;assert.throws(()=>parseWorkspace(foreign));
});
test('fix instructions distinguish statement periods from document dates',()=>{
 assert.match(findingText({code:'date_outdated',field:'Statement period'},{}),/date or period coverage/);
 assert.match(findingText({code:'unassessable'},{}),/No pass or fail/);
});

test('checks expire at Singapore midnight, including future-dated reports',async()=>{
 const {checkIsFresh}=await import('./compliance.js');
 const checked={checked_at:'2026-10-24T15:59:00Z'};
 assert.equal(checkIsFresh(checked,Date.parse('2026-10-24T15:59:59Z')),true);
 assert.equal(checkIsFresh(checked,Date.parse('2026-10-24T16:00:00Z')),false);
 assert.equal(checkIsFresh(checked,Date.parse('2026-10-24T15:58:00Z')),false);
});

test('notice checks require a notice digest and never describe fixed templates',()=>{
 const value={...report,scope:'notice_based',requirements_hash:'b'.repeat(64)};
 assert.equal(parseCheck(value).scope,'notice_based');
 assert.throws(()=>parseCheck({...value,requirements_hash:null}));
 assert.match(findingText({code:'provider_unavailable'},{}),/provider is unavailable/);
 assert.doesNotMatch(findingText({code:'missing_field',field:'Address'},{}),/Label:|template/);
});
