// CR15: monthly statements built from the client's own synthetic records (no stored PDF). Only posted transactions count;
// pending ones are listed separately because they have not changed the balance yet. Months are Singapore calendar months.
const ZONE_OFFSET=8*60*60*1000;
const round=value=>Math.round(value*100)/100;

// The last `count` full months before today, newest first, as 'YYYY-MM'.
export function lastFullMonths(today=new Date(),count=3) {
  const local=new Date(today.getTime()+ZONE_OFFSET);let year=local.getUTCFullYear(),month=local.getUTCMonth();  // month is 0-based: the current month
  const months=[];
  for(let i=0;i<count;i++){month-=1;if(month<0){month=11;year-=1;}months.push(`${year}-${String(month+1).padStart(2,'0')}`);}
  return months;
}
export function monthLabel(month) {
  const [year,number]=month.split('-').map(Number);
  return new Date(Date.UTC(year,number-1,1)).toLocaleDateString('en-SG',{month:'long',year:'numeric',timeZone:'UTC'});
}
export function monthBounds(month) {
  const [year,number]=month.split('-').map(Number);
  const last=new Date(Date.UTC(year,number,0)).getUTCDate();
  return {first:`${month}-01`,last:`${month}-${String(last).padStart(2,'0')}`};
}

// What a statement can be drawn for. Account rows carry no account id, so account transactions are only attributed when
// the client has exactly one open account in that currency; cards are matched by card id.
export function statementSources(accounts,cards) {
  const open=accounts.filter(row=>row.status!=='closed');
  const list=[];
  for(const account of open){if(open.filter(other=>other.currency===account.currency).length===1)list.push({key:`account:${account.account_id}`,kind:'account',source:account});}
  for(const card of cards)list.push({key:`card:${card.card_id}`,kind:'card',source:card});
  return list;
}

function belongs(kind,source) {
  return kind==='card'?row=>row.card_id===source.card_id:row=>row.card_id===null&&row.currency===source.currency;
}

// Balances are worked back from today's balance: closing = current minus everything posted after the month ended.
// For a card the balance is what is owed, so a purchase (negative amount) raises it.
export function buildStatement({kind,source},transactions,month) {
  const {first,last}=monthBounds(month),mine=transactions.filter(belongs(kind,source));
  const posted=mine.filter(row=>row.status==='posted');
  const sign=kind==='card'?-1:1,current=kind==='card'?source.outstanding:source.balance;
  const after=posted.filter(row=>row.date>last).reduce((sum,row)=>sum+row.amount,0);
  const closing=round(current-sign*after);
  const entries=posted.filter(row=>row.date>=first&&row.date<=last).sort((a,b)=>a.date.localeCompare(b.date)||a.txn_id.localeCompare(b.txn_id,undefined,{numeric:true}));
  const moved=entries.reduce((sum,row)=>sum+row.amount,0),opening=round(closing-sign*moved);
  let running=opening;
  const lines=entries.map(row=>{running=round(running+sign*row.amount);return {...row,balance:running};});
  const money_in=round(entries.filter(row=>row.amount>0).reduce((sum,row)=>sum+row.amount,0));
  const money_out=round(-entries.filter(row=>row.amount<0).reduce((sum,row)=>sum+row.amount,0));
  const pending=mine.filter(row=>row.status==='pending'&&row.date>=first&&row.date<=last);
  return {kind,month,first,last,currency:kind==='card'?'SGD':source.currency,opening,closing,money_in,money_out,lines,pending};
}
