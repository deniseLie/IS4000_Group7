import ComplianceWorkspace from './ComplianceWorkspace';

import Icon from './Icon';

export default function CompliancePage({onBack,...props}) {
  return <section className="app-page-body compliance-page rm-page" aria-label="Compliance review" data-testid="compliance-page">
    {onBack&&<button className="rm-back" onClick={onBack}><Icon name="chevron"/>Back to chat</button>}
    <header className="rm-page-head"><div><h1>Your compliance review</h1><p>Upload what the bank asked for. Each file gets a preliminary check before it is submitted.</p></div></header>
    <ComplianceWorkspace onPage {...props}/>
  </section>;
}
