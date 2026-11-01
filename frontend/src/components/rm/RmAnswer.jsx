import {useRef,useState} from 'react';
import Icon from '../Icon';
import DraftReview from './DraftReview';
import DocumentationReview from './DocumentationReview';
import {KIND_LABELS,REPORT_TITLES,reportText,validationNote} from '../../lib/rm';
import {formatDateTime} from '../../lib/format';

// One RM assistant reply, laid out as a short report: a title, the summary, then sections of points. Recorded facts are
// plain text; only what is not a recorded fact is marked (Client said, AI suggestion, Gap, Guidance). Each point ends with
// numbered references to the records it came from; a number opens that record in the Sources list at the end of the
// report (in place, no side panel). Drafts and documentation are review cards: nothing is sent or saved until approved.
export default function RmAnswer({reply:given,onEdit,onOption,token,onDraftChange,onDocumentationChange,onAsk,latest,client,compact=false}) {
  // The RM can edit the report herself (rewrite or remove points, change the summary) instead of asking the assistant.
  // Edited points are marked "Edited by you"; their source numbers still show what the original point was based on.
  const [local,setLocal]=useState(null),[work,setWork]=useState(null);
  const reply=local||given;
  const editing=!!work;
  function startEdit() {setWork({summary:reply.summary||'',sections:(reply.sections||[]).map(s=>({...s,items:s.items.map(i=>({...i}))}))});}
  function setItem(si,ii,patch) {setWork(w=>({...w,sections:w.sections.map((s,x)=>x!==si?s:{...s,items:s.items.map((i,y)=>y!==ii?i:{...i,...patch})})}));}
  function finishEdit() {
    const before=reply.sections||[];
    const sections=work.sections.map((s,si)=>({...s,items:s.items.map((i,ii)=>({i,ii})).filter(({i})=>!i.removed&&i.text.trim()).map(({i,ii})=>{
      const original=before[si]?.items[ii];
      return original&&original.text===i.text?i:{...i,text:i.text.trim(),edited:true};})})).filter(s=>s.items.length);
    const next={...reply,summary:work.summary.trim(),sections,edited:true};
    setWork(null);setLocal(next);onEdit?.(next);
  }
  const sources=reply.sources||[];
  const number=new Map(sources.map((s,i)=>[s.id,i+1]));
  const [open,setOpen]=useState(()=>new Set()),[copied,setCopied]=useState(false);
  const root=useRef(null);
  function show(id) {
    setOpen(previous=>new Set(previous).add(id));
    requestAnimationFrame(()=>{const row=root.current?.querySelector(`[data-source-row="${CSS.escape(id)}"]`);row?.scrollIntoView({behavior:'smooth',block:'nearest'});row?.focus({preventScroll:true});});
  }
  function toggle(id) {setOpen(previous=>{const next=new Set(previous);next.has(id)?next.delete(id):next.add(id);return next;});}
  async function copy() {
    try {await navigator.clipboard.writeText(reportText(reply,client));setCopied(true);setTimeout(()=>setCopied(false),2000);}catch{}
  }
  function print() {
    // Only this report is printed (browser "Save as PDF" works the same way): a copy goes into a top-level print root
    // and everything else on the page is hidden while printing.
    const holder=document.createElement('div');holder.id='rm-print-root';
    const copyNode=root.current.cloneNode(true);copyNode.querySelectorAll('.rm-report-tools').forEach(n=>n.remove());
    holder.appendChild(copyNode);document.body.appendChild(holder);document.body.classList.add('printing-report');
    const done=()=>{holder.remove();document.body.classList.remove('printing-report');window.removeEventListener('afterprint',done);};
    window.addEventListener('afterprint',done);
    requestAnimationFrame(()=>window.print());
  }
  // Called as a function (not a component) so a textarea keeps focus while typing.
  function point(item,si,ii) {
    if(editing){const w=work.sections[si].items[ii];return <li key={ii} className={`rm-item kind-${item.kind} editing ${w.removed?'removed':''}`}>
      <textarea aria-label="Edit point" rows={Math.max(1,Math.ceil(w.text.length/90))} value={w.text} disabled={w.removed}
        onChange={e=>setItem(si,ii,{text:e.target.value})}/>
      <button type="button" className="rmw-mini" onClick={()=>setItem(si,ii,{removed:!w.removed})}>{w.removed?'Keep':'Remove'}</button></li>;}
    return <li key={ii} className={`rm-item kind-${item.kind}`} data-kind={item.kind}>
      <span className="rm-item-text">{item.text}{item.edited&&<span className="rm-edited"> Edited by you</span>}
        {item.owner==='specialist'&&<span className="rm-owner"> (specialist team)</span>}
        {item.source_ids?.filter(id=>number.has(id)).map(id=><button key={id} type="button" className="rm-ref" data-source-id={id}
          aria-label={`Source ${number.get(id)}: ${sources[number.get(id)-1].title}`} title={sources[number.get(id)-1].title} onClick={()=>show(id)}>{number.get(id)}</button>)}</span>
    </li>;
  }
  const index_of=section=>(reply.sections||[]).indexOf(section);
  const title=REPORT_TITLES[reply.intent]||'RM assistant';
  const report=(reply.sections||[]).length>0&&reply.status!=='denied';
  return <article ref={root} className={`rm-answer ${report?'rm-report':''} ${compact?'compact':''}`} data-testid="rm-answer" data-intent={reply.intent} data-status={reply.status}>
    {report&&!compact&&<header className="rm-report-head">
      <div><span className="eyebrow">{reply.origin==='ai'?'AI-generated from your records':'From your records'}</span>
        <h2>{title}{client?<span> · {client}</span>:null}</h2></div>
      <div className="rm-report-tools">
        {editing?<><button type="button" className="rmw-tool" onClick={()=>setWork(null)}>Cancel</button>
          <button type="button" className="rmw-tool primary" onClick={finishEdit}><Icon name="check"/>Done</button></>
        :<button type="button" className="rmw-tool" onClick={startEdit}><Icon name="document"/>Edit</button>}
        {!editing&&<><button type="button" className="rmw-tool" onClick={copy}><Icon name={copied?'check':'document'}/>{copied?'Copied':'Copy'}</button>
        <button type="button" className="rmw-tool" onClick={print}><Icon name="download"/>Print or save PDF</button></>}</div>
    </header>}
    {editing?<textarea className="rm-report-summary rm-edit-summary" aria-label="Edit summary" rows={3} value={work.summary} onChange={e=>setWork({...work,summary:e.target.value})}/>
      :reply.summary&&<p className={report?'rm-report-summary':'rm-answer-summary'}>{reply.summary}</p>}
    {reply.edited&&!editing&&<p className="rm-validation"><Icon name="info"/>You edited this report. Edited points are yours, not the assistant’s; the numbers show the records the original point used.</p>}
    {reply.status==='denied'&&<p className="rm-denied" role="alert"><Icon name="lock"/>Access to clients outside your book is refused and recorded in the audit log.</p>}
    {(reply.sections||[]).map((section,index)=><section className="rm-answer-section" key={index}>
      {section.heading&&<h3>{section.heading}</h3>}
      {runs(section.items).map((run,r)=>run.kind==='record'
        ?<ul key={r} className="rm-points">{run.items.map(({item,index})=>point(item,index_of(section),index))}</ul>
        :<div key={r} className={`rm-group kind-${run.kind}`} data-kind={run.kind}><span className={`rm-kind ${run.kind}`}>{GROUP_LABELS[run.kind]||KIND_LABELS[run.kind]||run.kind}</span>
          <ul className="rm-points">{run.items.map(({item,index})=>point(item,index_of(section),index))}</ul></div>)}
    </section>)}
    {reply.clarification&&<div className="follow-ups rm-options" data-testid="rm-clarification"><span>{reply.clarification.question}</span>
      {reply.clarification.options.map(option=><button key={option.label} type="button" disabled={!latest} onClick={()=>onOption(option)}>{option.label}</button>)}</div>}
    {reply.draft&&<DraftReview draft={reply.draft} token={token} onChange={onDraftChange} onAsk={onAsk} latest={latest}/>}
    {reply.documentation&&<DocumentationReview documentation={reply.documentation} token={token} onChange={onDocumentationChange} latest={latest} onSource={s=>show(s.id)} sources={reply.sources}/>}
    {validationNote(reply.validation)&&<p className="rm-validation" data-testid="rm-validation"><Icon name="shield"/>{validationNote(reply.validation)}</p>}
    {reply.notice&&<p className="rm-validation"><Icon name="info"/>{reply.notice}</p>}
    {sources.length>0&&<section className="rm-sources" data-testid="rm-sources">
      <h3>Sources</h3>
      <p className="rm-sources-hint">Numbers in the text point to these records. Select a record to see what it says.</p>
      <ol>{sources.map((s,i)=><li key={s.id} data-source-row={s.id} tabIndex={-1} className={open.has(s.id)?'open':''}>
        <button type="button" className="rm-source-toggle" aria-expanded={open.has(s.id)} onClick={()=>toggle(s.id)}>
          <span className="rm-source-num">{i+1}</span><span className="rm-source-title">{s.title}</span>
          <small>{kindWord(s.kind)}{asOf(s.as_of)?` · ${asOf(s.as_of)}`:''}</small><Icon name="chevron"/></button>
        {open.has(s.id)&&<SourceDetails source={s}/>}
      </li>)}</ol>
    </section>}
  </article>;
}

