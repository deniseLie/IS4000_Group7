import {useEffect,useState} from 'react';
import Icon from './Icon';
import {supabase} from '../lib/supabase';
import {loadSections} from '../lib/customerData';
import {loadAppointments} from '../lib/relationship';
import {appointmentTime} from '../lib/appointmentData';
import {formatDate} from '../lib/format';

// Signed-in assistant home (design: client assistant): "For you right now" from her own records, then starters grouped
// by topic, written as her own questions. Reads are optional: if one fails, that row is simply not shown.
const GROUPS=[
  ['My money',[['When does my time deposit mature?','From your investments'],['What did I spend last month?','From your transactions'],['How do I get my statement?','Ready in one step']]],
  ['Cards and payments',[['Does paying only the minimum on my credit card avoid interest?','How interest works'],['I don’t recognise a charge on my card','Get it checked quickly'],['I lost my card','What to do now']]],
  ['Plans and financing',[['Which financing does DemoBank offer?','Property financing and more'],['Can I borrow against my investments?','How a Lombard loan works'],['I’m thinking of buying a home. Can I meet my relationship manager?','Request a meeting']]],
  ['Documents and requests',[['What does my periodic review still need?','Your documents'],['What’s the status of my requests?','Your requests'],['How do I upload a document?','Step by step']]]];
const REVIEW_DONE=['completed','closed'];

export default function ClientWelcome({onDraft,onNavigate}) {
  const [tab,setTab]=useState(0),[now,setNow]=useState([]);
  useEffect(()=>{
    const controller=new AbortController();
    (async()=>{try{
      const {data}=await supabase.auth.getSession();const access=data.session?.access_token;if(!access)return;
      const quiet=promise=>promise.catch(()=>null);
      const [sections,meetings]=await Promise.all([quiet(loadSections(access,['portfolio','compliance'],controller.signal)),quiet(loadAppointments(access,controller.signal))]);
      if(controller.signal.aborted)return;
      const rows=[];
      const deposit=(sections?.portfolio?.data?.holdings||[]).find(row=>/time deposit/i.test(row.description)&&/matures?/i.test(row.description));
      const when=deposit?.description.match(/matures?\s+(?:on\s+)?(\d{1,2}\s+\w+(?:\s+\d{4})?)/i)?.[1];
      if(deposit)rows.push({key:'deposit',tone:'date',text:<>Your {deposit.description.split(',')[0].replace(/^./,c=>c.toLowerCase())} matures{when?<> on <strong>{when}</strong></>:' soon'}</>,label:'What are my options?',go:()=>onDraft(`My ${deposit.description.split(',')[0].toLowerCase()} matures soon. What are my options?`)});
      const next=(meetings?.data||[]).filter(item=>['confirmed','held'].includes(item.status)&&Date.parse(item.ends_at)>Date.now()).sort((a,b)=>Date.parse(a.starts_at)-Date.parse(b.starts_at))[0];
      if(next)rows.push({key:'meeting',tone:'meeting',text:<>{next.status==='held'?'A meeting time is held for you':'Meeting with your relationship manager'} <strong>{appointmentTime(next.starts_at)}</strong></>,label:next.status==='held'?'Confirm':'Open meeting',go:()=>onNavigate('meetings')});
      const review=sections?.compliance?.data,missing=review&&!REVIEW_DONE.includes(review.status)?review.requested_items.filter(item=>['not_submitted','rejected'].includes(item.submission_status)):[];
      if(missing.length)rows.push({key:'review',tone:'urgent',text:<>Your periodic review needs <strong>{missing.length===1?'1 document':`${missing.length} documents`}</strong>{review.due_date?` by ${formatDate(review.due_date)}`:''}</>,label:'Continue',go:()=>onNavigate('compliance')});
      setNow(rows);
    }catch{}})();
    return ()=>controller.abort();
  },[]);  // eslint-disable-line react-hooks/exhaustive-deps
  return <>
    {now.length>0&&<section className="rm-now cw-now" aria-label="For you right now" data-testid="client-for-you"><div className="rm-now-head"><strong>For you right now</strong><span className="rm-small">from your accounts and requests</span></div>
      {now.map(row=><div key={row.key} className="rm-now-row"><span className={`rm-dot ${row.tone}`} aria-hidden="true"/><span className="rm-now-text">{row.text}</span>
        <button type="button" className="bank-chat-logout" onClick={row.go}>{row.label}</button></div>)}</section>}
    <nav className="page-tabs rm-starter-tabs" role="tablist" aria-label="What I can help with">{GROUPS.map(([label],i)=><button key={label} type="button" role="tab" aria-selected={tab===i} onClick={()=>setTab(i)}>{label}</button>)}</nav>
    <div className="bank-chat-suggestions client-starters">{GROUPS[tab][1].map(([question,detail])=><button key={question} onClick={()=>onDraft(question)}>
      <strong>{question}</strong><span>{detail}</span><span className="topic-arrow" aria-hidden="true">↗</span></button>)}</div>
  </>;
}
