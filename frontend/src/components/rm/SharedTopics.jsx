import {useEffect,useState} from 'react';
import Icon from '../Icon';
import {rmGet} from '../../lib/rm';
import {sharedTopicRows,TOPICS} from '../../lib/talkingPoints';

// Today (0108): topics clients shared with her themselves (lane 2's "Let her know"), confirmed only. Inferred topics
// never show here: they stay per client, behind her chat_topics grant and the 24-hour hold.
export default function SharedTopics({token,onAsk}) {
  const [rows,setRows]=useState(null),[error,setError]=useState('');
  useEffect(()=>{
    const controller=new AbortController();
    (async()=>{try{const result=await rmGet('/shared-topics',await token(),controller.signal);if(!controller.signal.aborted)setRows(sharedTopicRows(result.data));}
      catch(failure){if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}})();
    return ()=>controller.abort();
  },[]);  // eslint-disable-line react-hooks/exhaustive-deps
  return <section className="rmw-card" data-testid="rm-today-shared-topics">
    <div className="rmw-card-head"><h3><Icon name="people"/>Client chat insights shared with you</h3></div>
    {error&&<p role="alert" className="rm-error">{error}</p>}
    {!rows&&!error&&<p className="loading" role="status">Loading…</p>}
    {rows&&!rows.length&&<p className="rm-none">No client has shared a topic with you in the last 30 days.</p>}
    {rows&&rows.length>0&&<ul className="rmw-list">{rows.map(r=><li key={r.share_id}><div><strong>{r.client}</strong>
      <small data-testid="rm-shared-topic-line">{r.line}{r.shared_text&&` · ${r.shared_text}`}{r.isNew&&<span className="rmw-chip info rmw-new">New</span>}</small></div>
      <div className="rmw-list-actions"><button className="rmw-mini" onClick={()=>onAsk(`What should I know about ${r.client} before I follow up on ${TOPICS[r.topic].toLowerCase()}?`,
        {intent:'client_overview',context:{client_id:r.customer_id}})}><Icon name="spark"/>Ask</button></div></li>)}</ul>}
  </section>;
}
