import Meetings from './Meetings';
import {displayName} from '../lib/format';

// Meetings with her relationship manager: its own page (it used to be a tab under My requests).
export default function MeetingsPage({rmName}) {
  const who=rmName?displayName(rmName):'your relationship manager';
  // The header shows on the list; an opened meeting has its own title (Meetings › This meeting).
  const header=<header className="page-header"><div><span className="eyebrow">With {who}</span><h1>Meetings</h1>
    <p>What’s booked, what needs your answer, and a quick way to ask for a meeting.</p></div></header>;
  return <div className="app-page-body" data-testid="meetings-page"><Meetings rmName={rmName} header={header}/></div>;
}
