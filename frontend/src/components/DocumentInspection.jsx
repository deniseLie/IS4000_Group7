import {useEffect,useRef,useState} from 'react';
import {supabase} from '../lib/supabase';
import {inspectDocument} from '../lib/documentInspection';

const STATUS={text_extracted:'Text extracted',no_extractable_text:'No extractable text layer',image_decoded:'Image decoded',encrypted:'Encrypted file — not inspected',
  invalid_file:'File could not be opened reliably',limits_exceeded:'File exceeds technical inspection limits',unsupported_content:'This file content is not supported for inspection'};
const ISSUE={no_text_layer:'No text was extracted. A scan or image may need OCR.',ocr_unavailable:'Pixels decoded successfully. OCR and legibility assessment are unavailable.',
  encrypted_file:'Provide an unencrypted demo copy. Do not share a password in chat.',invalid_structure:'The file structure could not be read reliably. Export a new demo copy.',
  format_mismatch:'The bytes do not match the recorded file type. Export a new copy in the selected format.',file_limits:'This check supports up to 20 PDF pages, 32,000 extracted characters, 16 million image pixels and bounded decoding. Large or complex files may exceed its time or memory budget.',
  unsupported_content:'This check cannot inspect the file content reliably. Use a simpler demo copy.'};

export default function DocumentInspection({documentId}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[result,setResult]=useState(null);
  const active=useRef(null);
  useEffect(()=>()=>active.current?.abort(),[]);
  async function inspect() {
    const controller=new AbortController();active.current=controller;setBusy(true);setError('');setResult(null);
    try {
      const {data,error:failure}=await supabase.auth.getSession();
      if(failure)throw failure;
      if(!data.session)throw new Error('Sign in again to inspect your private file.');
      const value=await inspectDocument(documentId,data.session.access_token,controller.signal);
      if(!controller.signal.aborted)setResult(value);
    } catch(failure) {if(!controller.signal.aborted)setError(failure.message);}
    finally {if(!controller.signal.aborted)setBusy(false);}
  }
  return <div data-testid="document-inspection">
    <button className="bank-chat-logout" data-testid="document-inspect" disabled={busy} onClick={inspect}>{busy?'Checking local file readability…':'Run technical file check'}</button>
    {error&&<p role="alert" data-testid="document-inspection-error">{error}</p>}
    {result&&<div data-testid="document-inspection-result" data-inspection-status={result.status}><p><strong data-testid="document-inspection-status">{STATUS[result.status]}</strong></p>
      {result.issue&&<p>{ISSUE[result.issue]}</p>}
      {result.status==='text_extracted'&&<p>{result.extracted_characters.toLocaleString('en-SG')} characters extracted from {result.mime_type==='application/pdf'?'the PDF text layer':'DOCX body paragraphs and tables'}. Text extraction does not prove visual legibility or completeness.</p>}
      {result.page_count!==null&&<p>PDF pages: {result.page_count}.</p>}
      {result.width!==null&&<p>Image size: {result.width} × {result.height} pixels.</p>}
      <p>Technical checks only. OCR was not performed. Bank-specific assessment rules are not configured. This does not approve the document or change its preliminary check, checklist or review status. File contents stay local to this backend; this check makes no AI provider call.</p>
      <p>Checked <time dateTime={result.checked_at}>{new Date(result.checked_at).toLocaleString()}</time>.</p></div>}
  </div>;
}
