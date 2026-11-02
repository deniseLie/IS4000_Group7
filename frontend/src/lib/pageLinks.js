// CR2: the page a personal question is about, so the answer can end with a "Go to …" button. Typo-tolerant on purpose.
const LINKS=[
  ['meetings',/\b(?:meetings?|appointments?|book(?:ing)? a (?:time|slot))\b/i,'Go to Meetings'],
  ['requests',/\b(?:requests?|tickets?|tck-\d+)\b/i,'Go to My requests'],
  ['compliance',/\b(?:compliance|kyc|review|documents?|docs?|upload|proof of address|source of wealth|tax residency)\b/i,'Go to Compliance review'],
  ['inbox',/\b(?:inbox|reminders?|notifications?)\b/i,'Go to Inbox'],
  ['cards',/\b(?:cards?|crads?|cadr|credit limit|annual fee|minimum payment|min payment)\b/i,'Go to Cards'],
  ['loans',/\b(?:loans?|mortgage)\b/i,'Go to Loans'],
  ['transactions',/\b(?:transactions?|txns?|spent|spend|spending|charged|charges?|fees?|purchases?)\b/i,'Go to Recent activity'],
  ['accounts',/\b(?:balance|bal|blance|accounts?|acc|acct)\b/i,'Go to Accounts'],
];
export function pageLink(question) {
  const text=String(question||'');
  // An ambiguous request for spending capacity is not a request to view past transactions.
  // Do not route the user based on the word 'spending' alone.
  if(/\b(?:more|extra|greater|additional)\s+(?:spending\s+(?:power|capacity|room)|purchase\s+power)\b/i.test(text))return null;
  // Suspected fraud is an urgent specialist workflow, not ordinary Recent Activity.
  if(/\b(?:someone|somebody)\s+(?:has\s+|might\s+have\s+)?used\b.{0,55}\b(?:my\s+)?(?:credit\s+|debit\s+)?card\b|\b(?:unauthori[sz]ed|unrecogni[sz]ed|suspicious|fraudulent)\s+(?:card\s+)?(?:charge|payment|transaction)\b/i.test(text))return null;
  for(const [key,pattern,label] of LINKS)if(pattern.test(text))return {key,label};
  return null;
}
