const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const AGENDA_PROFILE_FIELDS={name:'Name',preferred_language:'Preferred language',employment_status:'Employment status'};
function invalid() {return new Error('The saved agenda could not be verified. Reload saved drafts before retrying.');}
function id(value) {if(typeof value!=='string'||!UUID.test(value))throw invalid();return value;}
function text(value,limit) {if(typeof value!=='string'||!value.trim()||Array.from(value).length>limit)throw invalid();return value;}
function timestamp(value) {
  if(typeof value!=='string'||!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value)||!Number.isFinite(Date.parse(value)))throw invalid();
  return value;
}
export function parseAgendaTarget(value) {
  if(!value||!Number.isSafeInteger(value.expected_revision)||value.expected_revision<0||value.expected_revision>2147483646||
    !Array.isArray(value.topics)||!value.topics.length||value.topics.length>12||!Array.isArray(value.profile_fields)||value.profile_fields.length>3||
    value.profile_fields.some(field=>!Object.hasOwn(AGENDA_PROFILE_FIELDS,field))||new Set(value.profile_fields).size!==value.profile_fields.length)throw invalid();
  const profile_fields=[...value.profile_fields].sort(),profile_snapshot={};
  for(const field of Object.keys(AGENDA_PROFILE_FIELDS)) {
    const selected=value.profile_snapshot?.[field];
    if(profile_fields.includes(field)) {
      profile_snapshot[field]=text(selected,2000);
      if(field==='employment_status'&&!['employed','self_employed','student','retired','unemployed'].includes(selected))throw invalid();
    } else {if(selected!==null)throw invalid();profile_snapshot[field]=null;}
  }
  const categories=profile_fields.length?['agenda','profile']:['agenda'];
  if(JSON.stringify(value.categories)!==JSON.stringify(categories))throw invalid();
  return {agenda_id:id(value.agenda_id),advisor_id:id(value.advisor_id),expected_revision:value.expected_revision,
    topics:value.topics.map(topic=>text(topic,300)),profile_fields,profile_snapshot,recipient_name:text(value.recipient_name,200),
    content:text(value.content,6000),categories};
}
export function parseAgendaResult(value) {
  const target=parseAgendaTarget(value);
  if(value.status!=='draft'||value.revision!==target.expected_revision+1)throw invalid();
  return {...target,revision:value.revision,status:'draft',updated_at:timestamp(value.updated_at)};
}
export function agendaTargetMatches(target,result) {
  return Object.entries(target).every(([key,value])=>JSON.stringify(value)===JSON.stringify(result[key]));
}
export function parseAgendas(value) {
  const data=value?.data;
  if(!data||!Array.isArray(data.agendas)||!Number.isSafeInteger(data.agendas_total)||data.agendas_total<0||data.agendas.length!==Math.min(data.agendas_total,200))throw invalid();
  const agendas=data.agendas.map(parseAgendaResult);
  if(new Set(agendas.map(item=>item.agenda_id)).size!==agendas.length)throw invalid();
  return {call_id:id(value.call_id),as_of:timestamp(value.as_of),agendas,agendas_total:data.agendas_total};
}
