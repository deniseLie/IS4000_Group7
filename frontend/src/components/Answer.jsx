
import {useState} from 'react';

// Sources as on the RM side: folded under "Sources · N", one numbered row per record with what kind of source it is.
// No dates or version numbers. A bank guide opens in place (a dropdown with its text and publisher), not in a dialog;
// her own records are named only.
function SourceRow({source,index,loadSource}) {
  const [open,setOpen]=useState(false),[details,setDetails]=useState(null),[error,setError]=useState('');
  async function toggle() {
    if(open){setOpen(false);return;}
    setOpen(true);
    if(details||!loadSource)return;
    try{setError('');setDetails(await loadSource(source));}catch{setError('This source could not be loaded. Please retry.');}
  }
  if(!source.url)return <li><span className="answer-source-row" data-source-id={source.id}><span className="rm-source-num">{index+1}</span><span className="answer-source-title">{source.title}</span><small>Your records</small></span></li>;
  return <li className={open?'open':''}>
    <button type="button" className="answer-source-row" data-source-id={source.id} aria-expanded={open} onClick={toggle}>
      <span className="rm-source-num">{index+1}</span><span className="answer-source-title">{source.title}</span><small>Bank guide</small><span className="answer-source-chev" aria-hidden="true">▾</span></button>
    {open&&<div className="answer-source-details" role="region" aria-label="Source details">
      {error?<p role="alert">{error}</p>:!details?<p className="rm-small">Loading…</p>:<>
        {details.notice&&<p className="source-notice">{details.notice}</p>}
        <p>{details.content}</p>
        <p className="source-publisher">Publisher: {details.publisher}</p>
        {details.reference_url&&<p><a href={details.reference_url} target="_blank" rel="noopener noreferrer">Read the original source <span aria-hidden="true">↗</span></a></p>}
        {details.current===false&&<p role="status">This source needs review and is not used for new answers.</p>}</>}
      <button type="button" className="link-button" onClick={()=>setOpen(false)}>Close source</button></div>}
  </li>;
}
function Sources({sources,loadSource}) {
  if(!sources?.length)return null;
  const seen=new Set(),unique=sources.filter(s=>!seen.has(s.title)&&seen.add(s.title));
  return <details className="answer-sources" data-testid="answer-sources"><summary data-count={unique.length}>Sources</summary>
    <ol className="answer-source-list">{unique.map((s,i)=><SourceRow key={s.id} source={s} index={i} loadSource={loadSource}/>)}</ol></details>;
}
// The page this answer is about ("Open Statements") sits inside the answer, next to its sources.
function PageLink({link}) {
  if(!link)return null;
  return <div className="answer-page-links"><button type="button" className="answer-page-link" data-testid={link.testid||'page-link'} onClick={link.go}>{link.label} <span aria-hidden="true">→</span></button>
    {link.second&&<button type="button" className="answer-page-second" data-testid={link.second.testid} aria-expanded={link.second.expanded} onClick={link.second.go}>{link.second.label} <span aria-hidden="true">{link.second.expanded?'▴':'▾'}</span></button>}</div>;
}
// Page names as the app shows them today, and no repeating the record's own title back to her.
export function tidy(text) {
  return String(text||'').replace(/\bCompliance review\b/g,'Periodic review').replace(/\bcompliance review\b/g,'periodic review')
    .replace(/\bMy requests\b/g,'Requests').replace(/\s+titled\s+["“]([^"”]{1,80})["”](,?)/g,(_,title,comma)=>comma||(/,$/.test(title)?',':''));
}

export default function Answer({message,pendingText,onSource,loadSource,link}) {
  const parts=message.sections?.length>1;  // several questions in one message: one answer, its parts numbered 1, 2, 3
  if(message.sections?.length)return <div className={`answer-sections ${parts?'numbered':''}`} data-testid="answer-sections">{message.sections.map((section,index)=><section className="answer-section" key={section.task_id} data-domain={section.domain} data-priority={section.priority} data-response-origin={section.response_origin} data-prompt-version={section.prompt_version} data-pending={section.pending?'true':undefined} aria-busy={section.pending||undefined}>
    {parts&&<span className="doc-n answer-part" aria-label={`Part ${index+1}`}>{index+1}</span>}
    {section.priority==='P0'&&<p className="answer-urgent">Urgent</p>}
    {section.pending?<span className="answer-text loading" role="status" data-testid="section-placeholder"><span className="skeleton-text" aria-hidden="true"/><span className="sr-only">Preparing this part…</span></span>
      :<span className="answer-text">{tidy(section.answer)}</span>}
    {section.response_origin==='validation'&&<small className="answer-origin">Could not verify</small>}
    {section.decision&&<p className="answer-decision" data-testid="answer-decision">{section.decision}</p>}
    {!parts&&<div className="answer-foot"><Sources sources={section.sources} loadSource={loadSource}/>{index===message.sections.length-1&&<PageLink link={link}/>}</div>}
  </section>)}{parts&&<div className="answer-foot"><Sources sources={message.sections.flatMap(section=>section.sources||[])} loadSource={loadSource}/><PageLink link={link}/></div>}</div>;
  return <>{message.content?<span className="answer-text">{tidy(message.content)}</span>:pendingText?<span className="answer-text typing" role="status"><span className="typing-dots" aria-hidden="true"><i/><i/><i/></span>{pendingText}</span>:<span className="answer-text"/>}<div className="answer-foot"><Sources sources={message.sources} loadSource={loadSource}/><PageLink link={link}/></div></>;
}
