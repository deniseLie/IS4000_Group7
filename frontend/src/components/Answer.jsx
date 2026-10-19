const DOMAIN_LABELS={retail:'Everyday banking',fraud:'Fraud & security',compliance:'Compliance review',document:'Document check',relationship:'Relationship manager',smalltalk:'Banking assistant'};

function Sources({sources,onSource}) {
  if(!sources?.length)return null;
  return <details className="answer-sources"><summary>Sources</summary><p>These sources were supplied to the assistant. A citation does not guarantee that every claim is correct.</p>{sources.map(s=><div key={s.id}>{s.url?<button className="source-link" data-source-id={s.id} onClick={()=>onSource(s)}>{s.title}</button>:<span>{s.title}</span>} <small>{s.version}</small></div>)}</details>;
}

export default function Answer({message,pendingText,onSource}) {
  if(message.sections?.length)return <div className="answer-sections" data-testid="answer-sections">{message.sections.map(section=><section className="answer-section" key={section.task_id} data-domain={section.domain} data-response-origin={section.response_origin} data-prompt-version={section.prompt_version}>
    <h3>{DOMAIN_LABELS[section.domain]}{section.priority==='P0'&&<small>Urgent</small>}</h3>
    <span className="answer-text">{section.answer}</span>
    {section.response_origin==='validation'&&<small className="answer-origin">Answer withheld</small>}
    <Sources sources={section.sources} onSource={onSource}/>
  </section>)}</div>;
  return <><span className="answer-text">{message.content||pendingText}</span><Sources sources={message.sources} onSource={onSource}/></>;
}
