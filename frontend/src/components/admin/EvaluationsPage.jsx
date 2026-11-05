import {Fragment,useEffect,useRef,useState} from 'react';
import Icon from '../Icon';
import {formatDate,formatDateTime} from '../../lib/format';
import {COMPARED,GROUPS,LABELS,datasetRow,defaultPair,delta,failingFirst,loadCompare,loadEvaluation,loadEvaluations,percent,problemFirst,scoreLabel,seconds} from '../../lib/admin';
import {HeatLegend,Heatmap,LineChart,Sparkline} from './charts';
import {Failed,Loading,Page,Status,useLoad} from './shared';

const ALL='all';
const VIEWS=[['run','Run detail'],['trends','Trends'],['cases','Cases × runs'],['compare','Compare']];
const short=name=>name.replace(/^demobank-/,'');
function RunPicker({label,timeline,value,onChange}) {
  return <label>{label} <select value={value||''} onChange={event=>onChange(event.target.value)}>
    {[...timeline].reverse().map(point=><option key={point.experiment} value={point.experiment}>
      {formatDateTime(point.started_at)} · {percent(point.pass_rate)}{point.subset?' · subset':''} · {short(point.experiment)}</option>)}</select></label>;
}

// One experiment's cases, errors and failures first: rule status, failed checks, the model-graded judge score; a row opens
// expected vs actual and the repro. `focus` (a case id) starts open and in view.
export function ExperimentCases({name,focus=null}) {
  const [state]=useLoad((auth,signal)=>loadEvaluation(name,auth,signal),name);
  const [open,setOpen]=useState(undefined);  // undefined until a row is clicked: the focused case is open
  const focused=useRef(null);
  const run=state.data;
  useEffect(()=>{focused.current?.scrollIntoView?.({block:'center'});},[run]);
  const isOpen=item=>open===undefined?!!focus&&item.case_id===focus:open===item.run_id;
  return <>
    {state.busy&&<p className="loading">Loading cases…</p>}
    <Failed error={state.error}/>
    {run&&<>
      <p className="admin-summary" data-testid="admin-case-summary">{run.counts.pass} pass · {run.counts.fail} fail · {run.counts.error} error · {run.counts.skipped} skipped
        {' '}({percent(run.pass_rate)}). Judge scores are model-graded and need review.</p>
      <p className="admin-muted">{formatDateTime(run.started_at)} · model {run.model||'—'} · pipeline {run.pipeline||'—'} · transport {run.transport||'—'}{run.judge_model?` · judge ${run.judge_model}`:''}
        {run.commit?` · commit ${run.commit}`:''}{run.url&&<> · <a href={run.url} target="_blank" rel="noreferrer">Open in LangSmith</a></>}</p>
      {focus&&!run.cases.some(item=>item.case_id===focus)&&<p className="admin-muted">{focus} is not in this run.</p>}
      <div className="admin-table-wrap"><table className="admin-table">
        <thead><tr><th>Case</th><th>Rule status</th><th>Failed rule checks</th><th className="num">Judge / 1</th><th>Agents</th><th className="num">Model calls</th><th className="num">Time</th></tr></thead>
        <tbody>{failingFirst(run.cases).map(item=><Fragment key={item.run_id}>
          <tr className={`admin-row${focus&&item.case_id===focus?' chosen':''}`} data-testid={`admin-case-${item.case_id}`} ref={focus&&item.case_id===focus?focused:undefined}
            onClick={()=>setOpen(isOpen(item)?null:item.run_id)} aria-expanded={isOpen(item)}>
            <td><button className="link-button">{item.case_id||'—'}</button></td><td><Status value={item.status}/></td>
            <td>{item.failed.join(', ')||'—'}</td><td className="num">{item.judge??'—'}</td>
            <td>{Object.entries(item.agents||{}).map(([agent,count])=>`${agent}×${count}`).join(', ')||'—'}</td>
            <td className="num">{item.model_calls??'—'}</td><td className="num">{seconds(item.latency_s)}</td></tr>
          {isOpen(item)&&<tr className="admin-detail"><td colSpan={7}>
            {item.asked?.length>0&&<><p className="admin-muted">Asked</p>{item.asked.map((text,index)=><blockquote key={index} className="admin-quote">{text}</blockquote>)}</>}
            <ul className="admin-checks">{Object.entries(item.checks).map(([key,check])=><li key={key}><Status value={scoreLabel(check.score)}/> <strong>{key}</strong>{check.comment?` — ${check.comment}`:''}</li>)}</ul>
            <div className="admin-grid">
              <div><p className="admin-muted">Expected (dataset)</p><pre className="admin-answer">{JSON.stringify(item.expected||{},null,2)}</pre></div>
              <div><p className="admin-muted">Actual</p>
                {item.answer?<pre className="admin-answer">{item.answer}</pre>:<p className="admin-muted">No answer text.</p>}
                {item.tools?.length>0&&<p><strong>Tools:</strong> {item.tools.join(', ')}</p>}
                {item.service_reads?.length>0&&<p><strong>Record reads:</strong> {item.service_reads.join(', ')}</p>}
                {item.error&&<p className="form-error">{item.error}</p>}</div>
            </div>
            {item.repro&&<><p className="admin-muted">Re-run this case</p><pre className="admin-answer admin-mono">{item.repro}</pre></>}
          </td></tr>}
        </Fragment>)}</tbody></table></div></>}
  </>;
}

