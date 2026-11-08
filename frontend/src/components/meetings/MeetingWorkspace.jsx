import {Suspense,lazy,useEffect,useRef,useState} from 'react';
import Icon from '../Icon';

import ZoomSpeakerReview from './ZoomSpeakerReview';
import MeetingRecovery from './MeetingRecovery';
import MeetingPreparation from './MeetingPreparation';
import MeetingPack from './MeetingPack';
import MeetingMinutesReview from './MeetingMinutesReview';
import {DocSection,OneLine} from '../DocParts';
import {MEETING_STEPS,approvalFor,checkedWorkspace,downloadMeetingPdf,guessRoles,meetingRequest,meetingStep,minutesAsNotes,notesCues,speakerList,stepState,transcriptSpeakers,transcriptSummary,clientWords,meetingTitle,stageWords,topicName} from '../../lib/meetingLifecycle';

// RM only, loaded on demand so the client app never loads the RM assistant code.
const HandoffDrafts=lazy(()=>import('../rm/HandoffDrafts'));

// One meeting as one page with four steps (Prepare → Meet → Document → Follow up). Every step keeps its own RM
// approval: the exact brief, the exact minutes, the selected follow-ups and the recap. Nothing here approves itself.
export default function MeetingWorkspace({meetingId,token,onClose,client,onAsk,rmName}){
  const [data,setData]=useState(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(true);
  const [outcome,setOutcome]=useState(''),[notes,setNotes]=useState(''),[file,setFile]=useState(null),[rmLabel,setRmLabel]=useState(''),[clientLabel,setClientLabel]=useState(''),[coverage,setCoverage]=useState('unknown');
  const [selected,setSelected]=useState([]),[completion,setCompletion]=useState({}),[edits,setEdits]=useState({}),[recapText,setRecapText]=useState(null);
  const [roles,setRoles]=useState({}),[method,setMethod]=useState('transcript'),[includeRecap,setIncludeRecap]=useState(true),[open,setOpen]=useState(null),[summary,setSummary]=useState(null);
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
    if(!signal.aborted&&!workspace)setOutcome(o=>o||appointment.purpose||'');
    if(!signal.aborted){setData({enabled:true,who,appointment,workspace,link,linkError,capability,zoomEvidence,recovery,preparation,pack,lifecycle,review,recap});setSelected(workspace?.draft?.approved&&!workspace.followups.length?workspace.draft.content.actions.filter(a=>a.commitment==='agreed'&&a.owner!=='unassigned').map(a=>a.action_id):[]);}
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
      speakers.push(...speakerList(roles));
      if(!speakers.length){if(rmLabel.trim())speakers.push({label:rmLabel.trim(),role:'rm'});if(clientLabel.trim())speakers.push({label:clientLabel.trim(),role:'client'});}
      await request(`/${meetingId}/transcript-import`,'POST',{revision:w.revision,filename:file.name,text,speakers,coverage,command_id:command(`import:${digest}:${JSON.stringify(speakers)}:${coverage}:${w.revision}`)},signal);
    },'Transcript imported. The Minutes Writer job is queued; refresh to check its result.');
  }
  async function generateNotes(event){event.preventDefault();await run(async signal=>{const cues=notesCues(notes);
    await request(`/${meetingId}/notes`,'POST',{revision:w.revision,cues,command_id:command(`notes:${notes}:${w.revision}`)},signal);
    await request(`/${meetingId}/generate-notes-draft`,'POST',{command_id:command(`draft:${notes}:${w.revision}`)},signal);
  },'Draft minutes are ready for review.');}
  async function chooseFile(event){
    const chosen=event.target.files[0]||null;setFile(chosen);setRoles({});setSummary(null);
    if(chosen&&chosen.size<=1048576){try{const text=await chosen.text();setRoles(guessRoles(transcriptSpeakers(text),client));setSummary(transcriptSummary(text));}catch{setRoles({});}}
  }
  // One review, one press: the selected follow-ups, then the recap. Each stays its own audited, exact-version approval.
  async function sendFollowUp(){
    await run(async signal=>{
      // 0141: only what she changed is sent as edits; the approved minutes stay as they are.
      const changed=changedEdits(),text=recapChanged?recapText.trim():null;
      if(selected.length&&!followupsDone)await request(`/${meetingId}/followups`,'POST',{...approvalFor(d,command(`/followups:${d.draft_id}:${d.content_hash}:${d.consent_epoch}:${JSON.stringify({action_ids:selected,changed})}`)),action_ids:selected,...(changed&&{edits:changed})},signal);
      if(includeRecap&&!w.shared_recap)await request(`/${meetingId}/share-recap`,'POST',{...approvalFor(d,command(`/share-recap:${d.draft_id}:${d.content_hash}:${d.consent_epoch}:${text||''}`)),...(text&&{text})},signal);
    },`${selected.length&&!followupsDone?`${selected.length} follow-up${selected.length===1?'':'s'} created`:'No new follow-ups'}${includeRecap&&!w.shared_recap?' and the recap shared with the client':''}.`);
  }
  const current=data?.enabled?meetingStep(data.lifecycle?.stage,w):0;
  const shown=open??Math.min(current,MEETING_STEPS.length-1);
  const roleNames=Object.keys(roles);
  const followupsDone=Boolean(d?.approved)&&w?.followups?.length>0;
  const handoffNotes=minutesAsNotes(d);
  const offered=(d?.content?.actions||[]).filter(x=>x.commitment==='agreed'&&x.owner!=='unassigned');
  const field=(item,key)=>edits[item.action_id]?.[key]??(key==='due_on'?item.due_on||'':item[key]);
  const setField=(item,key,value)=>setEdits(all=>({...all,[item.action_id]:{...all[item.action_id],[key]:value}}));
  function changedEdits(){const out={};for(const id of selected){const item=offered.find(x=>x.action_id===id),e=edits[id];if(!item||!e)continue;const c={};
    if(e.text!==undefined&&e.text.trim()!==item.text)c.text=e.text.trim();if(e.owner!==undefined&&e.owner!==item.owner)c.owner=e.owner;
    if(e.due_on!==undefined&&(e.due_on||null)!==(item.due_on||null))c.due_on=e.due_on||null;if(Object.keys(c).length)out[id]=c;}return Object.keys(out).length?out:null;}
  const recapValue=recapText??d?.content?.client_recap??'';
  const recapChanged=recapText!==null&&recapText.trim()!==(d?.content?.client_recap||'').trim();
  const badEdit=selected.some(id=>{const t=edits[id]?.text;return t!==undefined&&(t.trim().length<3||t.length>300);})||(includeRecap&&!w?.shared_recap&&(recapValue.trim().length<10||recapValue.length>2000));
  const a=data?.appointment,first=client?.split(' ')[0]||'the client';
  const clock=value=>new Date(value).toLocaleTimeString('en-SG',{hour:'numeric',minute:'2-digit',timeZone:'Asia/Singapore'}).toLowerCase().replace(' ','');
  const meta=a?[new Date(a.starts_at).toLocaleDateString('en-SG',{weekday:'short',day:'numeric',month:'short',year:'numeric',timeZone:'Asia/Singapore'}),
    a.ends_at?`${clock(a.starts_at)}–${clock(a.ends_at)}`:clock(a.starts_at),{video:'Video call',phone:'Phone call',in_person:'In person'}[a.format]||a.format,
    a.topics?.length?`Topics: ${a.topics.map(topicName).join(', ')}`:null].filter(Boolean).join(' · '):'';
  const prep=data?.preparation;
  const sub=[prep?.card?`Briefing v${prep.card.version} ${prep.decision==='approved'&&!prep.sources_changed?'approved':'to review'}`:'Brief and pack',
    !w?'Call times':w.state==='scheduled'?'Not started':w.state==='in_progress'?'In progress':'Call ended',
    d?(d.approved?'Minutes approved':'Minutes to review'):'Transcript or notes → minutes',w?.shared_recap?'Recap shared':'Recap and hand-offs'];
  const matched=roleNames.some(n=>roles[n]==='rm')&&roleNames.some(n=>roles[n]==='client');
  const who=name=>roles[name]==='rm'?'You':roles[name]==='client'?first:'Other';
  const zoomPath=Boolean(w?.zoom_capture&&(!w.source_provenance||w.source_provenance.origin==='zoom_my_notes_api'));
  const fallback=()=><>
          <nav className="page-tabs" role="tablist" aria-label="How to add the call">
            <button role="tab" type="button" aria-selected={method==='transcript'} data-testid="meeting-method-transcript" onClick={()=>setMethod('transcript')}>Upload transcript</button>
            <button role="tab" type="button" aria-selected={method==='notes'} data-testid="meeting-method-notes" onClick={()=>setMethod('notes')}>Type my own notes</button></nav>
          {method==='transcript'&&<form className="meeting-evidence-form" onSubmit={importFile}>
            {!w.recording_allowed&&<p className="rmw-warn">A transcript can be added once you and {first} both agree (step 2). You can type your notes instead.</p>}
            {file?<div className="meeting-file"><Icon name="document"/><span><strong>{file.name}</strong><small>{summary?`${summary.lines} lines${summary.first?` · ${summary.first}–${summary.last}`:''} · checked`:'Reading…'}</small></span>
              <label className="bank-chat-logout">Replace file<input type="file" className="vh" accept=".txt,.vtt" disabled={busy} onChange={chooseFile}/></label></div>
              :<label className="meeting-file empty"><Icon name="document"/><span><strong>Choose the transcript file</strong><small>Zoom TXT or VTT export, up to 1 MiB</small></span><input type="file" className="vh" data-testid="meeting-transcript-file" accept=".txt,.vtt" disabled={busy||!w.recording_allowed} onChange={chooseFile}/><span className="bank-chat-logout">Choose file</span></label>}
            {roleNames.length>0?<fieldset className="meeting-speakers"><legend>Who is who in the file</legend>{roleNames.map(name=><label key={name} className="form-field">“{name}” is
              <select data-testid="meeting-speaker-role" value={roles[name]} disabled={busy} onChange={e=>setRoles(r=>({...r,[name]:e.target.value}))}><option value="rm">You (relationship manager)</option><option value="client">{client?`${client} (client)`:'The client'}</option><option value="unmapped">Someone else / not sure</option></select></label>)}</fieldset>
              :file&&<div className="rm-form-grid"><label className="form-field">Your name in the file<input data-testid="meeting-rm-speaker" value={rmLabel} onChange={e=>setRmLabel(e.target.value)} disabled={busy}/></label><label className="form-field">The client’s name in the file<input data-testid="meeting-client-speaker" value={clientLabel} onChange={e=>setClientLabel(e.target.value)} disabled={busy}/></label></div>}
            {summary?.preview.length>0&&<div className="meeting-preview"><h4>Preview</h4><div>{summary.preview.map((cue,i)=><p key={i}><span className={`meeting-who ${roles[cue.name]||'unmapped'}`}>{who(cue.name)}</span>{cue.text}</p>)}</div></div>}
            <label className="form-field">Does the file cover the whole call?<select data-testid="meeting-coverage" value={coverage} onChange={e=>setCoverage(e.target.value)} disabled={busy}><option value="unknown">Not sure</option><option value="partial">Only part of it</option><option value="full_rm_attested">Yes, I checked</option></select></label>
            <div className="meeting-submit"><button className="primary-button" data-testid="meeting-import" disabled={busy||!file||!w.recording_allowed}>Draft minutes</button><span className="muted">Nothing is saved or sent to {first} until you approve.</span></div></form>}
          {method==='notes'&&<form className="meeting-evidence-form" onSubmit={generateNotes}><label className="form-field">Your notes, one statement per line<textarea rows={6} data-testid="meeting-notes" value={notes} onChange={e=>setNotes(e.target.value)} disabled={busy} placeholder={'rm: You mentioned a condo next year…\nclient: I’d rather not sell my investments…'} required/></label>
            <p className="rm-small">Start each line with rm: (you), client: or unmapped:. Up to 100 lines.</p>
            <div className="meeting-submit"><button className="primary-button" data-testid="meeting-generate-notes" disabled={busy||!notes.trim()}>Draft minutes</button><span className="muted">Nothing is saved or sent to {first} until you approve.</span></div></form>}</>;
  // The client's side of the same meeting (design: one meeting, after the call): three plain steps, her own to-dos
  // first, the recap, and who does what next. Same actions as before (agree to a transcript, join, mark her action done).
  if(data?.enabled&&!isRm){
    const rm=rmName?rmName.replace(/\s*\(DEMO\)\s*$/,''):'your relationship manager',rmFirst=rmName?rm.split(' ')[0]:'your relationship manager',RmFirst=rmFirst.charAt(0).toUpperCase()+rmFirst.slice(1);
    const ended=w&&!['scheduled','in_progress'].includes(w.state),recapped=Boolean(w?.shared_recap);
    const step=recapped?2:1;
    const mine=(w?.followups||[]).filter(item=>item.owner==='client'),theirs=(w?.followups||[]).filter(item=>item.owner==='rm');
    const open=mine.filter(item=>item.status==='open');
    const due=value=>value?new Date(`${value}T00:00:00+08:00`).toLocaleDateString('en-SG',{weekday:'short',day:'numeric',month:'short',timeZone:'Asia/Singapore'}):null;
    const STEPS=[['Booked',a?`${new Date(a.starts_at).toLocaleDateString('en-SG',{weekday:'short',day:'numeric',month:'short',timeZone:'Asia/Singapore'})}`:'Confirmed'],
      ['Meeting',!w||w.state==='scheduled'?'Coming up':w.state==='in_progress'?'In progress':'Done'],['Recap and next steps',recapped?(open.length?`${open.length} thing${open.length===1?'':'s'} for you to do`:'Nothing for you to do'):ended?`Waiting for ${rmFirst}’s recap`:'After the call']];
    return <section className="meeting-lifecycle meeting-page meeting-client" data-testid="meeting-workspace" aria-label="Meeting">
      <header className="meeting-head"><div><span className="eyebrow">Meeting with {rm}</span><h1>{a?meetingTitle(a.purpose,a.topics):'Meeting'}</h1>{meta&&<p className="meeting-meta">{meta}</p>}</div>
        <div className="meeting-head-actions"><button className="icon-button meeting-refresh" data-testid="meeting-refresh" aria-label="Refresh" title="Refresh" disabled={busy} onClick={()=>run(async()=>{},'Meeting refreshed.')}><Icon name="refresh"/></button></div></header>
      {busy&&<p className="loading" role="status">Updating meeting…</p>}{error&&<p className="rm-error" role="alert">{error}</p>}{notice&&<p className="chat-toast" role="status">{notice}</p>}
      <ol className="meeting-steps three" aria-label="Where this meeting is">{STEPS.map(([label,sub],i)=><li key={label} className={`meeting-step ${i<step?'done':i===step?'current':'todo'}`} aria-current={i===step?'step':undefined}>
        <div className="meeting-step-static"><span className="meeting-step-mark">{i<step?'✓':i+1}</span><span className="meeting-step-text"><strong>{label}</strong><small>{sub}</small></span></div></li>)}</ol>
      <div className="meeting-body"><div className="meeting-main">
        {open.length>0&&<section className="meeting-card meeting-todo" aria-label="For you to do"><span className="eyebrow">For you to do</span>
          {open.map(item=><form key={item.followup_id} className="meeting-todo-row" onSubmit={e=>{e.preventDefault();post(`/followups/${item.followup_id}/complete`,{evidence:completion[item.followup_id],command_id:command(`complete:${item.followup_id}:${completion[item.followup_id]}`)},'Marked as done. Thank you.');}}>
            <span className="meeting-todo-text"><strong>{item.text}</strong>{item.due_on&&<small>By {due(item.due_on)}</small>}</span>
            <label className="vh" htmlFor={`done-${item.followup_id}`}>What you did</label>
            <input id={`done-${item.followup_id}`} className="in" data-testid={`meeting-evidence-${item.followup_id}`} value={completion[item.followup_id]||''} maxLength={500} required disabled={busy} placeholder="e.g. uploaded in Periodic review" onChange={e=>setCompletion(values=>({...values,[item.followup_id]:e.target.value}))}/>
            <button className="primary-button" data-testid={`meeting-complete-${item.followup_id}`} disabled={busy}>Mark as done</button></form>)}</section>}

        {!ended&&<section className="meeting-card" aria-label="Before the meeting"><h3>Before the meeting</h3>
          {!w&&<><p className="muted">{RmFirst} opens the meeting page before the call. You can also open it now.</p>
            <button className="bank-chat-logout" data-testid="meeting-start" disabled={busy||!a?.purpose} onClick={()=>post('/start',{notice_id:data.who.notice.notice_id,purpose:a.purpose,outcome:a.purpose,command_id:command(`start:${a.purpose}`)},'Meeting opened.')}>Open the meeting page</button></>}
          {w&&<div className="meeting-notice"><h4>Can {rmFirst} use a transcript of this call to write the minutes?</h4><p className="rm-small">{w.notice}</p>
            {!w.notice_seen?<button className="primary-button" data-testid="meeting-notice-seen" disabled={busy} onClick={()=>post('/notice-seen',{notice_id:w.notice_id,revision:w.revision},'Thank you. You agreed for this meeting.')}>I agree</button>
              :<p className="rm-small"><Icon name="check"/> You agreed. Nothing is recorded by this; {rmFirst} reviews the minutes before anything is shared. <button type="button" className="link-button" data-testid="meeting-revoke-transcript" disabled={busy} onClick={()=>post('/transcript-revoke',{command_id:command('revoke')},'Transcript access withdrawn. Existing minutes remain retained.')}>Withdraw</button></p>}</div>}
          {data.link&&<a className="primary-button" data-testid="meeting-zoom-join" href={data.link.join_url} target="_blank" rel="noopener noreferrer">Join video call</a>}
          {w&&data.pack&&<MeetingPack pack={data.pack} isRm={false} busy={busy} onDownload={()=>download(`/pack/pdf?document_id=${data.pack.document_id}&version=${data.pack.version}&content_hash=${data.pack.content_hash}`,`meeting-pack-v${data.pack.version}.pdf`)}/>}</section>}

        <section className="meeting-card" aria-label="Recap"><div className="meeting-recap-head"><h3>Recap from {rmFirst}</h3></div>
          <p data-testid="meeting-shared-recap">{w?.shared_recap||(ended?`${RmFirst} is writing the recap. You’ll see it here and in your Inbox.`:'After the call, the recap and your next steps appear here.')}</p>
          {data.recap&&<button className="bank-chat-logout" data-testid="meeting-download-recap" disabled={busy} onClick={()=>download(`/recap/pdf?document_id=${data.recap.document_id}&version=${data.recap.version}&content_hash=${data.recap.content_hash}`,`meeting-recap-v${data.recap.version}.pdf`)}>Download recap (PDF)</button>}</section>
        {mine.some(item=>item.status!=='open')&&<details className="act-more"><summary>Done</summary><ul className="mtg-rows">{mine.filter(item=>item.status!=='open').map(item=><li key={item.followup_id}><span className="rm-dot clear" aria-hidden="true"/><span className="mtg-row-text"><strong>{item.text}</strong><small>Done</small></span></li>)}</ul></details>}
      </div>
      <aside className="meeting-rail">
        <section className="meeting-card" aria-label="What happens next"><h3>What happens next</h3>
          {!theirs.length&&<p className="muted">{recapped?`Nothing more is planned. Message ${rmFirst} if you need anything.`:`After the call, ${rmFirst}’s next steps appear here.`}</p>}
          <ul className="mtg-rows">{theirs.map(item=><li key={item.followup_id}><span className={`rm-dot ${item.status==='open'?'meeting':'clear'}`} aria-hidden="true"/><span className="mtg-row-text"><strong>{RmFirst}: {item.text}</strong><small>{item.status==='open'?(item.due_on?`By ${due(item.due_on)}`:'To do'):'Done'}</small></span></li>)}</ul>
          {recapped&&<p className="rm-small">If a specialist team is involved, they start from what you already told {rmFirst}. You won’t need to repeat it. Their updates appear in Requests.</p>}</section>
      </aside></div>
    </section>;
  }
  return <section className="meeting-lifecycle meeting-page" data-testid="meeting-workspace" aria-label="Meeting">
    <header className="meeting-head"><div><span className="eyebrow">Meeting{client?` · ${client}`:''}</span><h1>{a?meetingTitle(a.purpose,a.topics):'Meeting'}</h1>{meta&&<p className="meeting-meta">{meta}</p>}{clientWords(a?.purpose)&&<p className="meeting-meta">{first} asked: “{clientWords(a.purpose)}”</p>}</div>
      <div className="meeting-head-actions">
        {onAsk&&a&&<button className="bank-chat-logout" data-testid="meeting-ask-assistant" disabled={busy} onClick={()=>onAsk(`Brief me for my meeting${client?` with ${client}`:''}`,{intent:'meeting_brief',context:{client_id:a.customer_id,appointment_id:meetingId}})}><Icon name="spark"/>Continue in the assistant</button>}
        <button className="icon-button meeting-refresh" data-testid="meeting-refresh" aria-label="Refresh" title="Refresh" disabled={busy} onClick={()=>run(async()=>{},'Meeting refreshed.')}><Icon name="refresh"/></button></div></header>
    {busy&&<p className="loading" role="status">Updating meeting…</p>}{error&&<p className="rm-error" role="alert">{error}</p>}{notice&&<p className="chat-toast" role="status">{notice}</p>}
    {data&&!data.enabled&&<p className="page-empty">Meeting notes and follow-up are not available yet.</p>}
    {data?.enabled&&<>
      <ol className="meeting-steps" aria-label="Meeting steps">{MEETING_STEPS.map(([key,label],i)=><li key={key} className={`meeting-step ${stepState(i,current)}${shown===i?' shown':''}`} aria-current={i===current?'step':undefined}>
        <button type="button" data-testid={`meeting-step-${key}`} onClick={()=>setOpen(i)}><span className="meeting-step-mark">{stepState(i,current)==='done'?'✓':i+1}</span><span className="meeting-step-text"><strong>{label}</strong><small>{sub[i]}</small></span></button></li>)}</ol>
      <div className={`meeting-body${shown===3?' no-rail':''}`}><div className="meeting-main">

      {shown===0&&<div className="meeting-step-panel meeting-card" data-testid="meeting-panel-prepare"><h2>Prepare for the meeting</h2><p className="muted">Review the brief prepared from her records. Approving records that you checked this exact version.</p>
        {!w&&<form onSubmit={event=>{event.preventDefault();post('/start',{notice_id:data.who.notice.notice_id,purpose:data.appointment.purpose,outcome,command_id:command(`start:${outcome}`)},'Meeting opened.');}}>
          <p className="rm-small">{data.appointment.purpose?'Write what this meeting should achieve. The brief is then prepared from the client’s records for your review.':'Agree the meeting purpose with the client first.'}</p>
          <label className="form-field">Intended outcome<textarea data-testid="meeting-outcome" value={outcome} onChange={e=>setOutcome(e.target.value)} maxLength={300} required disabled={busy}/></label>
          <button className="primary-button" data-testid="meeting-start" disabled={busy||!data.appointment.purpose||!outcome.trim()}>Open meeting</button></form>}
        {w&&isRm&&<MeetingPreparation prepared={data.preparation} busy={busy}
          onPrepare={()=>post('/preparation',null,'Internal brief prepared for review.')}
          onRefresh={()=>post('/preparation/refresh',{command_id:command(`brief-refresh:${data.preparation?.brief_id}`)},'Refreshed brief prepared for review.')}
          onDownload={()=>download(`/preparation/pdf?version=${data.preparation.card.version}&content_hash=${data.preparation.content_hash}`,`meeting-brief-v${data.preparation.card.version}.pdf`)}
          onDecision={(outcome,note)=>post('/preparation/decision',{version:data.preparation.card.version,revision:data.preparation.revision,content_hash:data.preparation.content_hash,outcome,note,
            command_id:command(`brief-decision:${data.preparation.brief_id}:${outcome}:${note}`)},'Brief review recorded.')}/>}
        {w&&<MeetingPack pack={data.pack} isRm={isRm} busy={busy}
          onDownload={()=>download(`/pack/pdf?document_id=${data.pack.document_id}&version=${data.pack.version}&content_hash=${data.pack.content_hash}`,`meeting-pack-v${data.pack.version}.pdf`)}
          onDraft={amounts=>post('/pack/draft',{amounts,command_id:command(`pack-draft:${data.pack?.document_id}:${JSON.stringify(amounts)}`)},'Discussion pack drafted for separate review.')}
          onApprove={()=>post('/pack/approve',{document_id:data.pack.document_id,version:data.pack.version,revision:data.pack.revision,content_hash:data.pack.content_hash,command_id:command(`pack-approve:${data.pack.document_id}`)},'Exact pack approved. It can now be shared.')}
          onShare={()=>post('/pack/share',{document_id:data.pack.document_id,version:data.pack.version,revision:data.pack.revision,content_hash:data.pack.content_hash,command_id:command(`pack-share:${data.pack.document_id}`)},'Approved discussion pack shared with the client.')}/>}
      </div>}

      {shown===1&&w&&<div className="meeting-step-panel meeting-card" data-testid="meeting-panel-meet"><h2>The meeting</h2><p className="muted">In person or by video. You advise; the assistant does not.</p>
        <div className="meeting-notice"><h4>Can this meeting be documented from a transcript?</h4><p>{w.notice}</p>
          {!w.notice_seen&&<button className="bank-chat-logout" data-testid="meeting-notice-seen" disabled={busy} onClick={()=>post('/notice-seen',{notice_id:w.notice_id,revision:w.revision},'Notice read for this meeting.')}>I agree</button>}
          {!isRm&&<button className="bank-chat-logout" data-testid="meeting-revoke-transcript" disabled={busy} onClick={()=>post('/transcript-revoke',{command_id:command('revoke')},'Transcript access withdrawn. Existing minutes remain retained.')}>Withdraw transcript access</button>}
          <p className="rm-small">{w.recording_allowed?'You both agreed: a transcript can be added after the call.':`A transcript can be added once you and ${client?.split(' ')[0]||'the client'} both agree (she agrees in her app). Typed notes work either way.`} Agreeing does not start any recording.</p></div>
        {data.linkError&&<p className="rm-error" role="alert">{data.linkError}</p>}
        {data.link&&<a className="primary-button" data-testid="meeting-zoom-join" href={data.link.join_url} target="_blank" rel="noopener noreferrer">Join Zoom meeting</a>}
        {isRm&&!data.link&&data.appointment.format==='video'&&Date.parse(data.appointment.starts_at)>Date.now()&&<button className="bank-chat-logout" data-testid="meeting-create-zoom" disabled={busy||!data.capability.zoom_configured}
          onClick={()=>data.capability.zoom_automation_enabled
            ?post('/zoom/request-link',{revision:w.revision,command_id:command(`zoom-link:${w.revision}:${w.jobs?.at(-1)?.event_id}`)},'Zoom link requested. Refresh to see the confirmed provider link.')
            :post('/zoom',{},'Zoom meeting link created.')}>Create Zoom link</button>}
        {w.zoom_capture&&<div data-testid="meeting-zoom-capture-status"><h4>Zoom transcript</h4><p className="rm-small">A participant must start My Notes in Zoom and complete the note when finished. Meeting end alone does not make a transcript available.</p>
          <p role="status" data-testid="meeting-transcript-status">{w.source_provenance&&w.source_provenance.origin!=='zoom_my_notes_api'?`Saved meeting evidence is being used. ${minutesStatus}`:w.zoom_capture.state==='awaiting_note'?'Waiting for Zoom to deliver a captured note.':w.zoom_capture.state==='done'?`Transcript retrieved. ${minutesStatus}`:`Transcript retrieval: ${w.zoom_capture.state}.`}{w.zoom_capture.error_code?` ${w.zoom_capture.error_code}`:''}</p>
          {isRm&&w.state==='ended'&&!w.source_provenance&&<button className="bank-chat-logout" data-testid="meeting-check-transcript" disabled={busy||!w.recording_allowed}
            onClick={()=>post('/zoom/check-transcript',{revision:w.revision,command_id:command(`zoom-check:${w.revision}:${w.jobs?.at(-1)?.event_id}`)},'Zoom transcript check queued. Refresh to see the provider result.')}>Check completed My Notes</button>}</div>}
        {isRm&&<><h4>Call times</h4><p className="rm-small">{w.zoom_capture?'Registered Zoom events can record these. Record them here only when the provider events are unavailable.':'Press start when the meeting begins and end when it finishes (in person or by video).'}</p>
          <div className="meeting-actions"><button className="primary-button" data-testid="meeting-call-start" disabled={busy||w.state!=='scheduled'} onClick={()=>post('/documented-start',{revision:w.revision,at:documentedTime(`call-start:${w.revision}`),command_id:command(`call-start:${w.revision}`)},'Call start recorded.')}>Meeting started</button>
          <button className="bank-chat-logout" data-testid="meeting-call-end" disabled={busy||w.state!=='in_progress'} onClick={()=>post('/documented-end',{revision:w.revision,at:documentedTime(`call-end:${w.revision}`),command_id:command(`call-end:${w.revision}`)},'Call end recorded. Next: document the call.')}>Meeting ended</button></div></>}
      </div>}

      {shown===2&&w&&<div className="meeting-step-panel meeting-card" data-testid="meeting-panel-document"><h2>Document the call</h2><p className="muted">{zoomPath?'The Zoom transcript is used automatically when it arrives':'Add the transcript or your own notes'}. The assistant drafts the minutes from it; you review before anything is saved.</p>
        {isRm&&!d&&w.state!=='ended'&&<p className="rmw-warn">Record the meeting end in step 2 first. Then add the transcript or your notes here.</p>}
        {isRm&&!d&&w.zoom_capture&&(!w.source_provenance||w.source_provenance.origin==='zoom_my_notes_api')&&<div className="meeting-zoom-wait" data-testid="meeting-zoom-step3">
          <h3>Zoom transcript</h3>
          <p role="status">{w.zoom_capture.state==='done'?'Transcript received from Zoom. The minutes are being drafted; refresh in a moment.':w.zoom_capture.state==='awaiting_note'?'Waiting for Zoom. When the call ends, complete the My Notes note in Zoom; the minutes are then drafted here automatically.':`Zoom transcript: ${w.zoom_capture.state.replaceAll('_',' ')}.`}{w.zoom_capture.error_code?` (${w.zoom_capture.error_code.replaceAll('_',' ')})`:''}</p>
          <div className="meeting-actions"><button className="bank-chat-logout" disabled={busy} onClick={()=>run(async()=>{},'Checked for the Zoom transcript.')}><Icon name="refresh"/>Check again</button>
            {w.state==='ended'&&<button className="bank-chat-logout" disabled={busy||!w.recording_allowed} onClick={()=>post('/zoom/check-transcript',{revision:w.revision,command_id:command(`zoom-check:${w.revision}:${w.jobs?.at(-1)?.event_id}`)},'Asked Zoom for the completed My Notes transcript. Refresh in a moment.')}>Ask Zoom now</button>}</div></div>}
        {isRm&&w.state==='ended'&&!d&&(w.zoom_capture&&(!w.source_provenance||w.source_provenance.origin==='zoom_my_notes_api')?<details className="meeting-fallback"><summary>Zoom transcript didn’t arrive? Upload the file or type your notes instead</summary>{fallback()}</details>:fallback())}

        {(w.jobs?.length>0||(isRm&&data.recovery))&&<details className="meeting-technical"><summary>Technical details (only if something went wrong){w.jobs?.some(job=>job.status==='failed')&&<span className="rm-error"> · a step failed</span>}</summary>
          <p className="muted">Background steps the app ran for this meeting, and tools to correct the call times. You don’t need these in a normal meeting.</p>
          {w.jobs?.map(job=><p key={job.event_id} className={job.status==='failed'?'rm-error':'rm-small'} data-testid="meeting-job">{job.type}: {job.status}{job.error_code?` (${job.error_code})`:''}</p>)}
          {isRm&&data.recovery&&<MeetingRecovery recovery={data.recovery} busy={busy} onPost={(suffix,body,message)=>post(suffix,{...body,command_id:command(`recovery:${suffix}:${JSON.stringify(body)}`)},message)}/>}</details>}
        {isRm&&data.zoomEvidence&&<ZoomSpeakerReview evidence={data.zoomEvidence} busy={busy} onSave={mapping=>post('/zoom/speakers',{
          source_id:data.zoomEvidence.source_id,source_hash:data.zoomEvidence.source_hash,revision:data.zoomEvidence.revision,
          consent_epoch:data.zoomEvidence.consent_epoch,mapping,command_id:command(`zoom-speakers:${data.zoomEvidence.source_id}:${JSON.stringify(mapping)}`)},'Speaker roles saved as a new source version. Fresh minutes are queued for review.')}/>}
        {w.source_provenance&&<p className="rm-small">Source: {w.source_provenance.origin.replaceAll('_',' ')} · Coverage: {w.source_provenance.coverage||'your notes'}{w.source_provenance.coverage==='partial'?' — missing conversation is not covered.':''}</p>}
        {d&&<div className="meeting-minutes doc" data-testid="meeting-minutes">
          {/* Design: standard minutes. Header facts, one line, then purpose, key points with what was said, decisions, actions, open points, and the client recap kept apart. */}
          <div className="doc-head"><div><span className="eyebrow">{d.approved?'Approved minutes':'Draft minutes · review required'}</span><h4>{d.content.title}</h4>
            <p className="doc-meta">{a&&<span>{new Date(a.starts_at).toLocaleDateString('en-SG',{weekday:'short',day:'numeric',month:'short',year:'numeric',timeZone:'Asia/Singapore'})}</span>}
              <span>You and {client||'the client'}</span>{w.source_provenance&&<span>From: {w.source_provenance.origin==='zoom_my_notes_api'?'Zoom transcript':w.source_provenance.origin.replaceAll('_',' ')}{w.source_provenance.coverage?`, ${w.source_provenance.coverage==='full_rm_attested'?'whole call':w.source_provenance.coverage==='partial'?'part of the call':'coverage not confirmed'}`:''}</span>}</p></div></div>
          {d.source_revoked&&<p className="rmw-warn">The transcript was withdrawn. This existing draft may be reviewed but cannot be regenerated from that transcript.</p>}
          {d.content.summary[0]&&<OneLine>{d.content.summary[0].text}</OneLine>}
          {a?.purpose&&<DocSection n={1} title="Purpose"><p className="doc-point"><span className="doc-text">{clientWords(a.purpose)||a.purpose}</span></p></DocSection>}
          <DocSection n={a?.purpose?2:1} title="Key points">{d.content.summary.map((item,i)=><div key={i} className="doc-quoted"><p className="doc-point"><span className="doc-text">{item.text}</span></p>
            <details><summary>What was said</summary><blockquote>“{item.quote}” <span className="rm-small">· {item.cue_id}</span></blockquote></details></div>)}</DocSection>
          {d.content.decisions.length>0&&<DocSection n={(a?.purpose?2:1)+1} title="Decisions">{d.content.decisions.map((item,i)=><div key={i} className="doc-quoted"><p className="doc-point"><span className="doc-text">{item.text}</span></p>
            <details><summary>What was said</summary><blockquote>“{item.quote}” <span className="rm-small">· {item.cue_id}</span></blockquote></details></div>)}</DocSection>}
          <DocSection n={(a?.purpose?2:1)+(d.content.decisions.length?2:1)} title="Actions"><table className="doc-actions"><thead><tr><th>What</th><th>Who</th><th>Due</th><th>Status</th></tr></thead><tbody>
            {d.content.actions.map(item=><tr key={item.action_id} className="meeting-action-item"><td>{item.text}{item.condition&&<small className="rm-small"> · if {item.condition}</small>}
              <details><summary>What was said</summary><blockquote>“{item.quote}”</blockquote>{item.due_quote&&<p className="rm-small">Due date: “{item.due_quote}”</p>}</details></td>
              <td>{item.owner==='rm'?'You':item.owner==='client'?(client||'Client'):'Unassigned'}</td><td>{item.due_on?<span data-testid="meeting-action-due">{item.due_on}</span>:'—'}</td>
              <td><span className={`tag ${item.commitment==='agreed'?'warn':'grey'}`}>{item.commitment==='agreed'?'Agreed':item.commitment.replace(/^./,c=>c.toUpperCase())}</span></td></tr>)}</tbody></table></DocSection>
          {d.content.actions.some(item=>item.commitment!=='agreed')&&<DocSection n={(a?.purpose?2:1)+(d.content.decisions.length?3:2)} title="Still open"><p className="doc-point"><span className="doc-text">{d.content.actions.filter(item=>item.commitment!=='agreed').map(item=>item.text).join(' · ')}</span></p></DocSection>}
          <p className="rm-small">Advice in this meeting was given by you. The assistant drafted these minutes from what was said; open “What was said” to check each point.</p>
          <div className="doc-recap"><span className="eyebrow">Recap for {client||'the client'} · shared separately, in plain words</span><p>{d.content.client_recap}</p></div>
          {!d.approved&&<><MeetingMinutesReview review={data.review} busy={busy}
            onReview={(outcome,note)=>approve('/minutes/review',{outcome,note},'Minutes review recorded. This version cannot be approved.')}
            onRegenerate={()=>approve('/minutes/regenerate',{},'Regeneration requested. Refresh to check the new minutes version.')}/>
          <button className="primary-button" data-testid="meeting-approve-minutes" disabled={busy||Boolean(data.review)} onClick={()=>approve('/approve-minutes',{},'Minutes approved. Next: follow up.')}>Approve these minutes</button></>}
          {d.approved&&<button className="bank-chat-logout" onClick={()=>setOpen(3)}>Go to follow up →</button>}</div>}
      </div>}

      {shown===3&&w&&<div className="meeting-step-panel meeting-followup" data-testid="meeting-panel-follow-up">
        <div className="meeting-followup-head"><div><h2>{isRm?'Review the drafts':'Follow up'}</h2>{isRm&&d?.approved&&<p className="muted">Drafted from the approved minutes. Edit anything that isn’t right and untick what you don’t want. Nothing goes out until you press Send.</p>}</div></div>
        {!d?.approved&&<p className="rmw-warn">Approve the minutes in step 3 first. Follow-ups and the recap are drafted from the approved minutes.</p>}
        {isRm&&d?.approved&&<>
          <div className="meeting-draft-grid">
            <section className="rmw-card meeting-draft-card" aria-label="Follow-ups">
              <div className="meeting-draft-head"><label><input type="checkbox" aria-label="Select all follow-ups" disabled={busy||followupsDone||!offered.length} checked={offered.length>0&&offered.every(x=>selected.includes(x.action_id))}
                onChange={e=>setSelected(e.target.checked?offered.map(x=>x.action_id):[])}/><h3>Follow-ups ({offered.length})</h3></label><span className={`tag ${followupsDone?'ok':'warn'}`}>{followupsDone?'Sent':'Draft'}</span></div>
              <div className="meeting-task-table" role="table" aria-label="Follow-ups from the minutes"><div role="row" className="head"><span/><span className="eyebrow">Task</span><span className="eyebrow">Owner</span><span className="eyebrow">Due</span></div>
                {d.content.actions.map(item=>{const ok=item.commitment==='agreed'&&item.owner!=='unassigned',on=ok&&!followupsDone&&!busy;return <div role="row" key={item.action_id} className={ok?'':'off'}>
                  <input type="checkbox" data-testid="meeting-action-choice" aria-label={`Send: ${item.text}`} value={item.action_id} disabled={!on} checked={selected.includes(item.action_id)} onChange={e=>setSelected(ids=>e.target.checked?[...ids,item.action_id]:ids.filter(id=>id!==item.action_id))}/>
                  {ok?<input className="in" aria-label="Task" data-testid="meeting-action-text" value={field(item,'text')} maxLength={300} disabled={!on} onChange={e=>setField(item,'text',e.target.value)}/>
                    :<span>{item.text}<small className="rm-small"> ({item.commitment==='agreed'?'no owner':item.commitment}: not offered as a follow-up)</small></span>}
                  {ok?<select className="in" aria-label="Owner" value={field(item,'owner')} disabled={!on} onChange={e=>setField(item,'owner',e.target.value)}><option value="rm">You</option><option value="client">{first}</option></select>:<span>{item.owner==='rm'?'You':item.owner==='client'?first:'Unassigned'}</span>}
                  {ok?<input className="in" type="date" aria-label="Due" value={field(item,'due_on')} disabled={!on} onChange={e=>setField(item,'due_on',e.target.value)}/>:<span>{item.due_on||'—'}</span>}</div>;})}</div>
              <span className="rm-small">Each comes from the approved minutes · {first}’s tasks go to her to-dos{changedEdits()?' · edited by you':''}</span>
            </section>
            <section className="rmw-card meeting-draft-card" aria-label="Recap to the client">
              <div className="meeting-draft-head"><label><input type="checkbox" data-testid="meeting-include-recap" aria-label={`Send the recap to ${first}`} checked={includeRecap&&!w.shared_recap} disabled={busy||Boolean(w.shared_recap)} onChange={e=>setIncludeRecap(e.target.checked)}/><h3>Recap to {first}</h3></label><span className={`tag ${w.shared_recap?'ok':'warn'}`}>{w.shared_recap?'Sent':'Draft'}</span></div>
              <label className="form-field">Message to her Inbox<textarea className="meeting-recap-text" data-testid="meeting-recap-text" rows={7} maxLength={2000} disabled={busy||Boolean(w.shared_recap)} value={w.shared_recap||recapValue} onChange={e=>setRecapText(e.target.value)}/></label>
              <span className="rm-small">{recapChanged?<>Edited · <button type="button" className="link-button" onClick={()=>setRecapText(null)}>Undo my changes</button></>:'Sent through Notification · edit anything that isn’t right'}</span>
            </section>
            {HandoffDrafts&&<Suspense fallback={<p className="loading" role="status">Loading…</p>}><HandoffDrafts token={token} notes={handoffNotes} clientId={data.appointment.customer_id} meetingId={meetingId} client={client}/></Suspense>}
          </div>
          {(()=>{const n=(followupsDone?0:selected.length)+(includeRecap&&!w.shared_recap?1:0),total=(followupsDone?0:offered.length)+(w.shared_recap?0:1);
            return <div className="meeting-send-bar meeting-send-sticky"><span><strong>{n} of {total} selected</strong> <span className="muted">· each is saved and audited as its own change{total===0?' · everything here was sent':''}</span></span>
              <button className="primary-button" data-testid="meeting-send-followup" disabled={busy||!n||badEdit} onClick={sendFollowUp}>Send {n} item{n===1?'':'s'}</button></div>;})()}
        </>}
        {data.recap&&<button className="bank-chat-logout" data-testid="meeting-download-recap" disabled={busy} onClick={()=>download(`/recap/pdf?document_id=${data.recap.document_id}&version=${data.recap.version}&content_hash=${data.recap.content_hash}`,`meeting-recap-v${data.recap.version}.pdf`)}>Download approved recap PDF</button>}
        {(w.followups.length>0||w.shared_recap||!isRm)&&<section className="rmw-card meeting-tracker"><h3>After sending</h3>
          <h4>Follow-up tracker</h4>{!w.followups.length&&<p className="page-empty">No approved follow-ups yet.</p>}{w.followups.map(item=><div className="meeting-action-item" key={item.followup_id}><p><strong>{item.owner==='rm'?'You':client||'Client'}:</strong> {item.text}</p><p className="rm-small">{item.status}{item.due_on?` · due ${item.due_on}`:''}</p>
          {item.status==='open'&&item.owner===(isRm?'rm':'client')&&<form onSubmit={e=>{e.preventDefault();post(`/followups/${item.followup_id}/complete`,{evidence:completion[item.followup_id],command_id:command(`complete:${item.followup_id}:${completion[item.followup_id]}`)},'Your follow-up was completed.');}}><label className="form-field">Completion evidence<input data-testid={`meeting-evidence-${item.followup_id}`} value={completion[item.followup_id]||''} maxLength={500} required disabled={busy} onChange={e=>setCompletion(values=>({...values,[item.followup_id]:e.target.value}))}/></label><button className="bank-chat-logout" data-testid={`meeting-complete-${item.followup_id}`} disabled={busy}>Mark my action complete</button></form>}</div>)}
          <h4>Shared recap</h4><p data-testid="meeting-shared-recap">{w.shared_recap||'No recap has been shared yet.'}</p></section>}
      </div>}
      {shown>0&&!w&&<p className="rmw-warn">Open the meeting in step 1 first.</p>}
      </div>
      {shown!==3&&<aside className="meeting-rail">
        {shown===2&&isRm&&w&&!d&&<section className="meeting-card meeting-ready" data-testid="meeting-ready"><h3>Ready to draft minutes?</h3><ul>
          <li className={w.state==='ended'?'ok':'todo'}><span>{w.state==='ended'?'✓':'○'}</span>Call start and end recorded</li>
          {method==='transcript'&&<li className={w.recording_allowed?'ok':'todo'}><span>{w.recording_allowed?'✓':'○'}</span>You and {first} both agreed this meeting can be documented from a transcript</li>}
          {zoomPath&&<li className={w.zoom_capture.state==='done'?'ok':'todo'}><span>{w.zoom_capture.state==='done'?'✓':'○'}</span>Zoom transcript received (complete My Notes in Zoom)</li>}
          {!zoomPath&&method==='transcript'&&<li className={matched?'ok':'todo'}><span>{matched?'✓':'○'}</span>Both speakers matched</li>}
          {method==='notes'&&<li className={notes.trim()?'ok':'todo'}><span>{notes.trim()?'✓':'○'}</span>Your notes written</li>}</ul>
          {zoomPath?<p className="muted">Zoom didn’t send it? Use the upload or notes option under the Zoom box.</p>:method==='transcript'?<p className="muted">No transcript? <button type="button" className="link-button" onClick={()=>setMethod('notes')}>Type your notes instead</button>.</p>
            :<p className="muted">Have the file? <button type="button" className="link-button" onClick={()=>setMethod('transcript')}>Upload the transcript</button>.</p>}</section>}
        <section className="meeting-card meeting-glance"><h3>At a glance</h3>
          <span className="eyebrow">Briefing</span><p>{prep?.card?<>Version {prep.card.version} · {prep.decision==='approved'&&!prep.sources_changed?'approved':'to review'} · <button type="button" className="link-button" onClick={()=>setOpen(0)}>Open</button></>:'Not prepared yet'}</p>
          {a?.topics?.length>0&&<><span className="eyebrow">Topics</span><p>{a.topics.map(topicName).join(', ')}</p></>}
          {prep?.card?.consent&&<><span className="eyebrow">She shares</span><p>Chat topics: {prep.card.consent.chat_topics?'yes':'no'} · Transaction insights: {prep.card.consent.transaction_insights?'yes':'no'}</p></>}
          <span className="eyebrow">Status</span>      <p data-testid="meeting-lifecycle-stage">{stageWords(data.lifecycle.stage,data.lifecycle.waiting_for)}{data.preparation?.sources_changed?' · the brief needs a refresh':''}</p>
        </section>
      </aside>}</div>
    </>}
  </section>;
}
