import {useState} from 'react';

const agenda={goals:'Confirm the meeting goals',status:'Review the current status',documents:'Discuss the required documents',options:'Discuss the available options',next_steps:'Agree next steps and responsibilities',questions:'Answer your questions'};
const questions={clarify_objective:'What would you like to achieve?',confirm_documents:'Which documents or information should we clarify?',review_options:'Which options would you like to discuss?',confirm_next_steps:'What should happen next, and who will do it?',ask_product_risks:'What risks, terms and charges should we clarify?',confirm_timeframe:'What timeframe matters to you?'};

function money(value,currency){return new Intl.NumberFormat('en-SG',{style:'currency',currency,maximumFractionDigits:2}).format(value);}

export default function MeetingPack({pack,isRm,busy,onDraft,onApprove,onShare,onDownload}){
  const [amounts,setAmounts]=useState([]);
  function change(index,field,value){setAmounts(rows=>rows.map((row,i)=>i===index?{...row,[field]:value}:row));}
  return <section className="meeting-minutes" data-testid="meeting-pack" aria-label="Meeting discussion pack">
    <h3>Meeting discussion pack</h3><p className="rm-small">{isRm?'The agreed agenda and current approved demo catalogue terms. Your internal brief is kept separate.':'Review the agreed agenda and discussion material shared by your RM.'}</p>
    {isRm&&<form onSubmit={event=>{event.preventDefault();onDraft(amounts);}}>
      <p className="rm-small">Optional amounts you explicitly enter for this discussion.</p>
      {amounts.map((item,index)=><div className="rm-form-grid" key={index}>
        <label className="form-field">Amount label<input data-testid={`meeting-pack-amount-label-${index}`} maxLength={80} required value={item.label} disabled={busy} onChange={event=>change(index,'label',event.target.value)}/></label>
        <label className="form-field">Currency<input data-testid={`meeting-pack-currency-${index}`} pattern="[A-Z]{3}" maxLength={3} required value={item.currency} disabled={busy} onChange={event=>change(index,'currency',event.target.value.toUpperCase())}/></label>
        <label className="form-field">Amount<input data-testid={`meeting-pack-amount-${index}`} inputMode="decimal" pattern="[0-9]{1,14}([.][0-9]{1,2})?" required value={item.amount} disabled={busy} onChange={event=>change(index,'amount',event.target.value)}/></label>
        <button type="button" className="bank-chat-logout" data-testid={`meeting-pack-remove-amount-${index}`} disabled={busy} onClick={()=>setAmounts(rows=>rows.filter((_,i)=>i!==index))}>Remove amount</button></div>)}
      <div className="meeting-actions"><button type="button" className="bank-chat-logout" data-testid="meeting-pack-add-amount" disabled={busy||amounts.length>=5} onClick={()=>setAmounts(rows=>[...rows,{label:'',currency:'SGD',amount:''}])}>Add stated amount</button>
        <button className="bank-chat-logout" data-testid="meeting-draft-pack" disabled={busy}>{pack?'Create new pack version':'Draft discussion pack'}</button></div></form>}
    {!pack&&<p className="page-empty">{isRm?'No discussion pack has been drafted.':'Your RM has not shared a discussion pack yet.'}</p>}
    {pack&&<div data-testid="meeting-pack-document"><p className="act-pill">Version {pack.version} · {pack.status}</p>
      {pack.sources_changed&&<p className="rmw-warn">Agenda or catalogue terms have changed since this document was drafted. {isRm?'Create and review a refreshed version.':'Ask your RM for the current document.'}</p>}
      <h4>{pack.content.title}</h4><p>{pack.content.purpose}</p><ul>{pack.content.agenda.map(code=><li key={code}>{agenda[code]}</li>)}</ul>
      {pack.content.products.map(item=><div key={item.item_id}><h4>{item.name}</h4><p className="rm-small">Catalogue version {item.version} · effective {item.effective_from} to {item.effective_to}</p>
        {item.annual_rate!==null&&<p>Annual rate: {item.annual_rate}%</p>}{item.minimum_amount!==null&&<p>Minimum amount: {money(item.minimum_amount,item.currency)}</p>}
        {item.fee!==null&&<p>Fee: {money(item.fee,item.currency)}</p>}{item.term_months!==null&&<p>Term: {item.term_months} months</p>}</div>)}
      {pack.content.stated_amounts.map((item,index)=><p key={index}>{item.label}: {money(item.amount,item.currency)} <span className="rm-small">entered by RM for discussion</span></p>)}
      <ul>{pack.content.questions.map(code=><li key={code}>{questions[code]}</li>)}</ul><p className="rm-small">Discussion material. The meeting does not execute a transaction or establish suitability.</p>
      <button className="bank-chat-logout" data-testid="meeting-download-pack" disabled={busy||pack.sources_changed} onClick={onDownload}>Download discussion pack PDF</button>
      {isRm&&<><button className="primary-button" data-testid="meeting-approve-pack" disabled={busy||pack.sources_changed||pack.status!=='draft'} onClick={onApprove}>Approve exact pack</button>
        <button className="bank-chat-logout" data-testid="meeting-share-pack" disabled={busy||pack.sources_changed||pack.status!=='approved'} onClick={onShare}>Share approved pack with client</button></>}</div>}
  </section>;
}
