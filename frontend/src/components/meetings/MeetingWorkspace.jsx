import {useEffect,useRef,useState} from 'react';
import Icon from '../Icon';
import ZoomSpeakerReview from './ZoomSpeakerReview';
import MeetingRecovery from './MeetingRecovery';
import MeetingPreparation from './MeetingPreparation';
import MeetingPack from './MeetingPack';
import MeetingMinutesReview from './MeetingMinutesReview';
import {approvalFor,checkedWorkspace,downloadMeetingPdf,meetingRequest,notesCues} from '../../lib/meetingLifecycle';

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
    const [zoomEvidence,recovery,preparation,pack,lifecycle,review,recap]=await Promise.all([
      who.advisor_id&&workspace?.recording_allowed&&workspace?.source_provenance?.origin==='zoom_my_notes_api'?request(`/${meetingId}/zoom/evidence`,'GET',null,signal):null,
      who.advisor_id&&workspace?request(`/${meetingId}/recovery`,'GET',null,signal):null,
      who.advisor_id&&workspace?request(`/${meetingId}/preparation`,'GET',null,signal):null,
      workspace?request(`/${meetingId}/pack`,'GET',null,signal):null,
      request(`/${meetingId}/lifecycle`,'GET',null,signal),
      who.advisor_id&&workspace?request(`/${meetingId}/minutes/review`,'GET',null,signal):null,
      workspace?request(`/${meetingId}/recap`,'GET',null,signal):null]);
    if(!signal.aborted){setData({enabled:true,who,appointment,workspace,link,linkError,capability,zoomEvidence,recovery,preparation,pack,lifecycle,review,recap});setSelected([]);}
  }
  async function run(work,message){
    const control=new AbortController();active.current?.abort();active.current=control;setBusy(true);setError('');setNotice('');
    try{
      let conflict='';
      try{await work(control.signal);}
      catch(failure){if(failure.status!==409)throw failure;conflict=failure.message;}
      // Another participant or the worker may have advanced this exact workflow. Read it without replaying the write.
      await load(control.signal);
      if(!control.signal.aborted){if(conflict)setError(conflict);else setNotice(message);}
    }
    catch(failure){if(failure.name!=='AbortError'&&!control.signal.aborted)setError(failure.message);}
    finally{if(!control.signal.aborted)setBusy(false);}
  }
  useEffect(()=>{setData(null);setOutcome('');setNotes('');setFile(null);commands.current.clear();callTimes.current.clear();run(async()=>{},'');return()=>active.current?.abort();},[meetingId]);
  const w=data?.workspace,d=w?.draft,isRm=data?.who?.advisor_id!==null&&data?.who?.advisor_id!==undefined;
  const minutesStatus=isRm?(d?.approved?'Current minutes approved.':d?'Draft minutes require RM review.':'Draft minutes are not available yet.'):(data?.recap?'The approved recap is available below.':'Your RM reviews minutes before sharing a recap.');
  const post=(suffix,body,message)=>run(signal=>request(`/${meetingId}${suffix}`,'POST',body,signal),message);
  const download=(path,filename)=>run(async signal=>downloadMeetingPdf(`/${meetingId}${path}`,filename,await token(),signal),'PDF downloaded from the saved document.');
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
      <p className="rm-small" data-testid="meeting-lifecycle-stage">Workflow: {data.lifecycle.stage.replaceAll('_',' ')}{data.lifecycle.waiting_for?` · waiting for ${data.lifecycle.waiting_for==='rm'?'your RM':data.lifecycle.waiting_for}`:''}{data.lifecycle.error_code?` · ${data.lifecycle.error_code.replaceAll('_',' ')}`:''}{data.preparation?.sources_changed?' · brief sources changed; refresh and review required':''}</p>
      {!w&&<form onSubmit={event=>{event.preventDefault();post('/start',{notice_id:data.who.notice.notice_id,purpose:data.appointment.purpose,outcome,command_id:command(`start:${outcome}`)},'Meeting workspace opened.');}}>
        <label className="form-field">Intended outcome<textarea data-testid="meeting-outcome" value={outcome} onChange={e=>setOutcome(e.target.value)} maxLength={300} required disabled={busy}/></label>
        <button className="primary-button" data-testid="meeting-start" disabled={busy||!data.appointment.purpose||!outcome.trim()}>Open workspace</button></form>}
      {w&&<>{isRm&&<MeetingPreparation prepared={data.preparation} busy={busy}
        onPrepare={()=>post('/preparation',null,'Internal brief prepared for review.')}
        onRefresh={()=>post('/preparation/refresh',{command_id:command(`brief-refresh:${data.preparation?.brief_id}`)},'Refreshed brief prepared for review.')}
        onDownload={()=>download(`/preparation/pdf?version=${data.preparation.card.version}&content_hash=${data.preparation.content_hash}`,`meeting-brief-v${data.preparation.card.version}.pdf`)}
        onDecision={(outcome,note)=>post('/preparation/decision',{version:data.preparation.card.version,revision:data.preparation.revision,content_hash:data.preparation.content_hash,outcome,note,
          command_id:command(`brief-decision:${data.preparation.brief_id}:${outcome}:${note}`)},'Brief review recorded.')}/>}
        <MeetingPack pack={data.pack} isRm={isRm} busy={busy}
          onDownload={()=>download(`/pack/pdf?document_id=${data.pack.document_id}&version=${data.pack.version}&content_hash=${data.pack.content_hash}`,`meeting-pack-v${data.pack.version}.pdf`)}
          onDraft={amounts=>post('/pack/draft',{amounts,command_id:command(`pack-draft:${data.pack?.document_id}:${JSON.stringify(amounts)}`)},'Discussion pack drafted for separate review.')}
          onApprove={()=>post('/pack/approve',{document_id:data.pack.document_id,version:data.pack.version,revision:data.pack.revision,content_hash:data.pack.content_hash,command_id:command(`pack-approve:${data.pack.document_id}`)},'Exact pack approved. It can now be shared.')}
          onShare={()=>post('/pack/share',{document_id:data.pack.document_id,version:data.pack.version,revision:data.pack.revision,content_hash:data.pack.content_hash,command_id:command(`pack-share:${data.pack.document_id}`)},'Approved discussion pack shared with the client.')}/>
        {data.recap&&<button className="bank-chat-logout" data-testid="meeting-download-recap" disabled={busy} onClick={()=>download(`/recap/pdf?document_id=${data.recap.document_id}&version=${data.recap.version}&content_hash=${data.recap.content_hash}`,`meeting-recap-v${data.recap.version}.pdf`)}>Download approved recap PDF</button>}
        <div className="meeting-notice"><h3>Recording and transcript notice</h3><p>{w.notice}</p>
        {!w.notice_seen&&<button className="bank-chat-logout" data-testid="meeting-notice-seen" disabled={busy} onClick={()=>post('/notice-seen',{notice_id:w.notice_id,revision:w.revision},'Notice read for this meeting.')}>I have read this notice</button>}
        {!isRm&&<button className="bank-chat-logout" data-testid="meeting-revoke-transcript" disabled={busy} onClick={()=>post('/transcript-revoke',{command_id:command('revoke')},'Transcript access withdrawn. Existing minutes remain retained.')}>Withdraw transcript access</button>}
        <p className="rm-small">{w.recording_allowed?'Transcript intake is available.':'Transcript intake requires both participants to read the current notice and must not have been withdrawn.'} Reading the notice does not start Zoom recording.</p></div>
        {data.linkError&&<p className="rm-error" role="alert">{data.linkError}</p>}
        {data.link&&<a className="primary-button" data-testid="meeting-zoom-join" href={data.link.join_url} target="_blank" rel="noopener noreferrer">Join Zoom meeting</a>}
        {isRm&&!data.link&&data.appointment.format==='video'&&Date.parse(data.appointment.starts_at)>Date.now()&&<button className="bank-chat-logout" data-testid="meeting-create-zoom" disabled={busy||!data.capability.zoom_configured}
          onClick={()=>data.capability.zoom_automation_enabled
            ?post('/zoom/request-link',{revision:w.revision,command_id:command(`zoom-link:${w.revision}:${w.jobs?.at(-1)?.event_id}`)},'Zoom link requested. Refresh to see the confirmed provider link.')
            :post('/zoom',{},'Zoom meeting link created.')}>Create Zoom link</button>}
        {w.zoom_capture&&<div data-testid="meeting-zoom-capture-status"><h3>Zoom transcript</h3><p className="rm-small">A participant must start My Notes in Zoom and complete the note when finished. Meeting end alone does not make a transcript available.</p>
          <p role="status" data-testid="meeting-transcript-status">{w.source_provenance&&w.source_provenance.origin!=='zoom_my_notes_api'?`Saved meeting evidence is being used. ${minutesStatus}`:w.zoom_capture.state==='awaiting_note'?'Waiting for Zoom to deliver a captured note.':w.zoom_capture.state==='done'?`Transcript retrieved. ${minutesStatus}`:`Transcript retrieval: ${w.zoom_capture.state}.`}{w.zoom_capture.error_code?` ${w.zoom_capture.error_code}`:''}</p>
          {isRm&&w.state==='ended'&&!w.source_provenance&&<button className="bank-chat-logout" data-testid="meeting-check-transcript" disabled={busy||!w.recording_allowed}
            onClick={()=>post('/zoom/check-transcript',{revision:w.revision,command_id:command(`zoom-check:${w.revision}:${w.jobs?.at(-1)?.event_id}`)},'Zoom transcript check queued. Refresh to see the provider result.')}>Check completed My Notes</button>}</div>}
        {isRm&&<><h3>Document the call</h3><p className="rm-small">{w.zoom_capture?'Registered Zoom events can document the call. Record times here only when the provider events are unavailable; conflicting times require reconciliation.':'These times are recorded by you. Automatic Zoom start/end events are not connected for this meeting.'}</p>
          <button className="bank-chat-logout" data-testid="meeting-call-start" disabled={busy||w.state!=='scheduled'} onClick={()=>post('/documented-start',{revision:w.revision,at:documentedTime(`call-start:${w.revision}`),command_id:command(`call-start:${w.revision}`)},'Call start documented.')}>Record call started</button>
          <button className="bank-chat-logout" data-testid="meeting-call-end" disabled={busy||w.state!=='in_progress'} onClick={()=>post('/documented-end',{revision:w.revision,at:documentedTime(`call-end:${w.revision}`),command_id:command(`call-end:${w.revision}`)},'Call end documented.')}>Record call ended</button>
          {w.state==='ended'&&<><h3>Conversation evidence</h3><p className="rm-small">Import a saved Zoom TXT/VTT export, or write your own recollection. Speaker roles are mapped by you. Unknown speakers stay unassigned. Transcript intake: 1000 cues and 128000 bytes of redacted evidence, processed in bounded parts. Written notes support 100 statements.</p>
            <form className="meeting-evidence-form" onSubmit={importFile}><label className="form-field">Transcript export<input type="file" data-testid="meeting-transcript-file" accept=".txt,.vtt" disabled={busy||!w.recording_allowed} onChange={e=>setFile(e.target.files[0]||null)}/></label>
              <div className="rm-form-grid"><label className="form-field">Your speaker label<input data-testid="meeting-rm-speaker" value={rmLabel} onChange={e=>setRmLabel(e.target.value)} disabled={busy}/></label><label className="form-field">Client speaker label<input data-testid="meeting-client-speaker" value={clientLabel} onChange={e=>setClientLabel(e.target.value)} disabled={busy}/></label></div>
              <label className="form-field">Coverage<select data-testid="meeting-coverage" value={coverage} onChange={e=>setCoverage(e.target.value)} disabled={busy}><option value="unknown">Unknown</option><option value="partial">Partial capture</option><option value="full_rm_attested">I checked that the export covers the call</option></select></label>
              <button className="primary-button" data-testid="meeting-import" disabled={busy||!file||!w.recording_allowed}>Import and queue minutes</button></form>
            <form className="meeting-evidence-form" onSubmit={generateNotes}><label className="form-field">Your written recollection<textarea rows={5} data-testid="meeting-notes" value={notes} onChange={e=>setNotes(e.target.value)} disabled={busy} placeholder="One statement per line: rm: … / client: … / unmapped: …" required/></label>
              <button className="primary-button" data-testid="meeting-generate-notes" disabled={busy||!notes.trim()}>Save notes and draft minutes</button></form></>}
          {w.jobs?.map(job=><p key={job.event_id} className={job.status==='failed'?'rm-error':'rm-small'} data-testid="meeting-job">{job.type}: {job.status}{job.error_code?` (${job.error_code})`:''}</p>)}
          {data.recovery&&<MeetingRecovery recovery={data.recovery} busy={busy} onPost={(suffix,body,message)=>post(suffix,{...body,command_id:command(`recovery:${suffix}:${JSON.stringify(body)}`)},message)}/>}</>}
        {isRm&&data.zoomEvidence&&<ZoomSpeakerReview evidence={data.zoomEvidence} busy={busy} onSave={mapping=>post('/zoom/speakers',{
          source_id:data.zoomEvidence.source_id,source_hash:data.zoomEvidence.source_hash,revision:data.zoomEvidence.revision,
          consent_epoch:data.zoomEvidence.consent_epoch,mapping,command_id:command(`zoom-speakers:${data.zoomEvidence.source_id}:${JSON.stringify(mapping)}`)},'Speaker roles saved as a new source version. Fresh minutes are queued for review.')}/>}
        {w.source_provenance&&<p className="rm-small">Source: {w.source_provenance.origin.replaceAll('_',' ')} · Coverage: {w.source_provenance.coverage||'RM recollection'}{w.source_provenance.coverage==='partial'?' — missing conversation is not covered.':''}</p>}
        {d&&<div className="meeting-minutes" data-testid="meeting-minutes"><h3>{d.approved?'Approved minutes':'Draft minutes — review required'}</h3><h4>{d.content.title}</h4>{d.source_revoked&&<p className="rmw-warn">The transcript was withdrawn. This existing draft may be reviewed but cannot be regenerated from that transcript.</p>}
          {[...d.content.summary,...d.content.decisions].map((item,i)=><div key={i}><p>{item.text}</p><details><summary>Evidence {item.cue_id}</summary><blockquote>{item.quote}</blockquote></details></div>)}
          {d.content.actions.map(item=><div key={item.action_id} className="meeting-action-item"><label><input type="checkbox" data-testid="meeting-action-choice" value={item.action_id} disabled={busy||item.commitment!=='agreed'||item.owner==='unassigned'} checked={selected.includes(item.action_id)} onChange={e=>setSelected(ids=>e.target.checked?[...ids,item.action_id]:ids.filter(id=>id!==item.action_id))}/>{item.owner}: {item.text} <span className="act-pill">{item.commitment}</span></label>{item.due_on&&<p className="rm-small" data-testid="meeting-action-due">Due: {item.due_on}</p>}{item.condition&&<p>{item.condition}</p>}
            <details><summary>Evidence {item.cue_id}</summary><blockquote>{item.quote}</blockquote>{item.due_quote&&<p>Due date evidence: {item.due_quote}</p>}</details></div>)}
          <h4>Client recap for separate approval</h4><p>{d.content.client_recap}</p>
          {!d.approved&&<MeetingMinutesReview review={data.review} busy={busy}
            onReview={(outcome,note)=>approve('/minutes/review',{outcome,note},'Minutes review recorded. This version cannot be approved.')}
            onRegenerate={()=>approve('/minutes/regenerate',{},'Regeneration requested. Refresh to check the new minutes version.')}/>}
          <button className="primary-button" data-testid="meeting-approve-minutes" disabled={busy||d.approved||Boolean(data.review)} onClick={()=>approve('/approve-minutes',{},'Exact minutes version approved.')}>Approve this minutes version</button>
          <button className="bank-chat-logout" data-testid="meeting-create-followups" disabled={busy||!d.approved||!selected.length} onClick={()=>approve('/followups',{action_ids:selected},'Selected agreed follow-ups created.')}>Create selected follow-ups</button>
          <button className="bank-chat-logout" data-testid="meeting-share-recap" disabled={busy||!d.approved} onClick={()=>approve('/share-recap',{},'Approved recap shared with the client.')}>Approve and share recap</button></div>}
        <h3>Follow-up tracker</h3>{!w.followups.length&&<p className="page-empty">No approved follow-ups yet.</p>}{w.followups.map(item=><div className="meeting-action-item" key={item.followup_id}><p><strong>{item.owner==='rm'?'Relationship manager':'Client'}:</strong> {item.text}</p><p className="rm-small">{item.status}{item.due_on?` · due ${item.due_on}`:''}</p>
          {item.status==='open'&&item.owner===(isRm?'rm':'client')&&<form onSubmit={e=>{e.preventDefault();post(`/followups/${item.followup_id}/complete`,{evidence:completion[item.followup_id],command_id:command(`complete:${item.followup_id}:${completion[item.followup_id]}`)},'Your follow-up was completed.');}}><label className="form-field">Completion evidence<input data-testid={`meeting-evidence-${item.followup_id}`} value={completion[item.followup_id]||''} maxLength={500} required disabled={busy} onChange={e=>setCompletion(values=>({...values,[item.followup_id]:e.target.value}))}/></label><button className="bank-chat-logout" data-testid={`meeting-complete-${item.followup_id}`} disabled={busy}>Mark my action complete</button></form>}</div>)}
        <h3>Shared recap</h3><p data-testid="meeting-shared-recap">{w.shared_recap||'No recap has been shared yet.'}</p></>}
    </>}
  </section>;
}