function Compare({timeline}) {
  const [pair,setPair]=useState(()=>defaultPair(timeline));
  const [a,b]=pair;
  const [state]=useLoad((auth,signal)=>loadCompare(a,b,auth,signal),a&&b&&a!==b?`${a}|${b}`:null);
  const result=a&&b&&a!==b?state.data:null;
  return <section className="admin-card" data-testid="admin-compare">
    <div className="admin-card-head"><h2>Compare two runs</h2><span className="admin-muted">Case by case, from A (before) to B (after)</span></div>
    <div className="admin-controls"><RunPicker label="A" timeline={timeline} value={a} onChange={value=>setPair([value,b])}/>
      <RunPicker label="B" timeline={timeline} value={b} onChange={value=>setPair([a,value])}/></div>
    {a===b&&<p className="admin-muted">Pick two different runs.</p>}
    {state.busy&&<p className="loading">Comparing…</p>}
    <Failed error={state.error}/>
    {result&&<>
      {result.changes.length>0&&<p className="admin-muted">Changed: {result.changes.join(' · ')}</p>}
      <div className="admin-pills">{Object.entries(result.counts).filter(([,count])=>count).map(([kind,count])=><span key={kind} className={`admin-status admin-${kind}`}>{COMPARED[kind]} {count}</span>)}</div>
      <div className="admin-table-wrap"><table className="admin-table">
        <thead><tr><th>Case</th><th>Change</th><th>A</th><th>B</th></tr></thead>
        <tbody>{result.rows.filter(row=>row.kind!=='still_passing').map(row=><tr key={row.case_id}>
          <td>{row.case_id}</td><td><span className={`admin-status admin-${row.kind}`}>{COMPARED[row.kind]}</span></td>
          {[row.a,row.b].map((side,index)=><td key={index}>{side?<><Status value={side.status}/> {side.failed.join(', ')}
            {Object.entries(side.comments).map(([key,comment])=><div key={key} className="admin-muted">{key}: {comment}</div>)}</>:'—'}</td>)}
        </tr>)}</tbody></table></div>
      {result.counts.still_passing>0&&<p className="admin-muted">{result.counts.still_passing} cases pass in both runs.</p>}
    </>}
  </section>;
}

// The headline of every dataset in one table; a dataset opens on its Run detail, its problem cases on Cases × runs.
function AllDatasets({sets,onOpen}) {
  const rows=sets.map(datasetRow);
  return <section className="admin-card" data-testid="admin-all-datasets">
    <div className="admin-card-head"><h2>All datasets</h2><span className="admin-muted">The latest full run of each dataset, against the full run before it</span></div>
    <div className="admin-table-wrap"><table className="admin-table">
      <thead><tr><th>Dataset</th><th className="num">Pass rate</th><th className="num">Change</th><th>Pass · fail · error · skipped</th><th>Cases to look at</th><th>Last run</th></tr></thead>
      <tbody>{rows.map(row=>{const change=delta(row.pass_rate,row.previous_pass_rate);
        return <tr key={row.dataset} data-testid={`admin-all-${row.dataset}`}>
          <td className="admin-nowrap"><button className="link-button" onClick={()=>onOpen(row.dataset,'run')}>{short(row.dataset)}</button></td>
          <td className="num">{percent(row.pass_rate)}</td>
          <td className="num admin-nowrap">{change?<span className={`admin-delta ${change.tone}`}>{change.text}</span>:'—'}</td>
          <td className="admin-nowrap">{row.counts?`${row.counts.pass} · ${row.counts.fail} · ${row.counts.error} · ${row.counts.skipped}`:'—'}</td>
          <td>{!row.runs?'—':<>{row.problems.length?<button className="link-button" onClick={()=>onOpen(row.dataset,'cases')}>
            {row.problems.map(item=>`${item.count} ${LABELS[item.label].toLowerCase()}`).join(' · ')}</button>:<span className="admin-muted">none</span>}
            {row.fixed>0&&<span className="admin-muted"> · {row.fixed} fixed</span>}</>}</td>
          <td className="admin-nowrap">{row.last_run?formatDateTime(row.last_run):'never'}{row.stale&&<> <Status value="stale"/></>}</td>
        </tr>;})}</tbody></table></div>
    <p className="admin-muted">Each dataset tests something different, so their pass rates are not added together. Pass rate and counts are the latest full run;
      Last run also counts subset runs (<code>--case</code>). Cases to look at come from each case's whole history, re-runs included,
      so a failure in the full run that passed when re-run counts as fixed.</p>
  </section>;
}

