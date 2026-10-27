// One way to show dates, money and names: Singapore time, "27 Oct 2026" or "27 Oct 2026, 9:30 am", "SGD 5,230.45".
// Stored values are never changed; views that show exact recorded fields (Records names, Actions full details) keep them.
const ZONE='Asia/Singapore';
const DAY=/^\d{4}-\d\d-\d\d$/;  // a date-only value is a Singapore calendar day, not an instant

function instant(value) {return value instanceof Date?value:DAY.test(value)?new Date(`${value}T12:00:00+08:00`):new Date(value);}
export function formatDate(value,options={}) {return instant(value).toLocaleDateString('en-SG',{timeZone:ZONE,day:'numeric',month:'short',year:'numeric',...options});}
export function formatDateTime(value,options={}) {return instant(value).toLocaleString('en-SG',{timeZone:ZONE,day:'numeric',month:'short',year:'numeric',hour:'numeric',minute:'2-digit',hour12:true,...options});}
export function formatTime(value) {return instant(value).toLocaleTimeString('en-SG',{timeZone:ZONE,hour:'numeric',minute:'2-digit',hour12:true});}
export function formatAmount(value) {return value.toLocaleString('en-SG',{minimumFractionDigits:2,maximumFractionDigits:2});}
export function formatMoney(value,currency='SGD') {return `${currency} ${formatAmount(value)}`;}
// Seeded people carry a "(DEMO)" suffix; the page shows "Demo" once instead of after every name.
export function displayName(full) {return (full||'').replace(/\s*\(DEMO\)\s*$/,'');}
export function firstName(full) {return displayName(full).split(' ')[0];}
