import { useEffect, useState } from "react";
import { supabase } from "./lib/supabase";
import Login from "./Login";
import Chat from "./Chat";

export default function App() {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showLogin, setShowLogin] = useState(false);

  useEffect(() => {
    if (!supabase) { setLoading(false); return; }
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    }).catch(() => setLoading(false));

    const { data: { subscription } } =
      supabase.auth.onAuthStateChange((_event, session) => {
        setSession(session);
        setLoading(false);
        setShowLogin(false);
      });

    return () => subscription.unsubscribe();
  }, []);

  if (loading) return <div className="app-loading" role="status">Opening your banking assistant…</div>;

  if (showLogin && !session) return <Login onBack={() => setShowLogin(false)} />;
  return <Chat key={session?.user.id || 'guest'} session={session} onSignIn={() => setShowLogin(true)} />;
}

