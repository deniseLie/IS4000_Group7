import {Fragment,useState} from 'react';
import Icon from '../Icon';
import {formatDate} from '../../lib/format';
import {MIN_COMPARE,loadPrompts,number,percent,seconds,versionPair} from '../../lib/admin';
import {WindowPicker} from './OverviewPage';
import {Failed,Loading,Page,useLoad} from './shared';

const shown=(value,kind)=>value===null||value===undefined?'—':kind==='rate'?percent(value):kind==='seconds'?seconds(value):number(value,0);

function Badges({family}) {
  return <span className="admin-pills">
    {family.untested&&<span className="admin-status admin-untested" title="The live version is not the one the latest evaluation ran">live not evaluated</span>}
    {!family.latest_eval&&<span className="admin-status admin-not_recorded" title="No evaluation case recorded this prompt family">no eval recorded</span>}
  </span>;
}

function Log({log}) {
  if(!log)return <p className="admin-muted">No prompt-log entry (docs/prompt-log/).</p>;
  return <dl className="admin-kv">{[['Date','date'],['Goal','goal'],['Change','change'],['Results','results'],['Decision','decision']].filter(([,key])=>log[key])
    .map(([label,key])=><Fragment key={key}><dt>{label}</dt><dd>{log[key]}</dd></Fragment>)}</dl>;
}

function Compare({family}) {
  const names=family.versions.map(item=>item.version);
  const [pair,setPair]=useState([names[1]||names[0],names[0]]);
  const [a,b]=pair.map(name=>family.versions.find(item=>item.version===name));
  const {rows,enough}=versionPair(a,b);
  const pick=(index,label)=><label>{label} <select value={pair[index]} onChange={event=>setPair(index?[pair[0],event.target.value]:[event.target.value,pair[1]])}>
    {names.map(name=><option key={name} value={name}>{name}{family.live===name?' (live)':''}</option>)}</select></label>;
  return <div className="admin-compare-versions" data-testid={`admin-compare-${family.family}`}>
    <div className="admin-controls">{pick(0,'A')}{pick(1,'B')}</div>
    {!enough&&<p className="admin-muted">Too few runs to compare: each version needs at least {MIN_COMPARE} live calls or eval cases in this window. The numbers are shown, not judged.</p>}
    <div className="admin-table-wrap"><table className="admin-table">
      <thead><tr><th>Measure</th><th className="num">{pair[0]}</th><th className="num">{pair[1]}</th></tr></thead>
      <tbody>{rows.map(row=><tr key={row.label}><td>{row.label}</td><td className="num">{shown(row.a,row.kind)}</td><td className="num">{shown(row.b,row.kind)}</td></tr>)}</tbody>
    </table></div>
  </div>;
}

function Versions({family}) {
  const [open,setOpen]=useState(null);
  return <>
    <div className="admin-table-wrap"><table className="admin-table">
      <thead><tr><th>Version</th><th>Logged</th><th className="num">Live calls</th><th className="num">OK</th><th className="num">p50 / p95</th><th className="num">Tokens in / out</th>
        <th className="num">Eval cases</th><th className="num">Eval pass</th><th className="num">Judge / 1</th></tr></thead>
      <tbody>{family.versions.map(item=>{const live=item.live_metrics,ev=item.eval_metrics;return <Fragment key={item.version}>
        <tr className="admin-row" onClick={()=>setOpen(open===item.version?null:item.version)} aria-expanded={open===item.version}>
          <td><button className="link-button">{item.version}</button> {item.live&&<span className="admin-status admin-live">live</span>}
            {!item.on_disk&&<span className="admin-status admin-not_recorded" title="Seen in traces but not in backend/app/prompts">not on disk</span>}</td>
          <td>{item.log?.date||'—'}</td>
          <td className="num">{live?.calls??'—'}</td><td className="num">{percent(live?.ok_rate)}</td>
          <td className="num">{live?`${seconds(live.p50_s)} / ${seconds(live.p95_s)}`:'—'}</td>
          <td className="num">{live?`${number(live.input_tokens,0)} / ${number(live.output_tokens,0)}`:'—'}</td>
          <td className="num">{ev?.cases??'—'}</td><td className="num">{percent(ev?.pass_rate)}</td><td className="num">{number(ev?.judge,2)}</td>
        </tr>
        {open===item.version&&<tr className="admin-detail"><td colSpan={9}>
          <Log log={item.log}/>
          {live&&<p className="admin-muted">Live outcomes: {Object.entries(live.outcomes).map(([key,count])=>`${key} ${count}`).join(' · ')} · retries {live.retries}</p>}
          {ev&&<p className="admin-muted">Eval runs: {Object.entries(ev.datasets).map(([name,d])=>`${name.replace(/^demobank-/,'')} ${percent(d.pass_rate)} of ${d.cases}`).join(' · ')}
            {' '}· last {formatDate(ev.last_run)}</p>}
          {item.eval_calls&&<p className="admin-muted">Model calls inside evaluations: {item.eval_calls.calls} · OK {percent(item.eval_calls.ok_rate)} · p95 {seconds(item.eval_calls.p95_s)}</p>}
        </td></tr>}
      </Fragment>;})}</tbody></table></div>
    {family.versions.length>1&&<Compare family={family}/>}
  </>;
}

