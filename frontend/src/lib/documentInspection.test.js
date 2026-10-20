import test from 'node:test';
import assert from 'node:assert/strict';
import {parseInspection} from './documentInspection.js';
const id='00000000-0000-4000-8000-000000000001';
function inspection() {return {document_id:id,file_name:'Synthetic DEMO.pdf',mime_type:'application/pdf',size_bytes:604,
 checked_at:'2026-10-20T12:00:00Z',sha256:'a'.repeat(64),status:'text_extracted',issue:null,page_count:1,width:null,height:null,
 extracted_characters:29,scope:'technical_only',ocr:'not_performed',bank_rules:'not_configured'};}

test('technical results stay scoped and reject inconsistent or overstated findings',()=>{
 const value=inspection();assert.equal(parseInspection(value,id).extracted_characters,29);
 for(const changed of [{document_id:'other'},{size_bytes:true},{sha256:'invalid'},{checked_at:'yesterday'},{page_count:21},
  {extracted_characters:0},{extracted_characters:32001},{issue:'ocr_unavailable'},{ocr:'passed'},{bank_rules:'accepted'},{scope:'bank_approval'},
  {status:'image_decoded'},{mime_type:'image/png'},{width:32,height:32}])assert.throws(()=>parseInspection({...value,...changed},id));
 assert.equal(parseInspection({...value,mime_type:'image/png',status:'image_decoded',issue:'ocr_unavailable',page_count:null,width:32,height:32,extracted_characters:0},id).status,'image_decoded');
 assert.throws(()=>parseInspection({...value,mime_type:'image/png',status:'image_decoded',issue:'ocr_unavailable',page_count:null,width:5000,height:5000,extracted_characters:0},id));
});
