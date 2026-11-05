import {useEffect,useRef,useState} from 'react';
import {supabase} from '../../lib/supabase';

export async function token() {
  const {data,error}=await supabase.auth.getSession();if(error)throw error;
  if(!data.session)throw new Error('Please sign in again.');
  return data.session.access_token;
}

// Responses kept in memory for this tab, by page (the hash) and key: coming back to a page shows them at once.
// Younger than FRESH_MS: no reload; older: shown while a background reload runs. Refresh always reloads.
const saved=new Map(),FRESH_MS=60_000,KEEP=60;
const cacheKey=key=>`${typeof location==='undefined'?'':location.hash}|${key}`;
export function clearCache() {saved.clear();}

// Loads once per key; refresh() reloads. Errors are shown, never replaced with sample data.
export function useLoad(load,key) {
  const hit=key===null?null:saved.get(cacheKey(key));
  const [state,setState]=useState({data:hit?.data??null,error:'',busy:false,at:hit?.at??null});
  const active=useRef(null);
  async function refresh() {
    if(key===null)return;
    const name=cacheKey(key);
    active.current?.abort();const controller=new AbortController();active.current=controller;
    setState(old=>({...old,busy:true,error:''}));
    try {const data=await load(await token(),controller.signal);
      if(controller.signal.aborted)return;
      const at=Date.now();saved.delete(name);saved.set(name,{data,at});if(saved.size>KEEP)saved.delete(saved.keys().next().value);
      setState({data,error:'',busy:false,at});}
    catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setState(old=>({...old,error:failure.message,busy:false}));}
  }
  useEffect(()=>{
    const found=key===null?null:saved.get(cacheKey(key));
    if(found)setState({data:found.data,error:'',busy:false,at:found.at});
    if(!found||Date.now()-found.at>FRESH_MS)refresh();
    return ()=>active.current?.abort();
  },[key]);  // eslint-disable-line react-hooks/exhaustive-deps
  return [state,refresh];
}

// The first `step` items, and a button that shows `step` more; back to `step` when `reset` changes (new data or filter).
export function useMore(items,reset,step=20) {
  const [count,setCount]=useState(step);
  useEffect(()=>setCount(step),[reset,step]);
  const list=items||[];
  return [list.slice(0,count),list.length>count?()=>setCount(count+step):null,list.length];
}
export function More({onMore,shown,total}) {
  return onMore?<p className="admin-more"><button className="bank-chat-logout" onClick={onMore}>Load more</button>
    <span className="admin-muted"> Showing {shown} of {total}</span></p>:null;
}

export function Status({value}) {return <span className={`admin-status admin-${String(value).replace(/[^a-z_]/g,'')}`}>{value}</span>;}

export function Loading({label='Loading…'}) {
  return <div className="loading-list" role="status" aria-label={label}>{[0,1,2].map(k=><span key={k} className="skeleton-card"/>)}</div>;
}

export function Failed({error}) {return error?<p className="form-error" role="alert">{error}</p>:null;}

// A page with the window and source pickers in its header.
export function Page({id,eyebrow='AI engineering',title,intro,actions,children}) {
  return <div className="app-page-body admin-page" data-testid={`admin-${id}`}>
    <header className="page-header"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1>{intro&&<p>{intro}</p>}</div>
      {actions&&<div className="page-header-actions">{actions}</div>}</header>
    {children}
  </div>;
}
