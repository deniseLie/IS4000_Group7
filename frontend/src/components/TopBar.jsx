import Icon from './Icon';

// Pages live in the sidebar (a bottom tab bar on narrow screens); the header keeps help and sign-in.
// Account features live in the sidebar profile (on narrow screens CSS pins that same menu into the header).
export default function TopBar({session,onSupport,onSignIn}) {
  return <header className="bank-chat-header">
    <div className="bank-chat-brand"><span className="brand-name">DemoBank<span className="brand-dot">.</span></span><div className="brand-caption"><strong>Banking assistant</strong><span className="demo-badge">Demo · fictional data</span></div></div>
    <div className="chat-actions">
      <button className="bank-chat-logout support-action" onClick={onSupport}><Icon name="support"/><span>Talk to a person</span></button>
      {!session&&<button data-testid="sign-in" className="bank-chat-logout sign-in-action" onClick={onSignIn}>Sign in<Icon name="arrow"/></button>}
    </div>
  </header>;
}
