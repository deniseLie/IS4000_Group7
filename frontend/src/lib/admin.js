// Admin dashboard (AI engineering view): LangSmith evaluations and traces, read through the backend (the LangSmith key
// stays server-side). Only the page link depends on this check; the backend verifies the signed token on every call.
const API=import.meta.env?.VITE_API_URL||'/api';
export const ADMIN_ROLE='admin';

export function isAdmin(session) {return session?.user?.app_metadata?.demobank_role===ADMIN_ROLE;}

async function get(path,token,signal) {
  const response=await fetch(`${API}/admin${path}`,{headers:{Authorization:`Bearer ${token}`},signal});
  const body=await response.json().catch(()=>null);
  if(!response.ok)throw new Error(body?.error?.message||'LangSmith cannot be read right now. Please retry.');
  return body;
}
export const loadExperiments=(token,signal)=>get('/experiments',token,signal);
export const loadExperiment=(name,token,signal)=>get(`/experiments/${encodeURIComponent(name)}`,token,signal);
export const loadTraces=(token,signal)=>get('/traces',token,signal);
export const loadTrace=(runId,token,signal)=>get(`/traces/${encodeURIComponent(runId)}`,token,signal);

export function summary(cases) {
  const counts={pass:0,fail:0,skipped:0,error:0};
  for(const item of cases)counts[item.status]+=1;
  return counts;
}
export function scoreLabel(score) {return score===null||score===undefined?'n/a':score===1?'pass':score===0?'fail':String(score);}
export function seconds(value) {return value===null||value===undefined?'—':`${value.toFixed(1)} s`;}
