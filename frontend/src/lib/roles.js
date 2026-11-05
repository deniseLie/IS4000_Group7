// Which workspace a login opens, from the administrator-set app_metadata role (never user_metadata). Kept apart from
// lib/rm.js and lib/admin.js so App.jsx can choose without loading either workspace; the backend checks the same claim.
export const ADMIN_ROLE='admin';
export function isAdmin(session) {return session?.user?.app_metadata?.demobank_role===ADMIN_ROLE;}
export function isRm(session) {return session?.user?.app_metadata?.demobank_role==='rm';}