// Every prompt family: versions on disk, the live one, what the evaluations ran, and per-version calls, latency, tokens and pass rate.
export default function PromptsPage() {
  const [window,setWindow]=useState('30d');
  const [state,refresh]=useLoad((auth,signal)=>loadPrompts(window,auth,signal),window);
  const [open,setOpen]=useState(null);
  const families=state.data?.families||[];
  const untested=families.filter(family=>family.untested).length,unrecorded=families.filter(family=>!family.latest_eval).length;
  return <Page id="prompts" title="Prompts" intro="Each prompt family and its versions: what is live, what the evaluations ran, and how each version performed."
    actions={<div className="admin-controls"><WindowPicker value={window} onChange={setWindow}/>
      <button className="bank-chat-logout" onClick={refresh} disabled={state.busy}><Icon name="refresh"/>Refresh</button></div>}>
    <Failed error={state.error}/>
    {state.busy&&!state.data&&<Loading label="Loading prompts"/>}
    {state.data&&<>
      <div className="admin-tiles">
        <div className="admin-tile"><span>Prompt families</span><strong>{families.length}</strong><small>{families.reduce((sum,family)=>sum+family.versions.length,0)} versions</small></div>
        <div className={`admin-tile${untested?' alert':''}`}><span>Live but not evaluated</span><strong>{untested}</strong><small>latest eval ran an older version</small></div>
        <div className="admin-tile"><span>No eval recorded</span><strong>{unrecorded}</strong><small>no case in this window named it</small></div>
      </div>
      {state.data.capped&&<p className="admin-muted">Only the most recent model calls were read; older calls in this window are not counted.</p>}
      <section className="admin-card">
        <div className="admin-table-wrap"><table className="admin-table" data-testid="admin-prompt-families">
          <thead><tr><th>Family</th><th>Live</th><th>Latest eval</th><th className="num">Versions</th><th className="num">Live calls</th><th className="num">OK</th><th className="num">p95</th><th className="num">Eval pass</th><th/></tr></thead>
          <tbody>{families.map(family=>{const live=family.versions.find(item=>item.live);return <Fragment key={family.family}>
            <tr className="admin-row" data-testid={`admin-prompt-${family.family}`} onClick={()=>setOpen(open===family.family?null:family.family)} aria-expanded={open===family.family}>
              <td><button className="link-button">{family.family}</button></td><td>{family.live||'—'}</td><td>{family.latest_eval||'—'}</td>
              <td className="num">{family.versions.length}</td><td className="num">{live?.live_metrics?.calls??'—'}</td>
              <td className="num">{percent(live?.live_metrics?.ok_rate)}</td><td className="num">{seconds(live?.live_metrics?.p95_s)}</td>
              <td className="num">{percent(live?.eval_metrics?.pass_rate)}</td><td><Badges family={family}/></td>
            </tr>
            {open===family.family&&<tr className="admin-detail"><td colSpan={9}><Versions family={family}/></td></tr>}
          </Fragment>;})}</tbody></table></div>
      </section>
      <p className="admin-muted">Live numbers come from the model-call runs of real requests; eval numbers from the cases a version ran in (its recorded prompt ids or its trace).</p>
    </>}
  </Page>;
}
