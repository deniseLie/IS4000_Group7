import {useEffect,useState} from 'react';

export default function ZoomSpeakerReview({evidence,busy,onSave}){
  const [mapping,setMapping]=useState({});
  useEffect(()=>{setMapping(evidence.mapping);},[evidence.source_id]);
  const speakers=[...new Set(evidence.segments.map(segment=>segment.speaker_key))];
  return <form className="meeting-evidence-form" data-testid="meeting-zoom-speaker-review" onSubmit={event=>{event.preventDefault();onSave(mapping);}}>
    <h3>Review captured speakers</h3><p className="rm-small">Zoom speaker labels do not verify a person's role. Review the quoted speech before assigning a role. Unknown speakers stay unassigned. Saving creates a new evidence version and queues fresh draft minutes.</p>
    {speakers.map(speaker=><div key={speaker}><label className="form-field">{speaker.replace('speaker','Speaker ')}<select data-testid={`meeting-zoom-role-${speaker}`} value={mapping[speaker]||'unmapped'} disabled={busy} onChange={event=>setMapping(value=>({...value,[speaker]:event.target.value}))}>
      <option value="unmapped">Role not confirmed</option><option value="rm">Relationship manager</option><option value="client">Client</option></select></label>
      <details><summary>Review captured statements</summary>{evidence.segments.filter(segment=>segment.speaker_key===speaker).map(segment=><blockquote key={segment.cue_id}>{evidence.cues.find(cue=>cue.cue_id===segment.cue_id).text}</blockquote>)}</details></div>)}
    <button className="bank-chat-logout" data-testid="meeting-zoom-save-speakers" disabled={busy}>Confirm roles and regenerate draft</button>
  </form>;
}
