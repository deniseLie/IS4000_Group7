import {Fragment,useEffect,useRef,useState} from 'react';
import Icon from './Icon';
import {supabase} from '../lib/supabase';
import {formatDateTime} from '../lib/format';
import {loadExperiment,loadExperiments,loadTrace,loadTraces,scoreLabel,seconds,summary} from '../lib/quality';

async function token() {
  const {data,error}=await supabase.auth.getSession();if(error)throw error;
  if(!data.session)throw new Error('Please sign in again.');
  return data.session.access_token;
}

// Loads once per key; refresh() reloads. Errors are shown, never replaced with sample data.
function useLoad(load,key) {
  const [state,setState]=useState({data:null,error:'',busy:false});
  const active=useRef(null);
  async function refresh() {
    if(key===null)return;
    active.current?.abort();const controller=new AbortController();active.current=controller;
    setState(old=>({...old,busy:true,error:''}));
    try {const data=await load(await token(),controller.signal);if(!controller.signal.aborted)setState({data,error:'',busy:false});}
    catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setState({data:null,error:failure.message,busy:false});}
  }
  useEffect(()=>{refresh();return ()=>active.current?.abort();},[key]);  // eslint-disable-line react-hooks/exhaustive-deps
  return [state,refresh];
}

function Status({value}) {return <span className={`quality-status quality-${value}`}>{value}</span>;}

function Experiments() {
  const [list,refresh]=useLoad(loadExperiments,'all');
  const [chosen,setChosen]=useState(null);
  const name=chosen||list.data?.[0]?.name||null;
  const [cases]=useLoad((auth,signal)=>loadExperiment(name,auth,signal),name);
  const [open,setOpen]=useState(null);
  const counts=summary(cases.data||[]);
  const picked=list.data?.find(item=>item.name===name);
  return <section className="page-section" data-testid="quality-experiments">
    <div className="quality-toolbar">
      <label>Experiment <select value={name||''} onChange={event=>{setChosen(event.target.value);setOpen(null);}} disabled={!list.data?.length}>
        {(list.data||[]).map(item=><option key={item.name} value={item.name}>{item.name} · {formatDateTime(item.started_at)}</option>)}</select></label>
      <button className="bank-chat-logout" onClick={refresh} disabled={list.busy}><Icon name="refresh"/>Refresh</button>
      {picked?.url&&<a className="bank-chat-logout" href={picked.url} target="_blank" rel="noreferrer"><Icon name="arrow"/>Open in LangSmith</a>}
    </div>
    {list.error&&<p className="form-error" role="alert">{list.error}</p>}
    {list.data&&!list.data.length&&<p>No experiments yet. Run <code>python -m evaluation.langsmith_run</code> first.</p>}
    {picked&&<p className="quality-meta">{picked.dataset} · pipeline {picked.pipeline||'—'} · model {picked.model||'—'} · prompt {picked.answer_prompt||'—'}{picked.judge_model?` · judge ${picked.judge_model}`:''}</p>}
    {cases.busy&&<p className="loading">Loading cases…</p>}
    {cases.error&&<p className="form-error" role="alert">{cases.error}</p>}
    {cases.data&&<>
      <p className="quality-summary" data-testid="quality-summary">{counts.pass} pass · {counts.fail} fail · {counts.skipped} skipped · {counts.error} error</p>
      <div className="quality-table-wrap"><table className="quality-table">
        <thead><tr><th>Case</th><th>Status</th><th>Failed checks</th><th>Agents</th><th>Model calls</th><th>Time</th></tr></thead>
        <tbody>{cases.data.map(item=><Fragment key={item.run_id}>
          <tr className="quality-row" onClick={()=>setOpen(open===item.run_id?null:item.run_id)} aria-expanded={open===item.run_id}>
            <td><button className="link-button">{item.case_id||'—'}</button></td><td><Status value={item.status}/></td>
            <td>{item.checks.filter(check=>check.score===0).map(check=>check.key).join(', ')||'—'}</td>
            <td>{Object.entries(item.agents).map(([agent,count])=>`${agent}×${count}`).join(', ')||'—'}</td>
            <td>{item.model_calls??'—'}</td><td>{seconds(item.latency_s)}</td></tr>
          {open===item.run_id&&<tr className="quality-detail"><td colSpan={6}>
            <ul className="quality-checks">{item.checks.map(check=><li key={check.key}><Status value={scoreLabel(check.score)}/> <strong>{check.key}</strong>{check.comment?` — ${check.comment}`:''}</li>)}</ul>
            {item.tools.length>0&&<p><strong>Tools:</strong> {item.tools.join(', ')}</p>}
            {item.error&&<p className="form-error">{item.error}</p>}
            {item.answer&&<pre className="quality-answer">{item.answer}</pre>}
          </td></tr>}
        </Fragment>)}</tbody></table></div></>}
  </section>;
}

