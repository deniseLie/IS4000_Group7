const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const API_URL=import.meta.env?.VITE_API_URL || '/api';

function timestamp(value) {
  if(typeof value!=='string'||!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value)||!Number.isFinite(Date.parse(value)))throw new Error('The data-sharing response has an invalid timestamp. Please retry.');
  return value;
}

export function parseSharingStatus(value) {
  if(!value||value.purpose!=='rm_brief'||!Array.isArray(value.categories)||value.categories.length!==1||value.categories[0]!=='profile')throw new Error('The data-sharing response could not be verified. Please retry.');
  if(value.recipient===null&&value.consent===null)return {purpose:'rm_brief',categories:['profile'],recipient:null,consent:null};
  const recipient=value.recipient,consent=value.consent;
  if(!recipient||typeof recipient.rm_id!=='string'||!UUID.test(recipient.rm_id)||typeof recipient.name!=='string'||!recipient.name.trim()||recipient.name.length>200||!consent||typeof consent.granted!=='boolean')throw new Error('The data-sharing response could not be verified. Please retry.');
  const checked_at=timestamp(consent.checked_at);
  const expires_at=consent.expires_at===null?null:timestamp(consent.expires_at);
  if(consent.granted?typeof consent.grant_id!=='string'||!UUID.test(consent.grant_id):consent.grant_id!==null||expires_at!==null)throw new Error('The data-sharing grant could not be verified. Please retry.');
  if(expires_at!==null&&Date.parse(expires_at)<=Date.parse(checked_at))throw new Error('The data-sharing grant has an invalid expiry. Please retry.');
  return {purpose:'rm_brief',categories:['profile'],recipient:{rm_id:recipient.rm_id,name:recipient.name},
    consent:{granted:consent.granted,grant_id:consent.grant_id,expires_at,checked_at}};
}

export async function loadSharingStatus(token,signal) {
  const response=await fetch(`${API_URL}/consent/rm`,{headers:{Authorization:`Bearer ${token}`},signal});
  let body;
  try {body=await response.json();}
  catch(error) {
    if(error instanceof SyntaxError)throw new Error('The data-sharing response could not be verified. Please retry.',{cause:error});
    throw error;
  }
  if(!response.ok)throw new Error(typeof body?.error?.message==='string'&&body.error.message.trim()?body.error.message:'Data-sharing status could not be loaded. Please retry.');
  return parseSharingStatus(body);
}
