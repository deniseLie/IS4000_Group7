// A dispute draft lists owned posted card charges; its receipt repeats them with the customer's answer about the card.
const CARD=/^card_[1-9][0-9]{0,14}$/;
const TXN=/^txn_[1-9][0-9]{0,17}$/;
export const POSSESSION=[['yes','Yes, I still have it'],['no','No, it is lost or stolen'],['unsure','I am not sure']];
function invalid() {return new Error('The recorded dispute draft could not be verified. Reload Actions.');}
function day(value) {
  if(typeof value!=='string'||!/^\d{4}-\d\d-\d\d$/.test(value))throw invalid();
  const parsed=new Date(`${value}T00:00:00Z`);if(!Number.isFinite(parsed.getTime())||parsed.toISOString().slice(0,10)!==value)throw invalid();return value;
}
export function parseDisputeTarget(value) {
  if(typeof value?.card_id!=='string'||!CARD.test(value.card_id)||typeof value.last4!=='string'||!/^\d{4}$/.test(value.last4)
    ||!Array.isArray(value.transaction_ids)||!value.transaction_ids.length||value.transaction_ids.length>10||!Array.isArray(value.items)||value.items.length!==value.transaction_ids.length)throw invalid();
  const transaction_ids=value.transaction_ids.map(item=>{if(typeof item!=='string'||!TXN.test(item))throw invalid();return item;});
  const items=value.items.map(item=>{
    if(!transaction_ids.includes(item?.transaction_id)||typeof item.merchant!=='string'||!item.merchant.trim()||item.merchant.length>200
      ||typeof item.amount!=='number'||!Number.isFinite(item.amount)||item.amount>=0||typeof item.currency!=='string'||!/^[A-Z]{3}$/.test(item.currency))throw invalid();
    return {transaction_id:item.transaction_id,date:day(item.date),merchant:item.merchant,amount:item.amount,currency:item.currency};
  });
  if(new Set(transaction_ids).size!==transaction_ids.length||new Set(items.map(item=>item.transaction_id)).size!==items.length)throw invalid();
  return {card_id:value.card_id,last4:value.last4,transaction_ids,items};
}
export function parseDisputeAnswers(value) {
  if(!POSSESSION.some(([key])=>key===value?.card_in_possession)||Object.keys(value).length!==1)throw invalid();
  return {card_in_possession:value.card_in_possession};
}
export function parseDisputeResult(value) {
  if(value?.status!=='submitted_for_review')throw invalid();
  return {...parseDisputeTarget(value),...parseDisputeAnswers({card_in_possession:value.card_in_possession}),status:'submitted_for_review'};
}
export function disputeTargetMatches(target,result) {
  return target.card_id===result.card_id&&target.last4===result.last4&&JSON.stringify(target.transaction_ids)===JSON.stringify(result.transaction_ids)
    &&JSON.stringify(target.items)===JSON.stringify(result.items);
}
export function possessionLabel(value) {return POSSESSION.find(([key])=>key===value)?.[1]||'';}
