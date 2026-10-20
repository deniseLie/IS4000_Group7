import DocumentUpload from './DocumentUpload';
import DocumentDownload from './DocumentDownload';
export default function DocumentRecords({snapshot,review}) {
  return <section data-testid="records-documents"><h3>Document records</h3>
    <p>Latest {snapshot.data.length} document records, up to 200. Preliminary checks do not mean the bank accepted a document.</p>
    <DocumentUpload review={review}/>
    {snapshot.data.length===0&&<p data-testid="records-documents-empty">No document records.</p>}
    {snapshot.data.map(row=><article key={row.document_id} data-testid="records-document" data-document-id={row.document_id}>
      <h4>{row.file_name}</h4><p>{row.case_title}{row.item_label&&` · ${row.item_label}`}</p>
      <dl className="sharing-details"><dt>File type</dt><dd>{row.mime_type}</dd><dt>Size</dt><dd>{row.size_bytes.toLocaleString('en-SG')} bytes</dd>
        <dt>Recorded upload</dt><dd><time dateTime={row.uploaded_at}>{new Date(row.uploaded_at).toLocaleString()}</time></dd>
        <dt>Preliminary check</dt><dd data-testid="document-precheck-status">{row.precheck_status.replaceAll('_',' ')}</dd></dl>
      <DocumentDownload documentId={row.document_id}/>
    </article>)}
    <p className="source-notice">Metadata as of <time dateTime={snapshot.as_of}>{new Date(snapshot.as_of).toLocaleString()}</time>.</p>
  </section>;
}
