import {Fragment,useState} from 'react';
import Icon from '../Icon';
import {SIDES,SOURCES,loadAgents,mix,number,percent,seconds} from '../../lib/admin';
import {WindowPicker} from './OverviewPage';
import {Failed,Loading,Page,useLoad} from './shared';

const OUTCOME_TONE={ok:'pass',needs_human:'info',needs_clarification:'info',out_of_scope:'info',withheld:'withheld',timeout:'fail',error:'fail'};

function Mix({counts,tones={}}) {
  const rows=mix(counts);
  if(!rows.length)return <span className="admin-muted">—</span>;
  return <span className="admin-pills">{rows.map(row=><span key={row.key} className={`admin-status admin-${tones[row.key]||'na'}`} title={`${row.count} (${percent(row.share)})`}>
    {row.key.replaceAll('_',' ')} {row.count}</span>)}</span>;
}

export function SourcePicker({value,onChange}) {
  return <label>Source <select value={value} onChange={event=>onChange(event.target.value)} data-testid="admin-source">
    {SOURCES.map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>;
}

export function SidePicker({value,onChange}) {
  return <label>Asked from <select value={value} onChange={event=>onChange(event.target.value)} data-testid="admin-side">
    {SIDES.map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>;
}

function Kv({rows}) {
  return <dl className="admin-kv">{rows.map(([label,value])=><Fragment key={label}><dt>{label}</dt><dd>{value}</dd></Fragment>)}</dl>;
}

function Planner({data}) {
  const sides=[['client','Client planner'],['rm','RM planner']].filter(([key])=>data[key].plans);
  return <section className="admin-card" data-testid="admin-planner">
    <div className="admin-card-head"><h2>Planner</h2><span className="admin-muted">How each turn was routed and split into agent tasks</span></div>
    {!sides.length&&<p className="admin-empty">No planner runs in this window.</p>}
    <div className="admin-grid">{sides.map(([key,label])=>{const side=data[key];return <div key={key}>
      <h3 className="admin-subhead">{label}</h3>
      <Kv rows={[['Plans',side.plans],['Routes',<Mix key="r" counts={side.routes}/>],['Multi-agent turns',percent(side.multi_agent)]]}/>
    </div>;})}</div>
  </section>;
}

function Offers({offers}) {
  const accepted=offers.offered?offers.confirmed/offers.offered:null;
  return <section className="admin-card" data-testid="admin-offers">
    <div className="admin-card-head"><h2>Offers</h2><span className="admin-muted">Do people accept what the agents propose?</span></div>
    <Kv rows={[['Replies with an offer',offers.offered??'not recorded'],['Confirmed',offers.confirmed],['Cancelled',offers.cancelled],
      ['Expired before a reply',offers.expired],['Failed when confirmed',offers.failed??'not recorded'],
      ['Accepted',accepted===null?'—':percent(accepted)]]}/>
  </section>;
}

function ToolErrors({tools}) {
  const failing=tools.filter(row=>row.errors);
  return <section className="admin-card" data-testid="admin-tools">
    <div className="admin-card-head"><h2>Tool errors</h2><span className="admin-muted">Record reads and knowledge retrieval that failed</span></div>
    {!failing.length?<p className="admin-empty">{tools.length?`No errors in ${tools.reduce((sum,row)=>sum+row.calls,0)} tool calls.`:'No tool calls in this window.'}</p>:
      <div className="admin-table-wrap"><table className="admin-table">
        <thead><tr><th>Tool</th><th className="num">Calls</th><th className="num">Errors</th><th>Types</th></tr></thead>
        <tbody>{failing.map(row=><tr key={row.tool}><td className="admin-mono">{row.tool}</td><td className="num">{row.calls}</td>
          <td className="num">{row.errors} ({percent(row.error_rate)})</td><td><Mix counts={row.error_types}/></td></tr>)}</tbody></table></div>}
  </section>;
}

function Gather({gather}) {
  return <section className="admin-card" data-testid="admin-gather">
    <div className="admin-card-head"><h2>Dependent tasks</h2><span className="admin-muted">Turns where one agent waited for another's result</span></div>
    <Kv rows={[['Turns with a second wave',gather.second_wave],['Dependent agents sent',gather.sends],
      ['Skipped dependants',<Mix key="s" counts={gather.skipped}/>],['Handed to another agent',<Mix key="h" counts={gather.rerouted}/>]]}/>
  </section>;
}

function AgentRow({agent}) {
  const [open,setOpen]=useState(false);
  return <>
    <tr data-testid={`admin-agent-${agent.agent}`}>
      <td><button className="link-button" aria-expanded={open} onClick={()=>setOpen(!open)}>{open?'▾':'▸'} <strong>{agent.agent}</strong></button>
        {agent.side==='rm'&&<span className="admin-muted"> RM</span>}{agent.side==='meeting'&&<span className="admin-muted"> Meeting</span>}</td>
      <td className="num">{agent.runs}</td><td><Mix counts={agent.outcomes} tones={OUTCOME_TONE}/></td>
      <td className="num">{agent.tool_refusals??'—'} / {agent.consent_gaps??'—'}</td>
      <td className="num">{seconds(agent.p50_s)} / {seconds(agent.p95_s)}</td>
    </tr>
    {open&&<tr className="admin-detail-row"><td colSpan={5}>
      <Kv rows={[['Path',<Mix key="p" counts={agent.paths}/>],['Tool calls per loop',number(agent.tool_calls_per_loop)],
        ['Loop stops',<Mix key="l" counts={agent.stops} tones={{answered:'pass',cap:'withheld',malformed:'fail',timeout:'fail',error:'fail'}}/>],
        ['Evidence per answer',number(agent.evidence_per_answer)],
        ['Prompt',agent.prompt?`${agent.prompt} ${agent.live_prompt||''}`:'—']]}/>
    </td></tr>}
  </>;
}

// How well each agent handles its requests, and how the orchestration around them behaves.
export default function AgentsPage() {
  const [range,setRange]=useState('7d');
  const [source,setSource]=useState('all');
  const [side,setSide]=useState('all');
  const [state,refresh]=useLoad((auth,signal)=>loadAgents(range,source,side,auth,signal),`${range}|${source}|${side}`);
  const data=state.data;
  const runs=data?data.agents.reduce((sum,agent)=>sum+agent.runs,0):0;
  const bad=data?data.agents.reduce((sum,agent)=>sum+(agent.outcomes.withheld||0)+(agent.outcomes.timeout||0)+(agent.outcomes.error||0),0):0;
  return <Page id="agents" title="Agents" intro="How each agent behaved on real and evaluation requests in this window: outcomes, refusals and time. Correctness against fixed cases is on Evaluations."
    actions={<div className="admin-controls"><WindowPicker value={range} onChange={setRange}/><SourcePicker value={source} onChange={setSource}/>
      <SidePicker value={side} onChange={setSide}/>
      <button className="bank-chat-logout" onClick={refresh} disabled={state.busy}><Icon name="refresh"/>Refresh</button></div>}>
    <Failed error={state.error}/>
    {state.busy&&!data&&<><Loading label="Loading agents"/><p className="admin-muted">Reading LangSmith. With evaluations, the first load after a restart can take up to a minute; later loads are cached.</p></>}
    {data&&<>
      <div className="admin-tiles">
        <div className="admin-tile"><span>Agent runs</span><strong>{runs}</strong><small>{data.agents.length} agents</small></div>
        <div className={`admin-tile${bad?' alert':''}`}><span>Degraded agent runs</span><strong>{percent(runs?bad/runs:null)}</strong><small>withheld, timed out or failed</small></div>
      </div>
      {data.capped&&<p className="admin-muted">Sampled: this window has more runs than the dashboard reads at once.</p>}

      <section className="admin-card" data-testid="admin-scorecards">
        <div className="admin-card-head"><h2>Agent scorecards</h2><span className="admin-muted">Withheld, timed out and failed runs are degraded; open a row for its path, loop and evidence. Pass rates are on Evaluations.</span></div>
        {!data.agents.length?<p className="admin-empty">No agent runs in this window.</p>:<div className="admin-table-wrap"><table className="admin-table">
          <thead><tr><th>Agent</th><th className="num">Runs</th><th>Outcomes</th>
            <th className="num" title="Tool calls refused (outside the agent's tools, its limits or the RM's book) / consent-gated reads the client has not shared">Refused / gaps</th>
            <th className="num">p50 / p95</th></tr></thead>
          <tbody>{data.agents.map(agent=><AgentRow key={agent.agent} agent={agent}/>)}</tbody></table></div>}
      </section>

      <Planner data={data.planner}/>
      <div className="admin-grid"><Offers offers={data.offers}/><ToolErrors tools={data.tools}/></div>
      {data.gather.second_wave>0&&<Gather gather={data.gather}/>}
    </>}
  </Page>;
}
