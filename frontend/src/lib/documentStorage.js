import {createClient} from '@supabase/supabase-js';
import {parseRecord} from './customerData.js';
import {fileDescriptor,parsePreparedUpload,parseDocumentLocation} from './documentUploadData.js';
const API_URL=import.meta.env?.VITE_API_URL||'/api';

async function body(response) {
  const value=await response.json();
  if(!response.ok)throw new Error(typeof value?.error?.message==='string'?value.error.message:`Private document request failed (HTTP ${response.status}). Check Records before retrying.`);
  return value;
}
async function storageFetch(input,init) {
  for(let attempt=1;attempt<=3;attempt++) {
    const timeout=AbortSignal.timeout(60000);
    try {
      const response=await fetch(input,{...init,signal:init?.signal?AbortSignal.any([init.signal,timeout]):timeout});
      if(![408,429,500,502,503,504].includes(response.status)||attempt===3)return response;
      await response.body?.cancel();
      console.warn('Private Storage request will retry',{attempt,status:response.status});
    } catch(error) {
      if(init?.signal?.aborted||attempt===3||!(error instanceof TypeError||error.name==='TimeoutError'))throw error;
      console.warn('Private Storage request will retry',{attempt,error:error.name});
    }
    await new Promise(resolve=>setTimeout(resolve,attempt*300));
  }
  throw new Error('Private Storage retry loop ended without a response.');
}
function storage(token) {
  if(!token||!import.meta.env.VITE_SUPABASE_URL||!import.meta.env.VITE_SUPABASE_ANON_KEY)throw new Error('Private Storage requires a signed-in Supabase session and configured public app settings.');
  return createClient(import.meta.env.VITE_SUPABASE_URL,import.meta.env.VITE_SUPABASE_ANON_KEY,
    {accessToken:async()=>token,global:{fetch:storageFetch}}).storage;
}
export async function prepareUpload(query,customerId,token,signal) {
  return parseRecord(await body(await fetch(`${API_URL}/documents/uploads/prepare`,{method:'POST',
    headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify(query),signal})),
  value=>parsePreparedUpload(value,query,customerId)).data;
}
export async function uploadFile(prepared,file,token) {
  const descriptor=fileDescriptor(file);
  if(Object.entries(descriptor).some(([key,value])=>prepared[key]!==value))throw new Error('The selected file changed after preparation. Reopen Records before uploading.');
  const {data,error}=await storage(token).from(prepared.bucket).upload(prepared.storage_path,file,{contentType:prepared.mime_type,upsert:false});
  if(error)throw new Error(`Private file upload failed (${error.statusCode||error.name}): ${error.message}. If Storage received it, use Record uploaded file to check registration.`);
  if(data?.path!==prepared.storage_path||typeof data.id!=='string')throw new Error('Storage did not return the expected private file path. Use Record uploaded file to check registration.');
  return data.id;
}
export async function documentDownload(documentId,token,signal) {
  const location=parseRecord(await body(await fetch(`${API_URL}/documents/${documentId}/location`,{headers:{Authorization:`Bearer ${token}`},signal})),
    value=>parseDocumentLocation(value,documentId)).data;
  signal.throwIfAborted();
  const {data,error}=await storage(token).from(location.bucket).createSignedUrl(location.storage_path,60,{download:location.file_name});
  if(error)throw new Error(`Private download link failed (${error.statusCode||error.name}): ${error.message}`);
  signal.throwIfAborted();
  const url=new URL(data.signedUrl),base=new URL(import.meta.env.VITE_SUPABASE_URL);
  if(url.origin!==base.origin||url.pathname!==`/storage/v1/object/sign/${location.bucket}/${location.storage_path}`||!url.searchParams.get('token')||url.searchParams.get('download')!==location.file_name)throw new Error('Storage returned an unexpected download destination. Reload Records before retrying.');
  return url.href;
}
