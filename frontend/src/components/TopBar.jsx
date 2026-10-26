import Icon from './Icon';

// Account features live in the sidebar profile (on narrow screens CSS pins that same menu into the header).
export default function TopBar({session,unread=0,booting,view,inbox,menu,onNewChat,onSupport,onSignIn}) {
  return <header className="bank-chat-header">
    <div className="bank-chat-brand"><span className="brand-name">DemoBank<span className="brand-dot">.</span></span><div className="brand-caption"><strong>Banking assistant</strong><span>AI-powered · Student project</span></div></div>
    <div className="chat-actions">
      <button className="bank-chat-logout" data-testid="new-chat" disabled={booting} onClick={onNewChat}><Icon name="plus"/>New chat</button>
      <button className="bank-chat-logout support-action" onClick={onSupport}><Icon name="support"/><span>Talk to a person</span></button>
      {session&&<button className={`bank-chat-logout support-action inbox-action ${view==='inbox'?'active':''}`} aria-label={unread?`Open notifications, ${unread} unread`:'Open notifications'} aria-current={view==='inbox'?'page':undefined} data-testid="open-notifications" onClick={inbox}><Icon name="bell"/><span>Inbox</span>{unread>0&&<span className="unread-dot" aria-hidden="true"/>}</button>}
      {!session&&<button data-testid="sign-in" className="bank-chat-logout sign-in-action" onClick={onSignIn}>Sign in<Icon name="arrow"/></button>}
    </div>
  </header>;
}
