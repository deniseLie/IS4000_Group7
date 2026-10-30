import {useEffect,useRef,useState} from 'react';
import Icon from './Icon';
import {supabase} from '../lib/supabase';
import {confirmAction,loadActions,rejectAction} from '../lib/actions';
import {resumeWorkflow} from '../lib/workflows';
import {appointmentTime} from '../lib/appointmentData';
import {consentCategoryText} from '../lib/consentData';
import {displayName,formatDate,formatDateTime,formatMoney,formatTime} from '../lib/format';
import {POSSESSION,possessionLabel} from '../lib/disputeData';
import {cacheOwner,cached,remember} from '../lib/cache';

function commandTitle(command) {
  if(command.type==='card.temporary_block')return `Temporarily block card ending ${command.target.last4}`;
  if(command.type==='card.dispute')return `Dispute draft for card ending ${command.target.last4}`;
  if(command.type==='card.unblock')return command.staff?`Staff reactivation of card ending ${command.target.last4}`:`Unblock card ending ${command.target.last4}`;
  if(command.type==='card.cancel_permanently')return command.staff?`Staff cancellation of card ending ${command.target.last4}`:`Permanently cancel card ending ${command.target.last4}`;
  if(command.type==='handoff.claim')return 'Staff claimed a demo support handoff';
  if(command.type==='handoff.close')return 'Staff closed a demo support handoff';
  if(command.type==='handoff.create')return 'Record a demo support handoff';
  if(command.type==='appointment.hold')return 'Hold a appointment';
  if(command.type==='appointment.confirm')return 'Confirm a appointment';
  if(command.type==='appointment.cancel')return 'Cancel a appointment';
  if(command.type==='consent.request')return 'Record a data-sharing consent request';
  if(command.type==='consent.grant')return 'Grant reviewed data-sharing consent';
  if(command.type==='consent.revoke')return 'Revoke a recorded data-sharing grant';
  if(command.type==='compliance.update_checklist_item')return 'Save a checklist preparation mark';
  if(command.type==='compliance.answer_request')return 'Answer a question from the bank';
  if(command.type==='document.register')return 'Record a private document upload';
  if(command.type==='document.submit')return 'Submit a checked demo document for review';
  if(command.type==='requests.create')return 'Submit a relationship request';
  if(command.type==='ticket.create')return `Raise a request: ${command.target.category_label}`;
  if(command.type==='agenda.save')return 'Save a private agenda draft';
  if(command.type==='rm.share_summary')return 'Share a saved agenda snapshot';
  throw new Error('This action type has no review title. Reload its recorded status.');
}

