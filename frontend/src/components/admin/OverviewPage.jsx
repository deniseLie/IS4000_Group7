import {useState} from 'react';
import Icon from '../Icon';
import {formatDate,formatDateTime} from '../../lib/format';
import {WINDOWS,delta,loadOverview,number,percent} from '../../lib/admin';
import {Sparkline} from './charts';
import {Failed,Loading,Page,Status,useLoad} from './shared';

function Tile({label,value,note,change,spark,alert,testid}) {
  return <div className={`admin-tile${alert?' alert':''}`} data-testid={testid}>
    <span>{label}</span><strong>{value}</strong>
    <small>{change&&<b className={`admin-delta ${change.tone}`}>{change.text}</b>}{change&&note?' · ':''}{note}</small>
    {spark}
  </div>;
}

export function WindowPicker({value,onChange}) {
  return <label>Window <select value={value} onChange={event=>onChange(event.target.value)} data-testid="admin-window">
    {WINDOWS.map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>;
}

// What an AI engineer checks first: is anything failing, how well the agents answer, and how the evaluations stand.
export default function OverviewPage({onNavigate,views=[]}) {
  const [range,setRange]=useState('7d');
  const [state,refresh]=useLoad((auth,signal)=>loadOverview(range,auth,signal),range);
  const data=state.data,now=data?.live.now,before=data?.live.previous,series=data?.live.series||[];
  return <Page id="overview" title="Overview" intro="Live client and RM requests from the app's LangSmith project, the latest evaluation runs, and what needs attention."
    actions={<div className="admin-controls"><WindowPicker value={range} onChange={setRange}/>
      <button className="bank-chat-logout" onClick={refresh} disabled={state.busy}><Icon name="refresh"/>Refresh</button></div>}>
    <Failed error={state.error}/>
    {state.busy&&!data&&<><Loading label="Loading the overview"/><p className="admin-muted">Reading LangSmith. The first load after a restart takes a few seconds; later loads are cached.</p></>}
    {data&&<>
      <div className="admin-tiles" data-testid="admin-tiles">
        <Tile testid="admin-tile-requests" label="Requests" value={now.requests} note={`${now.client} client · ${now.rm} RM`}
          change={delta(now.requests,before.requests,{kind:'count',better:''})} spark={<Sparkline values={series.map(b=>b.requests)} label="Requests over the window"/>}/>
        <Tile testid="admin-tile-success" label="Success rate" value={percent(now.success_rate,1)} note={`${now.failed} failed`} alert={now.failed>0}
          change={delta(now.success_rate,before.success_rate)} spark={<Sparkline values={series.map(b=>b.requests?(b.requests-b.failed)/b.requests:null)} label="Success rate over the window"/>}/>
        <Tile testid="admin-tile-held" label="Held back by the checks" value={percent(now.held_back_rate,1)} note="withheld answers"
          change={delta(now.held_back_rate,before.held_back_rate,{better:'down'})} spark={<Sparkline values={series.map(b=>b.requests?b.held_back/b.requests:null)} label="Held-back rate over the window"/>}/>
        <Tile label="Handed to a person" value={percent(now.handoff_rate,1)} note="needs_human" change={delta(now.handoff_rate,before.handoff_rate,{better:''})}/>
        <Tile testid="admin-tile-latency" label="Latency p50 / p95" value={`${number(now.p50_s)} / ${number(now.p95_s)} s`} note="whole request"
          change={delta(now.p95_s,before.p95_s,{kind:'seconds',better:'down'})} spark={<Sparkline values={series.map(b=>b.p95_s)} label="p95 latency over the window"/>}/>
        <Tile label="Model calls / request" value={number(now.model_calls_per_request)} note={`${number(now.tokens_per_request,0)} tokens / request`}
          change={delta(now.model_calls_per_request,before.model_calls_per_request,{kind:'count',better:'down'})}/>
      </div>
      {!now.requests&&<p className="admin-empty">No live requests in this window. Evaluation runs are below; the Failures page can include them.</p>}
      {data.capped&&<p className="admin-muted">Sampled: this window has more runs than the dashboard reads at once.</p>}

      <section className="admin-card" data-testid="admin-attention">
        <div className="admin-card-head"><h2>Needs attention</h2><span className="admin-muted">Regressions, new failures and limits crossed in this window</span></div>
        {!data.attention.length&&<p className="admin-empty">Nothing needs attention: no regressions, no new failure types and every rate is within its limit.</p>}
        <ul className="admin-attention">{data.attention.map((item,index)=><li key={index} data-testid={`admin-attention-${item.kind}`}>
          <Status value={item.level}/>
          <div><strong>{item.title}</strong><p>{item.detail}</p></div>
          {views.includes(item.page)&&<button className="link-button" onClick={()=>onNavigate(item.page,item.filter)}>Open</button>}
        </li>)}</ul>
      </section>

      <section className="admin-card" data-testid="admin-latest-evals">
        <div className="admin-card-head"><h2>Latest evaluations</h2><span className="admin-muted">Pass rate of the latest full run per dataset, against the full run before it</span></div>
        {!data.evals.length&&<p className="admin-empty">No experiments yet. Run <code>python -m evaluation.langsmith_run</code> first.</p>}
        {data.evals.length>0&&<div className="admin-table-wrap"><table className="admin-table">
          <thead><tr><th>Dataset</th><th className="num">Pass rate</th><th className="num">Change</th><th>Pass · fail · error · skipped</th><th>Run</th><th>Model</th><th/></tr></thead>
          <tbody>{data.evals.map(row=>{const change=delta(row.pass_rate,row.previous_pass_rate);
            return <tr key={row.dataset} data-testid={`admin-eval-${row.dataset}`}>
              <td>{views.includes('evaluations')?<button className="link-button" onClick={()=>onNavigate('evaluations',{dataset:row.dataset})}>{row.dataset}</button>:row.dataset}</td>
              <td className="num">{percent(row.pass_rate)}</td>
              <td className="num">{change?<span className={`admin-delta ${change.tone}`}>{change.text}</span>:'—'}</td>
              <td>{row.counts.pass} · {row.counts.fail} · {row.counts.error} · {row.counts.skipped}</td>
              <td>{formatDateTime(row.started_at)}{row.stale&&<> <Status value="stale"/></>}
                {row.last_run!==row.started_at&&<div className="admin-muted">last run {formatDate(row.last_run)} (subset)</div>}</td>
              <td className="admin-mono">{row.model||'—'}</td>
              <td>{row.url&&<a href={row.url} target="_blank" rel="noreferrer">LangSmith</a>}</td>
            </tr>;})}</tbody></table></div>}
      </section>
      <p className="admin-muted">Tiles count live requests only (evaluation runs are scored on the Evaluations page). Latency is the whole request, wall clock, as LangSmith recorded it. All dates and times are Singapore time (SGT).</p>
    </>}
  </Page>;
}
