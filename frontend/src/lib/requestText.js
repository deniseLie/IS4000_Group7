// Plain words for a support request, shared by the RM's Ticket inbox and the client's Requests page (rule-based, no model).
// A ticket summary from the urgent hand-over reads "[Topic] What happened. Cards on record: … Client’s words: “…”".
// Split it so the row shows a short title and the panel shows her words; any other summary is kept as it is.
export function ticketParts(summary) {
  const text=String(summary||'').trim();
  const words=text.match(/Client[’']s words:\s*[“"]([\s\S]*?)[”"]?\s*$/)?.[1]?.trim()||'';
  const body=text.replace(/Client[’']s words:[\s\S]*$/,'').trim();
  const cards=body.match(/Cards on record:\s*([^]*?)\.\s*$/)?.[1]?.trim()||'';
  const topic=body.match(/^\[([^\]]+)\]/)?.[1]||'';
  const lead=body.replace(/^\[[^\]]+\]\s*/,'').replace(/Cards on record:[^]*$/,'').trim().replace(/\.$/,'');
  return {topic,lead,cards,words};
}
// The row title: what it is about, in plain words, plus the first amount she mentions.
// A title in plain words: system wording a hand-over line may carry ("Recorded case status: documents_requested…")
// becomes the request type, internal phrasing for the RM is reworded for her, and very long lines are cut at a sentence.
function plain(text,who) {
  let value=String(text||'');
  if(/recorded case status|requested item status|preliminary outcome/i.test(value))return who==='you'?'Help with your periodic review':'Periodic review support';
  if(who==='you')value=value.replace(/^client question for you:\s*/i,'Your question: ');
  value=value.replace(/\b([a-z]+)_([a-z_]+)\b/g,(m)=>m.replace(/_/g,' '));
  if(value.length>110){const cut=value.slice(0,110);const end=Math.max(cut.lastIndexOf('. '),cut.lastIndexOf(': '));value=end>20?cut.slice(0,end):`${cut.replace(/\s+\S*$/,'')}…`;}
  return value;
}
export function ticketTitle(t,who='she') {
  const {lead:rawLead,words}=ticketParts(t.summary);
  const lead=rawLead&&plain(rawLead,who);
  const base=(who==='you'?(lead||t.category_label||'Request').replace(/\bthe client does not\b/gi,'you don’t').replace(/\bthe client\b/gi,'you'):(lead||t.category_label||'Request').replace(/\bthe client\b/gi,'she').replace(/\bdoes not\b/g,'doesn’t')).replace(/^./,c=>c.toLowerCase());
  const amount=(words||t.summary||'').match(/\b(?:SGD|USD|S\$)\s?[\d,]+(?:\.\d+)?/i)?.[0];
  const money=amount&&!base.includes(amount)?amount.toUpperCase().replace(/^S\$/,'SGD '):'';
  // She led with something else (e.g. her annual fee): say that first, so it doesn't read like the other request.
  const issues=ticketIssues(t);
  if(issues.length>1&&issues[0]!=='Card charge'&&issues.includes('Card charge'))return `${issues[0].toLowerCase()} · also a ${base}${money?` (${money})`:''}`;
  return `${base}${money?` · ${money}`:''}`;
}
// Small tags for each problem she mentions (rule-based: no model).
const ISSUES=[['Card charge',/charge|transaction|don.?t recogni|not recogni/i],['Transfer pending',/transfer[\s\S]{0,40}pending|pending[\s\S]{0,40}transfer/i],
  ['App locked',/locked|lock(ed)? out|can.?t log ?in/i],['Unexpected OTP',/\botp\b|one.?time (pass|code)/i],['Annual fee',/annual fee/i],['Address change',/address/i]];
// In the order she raised them (her words first; the hand-over line only fills in what she did not say).
export function ticketIssues(t) {
  const {words,lead}=ticketParts(t.summary);
  const at=(re,text)=>{const m=re.exec(text);return m?m.index:-1;};
  return ISSUES.map(([label,re])=>[label,at(re,words),at(re,lead)]).filter(([,w,l])=>w>=0||l>=0)
    .sort((a,b)=>(a[1]<0?1e6+a[2]:a[1])-(b[1]<0?1e6+b[2]:b[1])).map(([label])=>label);
}
// Possible fraud: an unknown charge together with a code she didn't ask for or a locked account.
export function possibleFraud(t) {const i=ticketIssues(t);return i.includes('Card charge')&&(i.includes('Unexpected OTP')||i.includes('App locked'));}
// Two requests are the SAME only with the same client, the same request type and the same words (case, spaces and
// punctuation ignored). Same type alone is not enough: a different question stays its own row.
export function sameRequestKey(t) {
  const {words}=ticketParts(t.summary);
  const said=String(words||t.summary||'').toLowerCase().replace(/[^\p{L}\p{N}]+/gu,' ').trim();
  return `${t.customer_id}|${t.category_label||t.category||''}|${said}`;
}
// Waiting-for-you tickets merged into rows (oldest ticket leads: you reply there), most urgent first.
const URGENCY_RANK={urgent:0,high:1,normal:2,low:3};
export function ticketRows(tickets) {
  const rows=new Map();
  for(const t of tickets||[]){const k=sameRequestKey(t);const row=rows.get(k);
    if(!row)rows.set(k,{key:k,lead:t,same:[]});
    else if((t.age_hours??0)>(row.lead.age_hours??0)){row.same.push(row.lead);row.lead=t;}else row.same.push(t);}
  return [...rows.values()].map(r=>({...r,count:r.same.length+1,overdue:[r.lead,...r.same].some(x=>x.overdue)}))
    .sort((a,b)=>(URGENCY_RANK[a.lead.urgency]??9)-(URGENCY_RANK[b.lead.urgency]??9)||(b.overdue-a.overdue)||((b.lead.age_hours??0)-(a.lead.age_hours??0)));
}
