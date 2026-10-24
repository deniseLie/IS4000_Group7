import Icon from './Icon';
import AccountMenu from './AccountMenu';

export default function TopBar({session,booting,view,inbox,menu,onNewChat,onSupport,onSignIn}) {
  return <header className="bank-chat-header">
    <div className="bank-chat-brand"><span className="brand-name">DemoBank<span className="brand-dot">.</span></span><div className="brand-caption"><strong>Banking assistant</strong><span>AI-powered · Student project</span></div></div>
    <div className="chat-actions">
      <button className="bank-chat-logout" data-testid="new-chat" disabled={booting} onClick={onNewChat}><Icon name="plus"/>New chat</button>
      <button className="bank-chat-logout support-action" onClick={onSupport}><Icon name="support"/><span>Talk to a person</span></button>
      {session&&<button className={`bank-chat-logout support-action ${view==='inbox'?'active':''}`} aria-label="Open notifications" aria-current={view==='inbox'?'page':undefined} data-testid="open-notifications" onClick={inbox}><Icon name="bell"/><span>Inbox</span></button>}
      <span className="header-divider" aria-hidden="true"/>
      {session?<AccountMenu label="Account" items={menu}/>:<button data-testid="sign-in" className="bank-chat-logout sign-in-action" onClick={onSignIn}>Sign in<Icon name="arrow"/></button>}
    </div>
  </header>;
}
