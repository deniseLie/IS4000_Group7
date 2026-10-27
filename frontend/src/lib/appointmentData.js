import {formatDate,formatDateTime,formatTime} from './format.js';
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function invalid() {return new Error('The recorded appointment could not be verified. Reload its current status before retrying.');}
function id(value) {if(typeof value!=='string'||!UUID.test(value))throw invalid();return value;}
function timestamp(value) {
  if(typeof value!=='string'||!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value)||!Number.isFinite(Date.parse(value)))throw invalid();
  return value;
}
export function parseSlotTarget(value) {
  if(!value||!['video','phone','in_person'].includes(value.format))throw invalid();
  const starts_at=timestamp(value.starts_at),ends_at=timestamp(value.ends_at);
  if(Date.parse(ends_at)-Date.parse(starts_at)!==30*60*1000)throw invalid();
  return {advisor_id:id(value.advisor_id),starts_at,ends_at,format:value.format};
}
export function parseAppointmentTarget(value) {
  const slot=parseSlotTarget(value);
  return {...slot,appointment_id:id(value.appointment_id),hold_expires_at:value.hold_expires_at===null?null:timestamp(value.hold_expires_at)};
}
export function parseAppointmentResult(value) {
  const target=parseAppointmentTarget(value);
  if(!['held','confirmed','cancelled'].includes(value.status)||value.status==='held'&&target.hold_expires_at===null)throw invalid();
  return {...target,status:value.status};
}
export function parseAppointments(value) {
  if(!value||!Array.isArray(value.data)||value.data.length>200)throw invalid();
  const data=value.data.map(item=>{
    const target=parseAppointmentTarget(item);
    if(!['held','confirmed','cancelled','expired'].includes(item.status)||item.status==='held'&&target.hold_expires_at===null)throw invalid();
    return {...target,status:item.status,case_id:item.case_id===null?null:id(item.case_id)};
  });
  if(new Set(data.map(item=>item.appointment_id)).size!==data.length)throw invalid();
  return {call_id:id(value.call_id),as_of:timestamp(value.as_of),data};
}
export function appointmentTime(value) {return formatDateTime(value,{weekday:'short'});}
export function slotDay(value) {return formatDate(value,{weekday:'short',year:undefined});}
export {formatTime as slotTime};
