const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function invalid() {return new Error('The recorded relationship request could not be verified. Reload Actions.');}
function id(value) {if(typeof value!=='string'||!UUID.test(value))throw invalid();return value;}
function text(value,max) {if(typeof value!=='string'||!value.trim()||value.length>max)throw invalid();return value;}
function code(value,pattern) {if(value!==null&&(typeof value!=='string'||!pattern.test(value)))throw invalid();return value;}
function day(value) {
  if(typeof value!=='string'||!/^\d{4}-\d\d-\d\d$/.test(value))throw invalid();
  const parsed=new Date(`${value}T00:00:00Z`);if(!Number.isFinite(parsed.getTime())||parsed.toISOString().slice(0,10)!==value)throw invalid();return value;
}
export function parseRequestTarget(value) {
  if(!['low','normal','high'].includes(value?.urgency))throw invalid();
  return {advisor_id:id(value.advisor_id),who:text(value.who,2000),recipient_name:text(value.recipient_name,200),what:text(value.what,1000),
    urgency:value.urgency,deadline:value.deadline===null?null:day(value.deadline),country:code(value.country,/^[A-Z]{2}$/),currency:code(value.currency,/^[A-Z]{3}$/)};
}
export function parseRequestResult(value) {
  const target=parseRequestTarget(value);
  if(value.status!=='sent'||typeof value.created_at!=='string'||!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value.created_at)||!Number.isFinite(Date.parse(value.created_at)))throw invalid();
  return {...target,request_id:id(value.request_id),status:'sent',created_at:value.created_at};
}
export function requestTargetMatches(target,result) {return Object.entries(target).every(([key,value])=>result[key]===value);}
