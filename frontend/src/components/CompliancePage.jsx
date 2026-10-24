import ComplianceWorkspace from './ComplianceWorkspace';

export default function CompliancePage(props) {
  return <section className="app-page-body compliance-page" aria-label="Compliance review" data-testid="compliance-page">
    <div className="page-header"><div><span className="eyebrow">Compliance review</span><h1>Your recorded review</h1><p>Upload the requested synthetic documents, check preliminary findings, and follow recorded progress.</p></div></div>
    <ComplianceWorkspace {...props}/>
  </section>;
}
