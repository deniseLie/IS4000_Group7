import Icon from './Icon';

// Support links switch pages (or ask a guest to sign in); topic links draft a question in the chat.
export default function Sidebar({session,view,pages,topics,onNavigate,onDraft}) {
  return <aside className="bank-sidebar" aria-label="Banking topics">
    <nav className="sidebar-group" aria-label="Your support"><span className="eyebrow">Your support</span>
      {pages.map(page=><button key={page.view||page.label} data-testid={page.testid} aria-current={page.view===view?'page':undefined} className={page.view===view?'sidebar-current':''} onClick={()=>page.onSelect?page.onSelect():page.view?onNavigate(page.view):onDraft(page.question)}><Icon name={page.icon}/>{page.label}</button>)}
    </nav>
    <nav className="sidebar-group" aria-label="Explore topics"><span className="eyebrow">Explore topics</span>{topics.map(t=><button key={t.icon} onClick={()=>onDraft(t.question)}><Icon name={t.icon}/>{t.title}</button>)}</nav>
    <div className="sidebar-note"><span className="small-rule"/><h2>A little clarity.<br/>A helpful next step.</h2><p>General guidance for your everyday banking questions.</p></div>
    <div className="sidebar-session"><span className="session-avatar"><Icon name="user"/></span><div>{session?'Demo account':'Guest session'}<small>{session?'Signed in':'No account connected'}</small></div></div>
  </aside>;
}
