import {useState} from 'react';
import Icon from './Icon';
import {supabase} from '../lib/supabase';
import {confirmAction,createActionCase,proposeAction,rejectAction} from '../lib/actions';

async function token() {
  const {data,error}=await supabase.auth.getSession();if(error)throw error;
  if(!data.session)throw new Error('Please sign in again to manage your card.');
  return data.session.access_token;
}

const EFFECT={
  'card.temporary_block':['Freeze this card?','New payments, withdrawals and online purchases are declined until you unfreeze it. Payments already made are not reversed.','Freeze card'],
  'card.unblock':['Unfreeze this card?','New payments can go through again. Only unfreeze it if you have the card and it is safe.','Unfreeze card'],
};

// Styles live here so the card controls are one file; colours match index.css (--ink, --muted, --line, --red, --tint).
const S={
  wrap:{margin:'12px 0 4px 48px',display:'grid',gap:10},
  panel:{border:'1px solid #e4e4de',borderRadius:12,background:'#fff',overflow:'hidden'},
  freeze:{display:'flex',alignItems:'center',gap:12,padding:'12px 14px'},
  freezeIcon:on=>({width:34,height:34,borderRadius:'50%',display:'grid',placeItems:'center',flex:'none',background:on?'#e8f0fb':'#f7f7f4',color:on?'#1d4f8f':'#262624'}),
  text:{flex:1,minWidth:0,display:'grid',gap:2},
  title:{fontSize:14,fontWeight:600,color:'#262624'},
  sub:{fontSize:12,color:'#65655f',lineHeight:1.35},
  switch:(on,disabled)=>({position:'relative',width:44,height:26,flex:'none',borderRadius:999,border:'none',padding:0,cursor:disabled?'not-allowed':'pointer',
    background:on?'#1d4f8f':'#cfcfc8',opacity:disabled?.55:1,transition:'background .15s'}),
  knob:on=>({position:'absolute',top:3,left:on?21:3,width:20,height:20,borderRadius:'50%',background:'#fff',boxShadow:'0 1px 2px rgba(0,0,0,.25)',transition:'left .15s'}),
  actions:{display:'flex',borderTop:'1px solid #e4e4de'},
  action:last=>({flex:'1 1 0',minWidth:0,display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',gap:6,padding:'12px 6px',border:'none',
    borderRight:last?'none':'1px solid #e4e4de',background:'#fff',font:'inherit',fontSize:13,fontWeight:600,lineHeight:1.25,textAlign:'center',color:'#262624',cursor:'pointer'}),
  confirm:{border:'1px solid #f0c9c3',background:'#faeeeb',borderRadius:12,padding:'12px 14px',display:'grid',gap:8},
  buttons:{display:'flex',gap:8,justifyContent:'flex-end',flexWrap:'wrap'},
  note:{display:'flex',gap:6,alignItems:'center',fontSize:12,color:'#65655f',margin:0},
  done:{display:'flex',gap:6,alignItems:'center',fontSize:13,color:'#1f6b3a',margin:0},
  error:{fontSize:13,color:'#bd291e',margin:0},
};
const icon={display:'grid',placeItems:'center',width:30,height:30,borderRadius:'50%',background:'#faeeeb',color:'#bd291e',flex:'none'};

