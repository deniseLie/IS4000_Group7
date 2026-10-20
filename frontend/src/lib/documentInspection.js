import {parseRecord} from './customerData.js';
import {DOCUMENT_TYPES} from './documentUploadData.js';
const API_URL=import.meta.env?.VITE_API_URL||'/api';
const STATUSES=['text_extracted','no_extractable_text','image_decoded','encrypted','invalid_file','limits_exceeded','unsupported_content'];
const ISSUES=['no_text_layer','ocr_unavailable','encrypted_file','invalid_structure','format_mismatch','file_limits','unsupported_content'];
function invalid() {return new Error('The technical file result could not be verified. Reload Records before retrying.');}
function positive(value,limit) {if(!Number.isSafeInteger(value)||value<1||value>limit)throw invalid();return value;}
export function parseInspection(value,documentId) {
  if(value?.document_id!==documentId||typeof value?.file_name!=='string'||!value.file_name.trim()||Array.from(value.file_name).length>2000||!Object.hasOwn(DOCUMENT_TYPES,value?.mime_type)||
    !STATUSES.includes(value?.status)||value.issue!==null&&!ISSUES.includes(value.issue)||value.scope!=='technical_only'||value.ocr!=='not_performed'||value.bank_rules!=='not_configured'||
    typeof value.sha256!=='string'||!/^[0-9a-f]{64}$/.test(value.sha256)||typeof value.checked_at!=='string'||!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value.checked_at)||!Number.isFinite(Date.parse(value.checked_at)))throw invalid();
  const size_bytes=positive(value.size_bytes,10485760),page_count=value.page_count===null?null:positive(value.page_count,20);
  const width=value.width===null?null:positive(value.width,16000000),height=value.height===null?null:positive(value.height,16000000);
  if((width===null)!==(height===null)||width!==null&&width*height>16000000||!Number.isSafeInteger(value.extracted_characters)||value.extracted_characters<0||value.extracted_characters>32000)throw invalid();
  if(value.status==='text_extracted'&&(value.issue!==null||value.extracted_characters===0)||value.status!=='text_extracted'&&value.extracted_characters!==0||
    value.status==='image_decoded'&&(width===null||value.issue!=='ocr_unavailable'||page_count!==null))throw invalid();
  const expected={text_extracted:[null],no_extractable_text:['no_text_layer'],image_decoded:['ocr_unavailable'],encrypted:['encrypted_file'],
    invalid_file:['invalid_structure','format_mismatch'],limits_exceeded:['file_limits'],unsupported_content:['unsupported_content']};
  if(!expected[value.status].includes(value.issue)||width!==null&&value.status!=='image_decoded')throw invalid();
  if(['image/png','image/jpeg'].includes(value.mime_type)&&(page_count!==null||['text_extracted','no_extractable_text'].includes(value.status)))throw invalid();
  if(!['image/png','image/jpeg'].includes(value.mime_type)&&(width!==null||value.status==='image_decoded'))throw invalid();
  if(value.mime_type==='application/pdf'&&['text_extracted','no_extractable_text'].includes(value.status)&&page_count===null||value.mime_type!=='application/pdf'&&page_count!==null)throw invalid();
  return {document_id:documentId,file_name:value.file_name,mime_type:value.mime_type,size_bytes,checked_at:value.checked_at,sha256:value.sha256,
    status:value.status,issue:value.issue,page_count,width,height,extracted_characters:value.extracted_characters,scope:value.scope,ocr:value.ocr,bank_rules:value.bank_rules};
}
export async function inspectDocument(documentId,token,signal) {
  const response=await fetch(`${API_URL}/documents/${documentId}/inspection`,{headers:{Authorization:`Bearer ${token}`},signal});
  const value=await response.json();
  if(!response.ok)throw new Error(typeof value?.error?.message==='string'?value.error.message:`Technical file request failed (HTTP ${response.status}). Reload Records.`);
  return parseRecord(value,data=>parseInspection(data,documentId)).data;
}
