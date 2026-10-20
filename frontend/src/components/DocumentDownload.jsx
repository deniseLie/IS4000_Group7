import {useEffect,useRef,useState} from 'react';
import {supabase} from '../lib/supabase';
import {documentDownload} from '../lib/documentStorage';

export default function DocumentDownload({documentId}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const active=useRef(null);
  useEffect(()=>()=>active.current?.abort(),[]);
  async function download() {
    const controller=new AbortController();active.current=controller;setBusy(true);setError('');
    try {
      const {data,error:failure}=await supabase.auth.getSession();
      if(failure)throw failure;
      if(!data.session)throw new Error('Sign in again to download a private file.');
      const url=await documentDownload(documentId,data.session.access_token,controller.signal);
      controller.signal.throwIfAborted();window.location.assign(url);
    } catch(failure) {if(!controller.signal.aborted)setError(failure.message);}
    finally {if(!controller.signal.aborted)setBusy(false);}
  }
  return <div><button className="bank-chat-logout" data-testid="document-download" disabled={busy} onClick={download}>{busy?'Preparing private download…':'Download private file'}</button>
    {error&&<p role="alert" data-testid="document-download-error">{error}</p>}</div>;
}
