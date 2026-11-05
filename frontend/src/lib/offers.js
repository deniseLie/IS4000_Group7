// v5 offers (build plan phase 6): one reply's offers form a group with one Confirm and one Cancel; nothing an agent
// proposes runs until she confirms, by button (sent as `offer` on the next /chat turn) or by typing yes.
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const EXPIRED_LINE='Ask again if you still want this.';
export const OFFER_REPLIES={confirm:'Yes, confirm',cancel:'No, cancel'};

function invalid() {return new Error('The offer in this reply could not be read. Ask again if you still want it.');}
function time(value) {if(typeof value!=='string'||!Number.isFinite(Date.parse(value)))throw invalid();return value;}
function id(value) {if(typeof value!=='string'||!UUID.test(value))throw invalid();return value;}

export function parseOffer(value) {
  if(value===null||value===undefined)return null;
  if(!Array.isArray(value.offers)||!value.offers.length||value.offers.length>10)throw invalid();
  const offers=value.offers.map(o=>{
    if(typeof o?.type!=='string'||typeof o.summary!=='string'||!o.summary.trim())throw invalid();
    if(o.optional!==undefined&&typeof o.optional!=='boolean')throw invalid();
    return {command_id:id(o.command_id),type:o.type,summary:o.summary,expires_at:time(o.expires_at),optional:o.optional===true};
  });
  if(offers[0].optional)throw invalid();  // a switch always follows the step it belongs to
  if(new Set(offers.map(o=>o.command_id)).size!==offers.length)throw invalid();
  return {offer_group:id(value.offer_group),expires_at:time(value.expires_at),offers};
}

// Switches (0114): optional steps, off unless she turns them on; Confirm sends the ones she turned on, a typed yes none.
export const required=offer=>offer.offers.filter(o=>!o.optional);
export const switches=offer=>offer.offers.filter(o=>o.optional);
export function included(offer,on) {return switches(offer).filter(o=>on[o.command_id]===true).map(o=>o.command_id);}
export function offerReply(offer,decision,on={}) {
  const include=decision==='confirm'?included(offer,on):[];
  return {offer_group:offer.offer_group,decision,...(include.length?{include}:{})};
}

// 'open' (buttons work), 'expired' (buttons disabled with EXPIRED_LINE) or 'answered' (a later message replied to it).
export function offerState(messages,index,now=Date.now()) {
  const message=messages[index];
  if(!message?.offer)return null;
  if(messages.slice(index+1).some(m=>m.role==='user'))return 'answered';
  return Date.parse(message.offer.expires_at)<=now?'expired':'open';
}

// Button words: a Let-your-RM-know offer (talking points lane 2, 0108) reads "Let her know" / "Not now"; the reply sent is the same.
export function offerLabels(offer) {
  return offer?.offers?.length&&offer.offers.every(o=>o.type==='insight.share')?{confirm:'Let her know',cancel:'Not now'}:{confirm:'Confirm',cancel:'Cancel'};
}

export function expiresIn(offer,now=Date.now()) {
  const minutes=Math.ceil((Date.parse(offer.expires_at)-now)/60000);
  return minutes<=0?'Expired':minutes===1?'Expires in 1 minute':`Expires in ${minutes} minutes`;
}

// Sections stream in as each agent finishes; until then the plan's placeholders hold their places, in priority order.
export function placeholders(plan) {
  return plan.tasks.map(t=>({task_id:t.task_id,domain:t.domain,priority:t.priority,answer:'',pending:true,sources:[]}));
}
export function fill(sections,section) {
  const at=sections.findIndex(s=>s.task_id===section.task_id);
  return at<0?[...sections,section]:sections.map((s,i)=>i===at?section:s);
}
