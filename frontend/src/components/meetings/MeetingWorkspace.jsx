import {useEffect,useRef,useState} from 'react';
import Icon from '../Icon';
import {approvalFor,checkedWorkspace,meetingRequest,notesCues} from '../../lib/meetingLifecycle';

export default function MeetingWorkspace({meetingId,token,onClose}){
  const [data,setData]=useState(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(true);
  const [outcome,setOutcome]=useState(''),[notes,setNotes]=useState(''),[file,setFile]=useState(null),[rmLabel,setRmLabel]=useState(''),[clientLabel,setClientLabel]=useState(''),[coverage,setCoverage]=useState('unknown');
  const [selected,setSelected]=useState([]),[completion,setCompletion]=useState({});
  const active=useRef(null),commands=useRef(new Map()),callTimes=useRef(new Map());
  const command=key=>{if(!commands.current.has(key))commands.current.set(key,crypto.randomUUID());return commands.current.get(key);};
  const documentedTime=key=>{if(!callTimes.current.has(key))callTimes.current.set(key,new Date().toISOString());return callTimes.current.get(key);};
  async function request(path,method,body,signal){return meetingRequest(path,method,body,await token(),signal);}
  async function load(signal){
    const capability=await request('/capabilities','GET',null,signal);
    if(!capability.enabled){if(!signal.aborted)setData({enabled:false});return;}
    const [who,appointment]=await Promise.all([request('/session','GET',null,signal),request(`/${meetingId}/appointment`,'GET',null,signal)]);
    let workspace=null;try{workspace=checkedWorkspace(await request(`/${meetingId}`,'GET',null,signal));}catch(failure){if(failure.status!==404)throw failure;}
    let link=null,linkError='';
    if(workspace)try{link=await request(`/${meetingId}/zoom`,'GET',null,signal);}catch(failure){if(failure.status!==409)throw failure;linkError=failure.message;}
    if(!signal.aborted){setData({enabled:true,who,appointment,workspace,link,linkError,capability});setSelected([]);}
  }
  async function run(work,message){
    const control=new AbortController();active.current?.abort();active.current=control;setBusy(true);setError('');setNotice('');
    try{await work(control.signal);await load(control.signal);if(!control.signal.aborted)setNotice(message);}
    catch(failure){if(failure.name!=='AbortError'&&!control.signal.aborted)setError(failure.message);}
    finally{if(!control.signal.aborted)setBusy(false);}
  }
  useEffect(()=>{setData(null);setOutcome('');setNotes('');setFile(null);commands.current.clear();callTimes.current.clear();run(async()=>{},'');return()=>active.current?.abort();},[meetingId]);
  const w=data?.workspace,d=w?.draft,isRm=data?.who?.advisor_id!==null&&data?.who?.advisor_id!==undefined;
  const post=(suffix,body,message)=>run(signal=>request(`/${meetingId}${suffix}`,'POST',body,signal),message);
  const approve=(suffix,extra,message)=>post(suffix,{...approvalFor(d,command(`${suffix}:${d.draft_id}:${d.content_hash}:${d.consent_epoch}:${JSON.stringify(extra)}`)),...extra},message);
  async function importFile(event){event.preventDefault();
    if(!file||file.size>1048576){setError('Choose a TXT or VTT file no larger than 1 MiB.');return;}
    await run(async signal=>{const bytes=await file.arrayBuffer();const text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),n=>n.toString(16).padStart(2,'0')).join('');const speakers=[];
      if(rmLabel.trim())speakers.push({label:rmLabel.trim(),role:'rm'});if(clientLabel.trim())speakers.push({label:clientLabel.trim(),role:'client'});
      await request(`/${meetingId}/transcript-import`,'POST',{revision:w.revision,filename:file.name,text,speakers,coverage,command_id:command(`import:${digest}:${JSON.stringify(speakers)}:${coverage}:${w.revision}`)},signal);
    },'Transcript imported. The Minutes Writer job is queued; refresh to check its result.');
  }
  async function generateNotes(event){event.preventDefault();await run(async signal=>{const cues=notesCues(notes);
    await request(`/${meetingId}/notes`,'POST',{revision:w.revision,cues,command_id:command(`notes:${notes}:${w.revision}`)},signal);
    await request(`/${meetingId}/generate-notes-draft`,'POST',{command_id:command(`draft:${notes}:${w.revision}`)},signal);
  },'Draft minutes are ready for review.');}
  return <section className="meeting-lifecycle rmw-card" data-testid="meeting-workspace" aria-label="Meeting notes and follow-up">
    <div className="rmw-card-head"><div><span className="eyebrow">Meeting lifecycle</span><h2><Icon name="calendar"/>Meeting notes and follow-up</h2></div>
      <div className="meeting-actions"><button className="bank-chat-logout" data-testid="meeting-refresh" disabled={busy} onClick={()=>run(async()=>{},'Workspace refreshed.')}><Icon name="refresh"/>Refresh</button>{onClose&&<button className="bank-chat-logout" onClick={onClose} disabled={busy}>Close</button>}</div></div>
    {busy&&<p className="loading" role="status">Updating meeting workspace…</p>}{error&&<p className="rm-error" role="alert">{error}</p>}{notice&&<p className="chat-toast" role="status">{notice}</p>}
    {data&&!data.enabled&&<p className="page-empty">Meeting notes and follow-up are not available yet.</p>}
    {data?.enabled&&<><p><strong>{data.appointment.purpose||'Meeting purpose needs to be agreed'}</strong></p>
      <p className="rm-small">{w?`Call status: ${w.state}. ${w.outcome}`:'Agree the intended outcome before opening this meeting workspace.'}</p>
      {!w&&<form onSubmit={event=>{event.preventDefault();post('/start',{notice_id:data.who.notice.notice_id,purpose:data.appointment.purpose,outcome,command_id:command(`start:${outcome}`)},'Meeting workspace opened.');}}>
        <label className="form-field">Intended outcome<textarea data-testid="meeting-outcome" value={outcome} onChange={e=>setOutcome(e.target.value)} maxLength={300} required disabled={busy}/></label>
        <button className="primary-button" data-testid="meeting-start" disabled={busy||!data.appointment.purpose||!outcome.trim()}>Open workspace</button></form>}
      {w&&<><div className="meeting-notice"><h3>Recording and transcript notice</h3><p>{w.notice}</p>
        {!w.notice_seen&&<button className="bank-chat-logout" data-testid="meeting-notice-seen" disabled={busy} onClick={()=>post('/notice-seen',{notice_id:w.notice_id,revision:w.revision},'Notice read for this meeting.')}>I have read this notice</button>}
        {!isRm&&<button className="bank-chat-logout" data-testid="meeting-revoke-transcript" disabled={busy} onClick={()=>post('/transcript-revoke',{command_id:command('revoke')},'Transcript access withdrawn. Existing minutes remain retained.')}>Withdraw transcript access</button>}
        <p className="rm-small">{w.recording_allowed?'Transcript intake is available.':'Transcript intake requires both participants to read the current notice and must not have been withdrawn.'} Reading the notice does not start Zoom recording.</p></div>
        {data.linkError&&<p className="rm-error" role="alert">{data.linkError}</p>}
        {data.link&&<a className="primary-button" data-testid="meeting-zoom-join" href={data.link.join_url} target="_blank" rel="noopener noreferrer">Join Zoom meeting</a>}
        {isRm&&!data.link&&data.appointment.format==='video'&&Date.parse(data.appointment.starts_at)>Date.now()&&<button className="bank-chat-logout" data-testid="meeting-create-zoom" disabled={busy||!data.capability.zoom_configured} onClick={()=>post('/zoom',{},'Zoom meeting link created.')}>Create Zoom link</button>}
        {isRm&&<><h3>Document the call</h3><p className="rm-small">These times are recorded by you. Automatic Zoom start/end events are not connected yet.</p>
          <button className="bank-chat-logout" data-testid="meeting-call-start" disabled={busy||w.state!=='scheduled'} onClick={()=>post('/documented-start',{revision:w.revision,at:documentedTime(`call-start:${w.revision}`),command_id:command(`call-start:${w.revision}`)},'Call start documented.')}>Record call started</button>
          <button className="bank-chat-logout" data-testid="meeting-call-end" disabled={busy||w.state!=='in_progress'} onClick={()=>post('/documented-end',{revision:w.revision,at:documentedTime(`call-end:${w.revision}`),command_id:command(`call-end:${w.revision}`)},'Call end documented.')}>Record call ended</button>
          {w.state==='ended'&&<><h3>Conversation evidence</h3><p className="rm-small">Import a saved Zoom TXT/VTT export, or write your own recollection. Speaker roles are mapped by you. Unknown speakers stay unassigned. Current intake: 100 cues and 16 KB of redacted evidence; longer files are rejected.</p>
            <form className="meeting-evidence-form" onSubmit={importFile}><label className="form-field">Transcript export<input type="file" data-testid="meeting-transcript-file" accept=".txt,.vtt" disabled={busy||!w.recording_allowed} onChange={e=>setFile(e.target.files[0]||null)}/></label>
              <div className="rm-form-grid"><label className="form-field">Your speaker label<input data-testid="meeting-rm-speaker" value={rmLabel} onChange={e=>setRmLabel(e.target.value)} disabled={busy}/></label><label className="form-field">Client speaker label<input data-testid="meeting-client-speaker" value={clientLabel} onChange={e=>setClientLabel(e.target.value)} disabled={busy}/></label></div>
              <label className="form-field">Coverage<select data-testid="meeting-coverage" value={coverage} onChange={e=>setCoverage(e.target.value)} disabled={busy}><option value="unknown">Unknown</option><option value="partial">Partial capture</option><option value="full_rm_attested">I checked that the export covers the call</option></select></label>
              <button className="primary-button" data-testid="meeting-import" disabled={busy||!file||!w.recording_allowed}>Import and queue minutes</button></form>
            <form className="meeting-evidence-form" onSubmit={generateNotes}><label className="form-field">Your written recollection<textarea rows={5} data-testid="meeting-notes" value={notes} onChange={e=>setNotes(e.target.value)} disabled={busy} placeholder="One statement per line: rm: … / client: … / unmapped: …" required/></label>
              <button className="primary-button" data-testid="meeting-generate-notes" disabled={busy||!notes.trim()}>Save notes and draft minutes</button></form></>}
          {w.jobs?.map(job=><p key={job.event_id} className={job.status==='failed'?'rm-error':'rm-small'} data-testid="meeting-job">{job.type}: {job.status}{job.error_code?` (${job.error_code})`:''}</p>)}</>}
        {w.source_provenance&&<p className="rm-small">Source: {w.source_provenance.origin.replaceAll('_',' ')} · Coverage: {w.source_provenance.coverage||'RM recollection'}{w.source_provenance.coverage==='partial'?' — missing conversation is not covered.':''}</p>}
        {d&&<div className="meeting-minutes" data-testid="meeting-minutes"><h3>{d.approved?'Approved minutes':'Draft minutes — review required'}</h3><h4>{d.content.title}</h4>{d.source_revoked&&<p className="rmw-warn">The transcript was withdrawn. This existing draft may be reviewed but cannot be regenerated from that transcript.</p>}
          {[...d.content.summary,...d.content.decisions].map((item,i)=><div key={i}><p>{item.text}</p><details><summary>Evidence {item.cue_id}</summary><blockquote>{item.quote}</blockquote></details></div>)}
          {d.content.actions.map(item=><div key={item.action_id} className="meeting-action-item"><label><input type="checkbox" data-testid="meeting-action-choice" value={item.action_id} disabled={busy||item.commitment!=='agreed'||item.owner==='unassigned'} checked={selected.includes(item.action_id)} onChange={e=>setSelected(ids=>e.target.checked?[...ids,item.action_id]:ids.filter(id=>id!==item.action_id))}/>{item.owner}: {item.text} <span className="act-pill">{item.commitment}</span></label>{item.condition&&<p>{item.condition}</p>}
            <details><summary>Evidence {item.cue_id}</summary><blockquote>{item.quote}</blockquote>{item.due_quote&&<p>Due date evidence: {item.due_quote}</p>}</details></div>)}
          <h4>Client recap for separate approval</h4><p>{d.content.client_recap}</p>
          <button className="primary-button" data-testid="meeting-approve-minutes" disabled={busy||d.approved} onClick={()=>approve('/approve-minutes',{},'Exact minutes version approved.')}>Approve this minutes version</button>
          <button className="bank-chat-logout" data-testid="meeting-create-followups" disabled={busy||!d.approved||!selected.length} onClick={()=>approve('/followups',{action_ids:selected},'Selected agreed follow-ups created.')}>Create selected follow-ups</button>
          <button className="bank-chat-logout" data-testid="meeting-share-recap" disabled={busy||!d.approved} onClick={()=>approve('/share-recap',{},'Approved recap shared with the client.')}>Approve and share recap</button></div>}
        <h3>Follow-up tracker</h3>{!w.followups.length&&<p className="page-empty">No approved follow-ups yet.</p>}{w.followups.map(item=><div className="meeting-action-item" key={item.followup_id}><p><strong>{item.owner==='rm'?'Relationship manager':'Client'}:</strong> {item.text}</p><p className="rm-small">{item.status}{item.due_on?` · due ${item.due_on}`:''}</p>
          {item.status==='open'&&item.owner===(isRm?'rm':'client')&&<form onSubmit={e=>{e.preventDefault();post(`/followups/${item.followup_id}/complete`,{evidence:completion[item.followup_id],command_id:command(`complete:${item.followup_id}:${completion[item.followup_id]}`)},'Your follow-up was completed.');}}><label className="form-field">Completion evidence<input data-testid={`meeting-evidence-${item.followup_id}`} value={completion[item.followup_id]||''} maxLength={500} required disabled={busy} onChange={e=>setCompletion(values=>({...values,[item.followup_id]:e.target.value}))}/></label><button className="bank-chat-logout" data-testid={`meeting-complete-${item.followup_id}`} disabled={busy}>Mark my action complete</button></form>}</div>)}
        <h3>Shared recap</h3><p data-testid="meeting-shared-recap">{w.shared_recap||'No recap has been shared yet.'}</p></>}
    </>}
  </section>;
}