const GROUP_LABELS={client_stated:'Client said',suggestion:'AI suggestions',gap:'Gaps',guidance:'Bank guidance'};
// Consecutive points of the same kind share one label (one "Client said" above three statements, not three tags).
function runs(items) {
  const out=[];
  items.forEach((item,index)=>{const last=out.at(-1);if(last&&last.kind===item.kind)last.items.push({item,index});else out.push({kind:item.kind,items:[{item,index}]});});
  return out;
}

function kindWord(kind) {return kind==='client_stated'?'Client said':kind==='guidance'?'Bank guidance':kind==='note'?'Meeting note':kind==='ticket'?'Request':kind==='calendar'?'Calendar':kind==='consent'?'Shared by client':'Bank record';}
function asOf(value) {if(!value||value==='None')return '';const d=new Date(value);return Number.isNaN(d.getTime())?value:`as of ${formatDateTime(d)}`;}

const FIELD_LABELS={written_by_current_rm:'Written by you',met_on_text:'Date',met_on:'Date',client_requests:'Client requests',has_source_notes:'Original notes kept',
  rm_owned:'Your request',client_safe_status:'What the client sees',target_sgt:'Target',created_sgt:'Received',at_sgt:'When'};
// A source record in plain words: field names become labels; nested lists become short rows.
export function SourceDetails({source}) {
  const label=key=>FIELD_LABELS[key]||key.replace(/_text$/,'').replace(/_/g,' ').replace(/\b(sgt|id)\b/g,'').trim().replace(/^./,c=>c.toUpperCase());
  function value(v,depth=0) {
    if(v===null||v===undefined||v==='')return <span className="rm-none">Not recorded</span>;
    if(Array.isArray(v))return v.length?<ul className="rm-source-list">{v.map((x,i)=><li key={i}>{value(x,depth+1)}</li>)}</ul>:<span className="rm-none">None</span>;
    if(typeof v==='object')return <dl className="rm-source-fields">{Object.entries(v).filter(([k])=>!(`${k}_text` in v)).filter(([k,x])=>!(x===null&&/expires/.test(k))&&(!/(^|_)id$|_at$|^starts$|^ends$/.test(k)||depth>2)).map(([k,x])=><div key={k}><dt>{label(k)}</dt><dd>{value(x,depth+1)}</dd></div>)}</dl>;
    if(typeof v==='boolean')return v?'Yes':'No';
    return /^[a-z]+(_[a-z]+)+$/.test(String(v))?String(v).replace(/_/g,' '):String(v);
  }
  return <div className="rm-source" data-testid="rm-source">
    {source.record===undefined?<p className="rm-none">The full record is not kept with saved conversations. Ask again to read the current record.</p>:value(source.record)}
  </div>;
}
