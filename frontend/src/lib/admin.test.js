import test from 'node:test';
import assert from 'node:assert/strict';
import {MIN_COMPARE,defaultPair,delta,filterGroups,firstLine,isAdmin,kindCounts,number,percent,scoreLabel,seconds,sparkSegments,summary,versionPair} from './admin.js';
import {isRm} from './rm.js';

test('shows the admin dashboard only for the administrator-set admin role',()=>{
  assert.equal(isAdmin({user:{app_metadata:{demobank_role:'admin'}}}),true);
  for(const role of ['rm','support','customer'])assert.equal(isAdmin({user:{app_metadata:{demobank_role:role}}}),false);
  assert.equal(isAdmin({user:{app_metadata:{},user_metadata:{demobank_role:'admin'}}}),false);
  assert.equal(isAdmin(null),false);
  assert.equal(isRm({user:{app_metadata:{demobank_role:'admin'}}}),false);
});

test('counts cases and labels scores',()=>{
  assert.deepEqual(summary([{status:'pass'},{status:'fail'},{status:'fail'},{status:'skipped'}]),{pass:1,fail:2,skipped:1,error:0});
  assert.deepEqual([1,0,0.5,null].map(scoreLabel),['pass','fail','0.5','n/a']);
  assert.equal(seconds(31.46),'31.5 s');assert.equal(seconds(null),'—');
});

test('formats rates and the change since the previous window, with the good direction',()=>{
  assert.equal(percent(0.9444,1),'94.4%');assert.equal(percent(null),'—');assert.equal(number(1.111),'1.1');assert.equal(number(18),'18');
  assert.deepEqual(delta(0.95,0.9),{text:'+5.0 pts',tone:'up-good'});
  assert.deepEqual(delta(0.2,0.1,{better:'down'}),{text:'+10.0 pts',tone:'up-bad'});
  assert.deepEqual(delta(40,46,{kind:'seconds',better:'down'}),{text:'−6.0 s',tone:'down-good'});
  assert.deepEqual(delta(18,12,{kind:'count',better:''}),{text:'+6',tone:''});
  assert.deepEqual(delta(3,3),{text:'no change',tone:''});
  assert.equal(delta(0.9,null),null);
});

test('draws sparkline segments and breaks the line at a missing value',()=>{
  assert.deepEqual(sparkSegments([]),[]);
  assert.deepEqual(sparkSegments([null,null]),[]);
  const [first,second]=sparkSegments([0,10,null,5]);
  assert.equal(first,'0.00,26.00 33.33,2.00');
  assert.equal(second,'100.00,14.00 100.00,14.00');
});

test('filters failure groups by kind and text and counts them by kind',()=>{
  const groups=[{kind:'request_error',title:'Request failed: LLMError',error:'LLMError',in_window:2,items:[{question:'Why was my card declined?'}]},
    {kind:'eval_case',title:'C07 failing in demobank-qa1-client',case_id:'C07',dataset:'demobank-qa1-client',in_window:1,items:[{question:''}]}];
  assert.equal(filterGroups(groups,{kind:'eval_case'}).length,1);
  assert.equal(filterGroups(groups,{text:'declined'})[0].kind,'request_error');
  assert.equal(filterGroups(groups,{text:'c07'})[0].kind,'eval_case');
  assert.equal(filterGroups(groups,{text:'nothing'}).length,0);
  assert.deepEqual(kindCounts(groups),{request_error:2,eval_case:1});
});

test('keeps only the first line of an error',()=>{
  assert.equal(firstLine("LLMBudgetExceeded('Per-request token budget is used up')\n\nTraceback (most recent call last):\n  File x"),"LLMBudgetExceeded('Per-request token budget is used up')");
  assert.equal(firstLine("LLMTimeout('Model request timed out')Traceback (most recent call last):"),"LLMTimeout('Model request timed out')");
  assert.equal(firstLine(null),'');
});

test('the default comparison is the last two full runs, else the last two runs', () => {
  const point=(experiment,subset=false)=>({experiment,subset});
  assert.deepEqual(defaultPair([point('a'),point('b'),point('c',true)]),['a','b']);
  assert.deepEqual(defaultPair([point('a'),point('b',true)]),['a','b']);
  assert.deepEqual(defaultPair([point('a')]),[null,null]);
});

test('two prompt versions are compared only with enough calls or cases on both sides', () => {
  const version=(calls,cases)=>({live_metrics:calls?{calls,ok_rate:.9,p95_s:3,input_tokens:800,output_tokens:120}:null,eval_metrics:cases?{cases,pass_rate:.8,judge:null}:null});
  const few=versionPair(version(MIN_COMPARE,0),version(3,4));
  assert.equal(few.enough,false);
  assert.deepEqual(few.rows.find(row=>row.label==='Live calls'),{label:'Live calls',a:MIN_COMPARE,b:3,kind:'count'});
  const both=versionPair(version(0,MIN_COMPARE),version(40,0));
  assert.equal(both.enough,true);
  assert.equal(both.rows.find(row=>row.label==='Eval pass rate').b,null);
  assert.equal(versionPair(undefined,version(20,0)).enough,false);
});
