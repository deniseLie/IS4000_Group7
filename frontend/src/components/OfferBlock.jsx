import {EXPIRED_LINE,expiresIn} from '../lib/offers';

// One Confirm and one Cancel for the whole group (build plan phase 6); typing yes or no does the same.
export default function OfferBlock({offer,state,busy,onDecide}) {
  if(!offer||!state||state==='answered')return null;
  const expired=state==='expired';
  return <div className="offer-block" data-testid="offer-block" data-offer-group={offer.offer_group} data-state={state}>
    {offer.offers.length>1&&<ul>{offer.offers.map(o=><li key={o.command_id} data-testid="offer-item">{o.summary}</li>)}</ul>}
    <div className="offer-actions">
      <button type="button" className="primary-button" data-testid="offer-confirm" disabled={expired||busy} onClick={()=>onDecide('confirm')}>Confirm</button>
      <button type="button" className="bank-chat-logout" data-testid="offer-cancel" disabled={expired||busy} onClick={()=>onDecide('cancel')}>Cancel</button>
      <small data-testid="offer-expiry" role={expired?'status':undefined}>{expired?EXPIRED_LINE:expiresIn(offer)}</small>
    </div>
  </div>;
}
