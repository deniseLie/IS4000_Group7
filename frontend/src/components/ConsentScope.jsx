import Icon from './Icon';
import {CONSENT_CATEGORIES} from '../lib/consentData';

// Permitted categories come from active recorded grants for the current manager; everything else is restricted.
export function permittedCategories(registry,recipient) {
  const grants=registry.grants.filter(item=>item.active&&!item.revoked_at&&(!recipient||item.recipient===recipient.rm_id));
  return Object.keys(CONSENT_CATEGORIES).filter(code=>grants.some(item=>item.categories.includes(code)));
}

export default function ConsentScope({registry,recipient}) {
  const permitted=permittedCategories(registry,recipient);
  const restricted=Object.keys(CONSENT_CATEGORIES).filter(code=>!permitted.includes(code));
  return <div className="scope-grid" data-testid="consent-scope">
    <div className="scope-card permitted"><h3><span className="dot"/>Permitted</h3>
      {permitted.length?<ul>{permitted.map(code=><li key={code}><Icon name="check"/>{CONSENT_CATEGORIES[code]}</li>)}</ul>:<p>No categories are shared under an active grant.</p>}</div>
    <div className="scope-card restricted"><h3><Icon name="lock"/>Restricted</h3>
      {restricted.length?<ul>{restricted.map(code=><li key={code}><Icon name="close"/>{CONSENT_CATEGORIES[code]}</li>)}</ul>:<p>Every listed category is covered by an active grant.</p>}</div>
  </div>;
}
