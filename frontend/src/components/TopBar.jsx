import Icon from './Icon';

// Pages live in the sidebar (a bottom tab bar on narrow screens); the header keeps the Inbox, help and sign-in.
// Account features live in the sidebar profile (on narrow screens CSS pins that same menu into the header).
export default function TopBar({session,onSupport,onSignIn,unread=0,inboxOpen=false,onInbox}) {
  return <header className="bank-chat-header">
    <div className="bank-chat-brand"><span className="brand-name">DemoBank<span className="brand-dot">.</span></span><div className="brand-caption"><strong>Banking assistant</strong><span className="demo-badge">Fictional demo data</span></div></div>
    <div className="chat-actions">
      {session&&onInbox&&<button className={`bank-chat-logout inbox-action ${inboxOpen?'active':''}`} data-testid="open-notifications" aria-label={unread?`Inbox, ${unread} unread`:'Inbox'} aria-current={inboxOpen?'page':undefined} onClick={onInbox}>
        <Icon name="bell"/><span>Inbox</span>{unread>0&&<span className="inbox-badge" aria-hidden="true">{unread>9?'9+':unread}</span>}</button>}
      <button className="bank-chat-logout support-action" onClick={onSupport}><Icon name="support"/><span>Talk to a person</span></button>
      {!session&&<button data-testid="sign-in" className="bank-chat-logout sign-in-action" onClick={onSignIn}>Sign in<Icon name="arrow"/></button>}
    </div>
  </header>;
}
