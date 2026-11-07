import {useState} from 'react';

const labels={record:'Recorded',client_said:'Client shared',ai_suggestion:'AI suggestion',gap:'Information gap'};

export default function MeetingPreparation({prepared,busy,onPrepare,onRefresh,onDecision,onDownload}){
  const [note,setNote]=useState('');
  return <section className="meeting-minutes" data-testid="meeting-preparation" aria-label="Internal meeting brief">
    <h3>Internal meeting brief</h3><p className="rm-small">For your preparation. Each item is checked against the client’s current sharing choices when you open or approve it.</p>
    {!prepared&&<button className="bank-chat-logout" data-testid="meeting-prepare-brief" disabled={busy} onClick={onPrepare}>Prepare brief</button>}
    {prepared&&<><p className="act-pill">Version {prepared.card.version} · {prepared.sources_changed?'needs review':prepared.decision.replaceAll('_',' ')}</p>
      {prepared.sources_changed&&<p className="rmw-warn" data-testid="meeting-brief-sources-changed">Source records or sharing choices changed. Refresh this brief before approval. Unavailable information has been withheld.</p>}
      {prepared.card.sections.map((section,i)=><div key={i}><h4>{section.heading}</h4>{section.items.map((item,j)=><div key={j}><p><span className="act-pill">{labels[item.label]||item.label}</span> {item.text}</p>{item.sources.length>0&&<details><summary>Sources</summary><ul>{item.sources.map(source=><li key={source}>{source}</li>)}</ul></details>}</div>)}</div>)}
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
