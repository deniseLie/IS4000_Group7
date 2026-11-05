import {useEffect,useRef} from 'react';
import {sparkSegments} from '../../lib/admin';
import {formatDate,formatDateTime} from '../../lib/format';

const day=value=>formatDate(value,{year:undefined});  // Singapore day, "4 Nov"
const clock=value=>formatDateTime(value,{year:undefined,month:undefined,day:undefined,hour:'2-digit',minute:'2-digit',hour12:false});  // "14:39" SGT

// Small inline SVG charts (no chart library): the numbers are always shown next to them as text.
export function Sparkline({values,label}) {
  const segments=sparkSegments(values);
  if(!segments.length)return null;
  return <svg className="admin-spark" viewBox="0 0 100 28" preserveAspectRatio="none" role="img" aria-label={label}>
    {segments.map((points,index)=><polyline key={index} points={points}/>)}
  </svg>;
}

// A row of coloured squares, oldest first (a case's status in its last runs).
export function History({statuses,label}) {
  return <span className="admin-history" role="img" aria-label={`${label}: ${statuses.join(', ')}`}>
    {statuses.map((status,index)=><i key={index} className={`seg-${status}`} title={status}/>)}
  </span>;
}

// Enter or Space on a focused SVG mark or table cell acts like a click.
const pressed=action=>event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();action();}};

const W=640;
// Pass rate (0–1) over time. Hollow dots are subset runs and stay off the line; dashed lines mark a changed model or prompt.
// A point with `onPick` is a button (opens that run).
export function LineChart({points,height=190,label}) {
  const pad={l:40,r:14,t:12,b:26},plot=height-pad.t-pad.b;
  const xs=points.map(point=>new Date(point.x).getTime()),min=Math.min(...xs),max=Math.max(...xs);
  const x=value=>pad.l+(max===min?0.5:(value-min)/(max-min))*(W-pad.l-pad.r),y=value=>pad.t+(1-value)*plot;
  const line=points.map((point,index)=>({point,cx:x(xs[index])})).filter(({point})=>!point.hollow&&point.y!=null);
  return <svg className="admin-chart" viewBox={`0 0 ${W} ${height}`} role="img" aria-label={label}>
    {[0,.5,1].map(tick=><g key={tick}><line className="grid" x1={pad.l} x2={W-pad.r} y1={y(tick)} y2={y(tick)}/><text x={pad.l-6} y={y(tick)+3} textAnchor="end">{tick*100}%</text></g>)}
    {points.map((point,index)=>point.marker&&<line key={`m${index}`} className="change" x1={x(xs[index])} x2={x(xs[index])} y1={pad.t} y2={pad.t+plot}><title>{point.marker}</title></line>)}
    {line.length>1&&<polyline className="line" points={line.map(({point,cx})=>`${cx},${y(point.y)}`).join(' ')}/>}
    {points.map((point,index)=>point.y!=null&&<circle key={index} className={`dot${point.hollow?' subset':''}${point.chosen?' chosen':''}${point.onPick?' pick':''}`} cx={x(xs[index])} cy={y(point.y)} r={point.chosen?5:4}
      {...point.onPick&&{role:'button',tabIndex:0,onClick:point.onPick,onKeyDown:pressed(point.onPick),'aria-label':point.title}}><title>{point.title}</title></circle>)}
    {points.length>0&&<><text x={pad.l} y={height-6}>{day(min)}</text><text x={W-pad.r} y={height-6} textAnchor="end">{day(max)}</text></>}
  </svg>;
}

