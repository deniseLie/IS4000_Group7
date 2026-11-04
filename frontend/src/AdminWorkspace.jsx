import {useEffect,useState} from 'react';
import Sidebar,{TabBar} from './components/Sidebar';
import EvaluationsPage from './components/admin/EvaluationsPage';
import TracesPage from './components/admin/TracesPage';
import {supabase} from './lib/supabase';

const API_URL=import.meta.env.VITE_API_URL||'/api';
const PAGES=[
  {view:'evaluations',icon:'check',label:'Evaluations',short:'Evals',Page:EvaluationsPage},
  {view:'traces',icon:'history',label:'Traces',short:'Traces',Page:TracesPage},
];
const VIEWS=PAGES.map(page=>page.view);
function pageFromHash() {const page=location.hash.replace(/^#admin-?/,'');return VIEWS.includes(page)?page:VIEWS[0];}

// The AI engineering workspace (app_metadata role 'admin'): evaluations, agents, prompts, latency and failures from
// LangSmith. Read-only: nothing here changes a customer, an RM record or a prompt.
export default function AdminWorkspace({session}) {
  const [view,setView]=useState(pageFromHash);
  useEffect(()=>{const onHash=()=>setView(pageFromHash());window.addEventListener('hashchange',onHash);return ()=>window.removeEventListener('hashchange',onHash);},[]);
  function navigate(next) {setView(next);history.replaceState(null,'',`#admin-${next}`);}
  async function signOut() {
    try{const {data}=await supabase.auth.getSession();if(data.session)await fetch(`${API_URL}/auth/session-end`,{method:'POST',headers:{Authorization:`Bearer ${data.session.access_token}`}});}catch{}
    history.replaceState(null,'',location.pathname+location.search);
    await supabase.auth.signOut();
  }
  const pages=PAGES.map(page=>({...page,testid:`admin-open-${page.view}`,onSelect:()=>navigate(page.view)}));
  const menu=[{testid:'sign-out',icon:'logout',label:'Sign out',danger:true,onSelect:signOut}];
  const Current=PAGES.find(page=>page.view===view).Page;
  return <div className="bank-chat-page admin-shell" data-testid="admin-shell">
    <header className="bank-chat-header">
      <div className="bank-chat-brand"><span className="brand-name">DemoBank<span className="brand-dot">.</span></span>
        <div className="brand-caption"><strong>AI engineering dashboard</strong><span className="demo-badge">Synthetic data · LangSmith</span></div></div>
      <div className="chat-actions"><span className="rm-role-badge" title="Signed in with the admin role">Admin</span></div>
    </header>
    <div className="bank-chat-layout">
      <Sidebar session={session} name={session.user.email} subtitle="Administrator · signed in" menu={menu} current={view} pages={pages} onSignOut={signOut}/>
      <TabBar current={view} pages={pages}/>
      <main className="app-page page-enter" key={view}>
        <Current onNavigate={navigate}/>
        <footer className="bank-chat-footer"><span>IS4000 · Coursework demo · Synthetic data · Read-only view of LangSmith</span></footer>
      </main>
    </div>
  </div>;
}
