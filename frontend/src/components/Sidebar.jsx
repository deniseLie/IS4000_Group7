import Icon from './Icon';

// Support links switch pages (or ask a guest to sign in); topic links draft a question in the chat.
import {initials} from './SharingPage';

export default function Sidebar({session,name,view,pages,topics,onNavigate,onDraft,onSignOut}) {
  return <aside className="bank-sidebar" aria-label="Banking topics">
    <nav className="sidebar-group" aria-label="Your support"><span className="eyebrow">DemoBank</span>
      {pages.map(page=><button key={page.view||page.label} data-testid={page.testid} aria-current={page.view===view?'page':undefined} className={page.view===view?'sidebar-current':''} onClick={()=>page.onSelect?page.onSelect():page.view?onNavigate(page.view):onDraft(page.question)}><Icon name={page.icon}/>{page.label}</button>)}
    </nav>
    {!session&&<nav className="sidebar-group" aria-label="Explore topics"><span className="eyebrow">Explore topics</span>{topics.map(t=><button key={t.icon} onClick={()=>onDraft(t.question)}><Icon name={t.icon}/>{t.title}</button>)}</nav>}
    <div className="sidebar-session" data-testid="sidebar-profile">{session&&name?<span className="session-avatar initials" aria-hidden="true">{initials(name)}</span>:<span className="session-avatar"><Icon name="user"/></span>}
      <div>{session?(name||'Signed in'):'Guest session'}<small>{session?'Demo client · signed in':'No account connected'}</small></div>
      {session&&onSignOut&&<button className="icon-button sidebar-signout" aria-label="Sign out" onClick={onSignOut}><Icon name="logout"/></button>}</div>
  </aside>;
}
