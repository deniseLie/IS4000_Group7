import {REMOVED_REASONS,TOPICS,pauseNote,plainLine} from '../lib/talkingPoints';
import {formatDate,formatDateTime} from '../lib/format';

// Data sharing → Topics from your chats (0108, redesign 8 Nov): each topic her relationship manager can use for her
// meetings, in plain words with the same facts the RM reads. Never her words (0142). Delete (Undo for 7 days), Don't
// suggest this again and Resume are automatic actions with a receipt in Activity.
export default function TalkingPoints({data,rm,busy,saving,act}) {
  const Rm=rm[0].toUpperCase()+rm.slice(1),off=busy||saving;
  const run=(type,target,message)=>act(type,target,message,'talking-points');
  return <section className="sharing-topics" aria-label={`What ${rm} sees`} data-testid="talking-points">
    <h2>What {rm} sees</h2>
    {!data.chat_topics&&<p className="page-note" data-testid="talking-points-off">Topics aren't picked up from your chats while this is off. Topics you choose to share in a chat still show here.</p>}
    {data.pause&&<div className="sharing-ask" data-testid="talking-points-pause"><p>{pauseNote(data.pause.until,formatDate)}</p>
      <button type="button" className="bank-chat-logout" data-testid="talking-points-resume" disabled={off} onClick={()=>run('insight.resume',{topic:null},'Topic suggestions are on again.')}>Resume</button></div>}
    {data.points.length===0?<p className="page-empty" data-testid="talking-points-empty">{Rm} sees no topics from your chats.</p>:
    <ul className="sharing-list" data-testid="talking-point-list">{data.points.map(point=><li key={point.point_id} className="sharing-topic" data-topic={point.topic} data-origin={point.origin}>
      <div><span data-testid="talking-point-line">{plainLine(point)}</span>
        <small>{point.origin==='confirmed'?`You shared this · ${formatDate(point.first_seen)}`:`Picked up from your chat · ${formatDate(point.last_seen)}`}</small>
        {point.pending&&<small><span className="sharing-pill wait" data-testid="talking-point-pending">{Rm} sees this from {formatDateTime(point.visible_from)}</span></small>}</div>
      <div className="sharing-topic-actions">
        <button type="button" className="bank-chat-logout" data-testid="talking-point-delete" disabled={off}
          onClick={()=>run('insight.delete',{point_id:point.point_id},`${TOPICS[point.topic]} was deleted. ${Rm} no longer sees it. You can undo this for 7 days.`)}>Delete</button>
        {point.origin==='inferred'&&<details className="sharing-more"><summary aria-label={`More for ${TOPICS[point.topic].toLowerCase()}`}>⋯</summary>
          <button type="button" className="sharing-link" data-testid="talking-point-mute" disabled={off}
            onClick={()=>run('insight.mute',{topic:point.topic},`${TOPICS[point.topic]} won’t be suggested again.`)}>Don’t suggest this again</button></details>}
      </div></li>)}</ul>}
    {data.removed.length>0&&<details className="sharing-fold" data-testid="talking-point-removed"><summary>Recently removed ({data.removed.length})</summary>
      <ul className="sharing-list">{data.removed.map(p=><li key={p.point_id} data-topic={p.topic}>
        <span>{TOPICS[p.topic]} · {REMOVED_REASONS[p.reason]}</span><small>Removed {formatDate(p.removed_at)}. Gone for good after 7 days.</small>
        {p.can_restore&&<button type="button" className="sharing-link" data-testid="talking-point-undo" disabled={off}
          onClick={()=>run('insight.restore',{point_id:p.point_id},`${TOPICS[p.topic]} is back.`)}>Undo (until {formatDateTime(p.restore_until)})</button>}</li>)}</ul></details>}
    {data.muted.length>0&&<details className="sharing-fold" data-testid="talking-point-muted"><summary>Not suggested ({data.muted.length})</summary>
      <ul className="sharing-list">{data.muted.map(topic=><li key={topic}><span>{TOPICS[topic]}</span>
        <button type="button" className="sharing-link" disabled={off} onClick={()=>run('insight.resume',{topic},`${TOPICS[topic]} can be suggested again.`)}>Allow again</button></li>)}</ul></details>}
  </section>;
}
