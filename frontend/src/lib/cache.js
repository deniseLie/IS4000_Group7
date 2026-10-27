// The signed-in customer's recent reads, kept in this tab's memory only (never browser storage), so a page or panel
// opened again shows its last result at once while it refreshes in the background. Entries belong to one user:
// App sets the owner on every sign-in change, which empties the cache, and a read that finishes after the user
// changed is not kept. Pages still refresh on every open, and decisions stay disabled until the refresh lands.
const entries=new Map();
let owner=null;

export function setCacheOwner(userId) {if((userId||null)!==owner){entries.clear();owner=userId||null;}}
export function cacheOwner() {return owner;}
export function cached(name) {return owner?entries.get(`${owner}:${name}`)??null:null;}
// scope is the owner when the read started; pass it so a slow read never lands in another user's cache.
export function remember(name,value,scope=owner) {if(scope&&scope===owner)entries.set(`${scope}:${name}`,value);return value;}