// One dataset: its tiles, then Run detail (the default), Trends, Cases × runs and Compare. Picking a run on the chart or a
// cell in the heatmap opens Run detail on that run (and case).
function Dataset({data,start}) {
  const full=data.timeline.filter(point=>!point.subset);
  const latest=full.at(-1)?.experiment||data.timeline.at(-1)?.experiment||null;  // Run detail opens on the latest full run
  const [view,setView]=useState(start?.view||'run');
  const [run,setRun]=useState(null);
  const [focus,setFocus]=useState(null);
  const [hidePassing,setHidePassing]=useState(false);
  const name=run||latest;
  function open(experiment,caseId=null) {setRun(experiment);setFocus(caseId);setView('run');}
  if(!data.timeline.length)return <p className="admin-empty">No runs of {data.dataset} yet. Run <code>python -m evaluation.langsmith_run --dataset {data.dataset}</code>.</p>;
  const points=data.timeline.map(point=>({x:point.started_at,y:point.pass_rate,hollow:point.subset,chosen:point.experiment===name,
    marker:point.changes.length?point.changes.join(', '):null,onPick:()=>open(point.experiment),
    title:`${formatDateTime(point.started_at)} · ${percent(point.pass_rate)} · ${point.cases} cases (${point.counts.pass} pass, ${point.counts.fail} fail, ${point.counts.error} error, ${point.counts.skipped} skipped)`+
      ` · model ${point.model||'—'} · transport ${point.transport||'—'}${point.judge_model?` · judge ${point.judge_model}`:''}${point.subset?' · subset run':''}`}));
  const groups=Object.keys(GROUPS).map(group=>[group,data.checks.filter(row=>row.group===group)]).filter(([,rows])=>rows.length);
  const changed=data.timeline.filter(point=>point.changes.length);
  const cases=problemFirst(data.heatmap.rows).filter(row=>!hidePassing||row.label!=='passing');
  const shown=data.timeline.find(point=>point.experiment===name);
  const which=name===latest?(full.length?'The latest full run':'The latest run'):shown?.subset?'A subset run (--case)':'An earlier full run';
  return <>
    <div className="admin-tiles">
      <div className="admin-tile"><span>Latest full run</span><strong>{percent(data.pass_rate)}</strong><small>{data.cases_last_full} cases</small></div>
      <div className="admin-tile"><span>Runs</span><strong>{data.runs}</strong><small>{data.full_runs} full · {data.runs-data.full_runs} subset</small></div>
      <div className={`admin-tile${data.stale?' alert':''}`}><span>Last run</span><strong>{formatDate(data.last_run)}</strong><small>{data.stale?'older than 7 days':'recent'}</small></div>
      <div className="admin-tile"><span>Cases in the dataset</span><strong>{data.examples??'—'}</strong><small>synced examples</small></div>
    </div>

    <nav className="page-tabs admin-views" role="tablist" data-testid="admin-eval-views">{VIEWS.map(([key,label])=>
      <button key={key} role="tab" aria-selected={view===key} data-testid={`admin-eval-view-${key}`} onClick={()=>setView(key)}>{label}</button>)}</nav>

    {view==='run'&&<section className="admin-card" data-testid="admin-run-detail">
      <div className="admin-card-head"><h2>Run detail</h2><span className="admin-muted">{which}. Pick another here, on the Trends chart or in Cases × runs.</span></div>
      <div className="admin-controls"><RunPicker label="Run" timeline={data.timeline} value={name} onChange={value=>{setRun(value);setFocus(null);}}/>
        {name!==latest&&<button className="link-button" onClick={()=>{setRun(null);setFocus(null);}}>Back to the latest {full.length?'full ':''}run</button>}</div>
      {name&&<ExperimentCases key={`${name}|${focus||''}`} name={name} focus={focus}/>}
    </section>}

    {view==='trends'&&<>
      <section className="admin-card" data-testid="admin-timeline">
        <div className="admin-card-head"><h2>Pass rate over time</h2><span className="admin-muted">Each dot is a run: click one to open its cases</span></div>
        <LineChart points={points} label={`Pass rate of ${data.dataset} over time`}/>
        <div className="admin-legend"><span>● full run</span><span>○ subset run (--case), not on the line</span><span>┆ model, pipeline or prompt changed</span></div>
        {changed.length>0&&<ul className="admin-changes">{changed.map(point=><li key={point.experiment}>
          <button className="link-button" onClick={()=>open(point.experiment)}><strong>{formatDate(point.started_at)}</strong></button> {point.changes.join(' · ')}</li>)}</ul>}
      </section>
      <section className="admin-card">
        <div className="admin-card-head"><h2>Checks over time</h2><span className="admin-muted">Pass rate of each check per run: shows whether wording, citations, routing or tools broke</span></div>
        <div className="admin-table-wrap"><table className="admin-table">
          <thead><tr><th>Check</th><th>Trend</th><th className="num">Latest</th></tr></thead>
          <tbody>{groups.map(([group,rows])=><Fragment key={group}>
            <tr className="admin-group-row"><th colSpan={3}>{GROUPS[group]}</th></tr>
            {rows.map(row=><tr key={row.key}><td className="admin-mono">{row.key}</td>
              <td className="admin-trend"><Sparkline values={row.points.map(point=>point.rate)} label={`${row.key} over time`}/></td>
              <td className="num">{percent(row.latest)}</td></tr>)}
          </Fragment>)}</tbody></table></div>
      </section>
    </>}

    {view==='cases'&&<section className="admin-card" data-testid="admin-heatmap">
      <div className="admin-card-head"><h2>Cases in the last {data.heatmap.experiments.length} runs</h2>
        <span className="admin-muted">{Object.entries(data.heatmap.labels).map(([label,count])=>`${LABELS[label]||label} ${count}`).join(' · ')}</span></div>
      <HeatLegend/>
      <div className="admin-controls admin-heat-tools"><label><input type="checkbox" checked={hidePassing} onChange={event=>setHidePassing(event.target.checked)} data-testid="admin-hide-passing"/> Hide passing cases</label>
        <span className="admin-muted">Cases that need a look come first. Click a cell to open that case in that run, or a date to open the run.</span></div>
      {cases.length?<Heatmap columns={data.heatmap.experiments} rows={cases} highlight={start?.case||null} labels={LABELS} onPick={open}/>:<p className="admin-empty">Every case passes.</p>}
      <p className="admin-muted">Pattern, from each case's history: Regressed failed in its latest run after passing in the one before, and Fixed is the reverse;
        Always failing never passed; Flaky swapped between pass and fail twice or more (model non-determinism or a brittle check).</p>
    </section>}

    {view==='compare'&&(data.timeline.length>1?<Compare key={data.dataset} timeline={data.timeline}/>:<p className="admin-empty">Only one run so far: nothing to compare yet.</p>)}
  </>;
}

