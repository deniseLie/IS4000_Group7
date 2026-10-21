const API_URL=import.meta.env?.VITE_API_URL || '/api';
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function invalid() {return new Error('The recorded inbox response could not be verified. Reload notifications before retrying.');}
function timestamp(value) {
  if(typeof value!=='string'||!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value)||!Number.isFinite(Date.parse(value)))throw invalid();
  return value;
}
export function parseNotification(value) {
  if(!value||typeof value.notification_id!=='string'||!UUID.test(value.notification_id)||!Number.isSafeInteger(value.customer_id)||value.customer_id<=0||
    !(value.event_id===null||Number.isSafeInteger(value.event_id)&&value.event_id>0)||typeof value.template_key!=='string'||!value.template_key||
    !Number.isSafeInteger(value.template_version)||value.template_version<=0||typeof value.locale!=='string'||!value.locale||
    !['P0','P1','P2','P3'].includes(value.priority)||typeof value.is_service!=='boolean'||
    !['title','body'].every(key=>typeof value[key]==='string'&&value[key].trim()&&value[key].length<=1500))throw invalid();
  return {notification_id:value.notification_id,customer_id:value.customer_id,event_id:value.event_id,template_key:value.template_key,
    template_version:value.template_version,locale:value.locale,priority:value.priority,title:value.title,body:value.body,is_service:value.is_service,
    read_at:value.read_at===null?null:timestamp(value.read_at),created_at:timestamp(value.created_at)};
}
export function parseInbox(value) {
  if(!value||!Number.isSafeInteger(value.customer_id)||value.customer_id<=0||!Array.isArray(value.items)||value.items.length>50)throw invalid();
  const items=value.items.map(parseNotification);
  if(items.some(item=>item.customer_id!==value.customer_id)||new Set(items.map(item=>item.notification_id)).size!==items.length)throw invalid();
  return {customer_id:value.customer_id,items};
}
async function responseBody(response) {
  let body;
  try {body=await response.json();}
  catch(error) {if(error instanceof SyntaxError)throw invalid();throw error;}
  if(!response.ok)throw new Error(typeof body?.error?.message==='string'?body.error.message:'Notifications are unavailable. Reload the recorded inbox to retry.');
  return body;
}
export async function loadInbox(token,signal) {
  return parseInbox(await responseBody(await fetch(`${API_URL}/notifications`,{headers:{Authorization:`Bearer ${token}`},signal})));
}
export async function markNotificationRead(notificationId,customerId,token,signal) {
  if(typeof notificationId!=='string'||!UUID.test(notificationId))throw invalid();
  const result=parseNotification(await responseBody(await fetch(`${API_URL}/notifications/${notificationId}/read`,{method:'POST',
    headers:{Authorization:`Bearer ${token}`},signal})));
  if(result.notification_id!==notificationId||result.customer_id!==customerId||result.read_at===null)throw invalid();
  return result;
}