function receiptText(item) {
  if(item.receipt.outcome==='failed'&&item.command.type==='rm.share_summary')return 'This snapshot was not shared. The saved draft, assigned recipient or reviewed consent changed. Reopen Data sharing before proposing another snapshot.';
  if(item.receipt.outcome==='failed')return 'This command did not change the target. Its state changed before execution.';
  if(item.command.type==='card.temporary_block')return 'This command temporarily blocked the card.';
  if(item.command.type==='card.dispute')return 'This command recorded the reviewed dispute draft and your answer about the card for bank staff review. Staff decide any refund or cancellation; nothing has been refunded.';
  const noted=item.receipt.result.handoffs_noted?.length?' Bank staff reviewing a case for this card can see this change.':'';
  if(item.command.type==='card.unblock')return item.command.staff?'A verified demo support session reactivated this card.':'You unblocked this card; new card payments can go through again.'+noted;
  if(item.command.type==='card.cancel_permanently')return item.command.staff?'A verified demo support session permanently cancelled this card.':'You permanently cancelled this card. It cannot be reactivated.'+noted;
  if(item.command.type==='handoff.claim')return 'A verified demo support session claimed this support case.';
  if(item.command.type==='handoff.close')return 'A verified demo support session closed this support case. This does not change a card or reverse a transaction.';
  if(item.command.type==='handoff.create')return 'A support handoff is recorded in the demo. There is no staffed support queue.';
  if(item.command.type==='appointment.hold')return 'This command recorded a ten-minute appointment hold. Check current appointments before confirming.';
  if(item.command.type==='appointment.confirm')return 'This command confirmed the appointment. Check current appointments for its latest status. No real meeting or joining link is connected.';
  if(item.command.type==='appointment.cancel')return 'This command cancelled the appointment.';
  if(item.command.type==='consent.request')return 'This command recorded a consent request. It did not grant consent or share data.';
  if(item.command.type==='consent.grant')return 'This command granted the reviewed consent scope. Check Data sharing for its current status. Granting consent does not itself share data.';
  if(item.command.type==='consent.revoke')return 'This command revoked the reviewed grant. Other active grants remain until separately revoked or expired. Previously shared records are not erased.';
  if(item.command.type==='compliance.update_checklist_item')return 'This command saved your preparation mark only. It did not submit or verify a document or change the bank review status. Reload Records for the current mark.';
  if(item.command.type==='document.register')return 'This command recorded a private uploaded file. It did not accept the document for the bank, complete a preliminary check or change the review case status. Reload Records for its current status.';
  if(item.command.type==='document.submit')return 'This command submitted the checked file against its requested item. It is awaiting bank review, not accepted or approved.';
  if(item.command.type==='ticket.create')return `${item.receipt.result.created?'Recorded':'You already had an open request of this kind, so it was reused'}: ${item.receipt.result.reference}. The ${item.receipt.result.owner_team.toLowerCase()} decides the outcome, not the assistant. Check My requests for its status.`;
  if(item.command.type==='requests.create')return 'This command recorded the reviewed request with status sent. This does not mean the service is fulfilled or a real advisor was contacted. Check Requests for its current status.';
  if(item.command.type==='agenda.save')return 'This command saved a private agenda draft. It did not share data. Check Data sharing for the current draft version.';
  if(item.command.type==='rm.share_summary')return 'This command recorded the reviewed snapshot for the relationship manager. Later edits or consent revocation do not erase that shared copy. No real advisor was contacted.';
  throw new Error('This action type has no verified receipt description.');
}

