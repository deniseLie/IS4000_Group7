const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export const DOCUMENT_BUCKET='demobank-compliance-private';
export const DOCUMENT_TYPES={'application/pdf':['pdf'],'image/jpeg':['jpg','jpeg'],'image/png':['png'],
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document':['docx']};
function invalid() {return new Error('The private document upload record could not be verified. Check Records and Actions before retrying.');}
function id(value) {if(typeof value!=='string'||!UUID.test(value))throw invalid();return value;}
function label(value) {if(typeof value!=='string'||!value.trim()||Array.from(value).length>2000)throw invalid();return value;}
function timestamp(value) {
  if(typeof value!=='string'||!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value)||!Number.isFinite(Date.parse(value)))throw invalid();
  return value;
}
export function fileDescriptor(file) {
  if(!file||typeof file.name!=='string'||!file.name||Array.from(file.name).length>200||/[/\\\x00-\x1f\x7f]/.test(file.name)||/^ | $/.test(file.name))throw new Error('Choose a filename of 1–200 characters without slashes, control characters or surrounding spaces.');
  if(!Number.isSafeInteger(file.size)||file.size<1||file.size>10485760)throw new Error('Choose a non-empty file of at most 10 MiB.');
  const dot=file.name.lastIndexOf('.');
  if(dot<1||!DOCUMENT_TYPES[file.type]?.includes(file.name.slice(dot+1).toLowerCase()))throw new Error('Choose a PDF, DOCX, JPEG or PNG whose filename extension matches its file type.');
  return {file_name:file.name,mime_type:file.type,size_bytes:file.size};
}
export function parseUploadTarget(value) {
  const descriptor=fileDescriptor({name:value?.file_name,type:value?.mime_type,size:value?.size_bytes});
  return {document_id:id(value?.document_id),case_id:id(value?.case_id),item_id:value?.item_id===null?null:id(value?.item_id),...descriptor};
}
export function uploadTargetMatches(target,result) {
  return Object.entries(target).every(([key,value])=>key==='uploaded_at'?Date.parse(result[key])===Date.parse(value):result[key]===value);
}
export function parsePreparedUpload(value,query,customerId) {
  const target=parseUploadTarget(value);
  if(!uploadTargetMatches(query,target)||value.bucket!==DOCUMENT_BUCKET||value.storage_path!==`${customerId}/${target.case_id}/${target.document_id}`)throw invalid();
  return {...target,bucket:DOCUMENT_BUCKET,storage_path:value.storage_path};
}
export function parseStoredUpload(value,customerId) {
  const target=parseUploadTarget(value),case_title=label(value?.case_title),item_label=value?.item_label===null?null:label(value?.item_label);
  if((target.item_id===null)!==(item_label===null)||value?.storage_path!==`${customerId}/${target.case_id}/${target.document_id}`)throw invalid();
  return {...target,case_title,item_label,storage_path:value.storage_path,object_id:id(value?.object_id),uploaded_at:timestamp(value?.uploaded_at)};
}
export function parseUploadResult(value,customerId) {
  if(value?.status!=='uploaded')throw invalid();
  return {...parseStoredUpload(value,customerId),status:'uploaded'};
}
export function parseDocumentLocation(value,documentId) {
  const document_id=id(value?.document_id),case_id=id(value?.case_id);
  if(document_id!==documentId||value?.bucket!==DOCUMENT_BUCKET||typeof value?.storage_path!=='string'||!value.storage_path.match(/^[1-9][0-9]*\//)||value.storage_path.split('/').slice(1).join('/')!==`${case_id}/${document_id}`)throw invalid();
  return {document_id,case_id,bucket:DOCUMENT_BUCKET,storage_path:value.storage_path,file_name:label(value?.file_name)};
}
