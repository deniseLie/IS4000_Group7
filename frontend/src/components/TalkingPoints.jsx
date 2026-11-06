import {useEffect,useRef,useState} from 'react';
import {supabase} from '../lib/supabase';
import {createActionCase,proposeAction} from '../lib/actions';
import {loadTalkingPoints,pauseNote,REMOVED_REASONS,TOPICS} from '../lib/talkingPoints';
import {formatDate,formatDateTime} from '../lib/format';

// Data sharing → What <RM> sees (0108): each topic exactly as her relationship manager reads it, a Pending badge during
// the 24-hour hold, her own words under "Why we think this" (hers only), and Delete / Don't suggest this again / Resume,
// each an automatic action with a receipt in Actions.
export default function TalkingPoints({rmName,onReviewActions}) {
  const [data,setData]=useState(null);
  const [busy,setBusy]=useState(true);
  const [saving,setSaving]=useState(false);
  const [error,setError]=useState('');
  const [done,setDone]=useState('');
  const active=useRef(null);
  useEffect(()=>{refresh();return ()=>active.current?.abort();},[]);
  async function token() {
    const {data:auth,error:sessionError}=await supabase.auth.getSession();
    if(sessionError)throw sessionError;
    if(!auth.session)throw new Error('Please sign in again to review your topics.');
    return auth.session.access_token;
  }
  async function refresh() {
    const controller=new AbortController();active.current=controller;setBusy(true);setError('');
    try {const result=await loadTalkingPoints(await token(),controller.signal);if(!controller.signal.aborted)setData(result);}
    catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted){setError(failure.message);setData(null);}}
    finally {if(!controller.signal.aborted)setBusy(false);}
  }
  async function act(type,target,message) {
    const controller=new AbortController();active.current=controller;setSaving(true);setError('');setDone('');
    try {
      const key=await token();
      const scope=await createActionCase({session_id:crypto.randomUUID(),case_id:crypto.randomUUID()},key,controller.signal);
      const saved=await proposeAction({type,target,command_id:crypto.randomUUID(),task_id:'talking-points',case_id:scope.case_id},key,controller.signal);
      if(controller.signal.aborted)return;
      if(saved.receipt?.outcome!=='succeeded')throw new Error('This change did not go through. Refresh, then try again.');
      setDone(message);await refresh();
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    finally {if(!controller.signal.aborted)setSaving(false);}
  }
  const rm=data?.rm_first_name||rmName||'your relationship manager',off=busy||saving;
  return <section className="page-section" aria-label={`What ${rm} sees`} data-testid="talking-points">
    <h2 className="section-label">What {rm} sees</h2>
    <p>Topics from your chats that {rm} can use to prepare your meetings. {rm[0].toUpperCase()+rm.slice(1)} sees each line below exactly as shown, never your messages.</p>
    {busy&&!data&&<p className="loading" role="status">Loading your topics…</p>}
    {error&&<p role="alert" data-testid="talking-points-error">{error}</p>}
    {done&&<p role="status" data-testid="talking-points-done">{done} <button type="button" className="bank-chat-logout" onClick={onReviewActions}>See the receipt in Activity</button></p>}
    {data&&<>
      {!data.chat_topics&&<p className="page-note" data-testid="talking-points-off">Topics are not picked up from your chats: chat topics are not part of your sharing consent. Topics you choose to share in a chat still appear here.</p>}
      {data.pause&&<p className="page-note" data-testid="talking-points-pause">{pauseNote(data.pause.until,formatDate)}{' '}
        <button type="button" className="bank-chat-logout" data-testid="talking-points-resume" disabled={off} onClick={()=>act('insight.resume',{topic:null},'Topic suggestions are on again.')}>Resume</button></p>}
      {data.points.length===0&&<p className="page-empty" data-testid="talking-points-empty">{rm[0].toUpperCase()+rm.slice(1)} sees no topics from your chats.</p>}
      <ul className="relationship-list" data-testid="talking-point-list">{data.points.map(point=><li key={point.point_id} data-topic={point.topic} data-origin={point.origin}>
        <p><strong data-testid="talking-point-line">{point.line}</strong>{point.pending&&<> <span className="status-badge warning" data-testid="talking-point-pending" title={`Shown from ${formatDateTime(point.visible_from)}`}>Pending</span></>}</p>
        {point.pending&&<p className="page-note">Not shown to {rm} until {formatDateTime(point.visible_from)}. You can delete it before then.</p>}
        {point.evidence.length>0&&<details data-testid="talking-point-why"><summary>Why we think this</summary>
          <ul>{point.evidence.map(q=><li key={q.recorded_at+q.quote}>“{q.quote}” <small>{formatDate(q.recorded_at)}</small></li>)}</ul>
          <p className="page-note">Only you can see your words here. {rm[0].toUpperCase()+rm.slice(1)} sees the line above.</p></details>}
        <div className="panel-actions">
          <button type="button" className="bank-chat-logout" data-testid="talking-point-delete" disabled={off} onClick={()=>act('insight.delete',{point_id:point.point_id},`${TOPICS[point.topic]} was deleted. ${rm[0].toUpperCase()+rm.slice(1)} no longer sees it. You can undo this for 7 days.`)}>Delete</button>
          {point.origin==='inferred'&&<button type="button" className="bank-chat-logout" data-testid="talking-point-mute" disabled={off} onClick={()=>act('insight.mute',{topic:point.topic},`${TOPICS[point.topic]} won’t be suggested again.`)}>Don’t suggest this again</button>}
        </div>
      </li>)}</ul>
      {data.removed.length>0&&<><h3>Removed</h3><ul className="relationship-list" data-testid="talking-point-removed">{data.removed.map(p=><li key={p.point_id} data-topic={p.topic}>
        {TOPICS[p.topic]} · {REMOVED_REASONS[p.reason]} · <small>{formatDate(p.removed_at)}. Gone for good after 7 days.</small>{' '}
        {p.can_restore&&<button type="button" className="bank-chat-logout" data-testid="talking-point-undo" disabled={off}
          onClick={()=>act('insight.restore',{point_id:p.point_id},`${TOPICS[p.topic]} is back.`)}>Undo (until {formatDateTime(p.restore_until)})</button>}</li>)}</ul></>}
      {data.muted.length>0&&<><h3>Not suggested</h3><ul className="relationship-list" data-testid="talking-point-muted">{data.muted.map(topic=><li key={topic}>{TOPICS[topic]}{' '}
        <button type="button" className="bank-chat-logout" disabled={off} onClick={()=>act('insight.resume',{topic},`${TOPICS[topic]} can be suggested again.`)}>Resume</button></li>)}</ul></>}
    </>}
  </section>;
}