function Traces() {
  const [list,refresh]=useLoad(loadTraces,'all');
  const [open,setOpen]=useState(null);
  const [spans]=useLoad((auth,signal)=>loadTrace(open,auth,signal),open);
  return <section className="page-section" data-testid="quality-traces">
    <div className="quality-toolbar"><span>Latest assistant requests</span>
      <button className="bank-chat-logout" onClick={refresh} disabled={list.busy}><Icon name="refresh"/>Refresh</button></div>
    {list.error&&<p className="form-error" role="alert">{list.error}</p>}
    {list.busy&&!list.data&&<p className="loading">Loading traces…</p>}
    {list.data&&<div className="quality-table-wrap"><table className="quality-table">
      <thead><tr><th>When</th><th>Mode</th><th>Status</th><th>Time</th><th>Tokens</th></tr></thead>
      <tbody>{list.data.map(item=><Fragment key={item.run_id}>
        <tr className="quality-row" onClick={()=>setOpen(open===item.run_id?null:item.run_id)} aria-expanded={open===item.run_id}>
          <td><button className="link-button">{formatDateTime(item.started_at)}</button></td><td>{item.mode||'—'}</td>
          <td><Status value={item.error?'error':item.status||'—'}/></td><td>{seconds(item.latency_s)}</td><td>{item.tokens??'—'}</td></tr>
        {open===item.run_id&&<tr className="quality-detail"><td colSpan={5}>
          {spans.busy&&<p className="loading">Loading steps…</p>}
          {spans.error&&<p className="form-error">{spans.error}</p>}
          {spans.data&&<ol className="quality-spans">{spans.data.map((span,index)=><li key={index} style={{paddingLeft:`${span.depth*14}px`}}>
            <span className="quality-span-type">{span.run_type}</span> {span.name}{span.agent?` · ${span.agent}`:''}{span.tool&&span.tool!==span.name?` · ${span.tool}`:''}
            <span className="quality-span-time"> {seconds(span.latency_s)}{span.tokens?` · ${span.tokens} tokens`:''}</span>
            {span.error&&<span className="form-error"> {span.error}</span>}</li>)}</ol>}
        </td></tr>}
      </Fragment>)}</tbody></table></div>}
  </section>;
}

// Team-only: evaluation experiments and live /chat traces from LangSmith, read through the backend.
export default function QualityPage() {
  const [tab,setTab]=useState('experiments');
  return <div className="app-page-body" data-testid="quality-page">
    <header className="page-header"><div><span className="eyebrow">Team</span><h1>Quality</h1>
      <p>Evaluation results and recent assistant traces from LangSmith. Synthetic data, redacted before upload. Model-graded scores are an aid to review, not proof.</p></div></header>
    <div className="quality-tabs" role="tablist">
      {[['experiments','Evaluations'],['traces','Traces']].map(([key,label])=><button key={key} role="tab" aria-selected={tab===key} className={tab===key?'sidebar-current':''} onClick={()=>setTab(key)}>{label}</button>)}
    </div>
    {tab==='experiments'?<Experiments/>:<Traces/>}
  </div>;
}
