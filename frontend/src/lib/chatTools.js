export function redact(text) {
  const normalized=text.normalize('NFKC').replace(/[\u200b\u200c\u200d\ufeff]/g,'');
  const cleaned=normalized
    .replace(/\b(?:password|passcode)\s*(?:is\s+|[:=]\s*)(?:"[^"\n]+"|'[^'\n]+'|[^\s,;"']+)/gi, '[private detail removed]')
    .replace(/\b(?:pin|otp|cvv|cvc|one[- ]time (?:password|code))\s*(?:is\s+|[:=]\s*)?(?:\d[ -]?){2,9}\d\b/gi, '[private detail removed]')
    .replace(/(?<!\d)(?:\d[ -]?){13,19}(?!\d)/g, '[private detail removed]')
    .replace(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi, '[private detail removed]');
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

export function supportSummary(messages) {
  const questions=messages.filter(m => m.role==='user' && !m.error).slice(-5).map(m=>'- '+redact(m.content));
  return `Draft support summary — not sent\n\nRecent customer questions:\n${questions.join('\n') || '- No question entered yet.'}\n\nWhat still needs help:\n[Please describe the unresolved issue.]\n\nPlease review and remove any private details before sharing.`;
}
