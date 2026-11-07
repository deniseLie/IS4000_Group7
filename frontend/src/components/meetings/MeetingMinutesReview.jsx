import {useState} from 'react';

export default function MeetingMinutesReview({review,busy,onReview,onRegenerate}){
  const [note,setNote]=useState('');
  return <div data-testid="meeting-minutes-review"><h4>Corrections and review</h4>
    {review&&<><p className="rmw-warn">{review.outcome==='changes'?'Changes requested':'Rejected'}: {review.note}</p><p className="rm-small">This version cannot be approved.</p></>}
    <label className="form-field">Explain the correction or rejection<textarea data-testid="meeting-minutes-review-note" value={note} maxLength={1000} disabled={busy} onChange={event=>setNote(event.target.value)}/></label>
    <button className="bank-chat-logout" data-testid="meeting-minutes-changes" disabled={busy||!note.trim()} onClick={()=>onReview('changes',note.trim())}>Request changes to minutes</button>
    <button className="bank-chat-logout" data-testid="meeting-minutes-reject" disabled={busy||!note.trim()} onClick={()=>onReview('reject',note.trim())}>Reject minutes</button>
    {review?.outcome==='changes'&&<button className="bank-chat-logout" data-testid="meeting-minutes-regenerate" disabled={busy} onClick={onRegenerate}>Regenerate with review note</button>}
  </div>;
}
