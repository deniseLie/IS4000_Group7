import {useState} from 'react';
import {EXPIRED_LINE,expiresIn,offerLabels,required,switches} from '../lib/offers';

// One Confirm and one Cancel for the whole group (build plan phase 6); typing yes or no does the same. Optional steps
// (0114: share until this meeting request is answered) are switches, off unless she turns them on; a typed yes keeps them off.
export default function OfferBlock({offer,state,busy,onDecide}) {
  const [on,setOn]=useState({});
  if(!offer||!state||state==='answered')return null;
  const expired=state==='expired',labels=offerLabels(offer),steps=required(offer),extra=switches(offer);
  return <div className="offer-block" data-testid="offer-block" data-offer-group={offer.offer_group} data-state={state}>
    {steps.length>1&&<ul>{steps.map(o=><li key={o.command_id} data-testid="offer-item">{o.summary}</li>)}</ul>}
    {extra.length>0&&<fieldset className="offer-switches" data-testid="offer-switches" disabled={expired||busy}><legend>Optional, off unless you switch it on</legend>
      {extra.map(o=><label key={o.command_id} className="review-summary"><input type="checkbox" role="switch" data-testid="offer-switch" checked={on[o.command_id]===true}
        onChange={event=>setOn(previous=>({...previous,[o.command_id]:event.target.checked}))}/>{o.summary}</label>)}
      <small>You can switch sharing off at any time on Data sharing.</small></fieldset>}
    <div className="offer-actions">
      <button type="button" className="primary-button" data-testid="offer-confirm" disabled={expired||busy} onClick={()=>onDecide('confirm',on)}>{labels.confirm}</button>
      <button type="button" className="bank-chat-logout" data-testid="offer-cancel" disabled={expired||busy} onClick={()=>onDecide('cancel',{})}>{labels.cancel}</button>
      <small data-testid="offer-expiry" role={expired?'status':undefined}>{expired?EXPIRED_LINE:expiresIn(offer)}</small>
    </div>
  </div>;
}
