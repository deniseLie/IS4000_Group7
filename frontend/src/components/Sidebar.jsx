import Icon from './Icon';
import AccountMenu from './AccountMenu';
import {initials} from './SharingPage';

// One list of pages for everyone: guests see a lock on pages that need an account (they open a sign-in prompt).
function label(page) {return page.locked?`${page.label} (sign in required)`:page.badge>0?`${page.label}, ${page.badge} unread`:undefined;}
function marker(page) {return page.locked?<span className="nav-lock"><Icon name="lock"/></span>:page.badge>0?<span className="nav-badge" aria-hidden="true">{page.badge}</span>:null;}

// The profile at the bottom opens the account menu (records, data sharing, saved chats, Actions, sign out).
export default function Sidebar({session,name,current,pages,menu,onSignOut}) {
  // name is null while it loads: a placeholder holds its place instead of generic text that later changes
  const profile=<>{session&&name?<span className="session-avatar initials" aria-hidden="true">{initials(name)}</span>:<span className="session-avatar"><Icon name="user"/></span>}
    <span className="sidebar-who">{session&&name===null?<><span className="skeleton-text" aria-hidden="true"/><small className="skeleton-text short" aria-hidden="true"/><span className="sr-only">Loading your profile</span></>
      :<><span>{session?(name||'Signed in'):'Guest session'}</span><small>{session?'Demo client · signed in':'No account connected'}</small></>}</span></>;
  return <aside className="bank-sidebar" aria-label="DemoBank">
    <nav className="sidebar-group" aria-label="Pages"><span className="eyebrow">DemoBank</span>
      {pages.map(page=><button key={page.view} data-testid={page.testid} aria-label={label(page)} aria-current={page.view===current?'page':undefined} className={page.view===current?'sidebar-current':''} onClick={page.onSelect}>
        <Icon name={page.icon}/><span className="nav-label">{page.label}</span>{marker(page)}</button>)}
    </nav>
    <div className="sidebar-session" data-testid="sidebar-profile">
      {session&&menu?<AccountMenu className="sidebar-account" placement="up" label={profile} items={menu}/>:<div className="sidebar-account static">{profile}</div>}
      {session&&onSignOut&&<button className="icon-button sidebar-signout" aria-label="Sign out" onClick={onSignOut}><Icon name="logout"/></button>}</div>
  </aside>;
}

// Narrow screens: the same pages as a bottom tab bar (Actions stays in the account menu there).
export function TabBar({current,pages}) {
  return <nav className="tab-bar" aria-label="Pages">{pages.map(page=><button key={page.view} data-testid={`tab-${page.view}`} aria-label={label(page)} aria-current={page.view===current?'page':undefined} onClick={page.onSelect}>
    <span className="tab-icon"><Icon name={page.icon}/>{marker(page)}</span><span>{page.short}</span></button>)}</nav>;
}
