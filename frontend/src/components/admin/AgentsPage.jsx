import {Fragment,useState} from 'react';
import Icon from '../Icon';
import {formatDate} from '../../lib/format';
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

function Planner({data,onNavigate}) {
  const sides=[['client','Client planner'],['rm','RM planner']].filter(([key])=>data[key].plans);
  return <section className="admin-card" data-testid="admin-planner">
    <div className="admin-card-head"><h2>Planner</h2><span className="admin-muted">How each turn was routed and split into agent tasks</span></div>
    {!sides.length&&<p className="admin-empty">No planner runs in this window.</p>}
    <div className="admin-grid">{sides.map(([key,label])=>{const side=data[key];return <div key={key}>
      <h3 className="admin-subhead">{label}</h3>
      <Kv rows={[['Plans',side.plans],['Routes',<Mix key="r" counts={side.routes}/>],['Tasks per plan',number(side.tasks_per_plan)],
        ['Multi-agent turns',percent(side.multi_agent)],['With a dependent task',percent(side.dependent)],['p50 / p95',`${seconds(side.p50_s)} / ${seconds(side.p95_s)}`]]}/>
    </div>;})}</div>
    {data.accuracy?<p className="admin-muted">Planner dataset: {percent(data.accuracy.pass_rate)} of {data.accuracy.cases} cases on {formatDate(data.accuracy.started_at)}
      {Object.keys(data.accuracy.checks).length>0&&<> ({Object.entries(data.accuracy.checks).map(([check,rate])=>`${check} ${percent(rate)}`).join(' · ')})</>}
      {' '}<button className="link-button" onClick={()=>onNavigate('evaluations',{dataset:'demobank-planner'})}>Open the planner evaluations</button></p>
      :<p className="admin-muted">No full run of the planner dataset yet.</p>}
  </section>;
}

function Orchestration({gather,offers}) {
  const accepted=offers.offered?offers.confirmed/offers.offered:null;
  return <div className="admin-grid">
    <section className="admin-card" data-testid="admin-gather">
      <div className="admin-card-head"><h2>Dependencies (gather)</h2></div>
      <Kv rows={[['Turns gathered',gather.gathers],['With a second wave',gather.second_wave],['Dependent agents sent',gather.sends],
        ['Skipped dependants',<Mix key="s" counts={gather.skipped}/>],['Handed to another agent',<Mix key="h" counts={gather.rerouted}/>]]}/>
      {Object.keys(gather.exports).length>0&&<><p className="admin-muted">Exports passed (from &gt; to: name)</p>
        <ul className="admin-plain">{mix(gather.exports).map(row=><li key={row.key} className="admin-mono">{row.key} · {row.count}</li>)}</ul></>}
    </section>
    <section className="admin-card" data-testid="admin-offers">
      <div className="admin-card-head"><h2>Offers</h2><span className="admin-muted">Do people accept what the agents propose?</span></div>
      <Kv rows={[['Replies with an offer',offers.offered??'not recorded'],['Confirmed',offers.confirmed],['Cancelled',offers.cancelled],
        ['Expired before a reply',offers.expired],['Failed when confirmed',offers.failed??'not recorded'],['Chained steps',offers.chained],
        ['Accepted',accepted===null?'—':percent(accepted)]]}/>
      {offers.offered===null&&<p className="admin-muted">Requests before this dashboard did not record their offers; confirmations and expiries are counted from the turns that answered them.</p>}
    </section>
  </div>;
}

