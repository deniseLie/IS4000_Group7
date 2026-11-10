import {useState} from 'react';
import Icon from '../Icon';
import {formatDate,formatDateTime} from '../../lib/format';
import {P95_TARGET,WINDOWS,delta,evalSuccess,loadOverview,number,overTarget,percent,traceBudget,usd} from '../../lib/admin';
import {LineChart,Sparkline} from './charts';
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
  const [showNotes,setShowNotes]=useState(false);  // info items (history, set-up, coverage) stay folded: not something to act on now
  const [state,refresh]=useLoad((auth,signal)=>loadOverview(range,auth,signal),range);
  const data=state.data,now=data?.live.now,before=data?.live.previous,series=data?.live.series||[],evals=evalSuccess(data?.evals),trend=data?.eval_trend||[],budget=traceBudget(data?.trace_budget);
  const urgent=(data?.attention||[]).filter(item=>item.level!=='info'),notes=(data?.attention?.length||0)-urgent.length;
  return <Page id="overview" title="Overview" intro="How the evaluations stand, how fast and costly live requests are, the trace budget, and what needs attention."
    actions={<div className="admin-controls"><WindowPicker value={range} onChange={setRange}/>
      <button className="bank-chat-logout" onClick={refresh} disabled={state.busy}><Icon name="refresh"/>Refresh</button></div>}>
    <Failed error={state.error}/>
    {state.busy&&!data&&<><Loading label="Loading the overview"/><p className="admin-muted">Reading LangSmith. The first load after a restart takes a few seconds; later loads are cached.</p></>}
    {data&&<>
      <div className="admin-tiles admin-headline" data-testid="admin-tiles">
        <Tile testid="admin-tile-success" label="Evaluation pass rate" value={percent(evals.rate,1)} alert={evals.failing>0}
          note={`${evals.passed} of ${evals.scored} cases · ${evals.datasets} datasets${evals.failing?` · ${evals.failing} failing`:''}`}
          spark={<Sparkline values={trend.map(p=>p.rate)} label="Evaluation pass rate over 30 days"/>}/>
        <Tile testid="admin-tile-latency" label="Latency p95 (client / RM)" value={`${number(now.p95_client_s)} / ${number(now.p95_rm_s)} s`} alert={overTarget(now)}
          note={`p50 ${number(now.p50_s)} s · targets ${P95_TARGET.client} / ${P95_TARGET.rm} s`}
          change={delta(now.p95_s,before.p95_s,{kind:'seconds',better:'down'})} spark={<Sparkline values={series.map(b=>b.p95_s)} label="p95 latency over the window"/>}/>
        <Tile testid="admin-tile-cost" label="Cost in this window" value={usd(now.cost_total)}
          note={`${usd(now.cost_per_request)} / request · ${number(now.tokens_per_request,0)} tokens / request`}
          change={delta(now.cost_total,before.cost_total,{kind:'count',better:'down'})}/>
        <Tile testid="admin-tile-traces" label="LangSmith traces this month" alert={budget?.warn}
          value={budget?`${number(budget.left,0)} left`:'—'}
          note={budget?`${number(budget.used,0)} of ${number(budget.limit,0)} used · resets ${formatDate(budget.resets)}`:'Usage not readable with this key'}
          spark={budget&&<div className="admin-meter" role="img" aria-label={`${percent(budget.share)} of the monthly traces used`}><span style={{width:`${Math.min(budget.share,1)*100}%`}}/></div>}/>
      </div>
      <p className="admin-muted" data-testid="admin-live-line">Live in this window: {now.requests} requests ({now.client} client · {now.rm} RM) · {percent(now.success_rate,1)} answered without an error · {percent(now.held_back_rate,1)} held back · {percent(now.handoff_rate,1)} handed to a person · {number(now.model_calls_per_request)} model calls / request.
        {views.includes('failures')&&<> <button className="link-button" onClick={()=>onNavigate('failures')}>Failures</button></>}</p>
      {data.capped&&<p className="admin-muted">Sampled: this window has more runs than the dashboard reads at once.</p>}

      {trend.length>1&&<section className="admin-card" data-testid="admin-eval-trend">
        <div className="admin-card-head"><h2>Evaluation pass rate, last 30 days</h2><span className="admin-muted">Each point: every dataset's latest full run as of that day</span></div>
        <LineChart height={150} label="Evaluation pass rate over 30 days" points={trend.map(p=>({x:p.day,y:p.rate,title:`${formatDate(p.day)}: ${percent(p.rate,1)} (${p.passed} of ${p.scored})`}))}/>
      </section>}

      <section className="admin-card" data-testid="admin-attention">
        <div className="admin-card-head"><h2>Needs attention</h2><span className="admin-muted">What is still happening: regressions in the latest runs, and failures and limits crossed in the last 3 days</span></div>
        {!urgent.length&&<p className="admin-empty">Nothing needs attention: no regressions, no new failure types and every rate is within its limit.</p>}
        <ul className="admin-attention">{(showNotes?data.attention:urgent).map((item,index)=><li key={index} data-testid={`admin-attention-${item.kind}`}>
          <Status value={item.level}/>
          <div><strong>{item.title}</strong><p>{item.detail}</p></div>
          {views.includes(item.page)&&<button className="link-button" onClick={()=>onNavigate(item.page,item.filter)}>Open</button>}
        </li>)}</ul>
        {notes>0&&<button className="link-button" data-testid="admin-attention-notes" onClick={()=>setShowNotes(!showNotes)}>
          {showNotes?'Hide notes':`Show ${notes} note${notes===1?'':'s'} (older failures, setup and coverage reminders)`}</button>}
      </section>

      <section className="admin-card" data-testid="admin-latest-evals">
        <div className="admin-card-head"><h2>Latest evaluations</h2><span className="admin-muted">Pass rate of the latest full run per dataset, against the full run before it</span></div>
        {!data.evals.length&&<p className="admin-empty">No experiments yet. Run <code>python -m evaluation.langsmith_run</code> first.</p>}
        {data.evals.length>0&&<div className="admin-table-wrap"><table className="admin-table">
          <thead><tr><th>Dataset</th><th className="num">Pass rate</th><th className="num">Change</th><th>Pass · fail · error · skipped</th><th>Run</th><th/></tr></thead>
          <tbody>{[...data.evals].sort((a,b)=>(b.counts.fail+b.counts.error)-(a.counts.fail+a.counts.error)).map(row=>{const change=delta(row.pass_rate,row.previous_pass_rate);
            return <tr key={row.dataset} data-testid={`admin-eval-${row.dataset}`}>
              <td>{views.includes('evaluations')?<button className="link-button" onClick={()=>onNavigate('evaluations',{dataset:row.dataset})}>{row.dataset}</button>:row.dataset}</td>
              <td className="num">{percent(row.pass_rate)}</td>
              <td className="num">{change?<span className={`admin-delta ${change.tone}`}>{change.text}</span>:'—'}</td>
              <td>{row.counts.pass} · {row.counts.fail} · {row.counts.error} · {row.counts.skipped}</td>
              <td>{formatDateTime(row.started_at)}{row.stale&&<> <Status value="stale"/></>}
                {row.last_run!==row.started_at&&<div className="admin-muted">last run {formatDate(row.last_run)} (subset)</div>}</td>
              <td>{row.url&&<a href={row.url} target="_blank" rel="noreferrer">LangSmith</a>}</td>
            </tr>;})}</tbody></table></div>}
      </section>
      <p className="admin-muted">The evaluation pass rate counts the latest full run of each dataset (skipped cases left out). Latency and cost count live requests; cost is an estimate from tokens and a per-model price, not a bill. Traces are LangSmith's monthly count for the workspace. Latency is the whole request, wall clock, as LangSmith recorded it. All dates and times are Singapore time (SGT).</p>
    </>}
  </Page>;
}
