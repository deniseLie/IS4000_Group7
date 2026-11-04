import {sparkSegments} from '../../lib/admin';

// Small inline SVG charts (no chart library): the numbers are always shown next to them as text.
export function Sparkline({values,label}) {
  const segments=sparkSegments(values);
  if(!segments.length)return null;
  return <svg className="admin-spark" viewBox="0 0 100 28" preserveAspectRatio="none" role="img" aria-label={label}>
    {segments.map((points,index)=><polyline key={index} points={points}/>)}
  </svg>;
}

// A row of coloured squares, oldest first (a case's status in its last runs).
export function History({statuses,label}) {
  return <span className="admin-history" role="img" aria-label={`${label}: ${statuses.join(', ')}`}>
    {statuses.map((status,index)=><i key={index} className={`seg-${status}`} title={status}/>)}
  </span>;
}
