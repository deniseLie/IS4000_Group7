// Admin dashboard (AI engineering view): LangSmith evaluations and traces, read through the backend (the LangSmith key
// stays server-side). Only the page link depends on this check; the backend verifies the signed token on every call.
const API=import.meta.env?.VITE_API_URL||'/api';
export const ADMIN_ROLE='admin';

export function isAdmin(session) {return session?.user?.app_metadata?.demobank_role===ADMIN_ROLE;}

async function get(path,token,signal) {
  const response=await fetch(`${API}/admin${path}`,{headers:{Authorization:`Bearer ${token}`},signal});
  const body=await response.json().catch(()=>null);
  if(!response.ok)throw new Error(body?.error?.message||'LangSmith cannot be read right now. Please retry.');
  return body;
}
export const loadEvaluations=(token,signal)=>get('/evaluations',token,signal);
export const loadEvaluation=(name,token,signal)=>get(`/evaluations/${encodeURIComponent(name)}`,token,signal);
export const loadCompare=(a,b,token,signal)=>get(`/evaluations/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`,token,signal);
export const loadPrompts=(window,token,signal)=>get(`/prompts?window=${encodeURIComponent(window)}`,token,signal);
export const loadTraces=(token,signal)=>get('/traces',token,signal);
export const loadTrace=(runId,token,signal)=>get(`/traces/${encodeURIComponent(runId)}`,token,signal);

export function summary(cases) {
  const counts={pass:0,fail:0,skipped:0,error:0};
  for(const item of cases)counts[item.status]+=1;
  return counts;
}
export function scoreLabel(score) {return score===null||score===undefined?'n/a':score===1?'pass':score===0?'fail':String(score);}
export function seconds(value) {return value===null||value===undefined?'—':`${value.toFixed(1)} s`;}

// ---- Dashboard pages (docs/planning/admin-dashboard.md Part 2) ----
function query(params) {
  const found=new URLSearchParams(Object.entries(params).filter(([,value])=>value!==undefined&&value!==null&&value!==''));
  return found.size?`?${found}`:'';
}
export const WINDOWS=[['24h','Last 24 hours'],['7d','Last 7 days'],['30d','Last 30 days'],['90d','Last 90 days']];
export const SOURCES=[['all','Live and evaluations'],['live','Live only'],['eval','Evaluations only']];
export const SIDES=[['all','Client and RM'],['client','Client chat'],['rm','RM workspace']];  // who asked (build plan 3a)
export const loadOverview=(window,token,signal)=>get(`/overview${query({window})}`,token,signal);
export const loadFailures=(window,source,side,token,signal)=>get(`/failures${query({window,source,side})}`,token,signal);
export const loadFailure=(runId,token,signal)=>get(`/failures/${encodeURIComponent(runId)}`,token,signal);

const missing=value=>value===null||value===undefined||Number.isNaN(value);
export function percent(value,digits=0) {return missing(value)?'—':`${(value*100).toFixed(digits)}%`;}
export function number(value,digits=1) {return missing(value)?'—':Number.isInteger(value)?String(value):value.toFixed(digits);}

// The change since the previous window. `better` says which direction is good ('up', 'down' or '' when neither).
export function delta(now,before,{kind='rate',better='up'}={}) {
  if(missing(now)||missing(before))return null;
  const diff=now-before;
  if(Math.abs(diff)<1e-9)return {text:'no change',tone:''};
  const size=Math.abs(diff),sign=diff>0?'+':'−';
  const text=kind==='rate'?`${sign}${(size*100).toFixed(1)} pts`:kind==='seconds'?`${sign}${size.toFixed(1)} s`:`${sign}${Number.isInteger(size)?size:size.toFixed(1)}`;
  const way=diff>0?'up':'down';
  return {text,tone:better?`${way}-${way===better?'good':'bad'}`:''};
}

// Polyline points for a sparkline in a 100 × height box; a missing value breaks the line into segments.
export function sparkSegments(values,height=28) {
  const known=values.filter(value=>!missing(value));
  if(!known.length)return [];
  const top=Math.max(...known),bottom=Math.min(0,...known),span=top-bottom||1,step=values.length>1?100/(values.length-1):0;
  const segments=[];let current=[];
  values.forEach((value,index)=>{
    if(missing(value)){if(current.length)segments.push(current);current=[];return;}
    current.push(`${(index*step).toFixed(2)},${(height-2-((value-bottom)/span)*(height-4)).toFixed(2)}`);
  });
  if(current.length)segments.push(current);
  return segments.map(points=>points.length===1?[points[0],points[0]]:points).map(points=>points.join(' '));
}

export const KINDS={request_error:'Request failed',held_back:'Held back',agent_withheld:'Agent withheld',agent_timeout:'Agent timed out',
  agent_error:'Agent failed',model_call:'Model call',tool_error:'Tool failed',eval_case:'Eval case',loop_cap:'Read limit reached',loop_malformed:'Lookup unfinished'};