function Tools({tools,knowledge}) {
  return <div className="admin-grid">
    <section className="admin-card" data-testid="admin-tools">
      <div className="admin-card-head"><h2>Tools</h2><span className="admin-muted">Record reads and knowledge retrieval through the tool layer</span></div>
      {!tools.length?<p className="admin-empty">No tool calls in this window.</p>:<div className="admin-table-wrap"><table className="admin-table">
        <thead><tr><th>Tool</th><th className="num">Calls</th><th className="num">Errors</th><th className="num">p50 / p95</th></tr></thead>
        <tbody>{tools.map(row=><tr key={row.tool}><td className="admin-mono">{row.tool}</td><td className="num">{row.calls}</td>
          <td className="num" title={Object.entries(row.error_types).map(([type,count])=>`${type} ${count}`).join(', ')}>{row.errors?`${row.errors} (${percent(row.error_rate)})`:'0'}</td>
          <td className="num">{seconds(row.p50_s)} / {seconds(row.p95_s)}</td></tr>)}</tbody></table></div>}
    </section>
    <section className="admin-card" data-testid="admin-knowledge">
      <div className="admin-card-head"><h2>Knowledge search</h2></div>
      {!knowledge.searches?<p className="admin-empty">No knowledge searches in this window.</p>:
        <Kv rows={[['Searches',knowledge.searches],['Mode',<Mix key="m" counts={knowledge.modes} tones={{hybrid:'pass',keyword_only:'withheld'}}/>],
          ['Embedding errors',<Mix key="e" counts={knowledge.embedding_errors} tones={{}}/>],['No result (no evidence)',percent(knowledge.empty_rate)],
          ['p50 / p95',`${seconds(knowledge.p50_s)} / ${seconds(knowledge.p95_s)}`],['Embedding / SQL (p50)',`${number(knowledge.embed_ms_p50,0)} ms / ${number(knowledge.sql_ms_p50,0)} ms`]]}/>}
    </section>
  </div>;
}

const MEETING_ACTOR={assigned_rm:'Assigned RM',claimed_worker:'Worker with this meeting’s transcript claim'};
const MEETING_ACCESS={read:'Read',draft_write:'Save draft'};

function MeetingCatalogue({agents}) {
  return <section className="admin-card" data-testid="admin-meeting-catalogue">
    <div className="admin-card-head">
      <h2>Meeting workflow agents</h2>
      <span className="admin-muted">The workflow selects these tools and checks permissions for the current meeting.</span>
    </div>
    <div className="admin-table-wrap"><table className="admin-table">
      <thead><tr><th>Agent</th><th>Purpose</th><th>Permitted operations</th></tr></thead>
      <tbody>{agents.map(agent=><tr key={agent.agent}>
        <td className="admin-mono">{agent.agent}</td>
        <td>{agent.purpose}</td>
        <td>{agent.tools.length?<ul className="admin-plain">{agent.tools.map(tool=><li key={tool.name}>
          <code>{tool.name}</code><br/><span className="admin-muted">{MEETING_ACCESS[tool.access]} · {MEETING_ACTOR[tool.actor]}</span>
        </li>)}</ul>:'Tool-free'}</td>
      </tr>)}</tbody>
    </table></div>
  </section>;
}

