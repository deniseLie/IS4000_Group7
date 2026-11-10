import {Fragment,useState} from 'react';
import Icon from '../Icon';
import {formatDateTime} from '../../lib/format';
import {SURFACES,counted,loadLatency,loadTrace,number,percent,seconds,waterfall} from '../../lib/admin';
import {Stack,Waterfall} from './charts';
import {SourcePicker} from './AgentsPage';
import {WindowPicker} from './OverviewPage';
import {Failed,Loading,Page,useLoad} from './shared';

function Timings({title,rows,note,extra}) {
  return <section className="admin-card">
    <div className="admin-card-head"><h2>{title}</h2>{note&&<span className="admin-muted">{note}</span>}</div>
    {!rows.some(row=>row.n)?<p className="admin-empty">Nothing recorded in this window.</p>:<div className="admin-table-wrap"><table className="admin-table">
      <thead><tr><th>Name</th><th className="num">Runs</th><th className="num">p50</th><th className="num">p95</th>{extra&&<th className="num">{extra[0]}</th>}</tr></thead>
      <tbody>{rows.filter(row=>row.n).map(row=><tr key={row.name||row.agents}><td className="admin-mono">{row.name||row.agents}</td><td className="num">{row.n}</td>
        <td className="num">{seconds(row.p50_s)}</td><td className="num">{seconds(row.p95_s)}</td>{extra&&<td className="num">{extra[1](row)}</td>}</tr>)}</tbody>
    </table></div>}
  </section>;
}

function Steps({runId}) {
  const [state]=useLoad((auth,signal)=>loadTrace(runId,auth,signal),runId);
  const fall=waterfall(state.data);
  return <>
    {state.busy&&<p className="loading">Loading steps…</p>}
    <Failed error={state.error}/>
    {state.data&&(fall.rows.length?<Waterfall rows={fall.rows} total={fall.total} format={seconds}/>:<p className="admin-muted">No step timings recorded for this request.</p>)}
  </>;
}

// What limits scale: end-to-end percentiles, where the time goes, per agent, slowest requests.
export default function LatencyPage({onNavigate}) {
  const [range,setRange]=useState('7d');
  const [source,setSource]=useState('all');
  const [open,setOpen]=useState(null);
  const [state,refresh]=useLoad((auth,signal)=>loadLatency(range,source,auth,signal),`${range}|${source}`);
  const data=state.data,all=data?.surfaces.all,parts=data?.breakdown.parts||[];
  return <Page id="latency" title="Latency and cost" intro="Server time per request (the whole turn), where it goes, and the tokens it uses."
    actions={<div className="admin-controls"><WindowPicker value={range} onChange={setRange}/><SourcePicker value={source} onChange={setSource}/>
      <button className="bank-chat-logout" onClick={refresh} disabled={state.busy}><Icon name="refresh"/>Refresh</button></div>}>
    <Failed error={state.error}/>
    {state.busy&&!data&&<><Loading label="Loading latency"/><p className="admin-muted">Reading LangSmith. With evaluations, the first load after a restart can take up to a minute; later loads are cached.</p></>}
    {data&&<>
      <div className="admin-tiles">
        <div className="admin-tile"><span>Requests</span><strong>{data.requests}</strong><small>{number(data.throughput.per_hour,2)} per hour on average</small></div>
        <div className={`admin-tile${all.p95_s>30?' alert':''}`}><span>p50 / p95</span><strong>{number(all.p50_s)} / {number(all.p95_s)} s</strong><small>p99 {seconds(all.p99_s)}</small></div>
        <div className="admin-tile"><span>First section</span><strong>{all.first_section_n?`${number(all.first_section_p50_s)} s`:'—'}</strong>
          <small>{all.first_section_n?`p50 · p95 ${seconds(all.first_section_p95_s)}`:'not recorded'}</small></div>
        <div className={`admin-tile${data.tokens.budget_hits?' alert':''}`}><span>Tokens / request</span><strong>{number(data.tokens.per_request,0)}</strong>
          <small>p95 {number(data.tokens.p95_per_request,0)} · {data.tokens.budget_hits} budget hits</small></div>
      </div>
      {data.capped&&<p className="admin-muted">Sampled: this window has more runs than the dashboard reads at once.</p>}
      {!data.requests&&<p className="admin-empty">No requests in this window.</p>}

      {data.requests>0&&<>
      <section className="admin-card" data-testid="admin-surfaces">
        <div className="admin-card-head"><h2>End to end</h2></div>
        <div className="admin-table-wrap"><table className="admin-table">
          <thead><tr><th>Surface</th><th className="num">Requests</th><th className="num">p50</th><th className="num">p90</th><th className="num">p95</th><th className="num">p99</th></tr></thead>
          <tbody>{Object.entries(SURFACES).filter(([key])=>data.surfaces[key].n).map(([key,label])=>{const row=data.surfaces[key];return <tr key={key}>
            <td>{label}</td><td className="num">{row.n}</td><td className="num">{seconds(row.p50_s)}</td><td className="num">{seconds(row.p90_s)}</td>
            <td className="num">{seconds(row.p95_s)}</td><td className="num">{seconds(row.p99_s)}</td></tr>;})}</tbody></table></div>
      </section>

      <section className="admin-card" data-testid="admin-breakdown">
        <div className="admin-card-head"><h2>Where the time goes</h2><span className="admin-muted">Average per request; {data.breakdown.traced} of {data.breakdown.requests} requests had step timings</span></div>
        <Stack label="Share of time per request" parts={parts.map(part=>({label:part.part,share:part.share,text:`${seconds(part.seconds)} · ${percent(part.share)}`}))}/>
        <p className="admin-muted">Average request {seconds(data.breakdown.wall_s)}.{data.breakdown.overlap?' Agents run in parallel, so the parts add up to more than the request time; shares are of the parts.':''} Each hosted database call costs about 0.19 s (docs/database-speed.md).</p>
      </section>

      <Timings title="Per agent" rows={data.agents} extra={['Errors',row=>row.errors]}/>

      <section className="admin-card" data-testid="admin-slowest">
        <div className="admin-card-head"><h2>Slowest requests</h2><span className="admin-muted">Open one for its steps on a time axis</span></div>
        <div className="admin-table-wrap"><table className="admin-table">
          <thead><tr><th>When</th><th>Request</th><th className="num">Time</th><th className="num">Model calls</th><th>Agents</th></tr></thead>
          <tbody>{data.slowest.map(row=><Fragment key={row.run_id}>
            <tr className="admin-row" onClick={()=>setOpen(open===row.run_id?null:row.run_id)} aria-expanded={open===row.run_id} data-testid="admin-slow-row">
              <td><button className="link-button">{formatDateTime(row.started_at)}</button><div className="admin-muted">{SURFACES[row.surface]||row.surface}{row.case_id?` · ${row.case_id}`:''}</div></td>
              <td>{row.question||<em className="admin-muted">no question recorded</em>}</td><td className="num">{seconds(row.latency_s)}</td>
              <td className="num">{row.model_calls??'—'}</td><td>{counted(row.agents)||'—'}</td></tr>
            {open===row.run_id&&<tr className="admin-detail"><td colSpan={5}><Steps runId={row.run_id}/>
              {row.case_id&&<button className="link-button" onClick={()=>onNavigate('evaluations',{dataset:row.dataset,case:row.case_id})}>Open {row.case_id} in its evaluation</button>}</td></tr>}
          </Fragment>)}</tbody></table></div>
      </section>
      </>}
    </>}
  </Page>;
}
