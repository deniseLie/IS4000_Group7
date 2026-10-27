import {createActionCase,proposeAction} from './actions.js';

// The customer's own card changes (migration 0068): unblock a temporarily blocked card; permanently cancel an active or
// blocked one. Cancelled is terminal. Proposing changes nothing; the customer confirms in Actions.
export function cardChanges(status) {
  return [status==='temporarily_blocked'&&'card.unblock',['active','temporarily_blocked'].includes(status)&&'card.cancel_permanently'].filter(Boolean);
}
export async function proposeCardChange(cardId,type,token,signal) {
  const scope=await createActionCase({session_id:crypto.randomUUID(),case_id:crypto.randomUUID()},token,signal);
  return proposeAction({command_id:crypto.randomUUID(),case_id:scope.case_id,task_id:'manage_card',type,target:{card_id:cardId}},token,signal);
}
