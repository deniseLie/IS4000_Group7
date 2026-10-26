import ComplianceWorkspace from './ComplianceWorkspace';
import Icon from './Icon';

export default function CompliancePage({onBack,...props}) {
  return <section className="app-page-body compliance-page rm-page" aria-label="Compliance review" data-testid="compliance-page">
    {onBack&&<button className="rm-back" onClick={onBack}><Icon name="chevron"/>Back to chat</button>}
    <ComplianceWorkspace onPage {...props}/>
  </section>;
}