// Card controls on Overview (0096, CR19). Freezing and unfreezing are the client's own confirmed actions: the exact
// command is proposed, shown here, and runs only when she confirms (the database refuses an unfreeze of a card the bank
// froze). Everything that needs a bank decision opens a prefilled request in My requests instead.
export default function CardControls({card,onChanged,onRaise}) {
  const [pending,setPending]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[done,setDone]=useState('');
  const frozen=card.status==='temporarily_blocked',open=['active','temporarily_blocked'].includes(card.status);
  const locked=frozen&&!!card.unfreeze_blocked_reason;  // frozen by the bank, a fraud case or a lost-card report
  async function start(type) {
    setBusy(true);setError('');setDone('');
    try {
      const auth=await token();
      const scope=await createActionCase({session_id:crypto.randomUUID(),case_id:crypto.randomUUID()},auth);
      const proposed=await proposeAction({command_id:crypto.randomUUID(),case_id:scope.case_id,task_id:'card_controls',type,target:{card_id:card.card_id}},auth);
      setPending(proposed.command);
    } catch(failure) {setError(failure.message);} finally {setBusy(false);}
  }
  async function decide(yes) {
    setBusy(true);setError('');
    try {
      const auth=await token();
      if(!yes){await rejectAction(pending.command_id,auth);setPending(null);return;}
      const outcome=await confirmAction(pending.command_id,auth);
      setPending(null);
      if(outcome.receipt?.outcome!=='succeeded')throw new Error('Your card changed before this was confirmed, so nothing was done. Check the card again.');
      setDone(outcome.command.type==='card.temporary_block'?`Card ending ${card.last4} is frozen.`:`Card ending ${card.last4} is unfrozen.`);
      onChanged?.();
    } catch(failure) {setError(failure.message);} finally {setBusy(false);}
  }
  const raise=(category,summary)=>onRaise?.({category,details:{card:card.last4},summary});
  if(!open)return <p style={S.note}><Icon name="info"/>This card is {card.status.replaceAll('_',' ')}. Only the bank can change it.</p>;
  const ACTIONS=[
    ['card-limit','card','Change limit','Steps of SGD 1,000, reviewed by the cards and credit team',()=>raise('card_limit_change',`Change the limit on my ${card.name} ending ${card.last4}.`)],
    ['card-replace','refresh','Replace card','Lost, stolen or damaged: a lost or stolen card is frozen at once',()=>raise('card_replacement',`Replace my ${card.name} ending ${card.last4}.`)],
    ['card-dispute','alert','Dispute a charge','A charge you recognise but think is wrong, within 60 days',()=>raise('card_dispute',`Dispute a charge on my ${card.name} ending ${card.last4}.`)],
  ];
  return <div style={S.wrap} data-testid="card-controls" data-card={card.last4}>
    {pending?<div style={S.confirm} role="group" aria-label={EFFECT[pending.type][0]}>
        <strong style={S.title}>{EFFECT[pending.type][0]}</strong><p style={{...S.sub,fontSize:13,margin:0,color:'#262624'}}>{EFFECT[pending.type][1]}</p>
        <div style={S.buttons}><button className="bank-chat-logout" disabled={busy} onClick={()=>decide(false)}>Cancel</button>
          <button className="primary-button" data-testid="card-confirm" disabled={busy} onClick={()=>decide(true)}><Icon name="check"/>{EFFECT[pending.type][2]}</button></div></div>
    :<div style={S.panel}>
      <div style={S.freeze}>
        <span style={S.freezeIcon(frozen)}><Icon name="lock"/></span>
        <span style={S.text}><span style={S.title}>{frozen?'Card frozen':'Freeze card'}</span>
          <span style={S.sub}>{locked?card.unfreeze_blocked_reason:frozen?'New payments are declined. Switch off to unfreeze.':'Stop new payments at once. Undo any time.'}</span></span>
        <button type="button" role="switch" aria-checked={frozen} aria-label={frozen?'Unfreeze card':'Freeze card'} data-testid={frozen?'card-unfreeze':'card-freeze'}
          disabled={busy||locked} style={S.switch(frozen,busy||locked)} onClick={()=>start(frozen?'card.unblock':'card.temporary_block')}><span style={S.knob(frozen)}/></button>
      </div>
      <div style={S.actions}>{ACTIONS.map(([id,name,label,hint,run],i)=><button key={id} type="button" data-testid={id} title={hint} style={S.action(i===ACTIONS.length-1)} onClick={run}>
        <span style={icon}><Icon name={name}/></span>{label}</button>)}</div>
    </div>}
    {done&&<p style={S.done} role="status"><Icon name="check"/>{done}</p>}
    {error&&<p role="alert" style={S.error}>{error}</p>}
    <p style={S.note}><Icon name="shield"/>Don’t recognise a charge? Report it in the chat. Only the bank cancels a card.</p>
  </div>;
}