function groupOf(command) {
  const kind=command.type.split('.')[0];
  return kind==='card'?'Cards':['compliance','document'].includes(kind)?'Compliance':['consent','agenda','rm'].includes(kind)?'Data sharing':kind==='appointment'?'Appointments':['requests','ticket'].includes(kind)?'Requests':'Support';
}
// Plain-language card heading and summary; the exact recorded fields stay one click away under "Full details".
function plainTitle(command) {
  const t=command.target;
  switch(command.type) {
    case 'card.temporary_block':return `Freeze card ending ${t.last4}`;
    case 'card.dispute':return `Dispute ${t.items.length} charges on card ending ${t.last4}`;
    case 'card.unblock':return command.staff?`Card ending ${t.last4} reactivated`:`Unblock card ending ${t.last4}`;
    case 'card.cancel_permanently':return command.staff?`Card ending ${t.last4} cancelled`:`Cancel card ending ${t.last4} permanently`;
    case 'compliance.update_checklist_item':return t.item_label;
    case 'compliance.answer_request':return t.question;
    case 'document.register':return t.item_label||'Document upload';
    case 'document.submit':return t.item_label;
    case 'requests.create':return `Request to ${displayName(t.recipient_name)}`;
    case 'ticket.create':return t.category_label;
    case 'agenda.save':return 'Handover note draft';
    case 'rm.share_summary':return `Handover note shared with ${displayName(t.recipient_name)}`;
    case 'consent.request':case 'consent.grant':return 'Relationship manager data sharing';
    case 'consent.revoke':return 'Withdraw data-sharing consent';
    case 'appointment.hold':case 'appointment.confirm':case 'appointment.cancel':return `Meeting on ${appointmentTime(t.starts_at)}`;
    default:return command.type.startsWith('handoff.')?'Support handoff':commandTitle(command);
  }
}
function plainSummary(command) {
  const t=command.target,mark=value=>value.replaceAll('_',' '),who=t.recipient_name?displayName(t.recipient_name):t.recipient;
  switch(command.type) {
    case 'card.temporary_block':return 'Temporarily blocks this card so no new payments go through. It can be reactivated by support.';
    case 'card.dispute':return `Sends these charges to bank staff for review: ${t.items.map(charge).join('; ')}. Staff decide any refund.`;
    case 'card.unblock':return command.staff?'':'Reactivates this card so new payments go through again. If it was blocked for a suspicious charge, unblock it only if you are sure the card is safe.';
    case 'card.cancel_permanently':return command.staff?'':'Permanently cancels this card. This cannot be undone: the card can never be reactivated.';
    case 'compliance.answer_request':return `Your answer to "${t.question}" goes to the bank for review.`;
    case 'compliance.update_checklist_item':return `Your preparation mark changes from ${mark(t.expected_client_mark)} to ${mark(t.client_mark)} for ${t.case_title}. The bank status stays ${mark(t.submission_status)}.`;
    case 'document.register':return `${t.file_name} (${(t.size_bytes/1048576).toFixed(2)} MB) stored privately for ${t.case_title}.`;
    case 'document.submit':return `${t.file_name} sent to the bank for review.`;
    case 'requests.create':return `“${t.what}” · ${t.urgency} urgency${t.deadline?` · requested by ${formatDate(t.deadline)}`:''}.`;
    case 'ticket.create':return `“${t.summary}” · goes to the ${t.owner_team.toLowerCase()}, who decide.`;
    case 'agenda.save':return `Private draft for ${who} (revision ${t.expected_revision+1}). Nothing is shared.`;
    case 'rm.share_summary':return `Shares your saved note (revision ${t.agenda_revision}) with ${who}: ${consentCategoryText(t.categories)}.`;
    case 'consent.request':return `Ask to let ${who} see ${consentCategoryText(t.categories)} for ${t.duration_days} days.`;
    case 'consent.grant':return `Lets ${who} see ${consentCategoryText(t.categories)}${t.expires_at?` until ${formatDate(t.expires_at)}`:''}. Other records stay restricted.`;
    case 'consent.revoke':return `Stops ${who} seeing ${consentCategoryText(t.categories)} from now on.`;
    case 'appointment.hold':case 'appointment.confirm':case 'appointment.cancel':return `${appointmentTime(t.starts_at)} – ${formatTime(t.ends_at)} (Singapore time) · ${mark(t.format)}.`;
    case 'handoff.create':return t.summary;
    default:return '';
  }
}
const shortDate=formatDateTime;
const charge=item=>`${formatDate(item.date)} ${item.merchant} ${formatMoney(item.amount,item.currency)}`;
// Review wording per type; an unknown type gets neutral wording rather than another action's.
const REVIEW={'card.temporary_block':['Confirming temporarily blocks this card. Rejecting leaves it unchanged. This does not reverse a transaction or permanently cancel a card.','card','Confirm temporary block'],
  'card.unblock':['Confirming reactivates this card so new payments can go through. If it was blocked because of a suspicious charge, anyone using this signed-in session could do the same, so confirm only if you are sure the card is safe. Rejecting leaves it blocked.','card','Unblock card'],
  'card.cancel_permanently':['Confirming permanently cancels this card. This cannot be undone: it can never be reactivated, and a replacement card is a separate request to the bank. Rejecting leaves the card unchanged.','permanent card cancellation','Cancel card permanently'],
  'card.dispute':['Confirming sends this dispute draft and your answer about the card to bank staff, who decide any refund or cancellation. Nothing is refunded now. Rejecting sends nothing.','dispute draft','Send dispute draft'],
  'requests.create':['Confirming records these exact request details for the displayed recipient. It does not fulfil the request, guarantee its deadline or perform a financial transaction. Rejecting submits nothing.','relationship request','Submit reviewed request'],
  'ticket.create':['Confirming records this request with a reference number for the team shown. The assistant does not approve, reject or predict the outcome; that team decides. Rejecting records nothing.','request','Submit request'],
  'rm.share_summary':['Confirming shares exactly this saved snapshot with the displayed recipient. Consent and the saved revision are rechecked. Rejecting shares nothing.','saved agenda snapshot and consent','Confirm sharing'],
  'consent.grant':['Confirming grants only the recipient, purpose, categories and expiry displayed above. This does not share data.','data-sharing scope','Confirm consent grant'],
  'consent.revoke':['Confirming revokes this specific grant. Other grants continue until separately revoked or expired, and earlier sharing is not erased.','data-sharing scope','Confirm revocation'],
  'appointment.confirm':['Confirming books this time only while its hold remains active. Review the date, time and format above. No real meeting or joining link is connected.','appointment','Confirm booking'],
  'appointment.cancel':['Confirming cancels this appointment and releases its time.','appointment','Confirm cancellation']};