// How well each agent handles its requests, and how the orchestration around them behaves.
export default function AgentsPage({onNavigate}) {
  const [range,setRange]=useState('7d');
  const [source,setSource]=useState('all');
  const [side,setSide]=useState('all');
  const [state,refresh]=useLoad((auth,signal)=>loadAgents(range,source,side,auth,signal),`${range}|${source}|${side}`);
  const data=state.data;
  const runs=data?data.agents.reduce((sum,agent)=>sum+agent.runs,0):0;
  const bad=data?data.agents.reduce((sum,agent)=>sum+(agent.outcomes.withheld||0)+(agent.outcomes.timeout||0)+(agent.outcomes.error||0),0):0;
  return <Page id="agents" title="Agents" intro="One scorecard per agent (outcomes, paths, tools, time, prompt and evaluation pass rate), then the planner, dependencies, offers, tools and knowledge search."
    actions={<div className="admin-controls"><WindowPicker value={range} onChange={setRange}/><SourcePicker value={source} onChange={setSource}/>
      <SidePicker value={side} onChange={setSide}/>
      <button className="bank-chat-logout" onClick={refresh} disabled={state.busy}><Icon name="refresh"/>Refresh</button></div>}>
    <Failed error={state.error}/>
    {state.busy&&!data&&<><Loading label="Loading agents"/><p className="admin-muted">Reading LangSmith. With evaluations, the first load after a restart can take up to a minute; later loads are cached.</p></>}
    {data&&<>
      <div className="admin-tiles">
        <div className="admin-tile"><span>Requests</span><strong>{data.requests}</strong><small>{source==='live'?'live':source==='eval'?'inside evaluations':'live and evaluations'}</small></div>
        <div className="admin-tile"><span>Agent runs</span><strong>{runs}</strong><small>{data.agents.length} agents</small></div>
        <div className={`admin-tile${bad?' alert':''}`}><span>Degraded agent runs</span><strong>{percent(runs?bad/runs:null)}</strong><small>withheld, timed out or failed</small></div>
        <div className="admin-tile"><span>Fixed safety routes</span><strong>{percent(data.fixed_routes.share)}</strong><small>{data.fixed_routes.requests} requests, no model</small></div>
      </div>
      {data.capped&&<p className="admin-muted">Sampled: this window has more runs than the dashboard reads at once.</p>}

      <section className="admin-card" data-testid="admin-scorecards">
        <div className="admin-card-head"><h2>Agent scorecards</h2><span className="admin-muted">Withheld, timed out and failed runs are degraded; handing to a person or asking which one is intended</span></div>
        {!data.agents.length?<p className="admin-empty">No agent runs in this window.</p>:<div className="admin-table-wrap"><table className="admin-table">
          <thead><tr><th>Agent</th><th className="num">Runs</th><th className="num">Requests</th><th>Outcomes</th><th>Path</th><th className="num">Tools / loop</th><th>Loop stops</th>
            <th className="num" title="Tool calls refused (outside the agent's tools, its limits or the RM's book) / consent-gated reads the client has not shared">Refused / gaps</th>
            <th className="num">Evidence</th><th className="num">p50 / p95</th><th>Prompt</th><th className="num">Eval pass</th></tr></thead>
          <tbody>{data.agents.map(agent=><tr key={agent.agent} data-testid={`admin-agent-${agent.agent}`}>
            <td><strong>{agent.agent}</strong>{agent.side==='rm'&&<span className="admin-muted"> RM</span>}{agent.side==='meeting'&&<span className="admin-muted"> Meeting</span>}</td><td className="num">{agent.runs}</td>
            <td className="num">{agent.side==='meeting'?'—':percent(agent.share)}</td><td><Mix counts={agent.outcomes} tones={OUTCOME_TONE}/></td>
            <td><Mix counts={agent.paths}/></td>
            <td className="num" title={agent.refused?`${agent.refused} tool calls refused`:''}>{number(agent.tool_calls_per_loop)}{agent.refused?` (${agent.refused} refused)`:''}</td>
            <td><Mix counts={agent.stops} tones={{answered:'pass',cap:'withheld',malformed:'fail',timeout:'fail',error:'fail'}}/></td>
            <td className="num">{agent.tool_refusals??'—'} / {agent.consent_gaps??'—'}</td>
            <td className="num" title={`${agent.exports} exports passed`}>{number(agent.evidence_per_answer)}</td>
            <td className="num">{seconds(agent.p50_s)} / {seconds(agent.p95_s)}</td>
            <td>{agent.prompt?<button className="link-button" onClick={()=>onNavigate('prompts')}>{agent.prompt} {agent.live_prompt||''}</button>:'—'}</td>
            <td className="num" title={`${agent.eval_cases} evaluation cases ran this agent`}>{agent.eval_cases?`${percent(agent.eval_pass_rate)} of ${agent.eval_cases}`:'—'}</td>
          </tr>)}</tbody></table></div>}
        {data.fixed_routes.requests>0&&<p className="admin-muted">Fixed safety routes (no agent, no model): <Mix counts={data.fixed_routes.routes}/></p>}
      </section>

      {data.meeting_catalogue?.length>0&&<MeetingCatalogue agents={data.meeting_catalogue}/>}
      <Planner data={data.planner} onNavigate={onNavigate}/>
      <Orchestration gather={data.gather} offers={data.offers}/>
      <Tools tools={data.tools} knowledge={data.knowledge}/>
    </>}
  </Page>;
}