// Cases × runs, each cell the case's status in that run (dashed: the case was not in that run).
// With `onPick(run, caseId)` a column date opens that run and a cell opens that case in that run.
export function Heatmap({columns,rows,highlight,labels,onPick}) {
  const chosen=useRef(null);
  useEffect(()=>{chosen.current?.scrollIntoView?.({block:'center'});},[highlight]);
  const cell=(row,status,index)=>{
    const run=columns[index],title=`${row.case_id} in ${run.name} (${formatDateTime(run.started_at)}): ${status||'not in this run'}`;
    if(!status||!onPick)return <td key={index} className={status||'none'} title={title}/>;
    const open=()=>onPick(run.name,row.case_id);
    return <td key={index} className={`${status} pick`} title={`${title}. Open this case`} role="button" tabIndex={0} aria-label={`${title}. Open this case`}
      onClick={open} onKeyDown={pressed(open)} data-testid={`admin-heat-${row.case_id}-${index}`}/>;
  };
  const when=value=><>{day(value)}<br/>{clock(value)}</>;  // runs on the same day differ by their time
  return <div className="admin-table-wrap"><table className="admin-heatmap">
    <thead><tr><th>Case</th>{columns.map(column=><th key={column.name} title={`${column.name} · ${formatDateTime(column.started_at)}`} className="admin-heat-col">
      {onPick?<button className="admin-heat-run" onClick={()=>onPick(column.name,null)} aria-label={`Open the run of ${formatDateTime(column.started_at)}`}>{when(column.started_at)}</button>:when(column.started_at)}</th>)}<th>Pattern</th></tr></thead>
    <tbody>{rows.map(row=><tr key={row.case_id} className={highlight===row.case_id?'chosen':''} ref={highlight===row.case_id?chosen:undefined}>
      <th>{row.case_id}</th>
      {row.statuses.map((status,index)=>cell(row,status,index))}
      <td className="admin-heat-label"><span className={`admin-status admin-${row.label}`}>{labels[row.label]||row.label}</span></td>
    </tr>)}</tbody></table></div>;
}

// What the heatmap colours mean.
export function HeatLegend() {
  return <div className="admin-legend admin-heat-legend">
    {[['pass','Pass'],['fail','Fail (a rule check scored 0)'],['error','Error (the case crashed)'],['skipped','Skipped'],['none','Not in this run']].map(([kind,text])=>
      <span key={kind}><i className={`admin-heat-key ${kind}`}/>{text}</span>)}
  </div>;
}

// Horizontal bars with their numbers as text (counts or seconds); `value` is drawn against the largest.
export function Bars({rows,format=value=>value,label}) {
  const top=Math.max(...rows.map(row=>row.value||0),0);
  return <ul className="admin-bars" aria-label={label}>{rows.map(row=><li key={row.label} title={row.title||''}>
    <span className="admin-bar-label">{row.label}</span>
    <span className="admin-bar-track"><i style={{width:`${top?(row.value||0)/top*100:0}%`}}/></span>
    <span className="admin-bar-value">{format(row.value)}</span></li>)}</ul>;
}

// One bar split into parts (shares of 1), with a legend that carries the numbers.
export function Stack({parts,label}) {
  return <div className="admin-stack-wrap">
    <div className="admin-stack" role="img" aria-label={label}>{parts.map((part,index)=><i key={part.label} className={`tone-${index}`} style={{width:`${(part.share||0)*100}%`}} title={part.label}/>)}</div>
    <ul className="admin-stack-legend">{parts.map((part,index)=><li key={part.label}><i className={`tone-${index}`}/>{part.label} <b>{part.text}</b></li>)}</ul>
  </div>;
}

// Steps of one request on a shared time axis.
export function Waterfall({rows,total,format}) {
  return <ol className="admin-waterfall">{rows.map((row,index)=><li key={index} title={`${row.name} · ${format(row.latency_s)}`}>
    <span className="admin-wf-name" style={{paddingLeft:`${Math.min(row.depth,6)*10}px`}}>{row.name}{row.agent?` · ${row.agent}`:''}{row.tool&&row.tool!==row.name?` · ${row.tool}`:''}</span>
    <span className="admin-wf-track"><i className={row.error?'bad':row.run_type==='llm'?'llm':''} style={{left:`${row.left*100}%`,width:`${row.width*100}%`}}/></span>
    <span className="admin-bar-value">{format(row.latency_s)}</span></li>)}
    <li className="admin-wf-axis"><span/><span className="admin-wf-track"><em>0 s</em><em>{format(total)}</em></span><span/></li></ol>;
}