const review=type=>REVIEW[type]||['Confirming records the change shown above. Rejecting leaves it unchanged.','action','Confirm'];
// Exports only what this panel already loaded: one row per recorded action and its receipt.
function exportCsv(items) {
  const quote=value=>`"${String(value??'').replaceAll('"','""')}"`;
  const rows=[['command_id','action','group','state','proposed_at','receipt_id','receipt_outcome','receipt_created_at'],...items.map(({command,receipt})=>[command.command_id,commandTitle(command),groupOf(command),command.state,command.proposed_at,receipt?.receipt_id,receipt?.outcome,receipt?.created_at])];
  const link=document.createElement('a');link.href=URL.createObjectURL(new Blob([rows.map(row=>row.map(quote).join(',')).join('\n')],{type:'text/csv'}));
  link.download='demobank-action-receipts.csv';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);
}


// Plain-language activity: one sentence of what happened, one line of detail, and a status only when it is not simply done.
const QUESTION=/^(?:\[[^\]]{1,60}\]\s*)?Client question for you:\s*/;
function activity(command) {
  const t=command.target;
  switch(command.type) {
    case 'document.register':return ['upload',`Uploaded ${t.file_name}`,t.item_label?`For ${t.item_label}. Not sent to the bank yet.`:'Stored privately.'];
    case 'document.submit':return ['send',`Sent ${t.item_label} to the bank`,`${t.file_name} · waiting for the bank's review`];
    case 'compliance.answer_request':return ['send','Answered a question from the bank',`${t.question} · waiting for the bank's review`];
    case 'compliance.update_checklist_item':return ['check',`Marked ${t.item_label} as ${t.client_mark==='prepared'?'ready':'not ready'}`,'Your own checklist mark. It does not send anything to the bank.'];
    case 'handoff.create':{
      const summary=t.summary||'';
      if(t.reason==='card_lost')return ['shield','Lost or stolen card reported to the fraud team','They will contact you, block the card if needed and arrange a replacement.'];
      if(t.reason==='card_verification'||t.reason==='card_dispute')return ['shield','Card case sent to the fraud team','They will check your identity and the charges.'];
      if(QUESTION.test(summary))return ['people','Asked your relationship manager',`“${summary.replace(QUESTION,'').split(' | Earlier in this chat:')[0]}”`];
      if(/withheld/i.test(summary))return ['people','Passed a question to your relationship manager',"Part of an answer couldn't be checked, so your relationship manager will follow up."];
      if(/compliance/i.test(summary))return ['people','Asked for help with your compliance review','Your relationship manager will follow up.'];
      return ['people','Asked to talk to a person','Your relationship manager will follow up.'];
    }
    case 'handoff.claim':return ['people','The bank picked up your case',''];
    case 'handoff.close':return ['people','The bank closed your case',''];
    case 'ticket.create':return ['clip',`Raised a request: ${t.category_label}`,t.summary];
    case 'requests.create':return ['people',`Sent a request to ${displayName(t.recipient_name)}`,t.what];
    case 'card.temporary_block':return ['card',`Freeze card ending ${t.last4}`,'Stops new payments on this card until it is checked.'];
    case 'card.dispute':return ['card',`Dispute ${t.items.length} charges on card ending ${t.last4}`,t.items.map(charge).join('; ')];
    case 'card.unblock':return ['card',command.staff?`The bank reactivated card ending ${t.last4}`:`Unblock card ending ${t.last4}`,''];
    case 'card.cancel_permanently':return ['card',command.staff?`The bank cancelled card ending ${t.last4}`:`Cancel card ending ${t.last4} for good`,command.staff?'':'This cannot be undone.'];
    default:return ['document',plainTitle(command),plainSummary(command)];
  }
}
function chip(command,receipt) {
  if(command.state==='proposed')return ['Needs your OK','wait'];
  if(command.state==='confirmed')return ['In progress','wait'];
  if(command.state==='rejected')return ['You cancelled this','muted'];
  if(command.state==='expired')return ['Expired','muted'];
  if(command.state==='failed'||receipt?.outcome==='failed')return ["Didn't go through",'bad'];
  return null;
}
function meaning(item) {
  const {command,receipt}=item;
  if(!receipt)return '';
  if(receipt.outcome==='failed')return 'Nothing changed, because the record changed before this could run.';
  switch(command.type) {
    case 'document.register':return 'Your file is saved privately. It goes to the bank only after its preliminary check passes (Preliminary Check – Not Formal Compliance Approval).';
    case 'document.submit':return 'The bank has the file. Only the bank can accept it; you will see the outcome in Compliance review and your Inbox.';
    case 'compliance.update_checklist_item':return 'Only your own checklist mark changed.';
    case 'compliance.answer_request':return 'Your answer was sent to the bank. The bank decides whether it is enough.';
    case 'handoff.create':return 'Your relationship manager has the question and will reply. Follow it in My requests.';
    case 'ticket.create':return `Reference ${receipt.result.reference}. ${receipt.result.owner_team} decides; follow it in My requests.`;
    default:return receiptText(item);
  }
}
function dayLabel(value) {
  const day=new Date(value),today=new Date(),key=d=>d.toDateString(),yesterday=new Date(today);yesterday.setDate(today.getDate()-1);
  return key(day)===key(today)?'Today':key(day)===key(yesterday)?'Yesterday':formatDate(value);
}
const FILTERS=[['all','All'],['pending','Needs your OK'],['Compliance','Documents'],['Requests','Requests & help'],['Cards','Cards']];
const inFilter=(item,filter)=>filter==='all'||(filter==='pending'?item.command.state==='proposed':filter==='Requests'?['Requests','Support'].includes(groupOf(item.command)):groupOf(item.command)===filter);

