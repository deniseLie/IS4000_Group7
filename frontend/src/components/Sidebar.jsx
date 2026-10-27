import Icon from './Icon';
import AccountMenu from './AccountMenu';
import {initials} from './SharingPage';

// Support links switch pages (or ask a guest to sign in); the welcome cards, not the sidebar, draft topic questions.
// The profile at the bottom opens the account menu (records, data sharing, saved chats, activity, sign out).
export default function Sidebar({session,name,view,pages,menu,onNavigate,onSignOut}) {
  const profile=<>{session&&name?<span className="session-avatar initials" aria-hidden="true">{initials(name)}</span>:<span className="session-avatar"><Icon name="user"/></span>}
    <span className="sidebar-who"><span>{session?(name||'Signed in'):'Guest session'}</span><small>{session?'Demo client · signed in':'No account connected'}</small></span></>;
  return <aside className="bank-sidebar" aria-label="Banking topics">
    <nav className="sidebar-group" aria-label="Your support"><span className="eyebrow">DemoBank</span>
      {pages.map(page=><button key={page.view||page.label} data-testid={page.testid} aria-current={page.view===view?'page':undefined} className={page.view===view?'sidebar-current':''} onClick={()=>page.onSelect?page.onSelect():onNavigate(page.view)}><Icon name={page.icon}/>{page.label}</button>)}
    </nav>
    <div className="sidebar-session" data-testid="sidebar-profile">
      {session&&menu?<AccountMenu className="sidebar-account" placement="up" label={profile} items={menu}/>:<div className="sidebar-account static">{profile}</div>}
      {session&&onSignOut&&<button className="icon-button sidebar-signout" aria-label="Sign out" onClick={onSignOut}><Icon name="logout"/></button>}</div>
  </aside>;
}
