import {useState} from 'react';
import {humanize,sourceTitle} from '../../lib/meetingLifecycle';
import {DocSection,NotKnown,OneLine,Point} from '../DocParts';


export default function MeetingPreparation({prepared,busy,onPrepare,onRefresh,onDecision,onDownload}){
  const [note,setNote]=useState('');
  // Design: one line on top, numbered sections, the source next to each point, gaps in "Not known yet".
  const sections=prepared?prepared.card.sections.map(section=>({...section,items:section.items.filter(item=>item.label!=='gap')})).filter(section=>section.items.length):[];
  const gaps=prepared?prepared.card.sections.flatMap(section=>section.items.filter(item=>item.label==='gap')):[];
  const first=sections[0]?.items[0];
  const src=item=>[...new Set(item.sources.map(sourceTitle))].map(title=>({key:title,title}));
  return <section className="meeting-minutes doc" data-testid="meeting-preparation" aria-label="Internal meeting brief">
    <div className="doc-head"><div><span className="eyebrow">Pre-meeting brief · internal</span><h3>Your brief for this meeting</h3>
      {prepared&&<p className="doc-meta"><span>Version {prepared.card.version}</span><span>{prepared.sources_changed?'needs review':prepared.decision==='approved'?'approved by you':prepared.decision.replaceAll('_',' ')}</span>
        {prepared.sources_changed&&<span className="doc-warn">● records changed since this version</span>}</p>}</div></div>
    {!prepared&&<><p className="rm-small">Prepared from the client’s records. Each point is checked against her current sharing choices when you open or approve it.</p>
      <button className="bank-chat-logout" data-testid="meeting-prepare-brief" disabled={busy} onClick={onPrepare}>Prepare brief</button></>}
    {prepared&&<>
      {prepared.sources_changed&&<p className="rmw-warn" data-testid="meeting-brief-sources-changed">Source records or sharing choices changed. Refresh this brief before approval. Unavailable information has been withheld.</p>}
      {first&&<OneLine>{humanize(prepared.card.summary||first.text)}</OneLine>}
      {sections.map((section,i)=><DocSection key={i} n={i+1} title={section.heading}>{section.items.map((item,j)=><Point key={j} kind={item.label} sources={src(item)}>{humanize(item.text)}</Point>)}</DocSection>)}
      {gaps.length>0&&<NotKnown>{gaps.map((item,j)=><Point key={j} kind="gap" sources={src(item)}>{humanize(item.text)}</Point>)}</NotKnown>}
      {prepared&&<footer className="doc-foot"><span>✓ {sections.reduce((n,section)=>n+section.items.filter(item=>item.sources.length).length,0)} points checked against her records</span>{gaps.length>0&&<span>{gaps.length} not known yet</span>}</footer>}
      {prepared.review_note&&<p className="rm-small">Review note: {prepared.review_note}</p>}
      {['changes_requested','rejected'].includes(prepared.decision)&&<p className="rmw-warn">Create and review a refreshed version before approval.</p>}
      <label className="form-field">Review note<textarea data-testid="meeting-brief-note" maxLength={1000} value={note} disabled={busy} onChange={event=>setNote(event.target.value)}/></label>
      <div className="meeting-actions"><button className="primary-button" data-testid="meeting-approve-brief" disabled={busy||prepared.sources_changed||prepared.decision!=='draft'} onClick={()=>onDecision('approve',note.trim()||null)}>Approve exact brief</button>
        <button className="bank-chat-logout" data-testid="meeting-brief-changes" disabled={busy||!note.trim()} onClick={()=>onDecision('changes',note.trim())}>Request changes</button>
        <button className="bank-chat-logout" data-testid="meeting-reject-brief" disabled={busy||prepared.decision==='rejected'} onClick={()=>onDecision('reject',note.trim()||null)}>Reject brief</button>
        <button className="bank-chat-logout" data-testid="meeting-refresh-brief" disabled={busy} onClick={onRefresh}>Create refreshed version</button>
        <button className="bank-chat-logout" data-testid="meeting-download-brief" disabled={busy||prepared.sources_changed} onClick={onDownload}>Download internal brief PDF</button></div></>}
  </section>;
}
