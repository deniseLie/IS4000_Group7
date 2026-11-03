import {formatDate,formatMoney} from '../lib/format';

// One field of a request form, from the category's configuration (F20). Shared by My requests, the request card in the
// chat and the RM's service request form so every kind looks and behaves the same. 0096 adds two kinds:
// confirm (a declaration she ticks; sent as "yes") and transaction (one of her card charges, chosen from a list).
export function chargeLabel(charge) {
  return `${formatDate(charge.date)} · ${charge.description} · ${formatMoney(Math.abs(charge.amount),charge.currency)}`;
}

export default function RequestField({field,value='',onChange,disabled,required,testId,charges,card,className='',onBehalf=false}) {
  const needed=required??field.required;
  if(field.kind==='confirm')return <label className={`request-confirm ${className}`}>
    <input type="checkbox" data-testid={testId} checked={value==='yes'} disabled={disabled} required={needed}
      onChange={event=>onChange(event.target.checked?'yes':'')}/>
    <span>{onBehalf?<strong>The client confirms: </strong>:null}{field.label}{needed?'':' (if it applies)'}</span></label>;
  const label=`${field.label}${needed?'':' (optional)'}`;
  if(field.kind==='choice')return <label className={className}>{label}
    <select data-testid={testId} required={needed} value={value} disabled={disabled} onChange={event=>onChange(event.target.value)}>
      <option value="">Choose…</option>{field.choices.map(choice=><option key={choice} value={choice}>{choice}</option>)}</select></label>;
  if(field.kind==='transaction'&&charges) {
    const shown=charges.filter(charge=>!card||charge.card_last4===card);
    return <label className={className}>{label}
      <select data-testid={testId} required={needed} value={value} disabled={disabled||!shown.length} onChange={event=>onChange(event.target.value)}>
        <option value="">{!card?'Enter the card first…':shown.length?'Choose the charge…':'No charges on this card in the last 60 days'}</option>
        {shown.map(charge=><option key={charge.txn_id} value={charge.txn_id}>{chargeLabel(charge)}</option>)}</select></label>;
  }
  return <label className={className}>{label}
    <input data-testid={testId} required={needed} disabled={disabled} value={value}
      type={field.kind.startsWith('date')?'date':'text'} inputMode={field.kind==='amount'?'decimal':field.kind==='card'?'numeric':undefined}
      pattern={field.kind==='card'?'[0-9]{4}':field.kind==='amount'?'[0-9]{1,9}(\\.[0-9]{1,2})?':field.kind==='transaction'?'txn_[0-9]{1,18}':undefined}
      maxLength={field.kind==='card'?4:300} placeholder={field.kind==='transaction'?'Charge id, for example txn_100214':undefined}
      onChange={event=>onChange(event.target.value)}/></label>;
}

// What she should know before asking (0096): the bank's rules for each card request, in the same words the bank uses
// when it refuses one.
export const CARD_RULES={
  card_limit_change:'Limits change in steps of SGD 1,000, from SGD 1,000 to SGD 50,000, and never below what you owe. A temporary increase lasts up to 90 days. You can ask once per card every 30 days after the last request is decided.',
  card_replacement:'A lost or stolen card is frozen as soon as you submit. Card operations cancel it and post a new card with a new number to your registered address. Charges you don’t recognise: report them in the chat so the fraud team can act.',
  card_dispute:'For a charge you recognise but think is wrong, within 60 days of it being posted. Contact the merchant first if you can. A charge you don’t recognise is fraud, not a dispute: report it in the chat.',
};
