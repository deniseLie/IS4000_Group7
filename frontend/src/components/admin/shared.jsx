import {useEffect,useRef,useState} from 'react';
import {supabase} from '../../lib/supabase';

export async function token() {
  const {data,error}=await supabase.auth.getSession();if(error)throw error;
  if(!data.session)throw new Error('Please sign in again.');
  return data.session.access_token;
}

// Loads once per key; refresh() reloads. Errors are shown, never replaced with sample data.
export function useLoad(load,key) {
  const [state,setState]=useState({data:null,error:'',busy:false});
  const active=useRef(null);
  async function refresh() {
    if(key===null)return;
    active.current?.abort();const controller=new AbortController();active.current=controller;
    setState(old=>({...old,busy:true,error:''}));
    try {const data=await load(await token(),controller.signal);if(!controller.signal.aborted)setState({data,error:'',busy:false});}
    catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setState({data:null,error:failure.message,busy:false});}
  }
  useEffect(()=>{refresh();return ()=>active.current?.abort();},[key]);  // eslint-disable-line react-hooks/exhaustive-deps
  return [state,refresh];
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
