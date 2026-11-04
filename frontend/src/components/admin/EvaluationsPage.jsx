import {Fragment,useState} from 'react';
import Icon from '../Icon';
import {formatDateTime} from '../../lib/format';
import {loadExperiment,loadExperiments,scoreLabel,seconds,summary} from '../../lib/admin';
import {Failed,Loading,Page,Status,useLoad} from './shared';

// One experiment's cases: rule status, failed checks, the model-graded judge score, and the run's answer and trajectory.
export function ExperimentCases({name}) {
  const [cases]=useLoad((auth,signal)=>loadExperiment(name,auth,signal),name);
  const [open,setOpen]=useState(null);
  const counts=summary(cases.data||[]);
  return <>
    {cases.busy&&<p className="loading">Loading cases…</p>}
    <Failed error={cases.error}/>
    {cases.data&&<>
      <p className="admin-summary" data-testid="admin-case-summary">Rule checks: {counts.pass} pass · {counts.fail} fail · {counts.skipped} skipped · {counts.error} error. Judge scores are model-graded and need review.</p>
      <div className="admin-table-wrap"><table className="admin-table">
        <thead><tr><th>Case</th><th>Rule status</th><th>Failed rule checks</th><th>Judge / 1</th><th>Agents</th><th>Model calls</th><th>Time</th></tr></thead>
        <tbody>{cases.data.map(item=><Fragment key={item.run_id}>
          <tr className="admin-row" data-testid={`admin-case-${item.case_id}`} onClick={()=>setOpen(open===item.run_id?null:item.run_id)} aria-expanded={open===item.run_id}>
            <td><button className="link-button">{item.case_id||'—'}</button></td><td><Status value={item.status}/></td>
            <td>{item.checks.filter(check=>!check.key.startsWith('judge')&&check.score===0).map(check=>check.key).join(', ')||'—'}</td>
            <td>{item.checks.find(check=>check.key==='judge_rubric')?.score??'—'}</td>
            <td>{Object.entries(item.agents).map(([agent,count])=>`${agent}×${count}`).join(', ')||'—'}</td>
            <td>{item.model_calls??'—'}</td><td>{seconds(item.latency_s)}</td></tr>
          {open===item.run_id&&<tr className="admin-detail"><td colSpan={7}>
            <ul className="admin-checks">{item.checks.map(check=><li key={check.key}><Status value={scoreLabel(check.score)}/> <strong>{check.key}</strong>{check.comment?` — ${check.comment}`:''}</li>)}</ul>
            {item.tools.length>0&&<p><strong>Tools:</strong> {item.tools.join(', ')}</p>}
            {item.service_reads?.length>0&&<p><strong>Record reads:</strong> {item.service_reads.join(', ')}</p>}
            {item.error&&<p className="form-error">{item.error}</p>}
            {item.answer&&<pre className="admin-answer">{item.answer}</pre>}
          </td></tr>}
        </Fragment>)}</tbody></table></div></>}
  </>;
}

export default function EvaluationsPage() {
  const [list,refresh]=useLoad(loadExperiments,'all');
  const [chosen,setChosen]=useState(null);
  const name=chosen||list.data?.[0]?.name||null;
  const picked=list.data?.find(item=>item.name===name);
  return <Page id="evaluations" title="Evaluations" intro="LangSmith experiments: each case played through the real app and scored by rule checks (and an optional model judge)."
    actions={<button className="bank-chat-logout" onClick={refresh} disabled={list.busy}><Icon name="refresh"/>Refresh</button>}>
    <Failed error={list.error}/>
    {list.busy&&!list.data&&<Loading label="Loading experiments"/>}
    {list.data&&!list.data.length&&<p className="admin-empty">No experiments yet. Run <code>python -m evaluation.langsmith_run</code> first.</p>}
    {list.data?.length>0&&<section className="admin-card">
      <div className="admin-toolbar">
        <label>Experiment <select value={name||''} onChange={event=>setChosen(event.target.value)}>
          {list.data.map(item=><option key={item.name} value={item.name}>{item.name} · {formatDateTime(item.started_at)}</option>)}</select></label>
        {picked?.url&&<a className="bank-chat-logout" href={picked.url} target="_blank" rel="noreferrer"><Icon name="arrow"/>Open in LangSmith</a>}
      </div>
      {picked&&<p className="admin-muted">{picked.dataset} · pipeline {picked.pipeline||'—'} · model {picked.model||'—'} · prompt {picked.answer_prompt||'—'}{picked.judge_model?` · judge ${picked.judge_model}`:''}</p>}
      {name&&<ExperimentCases name={name}/>}
    </section>}
  </Page>;
}
