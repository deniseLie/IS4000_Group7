import {useEffect,useState} from 'react';
import {supabase} from '../lib/supabase';
import {meetingRequest} from '../lib/meetingLifecycle';
import {formatDate} from '../lib/format';

// Data sharing › Meeting recording (0149): one yes for every meeting, so she is not asked on each meeting page. Off means
// she is asked before each meeting again. She can still say "Not for this meeting" on one meeting's page.
async function token() {
  const {data,error}=await supabase.auth.getSession();
  if(error)throw error;
  if(!data.session)throw new Error('Please sign in again.');
  return data.session.access_token;
}
export default function TranscriptStanding({rm}) {
  const [state,setState]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  useEffect(()=>{const c=new AbortController();(async()=>{try{setState(await meetingRequest('/transcript-standing','GET',null,await token(),c.signal));}
    catch(failure){if(!c.signal.aborted)setError(failure.message);}})();return ()=>c.abort();},[]);
  async function set(on) {
    setBusy(true);setError('');
    try {setState(await meetingRequest('/transcript-standing','POST',{on},await token()));} catch(failure) {setError(failure.message);} finally {setBusy(false);}
  }
  const Rm=rm?rm[0].toUpperCase()+rm.slice(1):'Your relationship manager';
  return <div className="sharing-switch" data-testid="transcript-standing" data-on={!!state?.on}>
    <div><strong>Use a transcript for every meeting</strong>
      <small>{!state?(error?'':'Loading…'):state.on?`On since ${formatDate(state.agreed_at)}. ${Rm} can use the call transcript to write the minutes of every meeting, and you aren’t asked each time. To skip one meeting, choose “Not for this meeting” on its page. Approved minutes stay.`
        :`Off. You’re asked before each meeting whether a transcript may be used. Typed notes are used otherwise.`}</small>
      {error&&<small role="alert" className="sharing-alert">{error}</small>}</div>
    {state&&<button type="button" role="switch" aria-checked={!!state.on} aria-label="Use a transcript for every meeting" className="sharing-toggle"
      data-testid="transcript-standing-toggle" disabled={busy} onClick={()=>set(!state.on)}><span/></button>}
  </div>;
}