// Accuracy over time per evaluation dataset: All datasets first, then each dataset's run detail, trends, case heatmap and comparison.
export default function EvaluationsPage({filter}) {
  const [state,refresh]=useLoad(loadEvaluations,'all');
  const [picked,setPicked]=useState(()=>({dataset:filter?.dataset||ALL,view:filter?.case?'cases':'run',case:filter?.case||null}));
  const sets=state.data?.datasets||[];
  const current=picked.dataset===ALL?null:sets.find(item=>item.dataset===picked.dataset)||null;
  return <Page id="evaluations" title="Evaluations" intro="LangSmith experiments: every case played through the real app and scored by rule checks (and an optional model judge)."
    actions={<button className="bank-chat-logout" onClick={refresh} disabled={state.busy}><Icon name="refresh"/>Refresh</button>}>
    <Failed error={state.error}/>
    {state.busy&&!state.data&&<Loading label="Loading evaluations"/>}
    {state.data&&!sets.length&&<p className="admin-empty">No evaluation datasets yet. Run <code>python -m evaluation.langsmith_sync</code> first.</p>}
    {sets.length>0&&<>
      <div className="admin-tabs" role="tablist" data-testid="admin-datasets">
        <button role="tab" aria-selected={!current} data-testid="admin-dataset-all" onClick={()=>setPicked({dataset:ALL})}>All datasets</button>
        {sets.map(item=><button key={item.dataset} role="tab" aria-selected={item.dataset===current?.dataset} onClick={()=>setPicked({dataset:item.dataset,view:'run'})}>
          {short(item.dataset)} {item.pass_rate==null?'':percent(item.pass_rate)}</button>)}</div>
      {current?<Dataset key={current.dataset} data={current} start={picked}/>:<AllDatasets sets={sets} onOpen={(dataset,view)=>setPicked({dataset,view})}/>}
    </>}
  </Page>;
}