// Failure groups shown for the chosen kind and search text (title, error, agent, tool, case or any item's question).
export function filterGroups(groups,{kind='',text=''}={}) {
  const words=text.trim().toLowerCase();
  return groups.filter(group=>(!kind||group.kind===kind)&&(!words||[group.title,group.error,group.agent,group.tool,group.case_id,group.dataset,
    ...group.items.map(item=>item.question)].some(value=>value&&String(value).toLowerCase().includes(words))));
}
export function kindCounts(groups) {
  const counts={};
  for(const group of groups)counts[group.kind]=(counts[group.kind]||0)+group.in_window;
  return counts;
}

// An error's first line (the exception and its message); the traceback stays behind a toggle.
export function firstLine(text) {return (text||'').split(/\n|Traceback \(most recent call last\)/).find(line=>line.trim())?.trim()||'';}

// ---- Evaluations and prompts ----
export const LABELS={regressed:'Regressed',fixed:'Fixed',always_failing:'Always failing',flaky:'Flaky',failing:'Failing',passing:'Passing',not_scored:'Not scored'};
export const COMPARED={regressed:'Regressed',fixed:'Fixed',still_failing:'Still failing',only_a:'Only in A',only_b:'Only in B',skipped:'Skipped',still_passing:'Still passing'};
export const GROUPS={answer:'Answer (S)',trajectory:'Trajectory (T)',planner:'Planner',judge:'Judge (model-graded)'};
export const MIN_COMPARE=10;  // below this many runs or cases a comparison says "too few to compare"

// The full runs before and the latest full run of a dataset's timeline: the default pair to compare.
export function defaultPair(timeline) {
  const full=timeline.filter(point=>!point.subset);
  return full.length>=2?[full.at(-2).experiment,full.at(-1).experiment]:timeline.length>=2?[timeline.at(-2).experiment,timeline.at(-1).experiment]:[null,null];
}

// Side-by-side numbers of two prompt versions; `enough` is false below MIN_COMPARE on either side.
export function versionPair(a,b) {
  const row=(label,pick,kind)=>({label,a:pick(a),b:pick(b),kind});
  const rows=[row('Live calls',v=>v?.live_metrics?.calls??null,'count'),row('Live OK rate',v=>v?.live_metrics?.ok_rate??null,'rate'),
    row('Live p95',v=>v?.live_metrics?.p95_s??null,'seconds'),row('Input tokens',v=>v?.live_metrics?.input_tokens??null,'count'),
    row('Output tokens',v=>v?.live_metrics?.output_tokens??null,'count'),row('Eval cases',v=>v?.eval_metrics?.cases??null,'count'),
    row('Eval pass rate',v=>v?.eval_metrics?.pass_rate??null,'rate'),row('Judge (model-graded)',v=>v?.eval_metrics?.judge??null,'count')];
  const size=v=>Math.max(v?.live_metrics?.calls||0,v?.eval_metrics?.cases||0);
  return {rows,enough:size(a)>=MIN_COMPARE&&size(b)>=MIN_COMPARE};
}

// ---- Agents and latency ----
export const loadAgents=(window,source,side,token,signal)=>get(`/agents${query({window,source,side})}`,token,signal);
export const loadLatency=(window,source,token,signal)=>get(`/latency${query({window,source})}`,token,signal);
export const SURFACES={all:'All requests',public:'Public chat',personal:'Signed-in chat',rm:'RM assistant'};

// A count map as rows, largest first, each with its share of the total.
export function mix(counts) {
  const entries=Object.entries(counts||{}).filter(([,count])=>count>0);
  const total=entries.reduce((sum,[,count])=>sum+count,0);
  return entries.sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])).map(([key,count])=>({key,count,share:total?count/total:0}));
}

// Bars of a waterfall: each traced step's offset and length as a share of the request's time (steps without a start are left out).
export function waterfall(spans,limit=60) {
  const timed=(spans||[]).filter(span=>span.start_s!==null&&span.start_s!==undefined&&span.latency_s!==null&&span.latency_s!==undefined);
  const total=Math.max(...timed.map(span=>span.start_s+span.latency_s),0);
  if(!total)return {total:0,rows:[]};
  return {total,rows:timed.slice(0,limit).map(span=>({...span,left:Math.max(span.start_s,0)/total,width:Math.max(span.latency_s/total,0.004)}))};
}

// {'rm_ticket: not_in_book':2,'rm_tickets: limit':1} -> 'rm_ticket: not_in_book ×2, rm_tickets: limit' (largest first).
export function tally(counts) {return mix(counts).map(row=>row.count>1?`${row.key} ×${row.count}`:row.key).join(', ');}

// ['a','b','a'] -> 'a ×2, b' (agents a request ran, in first-seen order).
export function counted(names) {
  const found=new Map();for(const name of names||[])found.set(name,(found.get(name)||0)+1);
  return [...found].map(([name,count])=>count>1?`${name} ×${count}`:name).join(', ');
}
