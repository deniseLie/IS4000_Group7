// One look for every written summary (design: "Brief, minutes and overview: one structure"): a dark "In one line" box,
// numbered sections, the same four labels on each point (Record, Client said, AI suggestion, Gap), the source next to
// the point, and gaps gathered in a "Not known yet" box. Presentation only: the content and its checks are unchanged.
export const DOC_LABELS={record:'Record',client_said:'Client said',client_stated:'Client said',ai_suggestion:'AI suggestion',suggestion:'AI suggestion',gap:'Gap',guidance:'Bank guidance'};
const TONE={record:'rec',client_said:'said',client_stated:'said',ai_suggestion:'ai',suggestion:'ai',gap:'gap',guidance:'rec'};

export function OneLine({children}) {
  if(!children)return null;
  return <div className="doc-oneline" data-testid="doc-oneline"><span className="eyebrow">In one line</span><p>{children}</p></div>;
}
export function DocSection({n,title,children,extra=null}) {
  return <section className="doc-section"><h3>{n!=null&&<span className="doc-n">{n}</span>}{title}{extra}</h3>{children}</section>;
}
// sources: [{key,title,onClick?}]
export function Point({kind='record',children,sources=[],edited=false,tail=null}) {
  return <p className="doc-point" data-kind={kind}><span className={`doc-label ${TONE[kind]||'rec'}`}>{DOC_LABELS[kind]||kind}</span>
    <span className="doc-text">{children}{edited&&<span className="rm-edited"> Edited by you</span>}{tail}</span>
    {sources.map(s=>s.onClick?<button key={s.key} type="button" className="doc-src" data-source-id={s.id} title={s.title} onClick={s.onClick}>{s.title}</button>
      :<span key={s.key} className="doc-src">{s.title}</span>)}</p>;
}
export function NotKnown({children}) {
  return <section className="doc-section doc-gaps" aria-label="Not known yet"><h3>Not known yet</h3>{children}</section>;
}
