import {useState} from 'react';
import {supabase} from '../lib/supabase';
import {createActionCase,proposeAction} from '../lib/actions';
import {formatDate,formatDateTime} from '../lib/format';

// A question the bank asked on her review (CR6, 0080). Open: she answers in the box; the answer goes to the bank with a
// receipt in Actions. Answered: "Sent to the bank on … – waiting for review". Closed: the bank's outcome.
const LIMIT=2000;
export default function BankRequest({review,request,disabled,onAnswered,onAsk,help}) {
  const [answer,setAnswer]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const open=request.status==='open'&&!['completed','closed'].includes(review.status);
  async function send(event) {
    event.preventDefault();setBusy(true);setError('');
    try {
      const {data,error:failure}=await supabase.auth.getSession();
      if(failure)throw failure;
      if(!data.session)throw new Error('Please sign in again to send your answer.');
      const token=data.session.access_token;
      const scope=await createActionCase({session_id:crypto.randomUUID(),case_id:crypto.randomUUID()},token);
      const result=await proposeAction({command_id:crypto.randomUUID(),case_id:scope.case_id,task_id:crypto.randomUUID(),
        type:'compliance.answer_request',target:{case_id:review.case_id,request_id:request.request_id,answer:answer.trim()}},token);
      if(result.receipt?.outcome!=='succeeded')throw new Error('This question changed before your answer was saved. Reload the page to see it.');
      setAnswer('');onAnswered?.();
    } catch(failure) {setError(failure.message);}
    finally {setBusy(false);}
  }
  if(request.status==='closed')return <article className="cw-alert done" data-testid="bank-request" data-status="closed">
    <span className="cw-alert-icon ok" aria-hidden="true">✓</span>
    <div className="cw-alert-body"><div className="cw-alert-head"><span className="eyebrow">You answered the bank</span>
      <span className="cw-pill ok">{request.outcome==='more_info'?'The bank asked a follow-up question':'Accepted by bank'}</span></div>
      <p><strong>{request.question}</strong></p>{request.response&&<p className="cw-answer">{request.response}</p>}</div></article>;
  if(request.status==='responded')return <article className="cw-alert waiting" data-testid="bank-request" data-status="responded">
    <span className="cw-alert-icon wait" aria-hidden="true">…</span>
    <div className="cw-alert-body"><div className="cw-alert-head"><span className="eyebrow">You answered the bank</span>
      <span className="cw-pill wait">Sent to the bank on {formatDate(request.responded_at)} – waiting for review</span></div>
      <p><strong>{request.question}</strong></p>{request.response&&<p className="cw-answer">{request.response}</p>}</div></article>;
  return <article className="cw-alert" data-testid="bank-request" data-status="open">
    <span className="cw-alert-icon" aria-hidden="true">!</span>
    <div className="cw-alert-body"><div className="cw-alert-head"><span className="eyebrow">The bank asked you</span>
      <span className="cw-deadline">Reply by {request.due_date?formatDate(request.due_date):'no date recorded'}</span></div>
      <p><strong>{request.question}</strong></p>{request.instructions&&<p>{request.instructions}</p>}
      {open&&<form className="cw-reply" onSubmit={send}>
        <label htmlFor={`answer-${request.request_id}`}>Your answer</label>
        <textarea id={`answer-${request.request_id}`} data-testid="bank-request-answer" rows={4} maxLength={LIMIT} value={answer} disabled={busy||disabled}
          placeholder="For example: where the money came from, the date and who sent it." onChange={event=>setAnswer(event.target.value)}/>
        <div className="cw-reply-foot"><small>Don't include passwords, PINs or full card numbers. {answer.length}/{LIMIT}</small>
          <button className="primary-button" data-testid="bank-request-send" disabled={busy||disabled||!answer.trim()}>{busy?'Sending…':'Send to the bank'}</button></div>
        {error&&<p role="alert">{error}</p>}
      </form>}
      <div className="panel-actions">{onAsk&&<button className="bank-chat-logout" disabled={busy||disabled} onClick={onAsk}>Explain in chat</button>}{help}</div>
      <small className="cw-sent-at">Asked {formatDateTime(request.created_at)}</small>
    </div></article>;
}
