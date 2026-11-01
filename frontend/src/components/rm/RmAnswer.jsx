import Icon from '../Icon';
import DraftReview from './DraftReview';
import DocumentationReview from './DocumentationReview';
import {KIND_LABELS,validationNote} from '../../lib/rm';
import {formatDateTime} from '../../lib/format';

// One RM assistant reply: a direct summary, then sections of items. Every item says what it is (a recorded fact, what the
// client said, an AI suggestion or a gap) and links to the records it came from. Drafts and documentation are review
// cards: nothing is sent or saved until the RM approves it there.
export default function RmAnswer({reply,onSource,onOption,token,onDraftChange,onDocumentationChange,onAsk,latest}) {
  const sources=new Map((reply.sources||[]).map(s=>[s.id,s]));
  return <div className="rm-answer" data-testid="rm-answer" data-intent={reply.intent} data-status={reply.status}>
    {reply.summary&&<p className="rm-answer-summary">{reply.summary}</p>}
    {reply.status==='denied'&&<p className="rm-denied" role="alert"><Icon name="lock"/>Access to clients outside your book is refused and recorded in the audit log.</p>}
    {(reply.sections||[]).map((section,index)=><section className="rm-answer-section" key={index}>
      {section.heading&&<h3>{section.heading}</h3>}
      <ul>{section.items.map((item,i)=><li key={i} className={`rm-item kind-${item.kind}`} data-kind={item.kind}>
        <span className={`rm-kind ${item.kind}`}>{KIND_LABELS[item.kind]||item.kind}</span>
        <span className="rm-item-text">{item.text}</span>
        {item.owner==='specialist'&&<span className="rm-owner">Specialist team</span>}
        {item.source_ids?.length>0&&<span className="rm-cites">{item.source_ids.map(id=>sources.has(id)?
          <button key={id} type="button" className="rm-cite" data-source-id={id} onClick={()=>onSource(sources.get(id))} title={sources.get(id).title}>{shortLabel(sources.get(id))}</button>:null)}</span>}
      </li>)}</ul>
    </section>)}
    {reply.clarification&&<div className="follow-ups rm-options" data-testid="rm-clarification"><span>{reply.clarification.question}</span>
      {reply.clarification.options.map(option=><button key={option.label} type="button" disabled={!latest} onClick={()=>onOption(option)}>{option.label}</button>)}</div>}
    {reply.draft&&<DraftReview draft={reply.draft} token={token} onChange={onDraftChange} onAsk={onAsk} latest={latest}/>}
    {reply.documentation&&<DocumentationReview documentation={reply.documentation} token={token} onChange={onDocumentationChange} latest={latest} onSource={onSource} sources={reply.sources}/>}
    {validationNote(reply.validation)&&<p className="rm-validation" data-testid="rm-validation"><Icon name="shield"/>{validationNote(reply.validation)}</p>}
    {reply.notice&&<p className="rm-validation"><Icon name="info"/>{reply.notice}</p>}
    {reply.sources?.length>0&&<details className="answer-sources" data-testid="rm-sources"><summary>Sources ({reply.sources.length})</summary>
      <p>Records the assistant used, as of when they were read. A citation shows where a point came from; please check anything important.</p>
      {reply.sources.map(s=><div key={s.id}><button type="button" className="source-link" onClick={()=>onSource(s)}>{s.title}</button> <small>{asOf(s.as_of)}</small></div>)}</details>}
  </div>;
}

function shortLabel(source) {
  const id=source.id;
  if(id.startsWith('ticket-'))return id.slice(7);
  if(id.startsWith('note-'))return `Note ${id.slice(5,15)}`;
  if(id.startsWith('kb-'))return 'Guidance';
  if(id.startsWith('category-'))return 'Request rules';
  if(id==='shared-note')return 'Client’s note';
  if(id.startsWith('told-'))return 'What client was told';
  if(id.startsWith('context-'))return 'Handover context';
  if(id==='told')return 'What client was told';
  if(id.startsWith('followup-'))return source.title.split(':')[0];
  return source.title.split(' (')[0].split(':')[0].slice(0,28);
}
function asOf(value) {if(!value||value==='None')return '';const d=new Date(value);return Number.isNaN(d.getTime())?value:`as of ${formatDateTime(d)}`;}

// A source record in plain words: field names become labels; nested lists become short rows.
export function SourceDetails({source}) {
  const label=key=>key.replace(/_/g,' ').replace(/\b(sgt|id)\b/g,'').trim().replace(/^./,c=>c.toUpperCase());
  function value(v,depth=0) {
    if(v===null||v===undefined||v==='')return <span className="rm-none">Not recorded</span>;
    if(Array.isArray(v))return v.length?<ul className="rm-source-list">{v.map((x,i)=><li key={i}>{value(x,depth+1)}</li>)}</ul>:<span className="rm-none">None</span>;
    if(typeof v==='object')return <dl className="rm-source-fields">{Object.entries(v).filter(([k,x])=>!(x===null&&/expires/.test(k))&&(!/(^|_)id$|_at$|^starts$|^ends$/.test(k)||depth>2)).map(([k,x])=><div key={k}><dt>{label(k)}</dt><dd>{value(x,depth+1)}</dd></div>)}</dl>;
    if(typeof v==='boolean')return v?'Yes':'No';
    return /^[a-z]+(_[a-z]+)+$/.test(String(v))?String(v).replace(/_/g,' '):String(v);
  }
  return <div className="rm-source" data-testid="rm-source">
    <p className="source-notice">{source.kind==='client_stated'?'What the client said or shared.':source.kind==='guidance'?'Approved DemoBank guidance or request rules.':'Recorded in the bank’s demo records.'} {asOf(source.as_of)}</p>
    {source.record===undefined?<p className="rm-none">The full record is not kept with saved conversations. Ask again to read the current record.</p>:value(source.record)}
  </div>;
}
