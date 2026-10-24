const paths = {
  chat: <path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2V11.5a9.5 9.5 0 0 1 19 0Z"/>,
  plus: <path d="M12 5v14M5 12h14"/>,
  bell: <><path d="M5 16h14l-2-3V8a5 5 0 0 0-10 0v5l-2 3Z"/><path d="M10 20h4"/></>,
  history: <><path d="M3 11a9 9 0 1 1 2 7M3 4v7h7"/><path d="M12 7v5l3 2"/></>,
  arrow: <path d="m7 17 10-10M7 7h10v10"/>,
  send: <path d="M12 19V5m-6 6 6-6 6 6"/>,
  account: <><path d="m3 9 9-6 9 6H3Zm2 3v7m7-7v7m7-7v7M3 22h18"/></>,
  card: <><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18M7 15h3"/></>,
  loan: <><path d="M14 2H5v20h14V7l-5-5Zm0 0v6h5M8 12h8m-8 4h6"/></>,
  shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-5"/></>,
  support: <><path d="M4 14v-3a8 8 0 0 1 16 0v6c0 3-2 4-5 4"/><rect x="2" y="11" width="4" height="7" rx="2"/><rect x="18" y="11" width="4" height="7" rx="2"/></>,
  user: <><circle cx="12" cy="8" r="4"/><path d="M4 22v-2a8 8 0 0 1 16 0v2"/></>,
  spark: <path d="m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3 3-7Z"/>,
  close: <path d="m6 6 12 12M6 18 18 6"/>,
  chevron: <path d="m6 9 6 6 6-6"/>,
  check: <path d="m5 12 5 5 9-10"/>,
  lock: <><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></>,
  calendar: <><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4m8-4v4"/></>,
  people: <><circle cx="9" cy="8" r="3.5"/><path d="M2 21v-1a7 7 0 0 1 14 0v1M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6 6 0 0 1 4 6v1"/></>,
  document: <><path d="M14 2H6v20h12V6l-4-4Zm0 0v4h4M9 13h6m-6 4h4"/></>,
  info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-9.5v.5"/></>,
  logout: <><path d="M15 4h4v16h-4M10 17l5-5-5-5M15 12H3"/></>,
};

export default function Icon({name}) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
