import {useEffect,useState} from 'react';
import {supabase} from '../lib/supabase';
import {loadRequests,loadSlots} from '../lib/relationship';

function initialRange() {
  const now=new Date(Date.now()+8*60*60*1000);
  return {from_date:now.toISOString().slice(0,10),to_date:new Date(now.getTime()+6*24*60*60*1000).toISOString().slice(0,10),limit:10};
}
function slotTime(value) {
  return new Date(value).toLocaleString('en-SG',{timeZone:'Asia/Singapore',weekday:'short',day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
}

export default function RelationshipDetails() {
  const [query,setQuery]=useState(initialRange);
  const [range,setRange]=useState(query);
  const [result,setResult]=useState(null);
  const [error,setError]=useState('');
  useEffect(()=>{
    const controller=new AbortController();
    setResult(null);setError('');
    async function load() {
      try {
        const {data,error:sessionError}=await supabase.auth.getSession();
        if(sessionError)throw sessionError;
        if(!data.session)throw new Error('Please sign in again to view relationship support.');
        const [requests,slots]=await Promise.all([loadRequests(data.session.access_token,controller.signal),loadSlots(data.session.access_token,range,controller.signal)]);
        if(!controller.signal.aborted)setResult({requests,slots});
      } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    }
    load();
    return ()=>controller.abort();
  },[range]);
  return <section aria-label="Relationship support" className="relationship-details">
    <h3>Requests and appointment times</h3>
    <p>Review your synthetic requests and suggested thirty-minute times for your current relationship manager. Times are shown in Singapore time. A suggestion is not a hold or booking.</p>
    <form className="relationship-range" onSubmit={event=>{event.preventDefault();setRange({...query});}}>
      <label>From<input type="date" aria-label="Appointment search start" data-testid="slots-from" required value={query.from_date} onChange={event=>setQuery({...query,from_date:event.target.value})}/></label>
      <label>Through<input type="date" aria-label="Appointment search end" data-testid="slots-to" required value={query.to_date} onChange={event=>setQuery({...query,to_date:event.target.value})}/></label>
      <button className="primary-button" data-testid="refresh-relationship" type="submit">Refresh</button>
    </form>
    {!result&&!error&&<p role="status">Loading relationship records…</p>}
    {error&&<p role="alert" data-testid="relationship-error">{error}</p>}
    {result&&<>
      <h4>Your requests</h4>
      {!result.requests.data.length&&<p data-testid="requests-empty">No requests were found for your demo profile.</p>}
      <ul className="relationship-list" data-testid="relationship-requests">{result.requests.data.map(item=><li key={item.request_id} data-testid="relationship-request"><strong>{item.who}</strong><p>{item.what}</p><span>Status: {item.status} · Urgency: {item.urgency}{item.deadline&&` · Due: ${item.deadline}`}</span></li>)}</ul>
      <h4>Suggested appointment times</h4>
      {!result.slots.data.length&&<p data-testid="slots-empty">No available times were found in this range for an assigned manager.</p>}
      <ul className="relationship-list" data-testid="relationship-slots">{result.slots.data.map(item=><li key={`${item.advisor_id}:${item.starts_at}`} data-testid="relationship-slot"><time dateTime={item.starts_at}>{slotTime(item.starts_at)}</time> – <time dateTime={item.ends_at}>{slotTime(item.ends_at)}</time></li>)}</ul>
      <p className="source-notice">Read only. No request was sent, and no appointment was held or booked.</p>
    </>}
  </section>;
}