// Reopening shows the last loaded list at once; decisions stay disabled (busy) until the refreshed list arrives.
export default function ActionsPanel({onWorkflowChange}) {
  const [items,setItems]=useState(()=>cached('actions'));
  const [selected,setSelected]=useState(null);
  const [acknowledged,setAcknowledged]=useState(false),[answer,setAnswer]=useState('');
  const [busy,setBusy]=useState(false),[deciding,setDeciding]=useState(false);
  const [error,setError]=useState('');
  const scope=useRef(cacheOwner());
  useEffect(()=>{if(items)remember('actions',items,scope.current);},[items]);
  const active=useRef(null);
  useEffect(()=>{
    const controller=new AbortController();active.current=controller;
    refresh(controller);
    return ()=>active.current?.abort();
  },[]);
  async function token() {
    const {data,error:failure}=await supabase.auth.getSession();
    if(failure)throw failure;
    if(!data.session)throw new Error('Please sign in again to review actions.');
    return data.session.access_token;
  }
  async function refresh(controller) {
    setBusy(true);setError('');setSelected(null);setAcknowledged(false);setAnswer('');
    try {
      const result=await loadActions(await token(),controller.signal);
      if(!controller.signal.aborted)setItems(result);
    } catch(failure) {
      if(failure.name!=='AbortError'&&!controller.signal.aborted){setError(failure.message);setItems(null);}
    } finally {if(!controller.signal.aborted)setBusy(false);}
  }
  async function submit(operation,commandId) {
    const controller=new AbortController();active.current=controller;
    setBusy(true);setDeciding(true);setError('');
    try {
      const result=await operation(commandId,await token(),controller.signal);
      if(!controller.signal.aborted){
        setItems(previous=>previous.map(item=>item.command.command_id===commandId?result:item));setSelected(null);setAcknowledged(false);setAnswer('');
        if(result.workflow)onWorkflowChange(result.workflow);
        const recorded=await loadActions(await token(),controller.signal);
        if(!controller.signal.aborted)setItems(recorded);
      }
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    finally {if(!controller.signal.aborted){setBusy(false);setDeciding(false);}}
  }
  async function retry(turnId) {
    const controller=new AbortController();active.current=controller;setBusy(true);setDeciding(true);setError('');
    try {
      const workflow=await resumeWorkflow(turnId,await token(),controller.signal);
      const recorded=await loadActions(await token(),controller.signal);
      if(!controller.signal.aborted){onWorkflowChange(workflow);setItems(recorded);}
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    finally {if(!controller.signal.aborted){setBusy(false);setDeciding(false);}}
  }
  const [filter,setFilter]=useState('all'),[query,setQuery]=useState('');
  const all=items||[];
  const needle=query.trim().toLowerCase();
  const visible=[...all].sort((a,b)=>(a.command.state==='proposed'?0:1)-(b.command.state==='proposed'?0:1)||Date.parse(b.receipt?.created_at||b.command.proposed_at)-Date.parse(a.receipt?.created_at||a.command.proposed_at))
    .filter(item=>inFilter(item,filter)&&(!needle||[...activity(item.command).slice(1),item.receipt?.receipt_id||'',formatDate(item.command.proposed_at)].join(' ').toLowerCase().includes(needle)));
  const days=[];for(const item of visible){const label=item.command.state==='proposed'?'Needs your OK':dayLabel(item.receipt?.created_at||item.command.proposed_at);const last=days[days.length-1];if(last&&last[0]===label)last[1].push(item);else days.push([label,[item]]);}
  return <section className="app-page-body activity-page" aria-label="Actions" data-testid="actions-page">
    <header className="page-header"><div><span className="eyebrow">Account</span><h1>Actions</h1><p>A record of actions on your account.</p></div>
      <div className="page-header-actions"><button data-testid="reload-actions" className="bank-chat-logout" disabled={busy} onClick={()=>{const controller=new AbortController();active.current=controller;refresh(controller);}}><Icon name="refresh"/>Refresh</button>
      <button className="bank-chat-logout" disabled={!all.length} onClick={()=>exportCsv(all)}><Icon name="download"/>Download (.csv)</button></div></header>
    <nav className="page-tabs" role="tablist" aria-label="Filter actions">{FILTERS.map(([key,label])=>{const count=all.filter(item=>inFilter(item,key)).length;
      return (key==='all'||count>0)&&<button key={key} role="tab" aria-selected={filter===key} onClick={()=>setFilter(key)}>{label}<span className="count">{count}</span></button>;})}</nav>
    {all.length>0&&<label className="act-search activity-search"><Icon name="search"/><input aria-label="Filter actions" placeholder="Search actions" value={query} onChange={event=>setQuery(event.target.value)}/></label>}
    {busy&&<p className="loading" role="status">{deciding?'Saving your decision…':items?'Updating…':'Loading your actions…'}</p>}
    {busy&&!items&&!deciding&&<div className="loading-list" aria-hidden="true">{[0,1,2].map(key=><span key={key} className="skeleton-card large"/>)}</div>}
    {error&&<p role="alert" data-testid="actions-error">{error} Refresh, then try again.</p>}
    {items?.length===0&&<p className="act-empty" data-testid="actions-empty">Nothing yet. Uploads, requests and anything you approve will show up here.</p>}
    {items?.length>0&&visible.length===0&&<p className="act-empty">Nothing matches this view.</p>}
    <div data-testid="commands-list" className="tab-fade" key={filter}>{days.map(([label,group])=><section key={label} className="activity-day"><h2 className={label==='Needs your OK'?'attention':''}>{label}</h2>
     <ul className="activity-list">{group.map(item=>{
      const command=item.command,receipt=item.receipt,chosen=selected===command.command_id,[icon,title,detail]=activity(command),status=chip(command,receipt);
      return <li key={command.command_id} data-command-id={command.command_id} className={`activity-item ${status?status[1]:''}`}>
        <span className={`activity-icon ${groupOf(command).toLowerCase().replace(' ','-')}`} aria-hidden="true"><Icon name={icon}/></span>
        <div className="activity-body">
          <div className="activity-line"><h3>{title}</h3><time dateTime={receipt?.created_at||command.proposed_at}>{formatTime(receipt?.created_at||command.proposed_at)}</time></div>
          {detail&&<p className="activity-detail" data-testid={command.type==='compliance.update_checklist_item'?'command-checklist':command.type.startsWith('consent.')?'command-consent-scope':undefined}>{detail}</p>}
          {status&&<span className={`act-pill ${status[1]}`}>{status[0]}</span>}
          {item.workflow&&(item.workflow.state==='failed'||item.workflow.can_resume)&&<div data-testid="command-workflow" className="act-note">
            <p>Your decision is saved, but the chat reply didn't finish.</p>
            {item.workflow.can_resume&&<button className="bank-chat-logout" data-testid="resume-workflow" disabled={busy||!!error} onClick={()=>retry(item.workflow.turn_id)}>Finish the reply</button>}</div>}
          {command.state==='proposed'&&<>
            {!chosen&&<div className="act-actions"><button className="primary-button" data-testid="review-command" disabled={busy||!!error||item.workflow&&!item.workflow.ready} onClick={()=>{setSelected(command.command_id);setAcknowledged(false);setAnswer('');}}><Icon name="check"/>Review</button>
              <span className="act-meta">Open until {formatTime(command.expires_at)}</span></div>}
            {chosen&&<div className="command-review act-review" data-testid="command-review">
              <p>{review(command.type)[0]}</p>
              {command.type==='card.dispute'&&<fieldset className="dispute-answer" data-testid="command-dispute-answer"><legend>Do you still have the card?</legend>
                {POSSESSION.map(([key,label])=><label key={key}><input type="radio" name={`possession-${command.command_id}`} value={key} checked={answer===key} disabled={busy||!!error} onChange={()=>setAnswer(key)}/> {label}</label>)}</fieldset>}
              <label className="act-ack"><input type="checkbox" data-testid="acknowledge-command" checked={acknowledged} disabled={busy||!!error} onChange={event=>setAcknowledged(event.target.checked)}/> I have checked the details above.</label>
              <div className="act-actions"><button className="primary-button" data-testid="confirm-command" disabled={busy||!!error||!acknowledged||command.type==='card.dispute'&&!answer}
                onClick={()=>submit(command.type==='card.dispute'?(id,key,signal)=>confirmAction(id,key,signal,{card_in_possession:answer}):confirmAction,command.command_id)}><Icon name="check"/>{review(command.type)[2]}</button>
              <button className="bank-chat-logout" data-testid="reject-command" disabled={busy||!!error} onClick={()=>submit(rejectAction,command.command_id)}>Don't do this</button></div>
            </div>}
          </>}
          <details className="act-more"><summary>Details</summary>
            <div className="act-detail">
              {receipt&&<p>{meaning(item)}</p>}
              {command.type==='card.dispute'&&<div data-testid="command-dispute"><ul>{command.target.items.map(entry=><li key={entry.transaction_id}>{charge(entry)}</li>)}</ul>{command.answers&&<p>Do you still have the card? {possessionLabel(command.answers.card_in_possession)}.</p>}</div>}
              {command.type==='ticket.create'&&<div data-testid="command-ticket">{Object.keys(command.target.details).length>0&&<ul>{Object.entries(command.target.details).map(([name,value])=><li key={name}>{name.replaceAll('_',' ')}: {value}</li>)}</ul>}<p>Decided by: {command.target.decided_by}</p></div>}
              {command.type==='document.register'&&<p data-testid="command-document">{command.target.file_name} · {(command.target.size_bytes/1024).toFixed(0)} KB · {command.target.case_title}</p>}
              {['card.unblock','card.cancel_permanently'].includes(command.type)&&!command.staff&&<p data-testid="command-card-change">{plainSummary(command)}</p>}
              {(command.type.startsWith('consent.')||command.type.startsWith('appointment.')||['agenda.save','rm.share_summary','requests.create'].includes(command.type))&&<p>{plainSummary(command)}</p>}
              <p className="act-meta">{formatDateTime(receipt?.created_at||command.proposed_at)} · status <span data-testid="command-state">{command.state}</span></p>
              {receipt&&<p data-testid="command-receipt" className="act-meta">Reference <code title={receipt.receipt_id}>{receipt.receipt_id}</code></p>}
            </div>
          </details>
        </div>
      </li>;
    })}</ul></section>)}</div>
  </section>;
}
