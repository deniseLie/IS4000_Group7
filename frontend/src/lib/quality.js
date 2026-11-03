// Team-only Quality page: LangSmith experiments and /chat traces, read through the backend (the LangSmith key stays server-side).
const API=import.meta.env?.VITE_API_URL||'/api';
export const TEAM_ROLES=['rm','support'];

// Shows the page link only; the backend checks the signed token's app_metadata role on every call.
export function isTeam(session) {return TEAM_ROLES.includes(session?.user?.app_metadata?.demobank_role);}

async function get(path,token,signal) {
  const response=await fetch(`${API}/quality${path}`,{headers:{Authorization:`Bearer ${token}`},signal});
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
