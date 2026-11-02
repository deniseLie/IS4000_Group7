// Shared ticket intake: the Assistant and My Requests use the same validated
// backend proposal and confirmation APIs. A proposal is NOT a submitted ticket.
import {createActionCase,proposeAction} from './actions.js';

export function cleanTicketDetails(category,values={}) {
  if(!category?.fields)throw new Error('Select a supported request category.');
  const result={};
  for(const field of category.fields) {
    const raw=values[field.key];
    const value=typeof raw==='string'?raw.trim():'';
    if(value)result[field.key]=value;
  }
  return result;
}

export function missingTicketFields(category,values={}) {
  if(!category?.fields)return [];
  return category.fields.filter(field => (field.required || (category.category==='card_limit_change' && field.key==='until' && values.duration==='temporary'))
    && !String(values[field.key]||'').trim()).map(field=>field.label);
}

export async function prepareTicketAction({category,summary,urgency='normal',values={}},access,signal) {
  if(!category?.customer_raisable)throw new Error('This category cannot be submitted by a client.');
  if(missingTicketFields(category,values).length)throw new Error('Please complete the required request details.');
  const text=String(summary||'').trim();
  if(!text||text.length>500)throw new Error('Add a short summary (up to 500 characters).');
  const details=cleanTicketDetails(category,values);
  const scope=await createActionCase({session_id:crypto.randomUUID(),case_id:crypto.randomUUID()},access,signal);
  return proposeAction({command_id:crypto.randomUUID(),case_id:scope.case_id,task_id:'ticket',type:'ticket.create',
    target:{category:category.category,summary:text,urgency,details}},access,signal);
}

// Rebuild the editable summary when chat-extracted fields are corrected in the
// review form. Never submit stale values from an earlier draft amount/date.
export function cardLimitDraftSummary(values={},selectedCardLabel='') {
  const pieces=[];
  if(values.new_limit) {
    const n=Number(values.new_limit);
    pieces.push(`Requested new limit: ${Number.isFinite(n)?`SGD ${n.toLocaleString('en-SG')}`:values.new_limit}`);
  }
  if(values.duration)pieces.push(`${values.duration==='temporary'?'Temporary':'Permanent'} request`);
  if(selectedCardLabel)pieces.push(selectedCardLabel);
  else if(values.card)pieces.push(`Card ending ${values.card}`);
  if(values.until)pieces.push(`End date: ${values.until}`);
  if(values.reason)pieces.push(`Reason: ${values.reason}`);
  return pieces.length?`${pieces.join('; ')}.`:'';
}
