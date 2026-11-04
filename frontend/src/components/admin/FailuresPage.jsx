import {useState} from 'react';
import Icon from '../Icon';
import {formatDateTime} from '../../lib/format';
import {KINDS,SOURCES,filterGroups,firstLine,kindCounts,loadFailure,loadFailures,seconds} from '../../lib/admin';
import {History} from './charts';
import {WindowPicker} from './OverviewPage';
import {Failed,Loading,Page,Status,useLoad} from './shared';

function Field({label,children}) {return children===null||children===undefined||children===''||children===false?null:<><dt>{label}</dt><dd>{children}</dd></>;}
function ErrorText({text}) {
  const head=firstLine(text);
  if(!head)return null;
  return head===text.trim()?<span className="admin-mono">{head}</span>:
    <details className="admin-error"><summary className="admin-mono">{head}</summary><pre className="admin-answer admin-mono">{text}</pre></details>;
}
const TONE={held_back:'withheld',agent_withheld:'withheld',agent_timeout:'timeout'};
const yesNo=value=>value===true?'yes':value===false?'no':value;

// Everything needed to debug one failing request or evaluation case without opening LangSmith.
export function FailureCard({runId,onClose}) {
  const [state]=useLoad((auth,signal)=>loadFailure(runId,auth,signal),runId);
  const card=state.data;
  return <section className="admin-card admin-failure-card" data-testid="admin-failure-card" aria-live="polite">
    <div className="admin-card-head"><h2>Failure details</h2>
      <div className="admin-controls">{card?.url&&<a className="bank-chat-logout" href={card.url} target="_blank" rel="noreferrer"><Icon name="arrow"/>Open in LangSmith</a>}
        <button className="bank-chat-logout" onClick={onClose}><Icon name="close"/>Close</button></div></div>
    {state.busy&&<Loading label="Loading the trace"/>}
    <Failed error={state.error}/>
    {card&&<div className="admin-card-body">
      <p className="admin-muted"><Status value={card.source}/> {card.started_at&&formatDateTime(card.started_at)}{card.capped?' · trace cut at 500 runs':''}</p>

      <h3>What was asked</h3>
      {card.asked.length?card.asked.map((text,index)=><blockquote key={index} className="admin-quote">{text}</blockquote>):<p className="admin-muted">No question was recorded.</p>}

      <h3>Where it failed</h3>
      {card.failure?<dl className="admin-kv" data-testid="admin-failure-origin">
        <Field label="Step">{card.failure.step}</Field><Field label="Agent">{card.failure.agent}</Field><Field label="Tool">{card.failure.tool}</Field>
        <Field label="Status">{card.failure.status||card.failure.outcome}</Field><Field label="Cause">{card.failure.cause}</Field>
        <Field label="Error type">{card.failure.error_type}</Field><Field label="Retries">{card.failure.retries}</Field>
        <Field label="Checks">{card.failure.reject_reason}</Field><Field label="Error">{card.failure.error&&<ErrorText text={card.failure.error}/>}</Field>
      </dl>:<p className="admin-muted">No step failed in this trace{card.eval?': the case failed its checks (below).':'.'}</p>}

      {(card.plan.route||card.plan.agents.length>0)&&<><h3>Plan</h3>
        <p className="admin-muted">Route {card.plan.route||'—'}{card.plan.tasks!=null?` · ${card.plan.tasks} tasks`:''}</p>
        {card.plan.agents.length>0&&<div className="admin-table-wrap"><table className="admin-table">
          <thead><tr><th>Agent</th><th>Intent</th><th>Status</th><th>Path</th><th className="num">Wave</th><th className="num">Tool calls</th><th className="num">Time</th></tr></thead>
          <tbody>{card.plan.agents.map((agent,index)=><tr key={index}><td>{agent.agent}</td><td>{agent.intent||'—'}</td><td><Status value={agent.status}/></td>
            <td>{agent.path||'—'}</td><td className="num">{agent.wave??'—'}</td><td className="num">{agent.tool_calls??'—'}</td><td className="num">{seconds(agent.latency_s)}</td></tr>)}</tbody>
        </table></div>}</>}

      <h3>Steps</h3>
      <ol className="admin-spans" data-testid="admin-failure-steps">{card.steps.map(step=><li key={step.id} className={step.failing?'failing':''} style={{paddingLeft:`${step.depth*14}px`}}>
        <span className="admin-span-type">{step.kind}</span> {step.name}{step.agent&&step.name!=='llm'?` · ${step.agent}`:''}{step.prompt?` · ${step.prompt}`:''}{step.tool?` · ${step.tool}`:''}
        <span className="admin-muted"> {seconds(step.latency_s)}{step.status&&step.status!=='success'?` · ${step.status}`:''}</span>
        {step.origin&&<strong className="admin-origin"> ← failed here</strong>}
        {step.error&&<div className="form-error admin-mono">{firstLine(step.error)}</div>}
      </li>)}</ol>

      {card.saw&&<><h3>What the user saw</h3>
        {card.saw.answer?<blockquote className="admin-quote">{card.saw.answer}</blockquote>:<p className="admin-muted">No answer text.</p>}
        <dl className="admin-kv"><Field label="Origin">{card.saw.origin}</Field><Field label="Handed to a person">{yesNo(card.saw.needs_human)}</Field>
          <Field label="Source check">{card.saw.source_check}</Field><Field label="Sources">{card.saw.sources?.join(', ')}</Field>
          <Field label="Error">{card.saw.error&&firstLine(card.saw.error)}</Field></dl></>}

      {card.turns.length>1&&<><h3>Conversation ({card.turns.length} turns)</h3>
        <ol className="admin-turns">{card.turns.map((turn,index)=><li key={index}><strong>{turn.question||'—'}</strong>
          <span className="admin-muted"> {turn.origin||''}{turn.route?` · ${turn.route}`:''} · {seconds(turn.latency_s)}</span>
          {turn.answer&&<p>{turn.answer}</p>}</li>)}</ol></>}

      {card.eval&&<><h3>Evaluation case</h3>
        <dl className="admin-kv"><Field label="Case">{card.eval.case_id} <Status value={card.eval.status||'—'}/></Field>
          <Field label="Dataset">{card.eval.dataset}</Field>
          <Field label="Experiment">{card.eval.experiment_url?<a href={card.eval.experiment_url} target="_blank" rel="noreferrer">{card.eval.experiment}</a>:card.eval.experiment}</Field></dl>
        <ul className="admin-checks">{card.eval.checks.map(check=><li key={check.key}><Status value={check.score===1?'pass':check.score===0?'fail':check.score==null?'na':String(check.score)}/> <strong>{check.key}</strong>{check.comment?` — ${check.comment}`:''}</li>)}</ul>
        {Object.keys(card.eval.expected).length>0&&<><p className="admin-muted">Expected (from the dataset)</p>
          <pre className="admin-answer">{JSON.stringify(card.eval.expected,null,2)}</pre></>}
        {card.eval.repro&&<><p className="admin-muted">Re-run this case</p><pre className="admin-answer admin-mono">{card.eval.repro}</pre></>}</>}

      <h3>Configuration</h3>
      <dl className="admin-kv"><Field label="Models">{card.config.models.join(', ')}</Field><Field label="Transport">{card.config.transports.join(', ')}</Field>
        <Field label="Prompts">{card.config.prompts.join(', ')}</Field><Field label="Pipeline">{card.config.pipeline}</Field>
        <Field label="App version">{card.config.app_version}</Field></dl>
      {card.repro&&<p className="admin-muted">To reproduce, ask the same question in the app: “{card.repro}”</p>}
    </div>}
  </section>;
}

