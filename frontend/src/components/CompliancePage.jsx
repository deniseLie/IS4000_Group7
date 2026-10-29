import ComplianceWorkspace from './ComplianceWorkspace';

export default function CompliancePage(props) {
  return <section className="app-page-body compliance-page" aria-label="Compliance review" data-testid="compliance-page">
    <ComplianceWorkspace onPage {...props}/>
  </section>;
}
