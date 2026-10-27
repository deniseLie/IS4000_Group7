import Icon from './Icon';

const WHAT={overview:'your accounts overview',inbox:'your inbox',actions:'your actions and receipts',relationship:'your relationship manager',compliance:'your compliance review',sharing:'your data-sharing settings'};

// Guests see what a page holds and how to reach it, instead of being sent straight to the sign-in screen.
export default function LockedPage({page,onSignIn,onBack}) {
  return <section className="locked-page" aria-labelledby="locked-title" data-testid="locked-page">
    <span className="locked-icon" aria-hidden="true"><Icon name="lock"/></span>
    <h1 id="locked-title">Sign in to see {WHAT[page]}</h1>
    <p>This page uses your synthetic demo records, so it needs a demo account. General banking help in the assistant works without signing in.</p>
    <div className="panel-actions"><button className="primary-button" data-testid="locked-sign-in" onClick={onSignIn}>Sign in<Icon name="arrow"/></button><button className="bank-chat-logout" onClick={onBack}>Back to the assistant</button></div>
  </section>;
}