function Group({group,open,onToggle,chosen,onChoose}) {
  return <li className={`admin-group${open?' open':''}`} data-testid={`admin-group-${group.kind}`}>
    <button className="admin-group-head" onClick={onToggle} aria-expanded={open}>
      <span className={`admin-status admin-${TONE[group.kind]||'fail'}`}>{KINDS[group.kind]||group.kind}</span>
      <span><span className="admin-group-title"><strong>{group.title}</strong>{group.new&&<span className="admin-pill admin-new">new</span>}</span>
        <small className="admin-muted">{group.source==='eval'?'Evaluation':'Live'}{group.dataset&&group.kind!=='eval_case'?` · ${group.dataset}`:''}
          {Object.keys(group.surfaces).length>0?` · ${Object.entries(group.surfaces).map(([surface,count])=>`${surface} ${count}`).join(', ')}`:''}
          {` · last ${formatDateTime(group.last_seen)}`}</small></span>
      {group.history?.length>0&&<History statuses={group.history} label="Last runs of this case"/>}
      <b>{group.in_window}×</b>
    </button>
    {open&&<ul className="admin-items">{group.items.map(item=><li key={`${item.trace_id}-${item.step_id||''}`} className={chosen===item.trace_id?'chosen':''}>
      <button className="admin-item" onClick={()=>onChoose(item.trace_id)} data-testid="admin-failure-item">
        <span className="admin-muted">{formatDateTime(item.started_at)}{item.surface?` · ${item.surface}`:''}{item.case_id?` · ${item.case_id}`:''}</span>
        <span>{item.question||<em className="admin-muted">no question recorded</em>}</span>
        {item.detail&&<small className="admin-mono">{item.detail}</small>}
      </button></li>)}
      {group.count>group.items.length&&<li className="admin-muted">Showing the latest {group.items.length} of {group.count}.</li>}
    </ul>}
  </li>;
}

