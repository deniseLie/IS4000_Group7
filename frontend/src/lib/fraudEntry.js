// CR12: an entry into a confirmation-first report workflow, never a claim that a report was filed.
// The action appears only for the newest authenticated fraud reply. Explicit confirmation
// controls and recorded receipts have their own UI, so we must not offer duplicate actions.
export function canStartFraudReport({message,signedIn,latest}) {
  if(!signedIn||!latest||message?.role!=='assistant')return false;
  if(!message.sections?.some(section=>section.domain==='fraud'))return false;
  const text=String(message.content||'');
  if(/reply [“"]Yes, report it[”"]|reply [“"]Yes, report my card[”"]/i.test(text))return false;
  if(/\b(?:passed (?:this|it|them) to (?:our|the) fraud team|reference TCK-\d+)\b/i.test(text))return false;
  return true;
}
