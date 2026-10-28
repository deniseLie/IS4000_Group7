export function redact(text) {
  const normalized=text.normalize('NFKC').replace(/[\u200b\u200c\u200d\ufeff]/g,'');
  const cleaned=normalized
    .replace(/\b(?:password|passcode)\s*(?:is\s+|[:=]\s*)(?:"[^"\n]+"|'[^'\n]+'|[^\s,;"']+)/gi, '[private detail removed]')
    .replace(/\b(?:pin|otp|cvv|cvc|one[- ]time (?:password|code))\s*(?:is\s+|[:=]\s*)?(?:\d[ -]?){2,9}\d\b/gi, '[private detail removed]')
    // Same as privacy.py: a one-time code named earlier in the same sentence; not amounts or card endings.
    .replace(/\b(?:otps?|one[- ]time (?:passwords?|codes?|pins?)|(?:sms|verification|security|login|sign[- ]?in|authentication) codes?|tacs?)\b[^.!?\n\d$€£]{0,40}?(?<!\d)(?<!sgd )(?<!usd )(?<!ending )(?<!ending in )(?:\d[ -]?){3,7}\d(?!\d|[.,]\d)/gi, '[private detail removed]')
    .replace(/(?<!\d)(?:\d[ -]?){13,19}(?!\d)/g, '[private detail removed]')
    .replace(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi, '[private detail removed]')
    .replace(/\b[STFGM]\d{7}[A-Z]\b/gi, '[private detail removed]');
  return cleaned===normalized ? text : cleaned;
}

export function boundedHistory(messages) {
  const history = messages.filter(m => !m.error && m.content).map(({role,content}) => ({role,content:redact(content).slice(0,4000)}));
  while (history.length > 29 || history.reduce((n,m) => n + m.content.length,0) > 16000) history.shift();
  while (history.length > 1 && history[0].role !== 'user') history.shift();
  return history;
}

export async function readEvents(body, onEvent) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let pending = '';
  let completed = false;
  const dispatch = (block) => {
    const lines = block.split(/\r?\n/);
    const type = lines.find(l => l.startsWith('event:'))?.slice(6).trim() || 'message';
    const data = lines.filter(l => l.startsWith('data:')).map(l => l.slice(5).replace(/^ /,'')).join('\n');
    if (type === 'error') throw new Error(data || 'Assistant unavailable.');
    if (type === 'done') completed = true;
    onEvent(type,data);
  };
  try {
    while (true) {
      const {done,value} = await reader.read();
      pending += done ? decoder.decode() : decoder.decode(value,{stream:true});
      // Match CRLF only after both bytes arrive; preserve split stream chunks.
      let match;
      while ((match = /\r?\n\r?\n/.exec(pending))) {
        dispatch(pending.slice(0,match.index));
        pending = pending.slice(match.index + match[0].length);
      }
      if (done) break;
    }
    if (!completed) throw new Error('The response was interrupted. Please retry.');
  } finally { await reader.cancel().catch(() => {}); }
}

const TOPICS={retail:'Complex banking needs',fraud:'Fraud & security',compliance:'Compliance review',document:'Document check',relationship:'Relationship manager',smalltalk:'General conversation'};
// Staff handoff text: labels, counts and recorded outcomes only, so no customer wording or record values leave the chat.
export function handoffSummary(messages) {
  const sections=messages.filter(m=>m.role==='assistant'&&!m.error).flatMap(m=>m.sections||[]);
  const counts=new Map();
  for(const section of sections) {const label=(TOPICS[section.domain]||'Other')+(section.priority==='P0'?' (urgent)':'');counts.set(label,(counts.get(label)||0)+1);}
  const topics=[...counts].map(([label,count])=>count>1?`${label} ×${count}`:label).join(', ')||'General questions';
  const decision=sections.filter(section=>section.decision).at(-1)?.decision;
  // The urgent section's decision joins the block outcome and, when offered, the dispute draft outcome (fixed server wording).
  const block=decision===undefined?null:decision.includes('is temporarily blocked')?'confirmed':decision.includes('You chose not to block')?'declined':'not completed';
  const dispute=!decision?.includes('dispute draft')?null:/Your dispute draft for \d+ charges .*is recorded/.test(decision)?'sent to staff'
    :decision.includes('You chose not to send the dispute draft')?'declined':'not completed';
  const withheld=sections.filter(section=>section.response_origin==='validation').length;
  return ['Customer asked for a person.',`Topics: ${topics}.`,block&&`Card block: ${block}.`,dispute&&`Dispute draft: ${dispute}.`,withheld?`Answers withheld: ${withheld}.`:null].filter(Boolean).join(' ');
}

export function supportSummary(messages) {
  const questions=messages.filter(m => m.role==='user' && !m.error).slice(-5).map(m=>'- '+redact(m.content));
  return `Draft support summary — not sent\n\nRecent customer questions:\n${questions.join('\n') || '- No question entered yet.'}\n\nWhat still needs help:\n[Please describe the unresolved issue.]\n\nPlease review and remove any private details before sharing.`;
}
