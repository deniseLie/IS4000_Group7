import { lazy, Suspense, useEffect, useState } from "react";
import { supabase } from "./lib/supabase";
import { setCacheOwner } from "./lib/cache";
import Login from "./Login";
import { isAdmin, isRm } from "./lib/roles";

// Each login uses one workspace, so each is its own download: a client never fetches the RM or admin code.
const Chat = lazy(() => import("./Chat"));
const RmWorkspace = lazy(() => import("./RmWorkspace"));
const AdminWorkspace = lazy(() => import("./AdminWorkspace"));

// A tab left open across a deploy asks for chunk files that no longer exist: reload once to get the new ones.
// At most once per 10 seconds, so a chunk that is really missing cannot cause a reload loop.
window.addEventListener("vite:preloadError", () => {
  if (Date.now() - Number(sessionStorage.getItem("chunk-reload") || 0) < 10000) return;
  sessionStorage.setItem("chunk-reload", String(Date.now()));
  location.reload();
});

export default function App() {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showLogin, setShowLogin] = useState(false);

  useEffect(() => {
    if (!supabase) { setLoading(false); return; }
    supabase.auth.getSession().then(({ data }) => {
      setCacheOwner(data.session?.user.id);
      setSession(data.session);
      setLoading(false);
    }).catch(() => setLoading(false));

    // The client-side read cache belongs to one user: a new sign-in or a sign-out empties it before any page renders.
    const { data: { subscription } } =
      supabase.auth.onAuthStateChange((_event, session) => {
        setCacheOwner(session?.user.id);
        setSession(session);
        setLoading(false);
        setShowLogin(false);
      });

    return () => subscription.unsubscribe();
  }, []);

  const opening = <div className="app-loading" role="status">Opening your banking assistant…</div>;
  if (loading) return opening;

  if (showLogin && !session) return <Login onBack={() => setShowLogin(false)} />;
  // Persona 2: a relationship manager (administrator-set app_metadata role) gets the RM workspace; the backend and the
  // database check the same role and her client book on every call.
  // The AI engineering dashboard: an administrator-set app_metadata role; the backend checks the same claim on every call.
  return <Suspense fallback={opening}>
    {session && isAdmin(session) ? <AdminWorkspace key={session.user.id} session={session} />
      : session && isRm(session) ? <RmWorkspace key={session.user.id} session={session} />
      : <Chat key={session?.user.id || 'guest'} session={session} onSignIn={() => setShowLogin(true)} />}
  </Suspense>;
}

