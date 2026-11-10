import test from 'node:test';
import assert from 'node:assert/strict';
import {KINDS,MIN_COMPARE,SIDES,counted,datasetRow,defaultPair,delta,evalSuccess,overTarget,traceBudget,usd,failingFirst,filterGroups,firstLine,isAdmin,judgeSummary,loadAgents,mix,problemFirst,tally,waterfall,kindCounts,number,percent,scoreLabel,seconds,sparkSegments,summary,versionPair} from './admin.js';
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
  assert.deepEqual(judgeSummary([{judge:1},{judge:0.5},{judge:0},{judge:null},{}]),{scored:3,full:1});
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
  assert.deepEqual(delta(0.85,0.7,{kind:'score'}),{text:'+0.15',tone:'up-good'});
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

test('the All datasets row is the latest full run against the full run before it, with its problem cases', () => {
  const point=(experiment,pass_rate,subset=false)=>({experiment,pass_rate,subset,started_at:`2026-11-0${experiment.length}T00:00:00Z`,counts:{pass:1,fail:0,error:0,skipped:0}});
  const row=datasetRow({dataset:'demobank-graph',runs:3,last_run:'2026-11-04T00:00:00Z',stale:false,
    timeline:[point('a',0.6),point('bb',0.8),point('ccc',0.1,true)],heatmap:{labels:{passing:4,flaky:1,regressed:2,fixed:3}}});
  assert.equal(row.pass_rate,0.8);assert.equal(row.previous_pass_rate,0.6);assert.equal(row.started_at,'2026-11-02T00:00:00Z');assert.equal(row.fixed,3);
  assert.deepEqual(row.problems,[{label:'regressed',count:2},{label:'flaky',count:1}]);
  const never=datasetRow({dataset:'demobank-new',runs:0,timeline:[],heatmap:{labels:{}}});
  assert.equal(never.pass_rate,null);assert.equal(never.counts,null);assert.deepEqual(never.problems,[]);
  assert.equal(never.judge,null);assert.equal(never.judge_current,false);
});

test('the judge mean is the latest judged full run against the one before it by the same judge; a run without --judge keeps it', () => {
  const point=(experiment,judge,subset=false,judge_model='openai/gpt-6-luna')=>({experiment,judge,subset,pass_rate:1,judge_model:judge==null?null:judge_model,started_at:`2026-11-0${experiment.length}T00:00:00Z`});
  const judged=datasetRow({dataset:'demobank-safety',timeline:[point('a',0.7),point('bb',null),point('ccc',0.4,false,'sonnet'),point('dddd',0.85),point('eeeee',0.2,true)]});
  assert.equal(judged.judge,0.85);assert.equal(judged.previous_judge,0.7);assert.equal(judged.judge_current,true);assert.equal(judged.judge_model,'openai/gpt-6-luna');
  assert.equal(datasetRow({dataset:'demobank-qa1-rm',timeline:[point('a',0.97,false,'sonnet'),point('bb',0.62)]}).previous_judge,null);
  const later=datasetRow({dataset:'demobank-safety',timeline:[point('a',0.7),point('bb',0.85),point('ccc',null)]});
  assert.equal(later.judge,0.85);assert.equal(later.judge_current,false);assert.equal(later.judge_at,'2026-11-02T00:00:00Z');
  assert.equal(datasetRow({dataset:'demobank-planner',timeline:[point('a',null)]}).judge,null);
});

test('heatmap rows put cases that need a look first and a run lists errors and failures first, otherwise in order', () => {
  const rows=[['C01','passing'],['C02','flaky'],['C03','fixed'],['C04','regressed'],['C05','passing'],['C06','always_failing']].map(([case_id,label])=>({case_id,label}));
  assert.deepEqual(problemFirst(rows).map(row=>row.case_id),['C04','C02','C06','C03','C01','C05']);
  const cases=[['C01','pass'],['C02','fail'],['C03','error'],['C04','skipped'],['C05','fail']].map(([case_id,status])=>({case_id,status}));
  assert.deepEqual(failingFirst(cases).map(item=>item.case_id),['C03','C02','C05','C04','C01']);
  assert.equal(rows[0].case_id,'C01');  // the input order is kept
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

test('a count map becomes rows, largest first, with shares', () => {
  assert.deepEqual(mix({ok:3,withheld:1,error:0}),[{key:'ok',count:3,share:.75},{key:'withheld',count:1,share:.25}]);
  assert.deepEqual(mix(null),[]);
});

test('a waterfall places each step on the request time axis', () => {
  const fall=waterfall([{name:'chat',start_s:0,latency_s:10},{name:'llm',start_s:2,latency_s:3},{name:'old',start_s:null,latency_s:1}]);
  assert.equal(fall.total,10);
  assert.deepEqual(fall.rows.map(row=>[row.name,row.left,row.width]),[['chat',0,1],['llm',.2,.3]]);
  assert.deepEqual(waterfall([]),{total:0,rows:[]});
});

test('repeated agent names are counted', () => {
  assert.equal(counted(['rm_client','rm_requests','rm_client']),'rm_client ×2, rm_requests');
  assert.equal(counted([]),'');
});

test('refusal and consent-gap counts read largest first, and the pages filter by who asked', async () => {
  assert.equal(tally({'rm_tickets: limit':1,'rm_ticket: not_in_book':2}),'rm_ticket: not_in_book ×2, rm_tickets: limit');
  assert.equal(tally({}),'');
  assert.deepEqual(SIDES.map(([key])=>key),['all','client','rm']);
  assert.equal(KINDS.loop_cap,'Read limit reached');
  const seen=[];const real=globalThis.fetch;
  globalThis.fetch=async url=>{seen.push(url);return {ok:true,json:async()=>({agents:[]})};};
  try{await loadAgents('7d','live','rm','token');}finally{globalThis.fetch=real;}
  assert.equal(seen[0],'/api/admin/agents?window=7d&source=live&side=rm');
});

test('the overview success rate is cases passed over cases scored across the latest runs',()=>{
  const rows=[{counts:{pass:10,fail:0,error:0,skipped:2}},{counts:{pass:15,fail:1,error:1,skipped:0}}];
  assert.deepEqual(evalSuccess(rows),{rate:25/27,passed:25,scored:27,datasets:2,failing:1});
  assert.equal(evalSuccess([]).rate,null);
  assert.equal(evalSuccess(undefined).datasets,0);
});

test('the overview headline formats cost, latency targets and the trace budget',()=>{
  assert.equal(usd(0.0123),'≈ $0.012');assert.equal(usd(0.0004),'≈ <$0.001');assert.equal(usd(12.5),'≈ $12.50');assert.equal(usd(null),'—');
  assert.equal(overTarget({p95_client_s:12,p95_rm_s:61}),true);assert.equal(overTarget({p95_client_s:12,p95_rm_s:59}),false);
  assert.deepEqual(traceBudget({used:6900,limit:7796,resets:'2026-12-01'}),{left:896,used:6900,limit:7796,share:6900/7796,warn:true,resets:'2026-12-01'});
  assert.equal(traceBudget({used:8000,limit:7796}).left,0);assert.equal(traceBudget(null),null);
});