// Failures grouped by what went wrong (kind, agent or tool, error, prompt); each item opens the debug card.
export default function FailuresPage({filter}) {
  const [range,setRange]=useState('7d');
  const [source,setSource]=useState('all');
  const [kind,setKind]=useState('');
  const [text,setText]=useState('');
  const [open,setOpen]=useState(filter?.signature||null);
  const [chosen,setChosen]=useState(null);
  const [state,refresh]=useLoad((auth,signal)=>loadFailures(range,source,auth,signal),`${range}|${source}`);
  const data=state.data,groups=data?filterGroups(data.groups,{kind,text}):[],counts=data?kindCounts(data.groups):{};
  return <Page id="failures" title="Failures" intro="Failed requests, withheld answers, agent, model and tool errors, and failing evaluation cases, grouped by what went wrong."
    actions={<div className="admin-controls"><WindowPicker value={range} onChange={setRange}/>
      <label>Source <select value={source} onChange={event=>setSource(event.target.value)} data-testid="admin-source">
        {SOURCES.map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
      <button className="bank-chat-logout" onClick={refresh} disabled={state.busy}><Icon name="refresh"/>Refresh</button></div>}>
    <Failed error={state.error}/>
    {state.busy&&!data&&<><Loading label="Loading failures"/><p className="admin-muted">Reading LangSmith. With evaluations, the first load after a restart can take up to a minute (LangSmith rate-limits run queries); later loads are cached.</p></>}
    {data&&<>
      <div className="admin-toolbar">
        <div className="admin-tabs" role="tablist">
          <button role="tab" aria-selected={!kind} onClick={()=>setKind('')}>All {data.groups.reduce((sum,group)=>sum+group.in_window,0)}</button>
          {Object.entries(counts).map(([key,count])=><button key={key} role="tab" aria-selected={kind===key} onClick={()=>setKind(key)}>{KINDS[key]||key} {count}</button>)}
        </div>
        <input className="admin-search" type="search" placeholder="Search question, error, agent or case" value={text} onChange={event=>setText(event.target.value)} aria-label="Search failures"/>
      </div>
      {data.refusals>0&&<p className="admin-muted">{data.refusals} RM request{data.refusals===1?' was':'s were'} refused as outside the RM's book: intended, not counted as failures.</p>}
      {data.capped&&<p className="admin-muted">Sampled: this window has more runs than the dashboard reads at once.</p>}
      {chosen&&<FailureCard runId={chosen} onClose={()=>setChosen(null)}/>}
      {!data.groups.length&&<p className="admin-empty" data-testid="admin-no-failures">No failures in this window.</p>}
      {data.groups.length>0&&!groups.length&&<p className="admin-empty">No failure matches this filter.</p>}
      <ul className="admin-groups">{groups.map(group=><Group key={group.signature} group={group} open={open===group.signature}
        onToggle={()=>setOpen(open===group.signature?null:group.signature)} chosen={chosen} onChoose={id=>{setChosen(id);window.scrollTo?.({top:0,behavior:'smooth'});}}/>)}</ul>
    </>}
  </Page>;
}
