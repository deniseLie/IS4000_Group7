import test from 'node:test';
import assert from 'node:assert/strict';
import {fileDescriptor,parsePreparedUpload,parseStoredUpload,parseDocumentLocation} from './documentUploadData.js';
import {parseAction} from './actions.js';
const document_id='00000000-0000-4000-8000-000000000001',case_id='00000000-0000-4000-8000-000000000002';
const target={document_id,case_id,item_id:null,file_name:'Synthetic DEMO.png',mime_type:'image/png',size_bytes:123};
const stored={...target,case_title:'Synthetic review',item_label:null,storage_path:`1002/${case_id}/${document_id}`,
  object_id:'00000000-0000-4000-8000-000000000003',uploaded_at:'2030-01-01T00:00:00Z'};

test('file descriptors reject unsafe, empty, oversized and mismatched uploads',()=>{
  const valid={name:'DEMO 证明.PNG',type:'image/png',size:10485760};
  assert.equal(fileDescriptor(valid).size_bytes,10485760);
  for(const changed of [{size:0},{size:10485761},{size:true},{name:'../x.png'},{name:' x.png'},{name:'x.png '},
    {name:'x\n.png'},{name:'.png'},{name:'x.pdf'},{type:'text/plain'},{name:'x'.repeat(200)+'.png'}])assert.throws(()=>fileDescriptor({...valid,...changed}));
});

test('prepared and stored paths must identify the exact customer, case and file',()=>{
  const prepared={...target,bucket:'demobank-compliance-private',storage_path:stored.storage_path};
  assert.equal(parsePreparedUpload(prepared,target,1002).document_id,document_id);
  assert.equal(parseStoredUpload(stored,1002).file_name,target.file_name);
  for(const changed of [{storage_path:`1001/${case_id}/${document_id}`},{document_id:case_id},{item_label:'Unlinked item'},{uploaded_at:'invalid'}])assert.throws(()=>parseStoredUpload({...stored,...changed},1002));
  for(const changed of [{size_bytes:124},{bucket:'public'},{storage_path:`1001/${case_id}/${document_id}`}])assert.throws(()=>parsePreparedUpload({...prepared,...changed},target,1002));
  const location={document_id,case_id,file_name:target.file_name,bucket:prepared.bucket,storage_path:stored.storage_path};
  assert.equal(parseDocumentLocation(location,document_id).storage_path,stored.storage_path);
  assert.throws(()=>parseDocumentLocation({...location,storage_path:`1002/${case_id}/${document_id}/extra`},document_id));
});

test('a document receipt cannot claim a different object, scope or descriptor',()=>{
  const command_id='00000000-0000-4000-8000-000000000004';
  const command={command_id,customer_id:1002,case_id,type:'document.register',target:stored,state:'succeeded',
    idempotency_key:`${case_id}:upload:document.register`,proposed_at:'2030-01-01T00:00:00Z',expires_at:'2030-01-01T00:05:00Z',confirmed_at:null};
  const receipt={receipt_id:`rcpt_${command_id.replaceAll('-','')}`,command_id,customer_id:1002,outcome:'succeeded',
    result:{...stored,status:'uploaded'},audit_event_id:12,created_at:'2030-01-01T00:00:00Z'};
  assert.equal(parseAction({command,receipt}).receipt.result.status,'uploaded');
  for(const changed of [{size_bytes:124},{object_id:document_id},{case_title:'Other case'},{uploaded_at:'2030-01-02T00:00:00Z'},
    {status:'accepted'},{storage_path:`1001/${case_id}/${document_id}`}])assert.throws(()=>parseAction({command,receipt:{...receipt,result:{...receipt.result,...changed}}}));
  assert.throws(()=>parseAction({command:{...command,customer_id:1001},receipt}));
});
