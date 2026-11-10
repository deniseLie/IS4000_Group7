import Icon from '../Icon';

export default function MeetingRecap({published,sharedText,draft,busy,onDownload,onShare}){
  const text=sharedText||published?.text;
  const preview=draft&&draft.draft_id!==published?.document_id?draft:null;
  return <section className="meeting-recap" data-testid="meeting-recap" aria-label="Meeting recap">
    <div className="meeting-recap-head">
      <span className="meeting-recap-icon"><Icon name="document"/></span>
      <div><h3>Meeting recap</h3>
        {text?<p className="rm-small">Approved and shared</p>:!preview&&<p className="rm-small">The recap will appear here after the relationship manager shares it.</p>}
      </div>
    </div>
    {text&&<p className="meeting-recap-copy" data-testid="meeting-shared-recap">{text}</p>}
    {published&&<div className="meeting-actions meeting-recap-actions">
      <button className="bank-chat-logout" data-testid="meeting-download-recap" disabled={busy} onClick={onDownload}><Icon name="download"/>Download recap PDF</button>
    </div>}
    {preview&&<div className="meeting-recap-preview">
      <h4>{text?'Updated recap preview':'Client recap preview'}</h4>
      <p className="meeting-recap-copy">{preview.content.client_recap}</p>
      {preview.approved?<div className="meeting-actions meeting-recap-actions">
        <button className="primary-button" data-testid="meeting-share-recap" disabled={busy} onClick={onShare}>Share recap with client</button>
      </div>:<p className="rm-small">Approve the minutes before sharing this recap.</p>}
    </div>}
  </section>;
}
